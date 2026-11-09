<#
.SYNOPSIS
  Coleta o que é preciso para entender por que algo não aparece no painel (tarefa, sessão, sensores, FPS, áudio, tela).

.DESCRIPTION
  Não muda nada no PC. Rode num PowerShell (de preferência como administrador, para ver tudo) e cole a saída:

    powershell -ExecutionPolicy Bypass -File "C:\Program Files\SelfMonitoring\tools\diagnostico.ps1"
#>
$ErrorActionPreference = "Continue"
$Dest = Join-Path $env:ProgramFiles "SelfMonitoring"
function Sec($t) { Write-Host "`n===== $t =====" -ForegroundColor Cyan }
function Try-Run([scriptblock]$b) { try { & $b } catch { Write-Host "falhou: $($_.Exception.Message)" -ForegroundColor Yellow } }

Sec "Versão"
$init = Join-Path $Dest "sm\__init__.py"
if (Test-Path $init) { (Select-String -Path $init -Pattern '__version__\s*=\s*"([^"]+)"').Matches | ForEach-Object { "instalada: $($_.Groups[1].Value)" } }
else { "não instalado em $Dest" }
"Windows: $((Get-CimInstance Win32_OperatingSystem).Caption) build $([Environment]::OSVersion.Version.Build)"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
"este terminal: $env:USERDOMAIN\$env:USERNAME (administrador: $isAdmin)"

Sec "Tarefa agendada"
Try-Run {
    $t = Get-ScheduledTask -TaskName SelfMonitoring -ErrorAction Stop
    $i = $t | Get-ScheduledTaskInfo
    "estado: $($t.State) · usuário: $($t.Principal.UserId) · elevada: $($t.Principal.RunLevel)"
    "gatilho: logon de $($t.Triggers | ForEach-Object { $_.UserId })"
    "última execução: $($i.LastRunTime) · resultado: 0x{0:X}" -f $i.LastTaskResult
    "ação: $($t.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" })"
}

Sec "Processo do painel"
if (-not $isAdmin) { "(sem administrador o processo elevado do painel pode não aparecer)" }
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.ExecutablePath -like "$Dest\*" } |
    ForEach-Object {
        $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
        "PID $($_.ProcessId) · sessão $($_.SessionId) · conta $($o.Domain)\$($o.User) · $($_.CommandLine)"
    }

Sec "Sessões (quem está logado)"
Try-Run { quser 2>&1 }
"dono do console: $((Get-CimInstance Win32_ComputerSystem).UserName)"
Try-Run {
    $w = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -ErrorAction Stop
    "login automático: $($w.AutoAdminLogon) · conta: $($w.DefaultUserName)"
}

Sec "Porta e firewall"
$cfg = Join-Path $Dest "config.toml"
$port = 8765
if (Test-Path $cfg) { $m = Select-String -Path $cfg -Pattern '^\s*port\s*=\s*(\d+)' | Select-Object -First 1; if ($m) { $port = [int]$m.Matches[0].Groups[1].Value } }
"porta: $port"
Try-Run { Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop | ForEach-Object { "escutando em $($_.LocalAddress):$($_.LocalPort) (PID $($_.OwningProcess))" } }
Try-Run { Get-NetFirewallRule -DisplayName "Self Monitoring*" -ErrorAction Stop | ForEach-Object { "regra '$($_.DisplayName)': ativa=$($_.Enabled) perfis=$($_.Profile)" } }
Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object { "rede '$($_.Name)': $($_.NetworkCategory)" }

Sec "Placa-mãe"
Try-Run {
    $b = Get-CimInstance Win32_BaseBoard
    "placa: $($b.Manufacturer) $($b.Product)"
    "BIOS: $((Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion)"
}

Sec "LibreHardwareMonitor (sensores)"
$lhm = Get-Process LibreHardwareMonitor -ErrorAction SilentlyContinue
if ($lhm) { "rodando: PID $($lhm.Id -join ', ') · $($lhm[0].Path)" } else { "NÃO está rodando" }
Try-Run { Get-ScheduledTask -TaskName "LibreHardwareMonitor*" -ErrorAction Stop | ForEach-Object { "tarefa '$($_.TaskName)': $($_.State)" } }
"driver PawnIO: $(if (Get-Service PawnIO -ErrorAction SilentlyContinue) { (Get-Service PawnIO).Status } else { 'não instalado' })"
Try-Run {
    $d = Invoke-RestMethod "http://127.0.0.1:8085/data.json" -TimeoutSec 3 -ErrorAction Stop
    $stack = @($d); $fans = 0; $temps = 0; $hw = @()
    while ($stack.Count) {
        $n, $stack = $stack[0], @($stack | Select-Object -Skip 1)
        if ($n.ImageURL -match 'mainboard|cpu|nvidia|ati|intel|ram|hdd|nic|chip') { if ($n.Text) { $hw += $n.Text } }
        if ($n.Type -eq "Fan") { $fans++ }
        if ($n.Type -eq "Temperature") { $temps++ }
        if ($n.Children) { $stack += $n.Children }
    }
    "servidor web responde · temperaturas: $temps · ventoinhas: $fans"
    "hardware: $(($hw | Select-Object -Unique) -join ' | ')"
}

Sec "PresentMon (FPS)"
$pm = @(Get-ChildItem "$env:ProgramFiles\WinGet\Links\presentmon.exe", "$Dest\tools\PresentMon*.exe" -ErrorAction SilentlyContinue)
$pm += @(Get-ChildItem "$env:ProgramFiles\WinGet\Packages" -Recurse -Filter "PresentMon*.exe" -ErrorAction SilentlyContinue | Select-Object -First 2)
if ($pm) { $pm | ForEach-Object { "encontrado: $($_.FullName)" } } else { "não encontrado (instale com: install.cmd -PresentMon)" }
$pmp = Get-Process PresentMon* -ErrorAction SilentlyContinue
"rodando agora: $(if ($pmp) { $pmp.Id -join ', ' } else { 'não' })"
Try-Run {
    $s = Get-ItemProperty "HKCU:\Software\Valve\Steam" -ErrorAction Stop
    "Steam RunningAppID: $($s.RunningAppID)"
}

Sec "Áudio (dispositivos)"
Try-Run { Get-CimInstance Win32_SoundDevice | ForEach-Object { "$($_.Name) · $($_.Status)" } }
"serviço de áudio: $((Get-Service Audiosrv).Status)"

Sec "Monitores"
Try-Run {
    Get-CimInstance -Namespace root\wmi WmiMonitorID -ErrorAction Stop | ForEach-Object {
        $name = -join ($_.UserFriendlyName | Where-Object { $_ } | ForEach-Object { [char]$_ })
        "$name · ativo: $($_.Active)"
    }
}
Try-Run { Get-CimInstance Win32_VideoController | ForEach-Object { "GPU: $($_.Name) · driver $($_.DriverVersion) · $($_.CurrentHorizontalResolution)x$($_.CurrentVerticalResolution)" } }

Sec "Log do painel (últimas linhas)"
$log = Join-Path $Dest "data\self-monitoring.log"
if (-not $isAdmin) { "(rode como administrador para ver o log e o processo do painel)" }
elseif (Test-Path $log) { Get-Content $log -Tail 25 } else { "sem log em $log" }
