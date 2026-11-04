@echo off
rem Atalho para o atualizar.ps1 (o PowerShell bloqueia scripts .ps1 por padrao).
rem Exemplos: atualizar.cmd   |   atualizar.cmd -Verificar   |   atualizar.cmd -Forcar
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0atualizar.ps1" %*
rem Com dois cliques a janela fecharia antes de dar para ler o resultado.
if "%~1"=="" pause
