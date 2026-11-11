<#
.SYNOPSIS
  Atualização pedida pelo painel (botão "Atualizar agora" ou o ícone perto do relógio). Não roda sozinha.

.DESCRIPTION
  Roda pela tarefa agendada "SelfMonitoring Atualizar", como administrador, a partir da cópia em
  C:\Program Files\SelfMonitoring\tools (só administradores alteram essa pasta).
  Baixa a versão do GitHub numa cópia também protegida (C:\Program Files\SelfMonitoring\repo) e roda o instalador
  dela. A pasta do projeto do usuário (de onde se roda o install.cmd) não é usada: um programa comum consegue alterar
  aquela pasta, e o que roda aqui roda como administrador.
  O andamento fica em data\update-status.json (o painel mostra) e a saída completa em data\update.log.
#>
param(
    [switch]$Forcar,
    [string]$Dest = (Join-Path $env:ProgramFiles "SelfMonitoring"),   # outra pasta só para testar
    [switch]$SemInstalar                                              # teste: baixa e compara, sem instalar
)

$ErrorActionPreference = "Stop"
$Data = Join-Path $Dest "data"
$Clone = Join-Path $Dest "repo"
$StatusFile = Join-Path $Data "update-status.json"
$Branch = "main"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Get-Version([string]$File) {
    if ((Test-Path $File) -and ((Get-Content -Raw -Encoding UTF8 $File) -match '__version__\s*=\s*"([^"]+)"')) { return $Matches[1] }
    return $null
}
function Set-Status([string]$State, [string]$Message, [string]$To = $null) {
    $obj = [ordered]@{ state = $State; message = $Message; from = $script:From; to = $To; ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }   # (o -UFormat %s do PS 5.1 usa a hora local)
    [IO.File]::WriteAllText($StatusFile, ($obj | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding $false))
    Write-Host "[$State] $Message"
}
function Invoke-Native([scriptblock]$Block) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Block } finally { $ErrorActionPreference = $old }
}

$script:From = Get-Version (Join-Path $Dest "sm\__init__.py")
try {
    Set-Status "running" "Baixando a versão nova do GitHub..."

    # repositório: o mesmo que a aba Atualização consulta ([update] repo no config.toml)
    $Repo = "thematheusk3/self-monitoring-win"
    $conf = Join-Path $Dest "config.toml"
    if (Test-Path $conf) {
        $m = Select-String -Path $conf -Pattern '^\s*repo\s*=\s*"([\w.-]+/[\w.-]+)"' | Select-Object -First 1
        if ($m) { $Repo = $m.Matches[0].Groups[1].Value }
    }

    $git = (Get-Command git -ErrorAction SilentlyContinue).Source
    if (-not $git -and (Test-Path "$env:ProgramFiles\Git\cmd\git.exe")) { $git = "$env:ProgramFiles\Git\cmd\git.exe" }
    if (-not $git) { throw "O Git não está instalado. Rode o install.cmd uma vez (ele instala o Git) ou: winget install Git.Git" }

    if (Test-Path (Join-Path $Clone ".git")) {
        Invoke-Native { & $git -C $Clone fetch --quiet --depth 1 origin $Branch 2>&1 } | Write-Host
        if ($LASTEXITCODE) { throw "Não foi possível falar com o GitHub. Verifique a internet do PC." }
        # cópia só do painel: descarta qualquer diferença e fica igual ao GitHub
        Invoke-Native { & $git -C $Clone reset --quiet --hard "origin/$Branch" 2>&1 } | Write-Host
        if ($LASTEXITCODE) { throw "Falha ao preparar a cópia do GitHub (git reset)." }
    } else {
        if (Test-Path $Clone) { Remove-Item -Recurse -Force $Clone }
        Invoke-Native { & $git clone --quiet --depth 1 --branch $Branch "https://github.com/$Repo.git" $Clone 2>&1 } | Write-Host
        if ($LASTEXITCODE) { throw "Não foi possível baixar do GitHub ($Repo). Verifique a internet do PC." }
    }

    $to = Get-Version (Join-Path $Clone "sm\__init__.py")
    if (-not $to) { throw "A cópia baixada não tem a versão do painel (sm\__init__.py)." }
    if ($to -eq $script:From -and -not $Forcar) {
        Set-Status "current" "Já está na versão mais recente ($to)." $to
        exit 0
    }

    if ($SemInstalar) {
        Set-Status "current" "Teste: a versão $to foi baixada e não foi instalada." $to
        exit 0
    }
    Set-Status "installing" "Instalando a versão $to. O painel fica fora do ar por cerca de 1 minuto." $to
    # Já elevado, o instalador roda aqui mesmo: para o painel, copia os arquivos e inicia a tarefa de novo.
    & (Join-Path $Clone "install.ps1") -Unattended
    $after = Get-Version (Join-Path $Dest "sm\__init__.py")
    if ($after -ne $to) { throw "A instalação não terminou (instalada: $after). Veja data\update.log ou rode o install.cmd no PC." }
    Set-Status "done" "Atualizado de $script:From para $to." $to
} catch {
    Set-Status "error" $_.Exception.Message
    exit 1
}
