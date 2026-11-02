<#
.SYNOPSIS
  Cria processos INOFENSIVOS que imitam sinais de malware, para testar a aba Processos > Suspeitos.

.DESCRIPTION
  Nada aqui faz mal: são cópias do ping.exe e um PowerShell que só espera. Disparam os sinais de alto risco:
    1. "svchost.exe" rodando da pasta temporária (nome de processo do Windows fora do System32, sem assinatura)
    2. "fatura.pdf.exe" (extensão dupla)
    3. PowerShell com comando codificado em base64 (o comando é só Start-Sleep)
    4. linha de comando de minerador (o comando é só Start-Sleep com um comentário)
  Tudo é encerrado e apagado no fim (ou com Ctrl+C).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\simular-suspeito.ps1          # 120 s
  powershell -ExecutionPolicy Bypass -File tools\simular-suspeito.ps1 300      # 5 min
#>
param([int]$Seconds = 120)

$dir = Join-Path $env:TEMP "sm-simulacao-$PID"
New-Item -ItemType Directory -Force $dir | Out-Null
$ping = Join-Path $env:SystemRoot "System32\PING.EXE"
Copy-Item $ping (Join-Path $dir "svchost.exe")
Copy-Item $ping (Join-Path $dir "fatura.pdf.exe")

$procs = @()
try {
    $procs += Start-Process (Join-Path $dir "svchost.exe") -ArgumentList "-n", ($Seconds + 1), "127.0.0.1" -WindowStyle Hidden -PassThru
    $procs += Start-Process (Join-Path $dir "fatura.pdf.exe") -ArgumentList "-n", ($Seconds + 1), "127.0.0.1" -WindowStyle Hidden -PassThru
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes("Start-Sleep -Seconds $Seconds"))
    $procs += Start-Process powershell.exe -ArgumentList "-NoProfile", "-EncodedCommand", $enc -WindowStyle Hidden -PassThru
    $procs += Start-Process powershell.exe -ArgumentList "-NoProfile", "-Command", "Start-Sleep -Seconds $Seconds # xmrig --url stratum+tcp://exemplo.invalid:3333" -WindowStyle Hidden -PassThru

    Write-Host "Processos de teste rodando por $Seconds s (PIDs: $($procs.Id -join ', '))." -ForegroundColor Yellow
    Write-Host "Abra o painel em Processos > Suspeitos. Ctrl+C encerra antes."
    Start-Sleep -Seconds $Seconds
} finally {
    $procs | Where-Object { $_ -and -not $_.HasExited } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300
    Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
    Write-Host "Processos encerrados e arquivos apagados." -ForegroundColor Green
}
