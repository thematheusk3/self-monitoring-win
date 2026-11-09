# Paridade Linux × Windows

Este arquivo existe para as duas versões do self-monitoring ficarem o mais iguais possível:
[Linux](https://github.com/thematheusk3/my-pc-self-control) (CachyOS/Arch com KDE) e
[Windows](https://github.com/thematheusk3/self-monitoring-win). O frontend e a API são os mesmos; o que muda é o
backend de cada sistema.

**O arquivo é o mesmo nos dois repositórios.** Ao mudar em um, copie para o outro.

**Regra:** ao implementar algo novo em uma versão, registre aqui se dá para levar para a outra (e como), ou se é
impossível (e por quê). Quando for portado, mova a linha para "Já equalizado" e anote a versão.

Situação em: versão Linux 0.6.2, versão Windows 0.12.0-win.

## 1. Só no Windows: dá para levar ao Linux

| Recurso | Como foi feito no Windows | Como fazer no Linux | Esforço |
|---|---|---|---|
| Captura de tela sem programa externo | GDI (BitBlt) + encoder PNG próprio | No Wayland só pelo portal (`org.freedesktop.portal.Screenshot`) ou Spectacle; manter o Spectacle | — (não vale) |

Tudo o que valia a pena foi levado ao Linux na 0.5.0 (ver "Já equalizado"). Os guias de porte passo a passo estão no
histórico do git (commit `75c7d6c` do repositório Linux), se precisar repetir algo parecido.

Cuidados ao mexer no frontend, que custaram retrabalho no Windows e valem nos dois:

- Não use `light-dark()` nos tokens de cor: o tema escuro não aplicava. Cada cor tem regras explícitas para escuro,
  `[data-theme="light"]` e `prefers-color-scheme`.
- Não ponha `backdrop-filter` em `.topbar` no celular: a barra `.tabs` fica dentro dela e é fixa, e o desfoque a prende ao cabeçalho.
- `--bg` e `--accent` precisam continuar em hexadecimal: `theme.js` e `mural-backdrop.js` fazem contas com esses valores.
- O Linux tem a aba Ventoinha (`fan.js`, classes `.fan-*`), que não existe no Windows: confira-a nos três estilos ao mudar o CSS.

## 2. Só no Linux: dá para levar ao Windows

| Recurso | Como é no Linux | Como fazer no Windows | Esforço |
|---|---|---|---|
| **Histórico persistente + frota** (0.6.0) | `sm/store.py` (SQLite `data/store.db`: eventos, incidentes, métricas por minuto/hora, hardware), `sm/fleet.py` (identidade da máquina e pacote por `seq`), `sm/recorder.py`, `sm/hwinfo.py`. Desenho em `FROTA.md` | O banco, o recorder e o fleet são Python puro e entram sem mudança. A identidade usa `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid` no lugar do `/etc/machine-id`. O `hwinfo.py` vira WMI (`Win32_BaseBoard`, `Win32_BIOS`, `Win32_Processor`, `Win32_VideoController` com `DriverVersion`, `Win32_DiskDrive`) e o histórico de atualizações vem do Windows Update (`Get-HotFix`) e da versão do driver de vídeo | Médio |
| **Incidentes** (0.6.0): vigia de log + regras + investigador, com hipóteses e "Copiar para IA" | `sm/journal.py` (`journalctl -f -o json`), `sm/rules.py` (regex no kernel/systemd), `sm/incidents.py` (correlação, métricas, hipóteses, Markdown) | O `incidents.py` (correlação, hipóteses, texto para IA) é reaproveitável. Trocar a fonte: Event Log com `EvtSubscribe` (pywin32) nos canais System e Application. Regras equivalentes: TDR da GPU = `Display` evento 4101 / "LiveKernelEvent 141/117"; crash de app = `Application Error` 1000 e Windows Error Reporting 1001 (com o módulo que falhou); OOM = `Resource-Exhaustion-Detector` 2004; serviço = `Service Control Manager` 7031/7034; disco = `disk` 7/51/153 e `Ntfs` 55; WHEA = `WHEA-Logger` 17/18/19; desligamento inesperado = `Kernel-Power` 41 e `EventLog` 6008. Pilha: o WER grava o minidump em `%LOCALAPPDATA%\CrashDumps` | Alto |
| **Aba Serviços** (0.6.0) | `systemctl list-units/show` (memória, CPU e tarefas do cgroup) | `psutil.win_service_iter()` + `Win32_Service` (estado, PID, modo de início); consumo somando os processos do PID de cada serviço (svchost compartilhado mostra o grupo); ações com `sc`/`win32serviceutil` (a tarefa já roda elevada) | Médio |
| **Aba Logs** (0.6.0) | `journalctl` com filtros, boots anteriores, ao vivo | `EvtQuery` com XPath (nível, provedor, intervalo); "boots" viram intervalos entre eventos `Kernel-General` 12/13; ao vivo com `EvtSubscribe`. O frontend `logs.js` serve com outros rótulos (canal no lugar de unidade) | Médio |
| **Aba MangoHud: configurar o HUD do jogo ao vivo** (0.5.1–0.5.2) | `sm/mangohud.py` + `mangohud.js`: edita o `MangoHud.conf` (o mesmo do GOverlay; o MangoHud recarrega ao mudar o arquivo): posição, aparência, métricas, limite de FPS, atalhos, editor do arquivo, configs por jogo. Linhas `exec` bloqueadas. O GOverlay reescreve o arquivo inteiro ao abrir/salvar: a aba avisa quando ele está aberto e o `game.py` recoloca o bloco da leitura de FPS | **A avaliar.** Não há MangoHud no Windows; o equivalente é o RivaTuner (RTSS), que guarda as opções em `Profiles\Global` (INI) e talvez não releia o arquivo com o jogo aberto. Ver se o RTSS aplica mudanças ao vivo (ou pela API de memória compartilhada) antes de começar; se não der, registrar na seção 3 | Alto (talvez impossível) |
| Controle da ventoinha (curva automática, manual, devolver à placa, emergência) | PWM em `/sys/class/hwmon` | Carregar a `LibreHardwareMonitorLib.dll` (via `pythonnet`) e usar `Sensor.Control.SetSoftware(%)` / `SetDefault()`; a lógica de curva/histerese/emergência de `fan.py` pode ser reaproveitada inteira. Exige o driver do LHM (PawnIO nas versões novas). Alternativa: integrar com o FanControl (configuração em JSON) | Alto |
| Entrar pelo celular depois de reiniciar (painel no ar sem ninguém digitar a senha) | Login automático + sessão iniciando bloqueada (`LockOnStart`) | Login automático (Autologon da Sysinternals, guarda a senha cifrada pelo LSA) + tarefa no logon que roda `LockWorkStation` antes de tudo. O painel sobe com a tela já bloqueada. **Desbloquear continua impossível** (ver seção 3) | Médio |
| Ícone e categoria do app vindos do próprio app (`.desktop`: Jogos, Internet, Escritório...) | `Categories=` dos `.desktop` | O menu Iniciar não tem categorias; dá para usar a pasta do atalho no menu Iniciar (`Get-StartApps` não informa: ler os `.lnk` em `ProgramData\...\Start Menu\Programs`) | Médio |
| Volume acima de 100% | PipeWire amplifica até 150% | Só com APO (Equalizer APO) instalado no dispositivo; não vale a complexidade | — (não vale) |

## 3. Impossível ou não se aplica (e por quê)

| Recurso | Onde existe | Por que não dá no outro |
|---|---|---|
| Desbloquear a tela pelo celular | Linux (`loginctl unlock-session`) | No Windows a tela de bloqueio (LogonUI) só aceita credencial digitada, PIN ou Windows Hello. A única saída seria um *Credential Provider* próprio (DLL no LogonUI), que é complexo e abriria um buraco de segurança. |
| Serviço como root com os comandos da sessão rodando como o dono da área de trabalho (`setpriv`, 0.4.4) | Linux | Não se aplica: no Windows a tarefa já roda elevada **na conta do usuário**, então áudio, tela e notificações já são os da sessão dele. O equivalente útil é a checagem de conta errada (seção 2). |
| FPS sem configurar o jogo | Windows (PresentMon lê os eventos de apresentação do sistema) | O Linux não tem um ponto central equivalente; quem mede é a camada do jogo (MangoHud, gamescope). O mais próximo: detectar se o jogo roda no gamescope e ler as estatísticas dele. |
| Temperatura da CPU sem programa extra | Linux (hwmon) | O Windows exige driver em modo kernel para ler os sensores; o LibreHardwareMonitor é esse driver. |
| Ligar o servidor web do programa de sensores no instalador (0.11.1-win) | Windows (`Enable-LhmWebServer` em `install.ps1`) | O Linux lê hwmon direto, sem programa intermediário. |
| Assinatura digital do executável | Windows (Authenticode) | Executáveis do Linux não são assinados. O equivalente é "qual pacote instalou" e se o arquivo ainda é o que o pacote instalou: **feito na 0.4.7** (ver "Já equalizado"). |
| Processo crítico: bloqueio total × confirmação | Windows bloqueia (403); Linux pede confirmação (409 + `confirm_critical`) | No Windows matar `csrss`/`wininit`/`lsass` é tela azul na hora, sem uso legítimo. No Linux reiniciar o `plasmashell` ou o `pipewire` é um conserto comum, então o painel avisa o efeito e deixa seguir. |
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

Funciona igual nas duas versões (mesma API e, desde a 0.5.0 do Linux, a mesma interface): visão geral, processos (lista, encerrados, suspeitos,
análise, confiáveis), rede (por programa, conexões, encerradas, portas), apps pelo celular, áudio (saída padrão,
volume por dispositivo e **por app, inclusive a saída de cada app**, microfone), mídia com ±10 s, tela (resolução com
reversão, brilho DDC/CI, apagar, espera), captura de tela com aviso, janela em foco, downloads da Steam, jogo atual,
alertas (PC e ntfy), atualizações pendentes, desligar/reiniciar com timer, bloquear a tela, senha e sessões.

Portados depois (mesma API e mesma tela nas duas versões):

| Recurso | De → para | Versões | Observação |
|---|---|---|---|
| Quem está acessando (sessões, IP, origem, nome, MAC, encerrar, histórico de acessos) | Windows → Linux | 0.5.2-win → 0.4.5 | Só `arp_table()` muda: `GetIpNetTable` no Windows, `/proc/net/arp` no Linux |
| Notificação no PC a cada login e a cada bloqueio por senha errada | Windows → Linux | 0.5.2-win → 0.4.5 | `notify_pc` de cada sistema |
| Registro de ações feitas pelo painel | Windows → Linux | 0.5.3-win → 0.4.5 | No Linux há também a categoria "Ventoinha" (`fan_update`) e "Desbloquear a tela" é real |
| Guia de atualização (`ATUALIZAR.md`) | Windows → Linux | 0.5.3-win → 0.4.6 | No Linux vem junto o `atualizar.sh` |
| Nome real do monitor, tipo de conexão, chip "principal" e escala em % | Windows → Linux | 0.5.0-win → 0.4.7 | Linux: nome no EDID (`/sys/class/drm/card*-*/edid`, descritor 0xFC), tipo e principal do `kscreen-doctor -j` (`type`, `priority`) |
| Uso da GPU por tipo de engine (`gpu.engines`) | Windows → Linux | 0.5.0-win → 0.4.7 | Linux: fdinfo do DRM (`gfx`, `compute`, `dec`, `enc`...) e, na NVIDIA, NVML (3D + codificador/decodificador). Exibido só no Linux por enquanto (seção 2) |
| NVIDIA pelo NVML (sem subprocesso) | Windows → Linux | 0.5.0-win → 0.4.7 | Mesmo código ctypes com `libnvidia-ml.so.1`; `nvidia-smi` continua como plano B nos dois |
| Finalizar "educado" (fechar as janelas antes do SIGTERM) | Windows → Linux | 0.5.0-win → 0.4.7 | Linux: script de uma vez só no KWin (`closeWindow()` nas janelas do PID, resposta pelo journal); X11 sem KDE com `wmctrl -ic`. Resposta `closed_windows` / `note` igual |
| Processos críticos | Windows → Linux | 0.5.0-win → 0.4.7 | Linux avisa e pede confirmação em vez de bloquear (seção 3); campo `critical` na lista de processos |
| PID reaproveitado no histórico de processos | Windows → Linux | 0.5.0-win → 0.4.7 | Linux compara o `starttime` do `/proc/<pid>/stat` com o id `pid-starttime` a cada ~1 s |
| Atualizações que pedem reinício | Windows → Linux | 0.5.0-win → 0.4.7 | Linux: kernel rodando × instalado em `/usr/lib/modules` (`pkgbase`), `/run/reboot-required`, `needs-restarting -r` |
| Aviso de firewall bloqueando o celular | Windows → Linux | 0.5.0-win → 0.4.7 | Linux: `install.sh` avisa quando `nftables`/`iptables` têm política DROP e a porta não está liberada (ufw/firewalld já eram liberados) |
| Verificação do executável (assinatura digital × integridade do pacote) | Windows → Linux | 0.5.0-win → 0.4.7 | Linux: SHA-256 do executável × banco do pacote (`pacman` mtree, `dpkg` md5sums, `rpm --dump`); alterado = sinal alto |
| Interface repaginada (placa do PC, faixa de estado, barra lateral, "Mais" no celular, ícones e fonte) | Windows → Linux | 0.6.0-win → 0.5.0 | Interface antiga do Linux na marca `ui-classica-0.4.7`. No Linux há ainda a aba Ventoinha e o botão Desbloquear (ícone `lock-open` acrescentado ao `icons.js`) |
| Servidor aceita rajadas de pedidos (`request_queue_size = 128`) | Windows → Linux | 0.6.0-win → 0.5.0 | Mesma linha no `sm/server.py` |
| Aba Atualização e aviso de versão nova | Windows → Linux | 0.7.0-win → 0.5.0 | Só `UPDATE_STEPS`/`UPDATE_NOTE` mudam (`atualizar.sh` da cópia instalada); repositório padrão `thematheusk3/my-pc-self-control` |
| Notificações por webhook | Windows → Linux | 0.8.0-win → 0.5.0 | Sem mudança de lógica; na tela do PC usa o ícone `dialog-information` quando não é crítica |
| Mural (cores vivas, motor de eventos, fundos do Início e dock) | Windows → Linux | 0.8.0-win a 0.10.0-win → 0.5.0 | Arte do cache da Steam com o mesmo formato (nativa e Flatpak); FPS do MangoHud |
| Cores e estilos do painel (Placa, Vidro, Osciloscópio; seis cores) | Windows → Linux | 0.10.0-win, 0.11.0-win → 0.5.0 | Só frontend |
| Apps que não são jogos | Windows → Linux | 0.9.1-win → 0.5.0 | No Linux vale para jogo da Steam e do MangoHud (chave `exe:` vem do nome do CSV do MangoHud) |
| Script de atualização | Linux → Windows | 0.4.6 → 0.9.2-win | `atualizar.sh` no Linux; `atualizar.cmd` e `atualizar.ps1` no Windows, com `-Verificar` e `-Forcar`. No Windows roda do clone (não há `.origem`), e a cópia instalada não é usada. O Windows confere a versão instalada depois, porque o instalador elevado roda em outra janela |
| Aviso "precisa reiniciar" no cartão de atualizações | Linux → Windows | 0.4.7 → 0.12.0-win | Windows: chaves `Component Based Servicing\RebootPending` e `WindowsUpdate\Auto Update\RebootRequired` (`updates.reboot_pending`), lidas a cada consulta; o antigo "algum item pede reinício" virou `reboot_items` |
| Uso da GPU por engine exibido na Visão geral | Linux → Windows | 0.4.7 → 0.12.0-win | Mesmo `ENGINE`/`enginesText` do `overview.js` |
| Erro da API com `status` e `data` (`app.api`) | Linux → Windows | 0.4.7 → 0.12.0-win | Só o `app.js`; o Windows continua bloqueando processo crítico (seção 3) |
| Instalador não instala na conta errada | Linux → Windows | 0.4.4 → 0.12.0-win | Windows: compara `-ForUser` com o dono do console (`Win32_ComputerSystem.UserName`) e pergunta |
| Aviso de sessão aberta em outra conta | Linux → Windows | 0.4.4 → 0.12.0-win | Windows: `WTSEnumerateSessions` + `WTSUserName`; mesmos campos `service_user`/`other_users`, texto com `install.cmd -ForUser` |
| Script de diagnóstico | Linux → Windows | 0.4.4 → 0.12.0-win | `tools\diagnostico.ps1`: tarefa, processo, sessões, porta/firewall, placa, LibreHardwareMonitor + PawnIO, PresentMon, áudio, monitores, log |
| Dica quando nenhuma ventoinha aparece (`sensors.fan_hint`) | Linux → Windows | 0.4.4 → 0.12.0-win | Windows: sem chip da placa no LibreHardwareMonitor → PawnIO/administrador; com chip e sem RPM → hub/AIO |
| CPU/GPU do jogo junto do FPS | Linux → Windows | MangoHud → 0.12.0-win | Windows: colunas `CPUBusy`/`GPUBusy`/`DisplayLatency` do PresentMon 2.x, em % do frametime (não é o uso do sistema como no MangoHud); campo novo `latency` |
| Nome de jogos não-Steam adicionados à Steam | Linux → Windows | 0.5.1–0.5.2 → 0.12.0-win | Mesmo `parse_binary_vdf`/`shortcuts.vdf`. Windows: `RunningAppID` ≥ 2³¹ vira o atalho; e o jogo medido pelo PresentMon com o `.exe` em `steamapps\common\<pasta>` vira o jogo dessa pasta (o Windows não tem o `SteamLaunch AppId=` no comando) |
