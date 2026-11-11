<#
.SYNOPSIS
  Baixa a versão nova do GitHub e reinstala o self-monitoring.

.DESCRIPTION
  Mostra a versão instalada, a do GitHub e os commits novos, roda "git pull --ff-only" e chama o install.ps1, que pede
  permissão de administrador. O git roda como o usuário comum; só o instalador é elevado. Nada local é descartado: com
  arquivos alterados ou fora da branch main, o script para e explica o que fazer.

.EXAMPLE
  .\atualizar.cmd               atualiza, se houver novidade
  .\atualizar.cmd -Verificar    só mostra o que há de novo
  .\atualizar.cmd -Forcar       reinstala mesmo sem novidade
#>
param(
    [switch]$Verificar,
    [switch]$Forcar
)

$Branch = "main"
$Remote = "origin/$Branch"
$Installed = Join-Path $env:ProgramFiles "SelfMonitoring\sm\__init__.py"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)     # acentos nos commits

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Fail($msg) { Write-Host "`n$msg" -ForegroundColor Red; exit 1 }
function Invoke-Git { & git -C $PSScriptRoot @args }

function Get-Version([string]$Text) {
    if ($Text -match '__version__\s*=\s*"([^"]+)"') { return $Matches[1] }
    return $null
}
function Get-InstalledVersion {
    if (Test-Path $Installed) { return Get-Version (Get-Content -Raw -Encoding UTF8 $Installed) }
    return $null
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "O git não foi encontrado. Rode o install.cmd (ele instala o Git) ou:  winget install Git.Git  — ou siga a opção sem git em ATUALIZAR.md."
}
if (-not (Test-Path (Join-Path $PSScriptRoot ".git"))) {
    Fail "Esta pasta não veio de um git clone. Siga a opção com ZIP em ATUALIZAR.md."
}

Say "Consultando o GitHub..." Cyan
Invoke-Git fetch --quiet --tags origin
if ($LASTEXITCODE) { Fail "Não foi possível falar com o GitHub. Verifique a internet e tente de novo." }

$current = Invoke-Git rev-parse --abbrev-ref HEAD
if ($current -ne $Branch) {
    Fail "A pasta está em '$current', não em '$Branch' (talvez você tenha voltado a uma versão antiga).`nPara voltar a acompanhar as atualizações:  git checkout $Branch"
}
$dirty = @(Invoke-Git status --porcelain --untracked-files=no)
if ($dirty.Count) {
    Say "`nHá arquivos alterados nesta pasta:" Yellow
    $dirty | ForEach-Object { Say "  $_" }
    Fail "Nada foi mudado. Se você voltou à interface antiga, desfaça com:  git restore -- web`nVeja o que mudou com:  git status"
}

$installedVer = Get-InstalledVersion
$remoteVer = Get-Version ((Invoke-Git show "${Remote}:sm/__init__.py") -join "`n")
$behind = [int](Invoke-Git rev-list --count "HEAD..$Remote")
$commits = if ($behind) { @(Invoke-Git log --format="  %h %s" -n 20 "HEAD..$Remote") } else { @() }

Say ""
Say "Instalada:  $(if ($installedVer) { $installedVer } else { 'nenhuma' })"
Say "No GitHub:  $remoteVer"
if ($commits.Count) {
    Say "`nNovidades ($behind):" Cyan
    $commits | ForEach-Object { Say $_ }
    if ($behind -gt $commits.Count) { Say "  ... e mais $($behind - $commits.Count)" }
}

$pending = $behind -gt 0 -or $installedVer -ne $remoteVer
if (-not $pending -and -not $Forcar) {
    Say "`nJá está na versão mais recente." Green
    exit 0
}
if ($Verificar) {
    Say "`n$(if ($pending) { 'Há atualização. Rode atualizar.cmd para instalar.' } else { 'Nada a atualizar.' })" Yellow
    exit 0
}

if ($behind) {
    Say "`nBaixando..." Cyan
    Invoke-Git pull --quiet --ff-only origin $Branch
    if ($LASTEXITCODE) { Fail "O git pull falhou: há commits locais que não estão no GitHub. Veja com:  git status" }
}

Say "`nInstalando (aceite o pedido de administrador)..." Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "install.ps1")
if ($LASTEXITCODE) { Fail "A instalação não terminou. A pasta do projeto já está atualizada: rode install.cmd de novo." }

# Elevado, o instalador roda em outra janela: o que confirma o resultado é a versão instalada.
$after = Get-InstalledVersion
if ($after -ne $remoteVer) {
    Fail "A versão instalada é $after, não $remoteVer. Veja a mensagem na janela do instalador e rode install.cmd de novo."
}
Say "`n$(if ($installedVer -eq $after) { "Reinstalado: $after" } else { "Atualizado: $installedVer -> $after" })" Green
Say "Se o painel estiver aberto em outro aparelho, recarregue a página."
