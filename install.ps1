<#
.SYNOPSIS
  Instala, atualiza ou remove o self-monitoring no Windows.

.DESCRIPTION
  - Copia o painel e um Python próprio para "C:\Program Files\SelfMonitoring" (só administradores podem
    alterar: o painel roda elevado, então nada que ele executa pode ficar numa pasta do usuário).
  - Instala as dependências (psutil, pycaw, winrt) nesse Python.
  - Cria a tarefa agendada "SelfMonitoring": inicia oculta no logon do usuário, como administrador.
  - Libera a porta no Firewall do Windows (só redes Privadas/Domínio).

.EXAMPLE
  .\install.cmd                    instala ou atualiza
  .\install.cmd -PresentMon        também instala o PresentMon (FPS dos jogos)
  .\install.cmd -LHM               também instala o LibreHardwareMonitor (temperaturas e ventoinhas)
  .\install.cmd -SetPassword       troca a senha do painel
  .\install.cmd -Uninstall         remove (mantém configuração e dados)
  .\install.cmd -Uninstall -Purge  remove tudo
#>
param(
    [switch]$Uninstall,
    [switch]$Purge,
    [switch]$SetPassword,
    [switch]$PresentMon,
    [switch]$LHM,
    [int]$Port = 0,
    [string]$ForUser = "",
    [switch]$Elevated
)

$ErrorActionPreference = "Stop"
$App = "SelfMonitoring"
$Dest = Join-Path $env:ProgramFiles $App
$Src = $PSScriptRoot
$TaskName = $App
$FwName = "Self Monitoring (painel)"

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# Programas externos (pip, py, winget) escrevem avisos na saída de erro. No PowerShell 5.1, com
# $ErrorActionPreference = "Stop", isso vira erro fatal; o que vale é o código de saída ($LASTEXITCODE).
function Invoke-Native([scriptblock]$Block) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block } finally { $ErrorActionPreference = $old }
}

