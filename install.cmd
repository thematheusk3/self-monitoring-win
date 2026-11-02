@echo off
rem Atalho para o install.ps1 (o PowerShell bloqueia scripts .ps1 por padrao).
rem Exemplos: install.cmd   |   install.cmd -PresentMon -LHM   |   install.cmd -Uninstall
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
