# Paridade Windows × Linux

Este arquivo existe para as duas versões do self-monitoring ficarem o mais iguais possível:
[Linux](https://github.com/thematheusk3/my-pc-self-control) (CachyOS/KDE) e
[Windows](https://github.com/thematheusk3/self-monitoring-win). O frontend e a API são os mesmos; o que muda é o
backend de cada sistema.

**Regra:** ao implementar algo novo em uma versão, registre aqui se dá para levar para a outra (e como), ou se é
impossível (e por quê). Quando for portado, mova a linha para "Já equalizado".

Situação em: versão Windows 0.5.1-win, versão Linux 0.4.3.

## 1. Só no Windows: dá para levar ao Linux

| Recurso | Como foi feito no Windows | Como fazer no Linux | Esforço |
|---|---|---|---|
| Nome real do monitor e tipo de conexão (ex.: "LG ULTRAGEAR · DisplayPort") e chip "principal" | DisplayConfig (Win32) | Ler o EDID em `/sys/class/drm/card*-*/edid` (nome no descritor 0xFC); o `kscreen-doctor -j` já informa a saída primária | Baixo |
| Escala da tela em % no cartão do monitor | `GetDpiForMonitor` | O `kscreen-doctor -j` já devolve `scale`; só formatar como % no frontend | Baixo |
| Uso da GPU por tipo de engine (3D, cópia, vídeo) | Contadores "GPU Engine" | O fdinfo do DRM já separa engines (`gfx`, `compute`, `dec`, `enc`); expor em `gpu.engines` | Baixo |
| NVIDIA sem subprocesso (mais leve que chamar `nvidia-smi` a cada segundo) | NVML via ctypes (`nvml.dll`) | O mesmo código com `libnvidia-ml.so.1` | Baixo |
| Finalizar "educado" (como clicar no X; o app pode perguntar se quer salvar) antes do SIGTERM | `WM_CLOSE` nas janelas do processo | Script do KWin (`window.closeWindow()`) para janelas do PID; sem janela, SIGTERM | Médio |
| Processos críticos protegidos (finalizá-los derruba o sistema) | Lista: csrss, lsass, winlogon, dwm... | Proteger `systemd`, `kwin_wayland`, `plasmashell`, `sddm`/`plasmalogin`, `pipewire`, `Xwayland`, `dbus-daemon`, com aviso em vez de bloqueio total | Baixo |
| Detecção de PID reaproveitado no histórico de processos | Compara o horário de início | Já usa `pid-starttime` como id; falta só verificar PIDs conhecidos (raro no Linux, `pid_max` alto) | Baixo |
| Atualizações que pedem reinício | Campo `RebootRequired` do Windows Update | Comparar `uname -r` com o kernel instalado em `/usr/lib/modules`; `needs-restarting` no Fedora; `/var/run/reboot-required` no Debian/Ubuntu | Baixo |
| Aviso de rede "Pública" bloqueando o acesso pelo celular | `Get-NetConnectionProfile` no instalador | No `install.sh`: detectar `firewalld`/`ufw` ativo e liberar a porta (ou avisar) | Baixo |
| Captura de tela sem programa externo | GDI (BitBlt) + encoder PNG próprio | No Wayland só pelo portal (`org.freedesktop.portal.Screenshot`) ou Spectacle; manter o Spectacle | — (não vale) |

## 2. Só no Linux: dá para levar ao Windows

| Recurso | Como é no Linux | Como fazer no Windows | Esforço |
|---|---|---|---|
| Controle da ventoinha (curva automática, manual, devolver à placa, emergência) | PWM em `/sys/class/hwmon` | Carregar a `LibreHardwareMonitorLib.dll` (via `pythonnet`) e usar `Sensor.Control.SetSoftware(%)` / `SetDefault()`; a lógica de curva/histerese/emergência de `fan.py` pode ser reaproveitada inteira. Exige o driver do LHM (PawnIO nas versões novas). Alternativa: integrar com o FanControl (configuração em JSON) | Alto |
| Entrar pelo celular depois de reiniciar (painel no ar sem ninguém digitar a senha) | Login automático + sessão iniciando bloqueada (`LockOnStart`) | Login automático (Autologon da Sysinternals, guarda a senha cifrada pelo LSA) + tarefa no logon que roda `LockWorkStation` antes de tudo. O painel sobe com a tela já bloqueada. **Desbloquear continua impossível** (ver seção 3) | Médio |
| FPS com uso de CPU/GPU do jogo | Colunas `cpu_load`/`gpu_load` do MangoHud | O PresentMon 2.x tem `GPUBusy`, `CPUBusy`, `DisplayLatency`; ler essas colunas no `game.py` | Baixo |
| Ícone e categoria do app vindos do próprio app (`.desktop`: Jogos, Internet, Escritório...) | `Categories=` dos `.desktop` | O menu Iniciar não tem categorias; dá para usar a pasta do atalho no menu Iniciar (`Get-StartApps` não informa: ler os `.lnk` em `ProgramData\...\Start Menu\Programs`) | Médio |
| Volume acima de 100% | PipeWire amplifica até 150% | Só com APO (Equalizer APO) instalado no dispositivo; não vale a complexidade | — (não vale) |

## 3. Impossível de equalizar (e por quê)

| Recurso | Onde existe | Por que não dá no outro |
|---|---|---|
| Desbloquear a tela pelo celular | Linux (`loginctl unlock-session`) | No Windows a tela de bloqueio (LogonUI) só aceita credencial digitada, PIN ou Windows Hello. A única saída seria um *Credential Provider* próprio (DLL no LogonUI), que é complexo e abriria um buraco de segurança. |
| FPS sem configurar o jogo | Windows (PresentMon lê os eventos de apresentação do sistema) | O Linux não tem um ponto central equivalente; quem mede é a camada do jogo (MangoHud, gamescope). O mais próximo: detectar se o jogo roda no gamescope e ler as estatísticas dele. |
| Temperatura da CPU sem programa extra | Linux (hwmon) | O Windows exige driver em modo kernel para ler os sensores; o LibreHardwareMonitor é esse driver. |
| Assinatura digital do executável | Windows (Authenticode) | Executáveis do Linux não são assinados. O equivalente é "qual pacote instalou" (já existe) e verificar se o arquivo foi alterado depois de instalado: `pacman -Qkk <pacote>` / `rpm -V` / `debsums`. **Isso dá para levar ao Linux** como sinal "binário alterado em relação ao pacote". |
| Bytes por conexão desde o início | Linux (`ss` mostra o total da conexão) | O Windows só conta depois que alguém liga a coleta naquela conexão; o painel liga ao ver a conexão, então conexões antigas começam do zero. |
| Detalhes do processo de outros usuários sem root | — | Nos dois sistemas exige privilégio (root / administrador). |

## 4. Ideias que nenhum dos dois tem (fazer nos dois)

| Ideia | Windows | Linux |
|---|---|---|
| **Programas que iniciam sozinhos** (onde malware se instala para sobreviver ao reinício): listar e avisar quando surge um novo | Chaves `Run`/`RunOnce` (HKCU/HKLM), pasta Inicializar, tarefas agendadas, serviços novos | `~/.config/autostart`, `/etc/xdg/autostart`, unidades `systemd --user` e de sistema, timers, `crontab` |
| **Saúde dos discos** (SMART: desgaste do SSD, setores ruins, temperatura) | `Get-PhysicalDisk` / `MSFT_StorageReliabilityCounter` ou LibreHardwareMonitor | `smartctl -j` (smartmontools) |
| **Mensagem na tela do PC** enviada do celular ("já vou desligar", "janta!") | Notificação (já existe `notify_pc`) | `notify-send` (já existe) |
| **Prioridade do processo** (baixar a prioridade de algo que está travando o PC) | `psutil.Process.nice()` com classes de prioridade | `renice` / `psutil` |
| **Ligar o PC pelo celular (Wake-on-LAN)** | O painel não pode se ligar sozinho, mas pode mostrar o MAC e checar se o WoL está habilitado na placa de rede; o "acordar" precisa de outro aparelho ligado na rede (roteador, Raspberry Pi) | Idem (`ethtool <iface>` mostra `Wake-on: g`) |
| **Histórico de temperatura/uso maior que 5 min** (ex.: 24 h em resolução menor) | Comum aos dois (`history.py`) | Comum aos dois |

## Já equalizado

Funciona igual nas duas versões (mesma API e mesma tela): visão geral, processos (lista, encerrados, suspeitos,
análise, confiáveis), rede (por programa, conexões, encerradas, portas), apps pelo celular, áudio (saída padrão,
volume por dispositivo e **por app, inclusive a saída de cada app**, microfone), mídia com ±10 s, tela (resolução com
reversão, brilho DDC/CI, apagar, espera), captura de tela com aviso, janela em foco, downloads da Steam, jogo atual,
alertas (PC e ntfy), atualizações pendentes, desligar/reiniciar com timer, bloquear a tela, senha e sessões.