function Test-Admin {
    $p = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------- elevação ----------
if (-not (Test-Admin)) {
    $user = "$env:USERDOMAIN\$env:USERNAME"     # a tarefa roda no logon de quem pediu a instalação
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-ForUser", "`"$user`"", "-Elevated")
    foreach ($k in $PSBoundParameters.Keys) {
        $v = $PSBoundParameters[$k]
        if ($v -is [switch]) { if ($v) { $argList += "-$k" } } else { $argList += "-$k"; $argList += "`"$v`"" }
    }
    Say "Pedindo permissão de administrador..." Yellow
    try {
        Start-Process powershell.exe -Verb RunAs -ArgumentList $argList -Wait
    } catch {
        Say "Permissão negada. A instalação precisa de administrador." Red
        exit 1
    }
    exit 0
}
if (-not $ForUser) { $ForUser = "$env:USERDOMAIN\$env:USERNAME" }

function Pause-IfElevated {
    if ($Elevated) { Write-Host ""; Read-Host "Pressione Enter para fechar" | Out-Null }
}

function Stop-Panel {
    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop } catch {}
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith("$Dest\", [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
}

# region LHM
# O painel lê os sensores pelo servidor web do LibreHardwareMonitor. Ele só inicia ligado se a opção estiver gravada no arquivo
# de configuração, que é salvo ao fechar o programa: desligar o PC com ele aberto perde o "Run" clicado no menu.
function Enable-LhmWebServer {
    param(
        [string]$Exe = "",
        [string]$Task = "LibreHardwareMonitor",
        [string]$Url = "http://127.0.0.1:8085/data.json",
        [int]$WaitSeconds = 20
    )
    $name = "LibreHardwareMonitor"
    if (-not $Exe) {
        $action = (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue).Actions | Select-Object -First 1
        $Exe = if ($action) { $action.Execute } else { (Get-Process $name -ErrorAction SilentlyContinue | Select-Object -First 1).Path }
    }
    if (-not $Exe) { return "ausente" }
    $name = [IO.Path]::GetFileNameWithoutExtension($Exe)
    $config = Join-Path (Split-Path $Exe) "LibreHardwareMonitor.config"
    if (-not (Test-Path $config)) { return "sem-config" }

    function Read-Option {
        $doc = New-Object System.Xml.XmlDocument
        $doc.PreserveWhitespace = $true
        $doc.Load($config)
        return $doc
    }
    $key = "//appSettings/add[@key='runWebServerMenuItem']"
    if ((Read-Option).SelectSingleNode($key).value -eq "true") { return "ja-ligado" }

    # fecha antes de gravar: ao sair, o programa reescreve o arquivo e apagaria a mudança
    $running = @(Get-Process $name -ErrorAction SilentlyContinue)
    foreach ($p in $running) {
        [void]$p.CloseMainWindow()
        if (-not $p.WaitForExit(5000)) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; [void]$p.WaitForExit(5000) }
    }
    Copy-Item -Force $config "$config.bak"
    $doc = Read-Option
    $node = $doc.SelectSingleNode($key)
    if (-not $node) {
        $node = $doc.CreateElement("add")
        $node.SetAttribute("key", "runWebServerMenuItem")
        [void]$doc.SelectSingleNode("//appSettings").AppendChild($node)
    }
    $node.SetAttribute("value", "true")
    $doc.Save($config)

    if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) { Start-ScheduledTask -TaskName $Task }
    else { Start-Process -FilePath $Exe -WorkingDirectory (Split-Path $Exe) }
    $limit = (Get-Date).AddSeconds($WaitSeconds)
    do {
        try {
            if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $Url).StatusCode -eq 200) { return "ligado" }
        } catch { Start-Sleep -Milliseconds 500 }
    } while ((Get-Date) -lt $limit)
    return "sem-resposta"
}
# endregion LHM

$Py = Join-Path $Dest "python\python.exe"
$PyW = Join-Path $Dest "python\pythonw.exe"

try {
    # ---------- trocar senha ----------
    if ($SetPassword) {
        if (-not (Test-Path $Py)) { throw "O self-monitoring não está instalado em $Dest." }
        Invoke-Native { & $Py -X utf8 (Join-Path $Dest "server.py") --set-password }
        Step "Reiniciando o painel"
        Stop-Panel
        Start-ScheduledTask -TaskName $TaskName
        Pause-IfElevated
        exit 0
    }

    # ---------- desinstalar ----------
    if ($Uninstall) {
        Step "Removendo o self-monitoring"
        Stop-Panel
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Get-NetFirewallRule -DisplayName $FwName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        if (Test-Path $Dest) {
            if ($Purge) {
                Remove-Item -Recurse -Force $Dest
                Say "Tudo removido (inclusive configuração e dados)."
            } else {
                Get-ChildItem $Dest | Where-Object { $_.Name -notin @("data", "config.toml") } | Remove-Item -Recurse -Force
                Say "Removido. Configuração e dados mantidos em $Dest (use -Purge para apagar)."
            }
        }
        Remove-Item "HKCU:\Software\Classes\AppUserModelId\SelfMonitoring.Panel" -Recurse -ErrorAction SilentlyContinue
        Pause-IfElevated
        exit 0
    }

    # ---------- Python ----------
    Step "Procurando o Python (3.11 ou mais novo)"
    $base = $null
    foreach ($cand in @(@("py", "-3"), @("python"))) {
        try {
            $out = Invoke-Native { & $cand[0] $cand[1..9] -c "import sys; print(sys.base_prefix); print('%d.%d' % sys.version_info[:2])" 2>$null }
            if ($LASTEXITCODE -eq 0 -and $out.Count -ge 2) {
                $ver = [version]$out[1]
                if ($ver -ge [version]"3.11" -and -not $out[0].Contains("WindowsApps")) { $base = $out[0]; break }
            }
        } catch {}
    }
    if (-not $base) {
        throw "Python 3.11+ não encontrado. Instale com:  winget install Python.Python.3.13  (o da Microsoft Store não serve) e rode de novo."
    }
    Say "Python base: $base ($ver)"

    Step "Parando o painel (se estiver rodando)"
    Stop-Panel

    Step "Copiando o Python para $Dest\python"
    New-Item -ItemType Directory -Force $Dest | Out-Null
    # /MIR com /XD: as pastas excluídas não são copiadas nem apagadas no destino (mantém o site-packages)
    robocopy $base (Join-Path $Dest "python") /MIR /NFL /NDL /NJH /NJS /NP `
        /XD "$base\Lib\site-packages" "$base\Scripts" "$base\Doc" "$base\tcl" "$base\Lib\test" "$base\Lib\idlelib" "$base\Lib\tkinter" "__pycache__" `
        /XF "*.pyc" | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Falha ao copiar o Python (robocopy $LASTEXITCODE)." }

    Step "Instalando dependências"
    Invoke-Native { & $Py -m ensurepip --upgrade 2>&1 | Out-Null }
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar o pip no Python copiado (ensurepip)." }
    Invoke-Native { & $Py -m pip install --upgrade --disable-pip-version-check --no-warn-script-location -q -r (Join-Path $Src "requirements.txt") }
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependências (pip)." }

    Step "Copiando o painel"
    foreach ($d in @("sm", "web", "tools")) {
        if (Test-Path (Join-Path $Src $d)) {
            robocopy (Join-Path $Src $d) (Join-Path $Dest $d) /MIR /NFL /NDL /NJH /NJS /NP /XD "__pycache__" /XF "*.pyc" | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "Falha ao copiar $d (robocopy $LASTEXITCODE)." }
        }
    }
    foreach ($f in @("server.py", "install.ps1", "install.cmd", "config.example.toml", "requirements.txt", "README.md", "CHANGELOG.md", "PARIDADE.md")) {
        if (Test-Path (Join-Path $Src $f)) { Copy-Item -Force (Join-Path $Src $f) (Join-Path $Dest $f) }
    }

    # ---------- dados e configuração ----------
    $data = Join-Path $Dest "data"
    New-Item -ItemType Directory -Force $data | Out-Null
    # senha (hash), sessões, capturas de tela: só administradores e o sistema leem
    icacls $data /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" | Out-Null
    $conf = Join-Path $Dest "config.toml"
    if (-not (Test-Path $conf)) {
        Copy-Item (Join-Path $Dest "config.example.toml") $conf
        Say "Configuração criada: $conf"
    }
    if ($Port -gt 0) {
        $text = (Get-Content $conf -Raw -Encoding UTF8) -replace '(?m)^port\s*=\s*\d+', "port = $Port"
        [IO.File]::WriteAllText($conf, $text, (New-Object Text.UTF8Encoding $false))   # sem BOM (o TOML não aceita)
    }
    $portLine = Select-String -Path $conf -Pattern '^\s*port\s*=\s*(\d+)' | Select-Object -First 1
    $PanelPort = if ($portLine) { [int]$portLine.Matches[0].Groups[1].Value } else { 8765 }

    # ---------- extras ----------
    if ($PresentMon) {
        Step "Instalando o PresentMon (FPS dos jogos)"
        Invoke-Native { winget install --id Intel.PresentMon.Console --scope machine --accept-source-agreements --accept-package-agreements --disable-interactivity }
    }
    if ($LHM) {
        Step "Instalando o LibreHardwareMonitor (sensores)"
        Invoke-Native { winget install --id LibreHardwareMonitor.LibreHardwareMonitor --accept-source-agreements --accept-package-agreements --disable-interactivity }
    }
    Step "Conferindo o servidor web do LibreHardwareMonitor (sensores)"
    try {
        switch (Enable-LhmWebServer) {
            "ausente" { Say "LibreHardwareMonitor não encontrado. Para temperaturas e ventoinhas: .\install.cmd -LHM" }
            "sem-config" {
                Say "Abra o LibreHardwareMonitor como administrador uma vez e rode o instalador de novo: ele liga o servidor web." Yellow
                Say "Para iniciar junto com o Windows: Options > Run On Windows Startup." Yellow
            }
            "ja-ligado" { Say "Já está ligado." }
            "ligado" { Say "Ligado: agora ele inicia com o servidor web, sem precisar clicar em Run." Green }
            "sem-resposta" { Say "A opção foi gravada, mas ele não respondeu. Abra o LibreHardwareMonitor e confira a porta 8085." Yellow }
        }
    } catch {
        Say "Não foi possível ajustar o LibreHardwareMonitor: $($_.Exception.Message)" Yellow
    }

    # ---------- firewall ----------
    Step "Liberando a porta $PanelPort no firewall (redes Privadas)"
    Get-NetFirewallRule -DisplayName $FwName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $FwName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $PanelPort `
        -Program $PyW -Profile Private, Domain | Out-Null
    $public = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object NetworkCategory -eq "Public"
    if ($public) {
        Say ("Atenção: a rede '{0}' está como Pública; o celular não vai conseguir acessar. " -f $public[0].Name) Yellow
        Say "Mude para Privada em Configurações > Rede e Internet > (sua conexão) > Tipo de perfil de rede." Yellow
    }

    # ---------- tarefa agendada ----------
    Step "Criando a tarefa '$TaskName' (inicia no logon de $ForUser, como administrador)"
    $action = New-ScheduledTaskAction -Execute $PyW -Argument "-X utf8 `"$Dest\server.py`"" -WorkingDirectory $Dest
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $ForUser
    $principal = New-ScheduledTaskPrincipal -UserId $ForUser -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
        -Description "Painel self-monitoring (http://localhost:$PanelPort)" -Force | Out-Null

    Step "Iniciando o painel"
    Start-ScheduledTask -TaskName $TaskName
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try { Invoke-WebRequest "http://127.0.0.1:$PanelPort/login" -UseBasicParsing -TimeoutSec 2 -MaximumRedirection 0 -ErrorAction Stop | Out-Null; $ok = $true; break }
        catch { if ($_.Exception.Response) { $ok = $true; break } }
    }
    if (-not $ok) {
        Say "O painel não respondeu. Veja o log: $data\self-monitoring.log" Red
    } else {
        Say "Painel rodando." Green
        $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.InterfaceAlias -notmatch 'vEthernet|Loopback' } |
            Select-Object -ExpandProperty IPAddress
        Say "`nNesta máquina:  http://localhost:$PanelPort" Green
        foreach ($ip in $ips) { Say "Na rede:        http://${ip}:$PanelPort" Green }
        if (-not (Test-Path (Join-Path $data "auth.json"))) {
            Say "`nCadastre a senha no navegador que vai abrir (só funciona nesta máquina)." Yellow
            Start-Process explorer.exe "http://localhost:$PanelPort/setup"   # pelo Explorer: o navegador não abre como admin
        }
    }
    Say "`nConfiguração: $conf  (depois de editar, rode este instalador de novo ou reinicie o PC)"
    Say "Log:          $data\self-monitoring.log"
} catch {
    Say "`nErro: $($_.Exception.Message)" Red
    Pause-IfElevated
    exit 1
}
Pause-IfElevated
