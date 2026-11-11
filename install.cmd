@echo off
rem Atalho para o install.ps1 (o PowerShell bloqueia scripts .ps1 por padrao).
rem Exemplos: install.cmd   |   install.cmd -PresentMon -LHM   |   install.cmd -Uninstall
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
rem Se der erro antes de abrir a janela de administrador, a janela fecharia antes de dar para ler.
if errorlevel 1 pause
