# Changelog

## 0.5.0-win
Primeira versão para Windows 10/11, a partir da 0.4.3 do Linux. Mesmo frontend e mesma API; backend reescrito:

- Roda como tarefa agendada no logon, elevada, com um Python próprio em `C:\Program Files\SelfMonitoring`
  (`install.ps1` / `install.cmd`: instalar, atualizar, `-Uninstall`, `-Purge`, `-SetPassword`, `-PresentMon`, `-LHM`).
- CPU: clock real com boost (contadores de desempenho), temperatura pelo LibreHardwareMonitor.
- GPU: lista pelo DXGI, uso por adaptador e por processo pelos contadores "GPU Engine" (como o Gerenciador de Tarefas),
  detalhes da NVIDIA pelo NVML (sem subprocesso); temperatura de AMD/Intel pelo LibreHardwareMonitor.
- Sensores: LibreHardwareMonitor (servidor web local). Controle de ventoinha removido (só leitura).
- Processos: finalizar = fechar a janela (WM_CLOSE), forçar = TerminateProcess; processos críticos protegidos;
  detecção de PID reaproveitado no histórico; serviços do Windows por PID.
- Análise/suspeitos: assinatura digital (Authenticode) e informações de versão; heurísticas para malware de Windows.
- Rede: tabela TCP do Windows com PID e bytes por conexão (estatísticas por conexão, como administrador).
- Áudio: Core Audio (pycaw) com volume por app; mídia pelos controles de mídia do Windows (WinRT), com capa.
- Tela: resolução com reversão, nomes reais dos monitores, DDC/CI pela dxva2, espera.
- Janela em foco (Win32, inclusive apps da Loja), captura de tela por GDI com notificação obrigatória do Windows.
- Apps: menu Iniciar (programas, Loja, Steam) com ícones do Windows; abrem sem herdar o administrador.
- Jogo: Steam pelo registro (RunningAppID); FPS pelo PresentMon.
- Atualizações: Windows Update + winget. Energia: shutdown.exe (com timer).
- Sessão: bloquear (desbloquear remotamente não é possível no Windows).
