<#
.SYNOPSIS
  Instala, atualiza ou remove o self-monitoring no Windows.

.DESCRIPTION
  - Copia o painel e um Python próprio para "C:\Program Files\SelfMonitoring" (só administradores podem
    alterar: o painel roda elevado, então nada que ele executa pode ficar numa pasta do usuário).
  - Se faltar, instala o Python 3.13 oficial (python.org) e o Git pelo winget. O Python da Microsoft Store não serve.
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
    [switch]$Elevated,
    [switch]$Unattended      # sem perguntas nem pausa (atualização pedida pelo painel: tools\atualizar-painel.ps1)
)

$ErrorActionPreference = "Stop"
$App = "SelfMonitoring"
$Dest = Join-Path $env:ProgramFiles $App
$Src = $PSScriptRoot
$TaskName = $App
$UpdateTask = "$App Atualizar"
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

# A tarefa roda no logon de $ForUser. Se o instalador foi aberto como OUTRA conta de administrador (terminal
# "Executar como outro usuário", conta de admin separada), o painel ficaria na conta errada: confere com quem está
# usando o PC (dono da sessão do console) e pergunta.
if (-not $Uninstall -and -not $SetPassword -and -not $Unattended) {
    $consoleUser = ""
    try { $consoleUser = [string](Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).UserName } catch {}
    if ($consoleUser -and $consoleUser -ne $ForUser -and ($consoleUser -split '\\')[-1] -ne ($ForUser -split '\\')[-1]) {
        Write-Host ""
        Write-Host "Atenção: o painel seria instalado para a conta '$ForUser', mas quem está usando o PC é '$consoleUser'." -ForegroundColor Yellow
        Write-Host "O painel roda no logon dessa conta: áudio, tela e apps seriam os dela." -ForegroundColor Yellow
        $ans = Read-Host "Instalar para '$consoleUser'? [S/n] (para escolher outra: install.cmd -ForUser CONTA)"
        if ($ans -notmatch '^[nN]') { $ForUser = $consoleUser }
    }
}

function Pause-IfElevated {
    if ($Elevated -and -not $Unattended) { Write-Host ""; Read-Host "Pressione Enter para fechar" | Out-Null }
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

# region requisitos
# Num Windows novo, "python" é um atalho falso (pasta WindowsApps) que abre a Microsoft Store: não chamamos esse.
# Procura pelo registro (PEP 514: onde os instaladores do python.org se cadastram; o lançador py.exe usa o mesmo
# registro) e pelo PATH.
function Find-Python {
    $cands = New-Object System.Collections.Generic.List[string]
    foreach ($hive in @("HKLM:\SOFTWARE\Python\PythonCore", "HKCU:\SOFTWARE\Python\PythonCore", "HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore")) {
        Get-ChildItem $hive -ErrorAction SilentlyContinue | ForEach-Object {
            $ip = Get-ItemProperty (Join-Path $_.PSPath "InstallPath") -ErrorAction SilentlyContinue
            $exe = if ($ip.ExecutablePath) { $ip.ExecutablePath } elseif ($ip.'(default)') { Join-Path $ip.'(default)' "python.exe" }
            if ($exe) { $cands.Add($exe) }
        }
    }
    $cmd = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") { $cands.Add($cmd.Source) }
    $best = $null
    foreach ($exe in ($cands | Select-Object -Unique)) {
        if (-not (Test-Path $exe) -or $exe -match "WindowsApps") { continue }
        try {
            $out = Invoke-Native { & $exe -c "import sys, struct; print(sys.base_prefix); print('%d.%d' % sys.version_info[:2]); print(struct.calcsize('P') * 8)" 2>$null }
        } catch { continue }
        if ($LASTEXITCODE -ne 0 -or $out.Count -lt 3) { continue }
        $v = [version]$out[1]
        # 64 bits (o painel usa APIs de 64 bits) e fora da pasta da Store
        if ($v -ge [version]"3.11" -and $out[2] -eq "64" -and -not $out[0].Contains("WindowsApps")) {
            if (-not $best -or $v -gt $best[1]) { $best = @($out[0], $v) }
        }
    }
    return $best
}

# O git não é usado pelo painel, só para baixar e atualizar o repositório (atualizar.cmd).
function Install-GitIfMissing {
    if (Get-Command git -ErrorAction SilentlyContinue) { return }
    if (Test-Path "$env:ProgramFiles\Git\cmd\git.exe") { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Say "Git não encontrado: o atualizar.cmd precisa dele. Instale em https://git-scm.com/download/win (o painel funciona sem)." Yellow
        return
    }
    Step "Git não encontrado: instalando (usado pelo atualizar.cmd para baixar as versões novas)"
    Invoke-Native { winget install --id Git.Git --exact --source winget --scope machine --silent --accept-source-agreements --accept-package-agreements --disable-interactivity }
    if ($LASTEXITCODE -ne 0) { Say "Não foi possível instalar o Git (winget $LASTEXITCODE). O painel funciona sem; só o atualizar.cmd precisa dele." Yellow }
}
# endregion requisitos

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
        Unregister-ScheduledTask -TaskName $UpdateTask -Confirm:$false -ErrorAction SilentlyContinue
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
    $found = Find-Python
    if (-not $found) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw ("Python 3.11+ não encontrado, e o winget (que o instalaria sozinho) também não.`n" +
                "  Opção 1: instale o 'Instalador de Aplicativo' da Microsoft Store (traz o winget) e rode este instalador de novo.`n" +
                "  Opção 2: baixe o Python 3.13 em https://www.python.org/downloads/windows/ , instale e rode este instalador de novo.`n" +
                "  O Python da Microsoft Store não serve.")
        }
        Step "Python não encontrado: instalando o Python 3.13 oficial (python.org, pelo winget; leva 1 a 3 minutos)"
        Invoke-Native { winget install --id Python.Python.3.13 --exact --source winget --scope machine --silent --accept-source-agreements --accept-package-agreements --disable-interactivity }
        $wingetCode = $LASTEXITCODE
        $found = Find-Python
        if (-not $found) {
            throw "A instalação do Python não deu certo (winget $wingetCode). Instale o Python 3.13 em https://www.python.org/downloads/windows/ e rode de novo."
        }
    }
    $base, $ver = $found
    Say "Python base: $base ($ver)"
    Install-GitIfMissing

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
        Invoke-Native { winget install --id Intel.PresentMon.Console --exact --source winget --scope machine --accept-source-agreements --accept-package-agreements --disable-interactivity }
    }
    if ($LHM) {
        Step "Instalando o LibreHardwareMonitor (sensores)"
        Invoke-Native { winget install --id LibreHardwareMonitor.LibreHardwareMonitor --exact --source winget --accept-source-agreements --accept-package-agreements --disable-interactivity }
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

    # Atualização pelo painel: só roda quando o usuário clica em "Atualizar agora" (não tem gatilho de horário). O script
    # fica em Arquivos de Programas e baixa o GitHub numa cópia protegida (ver tools\atualizar-painel.ps1).
    $upd = Join-Path $Dest "tools\atualizar-painel.ps1"
    # Abre uma janela no PC com o andamento (a saída também vai para o update.log) e espera o Enter no fim.
    $uAction = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $Dest `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$upd`" -Janela"
    $uSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $UpdateTask -Action $uAction -Principal $principal -Settings $uSettings `
        -Description "Atualiza o self-monitoring quando pedido pelo painel (não roda sozinha)" -Force | Out-Null

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
