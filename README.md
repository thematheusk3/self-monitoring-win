# self-monitoring (Windows)

Painel web para monitorar o PC pelo celular ou outro computador na mesma rede.
Versão para **Windows 10/11** do [self-monitoring para Linux](https://github.com/thematheusk3/my-pc-self-control):
o mesmo frontend (HTML/JS puro, sem build) e a mesma API, com o backend reescrito para as APIs do Windows.

Backend em Python (biblioteca padrão + `psutil`, `pycaw` e `winrt`); o resto é `ctypes` direto nas APIs do Windows
(contadores de desempenho, DXGI, NVML, IP Helper, Win32, DDC/CI).

## O que mostra

- **Visão geral**: CPU (uso total, por thread, clock real com boost, temperatura), GPU (uso, temperatura, VRAM,
  clocks, potência, ventoinha; NVIDIA pelo driver, AMD/Intel pelos contadores do Windows), memória, rede, discos
  e os maiores consumidores de CPU/RAM/GPU.
- **Processos**: lista completa com busca, filtro por usuário, ordenação, uso de GPU por processo (o mesmo do
  Gerenciador de Tarefas) e opção de **finalizar** (pede para fechar, como clicar no X) ou **forçar**, com confirmação.
  - **Encerrados**: histórico dos processos que já terminaram (quando, quanto duraram, quem os iniciou).
  - **Suspeitos**: processos vivos ou encerrados com sinais comuns em malware para Windows.
  - **Analisar**: o que é o processo (descrição, empresa, **assinatura digital**), executável, SHA-256 + link do
    VirusTotal, cadeia de processos pais, serviço do Windows, portas e conexões.
- **Controles**: áudio (mídia tocando com ⏮ ⏯ ⏭ e ±10 s, saída padrão, volume por dispositivo e **por aplicativo**,
  microfone), tela (resolução e taxa, brilho/contraste por DDC/CI, apagar/espera) e captura de tela (sempre com aviso no PC).
- **Alertas**: regras configuráveis com aviso no painel, na tela do PC (notificação do Windows) e no celular (ntfy).
- **Jogo/FPS** (na Visão geral): jogo da Steam aberto e FPS via PresentMon.
- **No PC agora** (na Visão geral): janela em foco e o que está tocando (Spotify, navegador, VLC...), com controles.
  No Mural e em Controles > Áudio também há aleatório, repetir e ±10 s; no Mural, o volume só do app que está tocando
  e a letra sincronizada (LRCLIB).
- **Downloads Steam** (na Visão geral, só quando há algo baixando): progresso, velocidade e tempo restante.
- **Apps**: abre pelo celular, na tela do PC, qualquer item do menu Iniciar (programas, apps da Loja, jogos da Steam).
- **Rede**: conexões por programa (para onde, quanto envia/recebe), conexões encerradas e portas abertas.
- **Sensores**: temperaturas, ventoinhas, tensões e potências do LibreHardwareMonitor, agrupados por componente.
- **Serviços**: todos os serviços do Windows com estado, consumo ao vivo, dependências, eventos do serviço e
  iniciar/parar/reiniciar.
- **Logs**: busca no log de eventos do Windows (Sistema e Aplicativo) por texto, nível, fonte e período, com "ao vivo".
- **Incidentes**: quando o driver de vídeo trava, um programa cai, há tela azul ou o PC desliga sem aviso, o painel monta
  um relatório com linha do tempo, métricas, hipóteses e um texto pronto para colar numa IA.
- **Sistema**: informações do PC e do monitor, **quem está acessando o painel** (aparelho, IP, nome na rede, MAC,
  última atividade, encerrar a sessão de outro aparelho, histórico de logins e senhas erradas), **registro de todas
  as ações feitas pelo painel** (quem, quando, o quê e se deu certo), **bloquear a tela**, **desligar/reiniciar** (com timer),
  atualizações pendentes (Windows Update + winget).

Gráficos com os últimos 5 minutos, atualização em tempo real (SSE, 1 s), tema claro/escuro, layout para celular e PC.
Pode ser adicionado à tela inicial do celular.

### O que muda em relação à versão Linux

O que existe só em uma versão, o que dá para levar para a outra e o que é impossível está em
[PARIDADE.md](PARIDADE.md) (atualize ao implementar algo novo). Resumo:

| Recurso | Windows |
|---|---|
| Temperatura da CPU, sensores da placa | Só com o **LibreHardwareMonitor** rodando (o Windows não expõe esses sensores). Sem ele, o resto funciona. |
| Controle da ventoinha (curva/manual) | **Não existe** nesta versão. As ventoinhas aparecem em Sensores (só leitura). Use o software da placa ou o FanControl. |
| Desbloquear a tela pelo celular | **Impossível** no Windows: a tela de bloqueio só aceita senha/PIN/Windows Hello no próprio PC. Bloquear funciona. |
| Mandar um app para outra saída de áudio | Funciona (Windows 10 1803+), pela mesma interface interna que o Mixer de volume usa. O Windows lembra a escolha por executável. |
| Volume | Até 100% (o Windows não amplifica). |
| FPS | PresentMon (mede qualquer jogo DirectX/Vulkan/OpenGL, sem configurar o jogo) em vez do MangoHud. |
| "De qual pacote veio" | Assinatura digital (Authenticode) + informações de versão do executável. |
| Atualizações | Windows Update (inclui drivers) + winget. |
| Mural | Tela cheia com relógio, clima, jogo (arte, FPS, CPU, GPU) e música (com letra sincronizada), em três layouts (Cinema, Painel, Clássico) e com seis fundos para o Início. |
| Aparência | Três estilos (Placa, Vidro, Osciloscópio) e seis cores de placa, cada uma no tema escuro e no claro, salvos em cada aparelho. |
| Webhook | Outros programas enviam notificações para o painel. |
| Atualização do painel | Versão instalada × GitHub, changelog, link do repositório e aviso "Nova" na navegação. |

## Instalar

Requisitos: Windows 10/11 e **Python 3.11+ do python.org** (`winget install Python.Python.3.13`; o da Microsoft Store não serve).

```powershell
# na pasta do projeto (clique duas vezes no install.cmd ou rode no PowerShell):
.\install.cmd                    # instala ou atualiza
.\install.cmd -PresentMon -LHM   # também instala o PresentMon (FPS) e o LibreHardwareMonitor (sensores)
```

O instalador pede permissão de administrador e:

1. copia o painel **e um Python próprio** para `C:\Program Files\SelfMonitoring` e instala as dependências nele;
2. cria a tarefa agendada **SelfMonitoring**, que inicia o painel oculto **no seu logon, como administrador**;
3. libera a porta 8765 no Firewall do Windows **só para redes Privadas** (se a sua rede estiver como Pública, o
   instalador avisa: mude em Configurações > Rede e Internet > sua conexão > Tipo de perfil de rede);
4. abre `http://localhost:8765` no navegador para você criar a senha (a primeira senha só pode ser criada no próprio PC).

Depois acesse de qualquer aparelho da rede (ex.: `http://192.168.1.50:8765`). O login dura 30 dias.

**Por que tarefa no logon e não serviço do Windows?** Serviços rodam isolados da área de trabalho (sessão 0): não
conseguiriam capturar a tela, ler o que está tocando, controlar o áudio por app, ver a janela em foco nem mostrar
notificações. **Por que como administrador?** Para ver e finalizar todos os processos, ver o dono de todas as conexões,
contar os bytes por conexão e rodar o PresentMon.
**Por que um Python próprio em Arquivos de Programas?** Como o painel roda elevado, nada que ele executa pode ficar
numa pasta que um programa comum consiga alterar (senão qualquer programa poderia trocar um arquivo e ganhar admin).

**Configuração**: `C:\Program Files\SelfMonitoring\config.toml` (criado a partir de `config.example.toml` na primeira
instalação e **nunca sobrescrito**). Depois de editar, rode o instalador de novo ou reinicie o PC.

**Atualizar**: `git pull` (ou baixe o ZIP) e rode `.\install.cmd` de novo. Configuração e dados (`data\`) são
preservados. O `atualizar.cmd` faz tudo com dois cliques: baixa a versão nova e reinstala. Passo a passo, como conferir a
versão, problemas comuns e como voltar à versão anterior: [ATUALIZAR.md](ATUALIZAR.md).

**Desinstalar**: `.\install.cmd -Uninstall` (mantém config e dados) ou `-Uninstall -Purge` (apaga tudo).

**Senha**: troque na aba **Sistema**. Esqueceu? `.\install.cmd -SetPassword` no PC.

Log: `C:\Program Files\SelfMonitoring\data\self-monitoring.log`.

## Sensores (LibreHardwareMonitor)

O Windows não deixa programas comuns lerem a temperatura da CPU, as ventoinhas e as tensões da placa-mãe: isso
exige um driver. O [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (gratuito,
open source) faz a leitura e publica os valores no próprio servidor web; o painel lê `http://127.0.0.1:8085/data.json`.

1. Instale: `winget install LibreHardwareMonitor.LibreHardwareMonitor` (ou `.\install.cmd -LHM`).
2. Abra como administrador uma vez e rode o `install.cmd`: ele grava **Options > Remote Web Server > Run** (porta 8085) na
   configuração do programa. Marcar pelo menu pode se perder, porque o programa só salva ao fechar.
3. Para iniciar junto com o Windows: **Options > Run On Windows Startup** e **Start Minimized**.

Sem ele, a temperatura da GPU NVIDIA continua aparecendo (vem do driver). A iGPU AMD/Intel ganha temperatura com ele.
A porta 8085 do LibreHardwareMonitor não precisa ser liberada no firewall: o painel lê pelo localhost.
Se nenhuma ventoinha aparecer, a aba Sensores diz o provável motivo (falta o driver PawnIO, o programa não está como
administrador, ou as ventoinhas estão num hub fora da placa). Para um relatório completo, rode `tools\diagnostico.ps1`.

## FPS dos jogos (PresentMon)

O Windows não tem um contador de FPS global. O **PresentMon** (Intel, open source) lê os eventos de apresentação de
quadros do próprio Windows (ETW) e mede qualquer jogo, sem injetar nada nele e sem configurar o jogo.

Instale com `.\install.cmd -PresentMon` (ou `winget install Intel.PresentMon.Console --scope machine`) e ligue
"FPS" no cartão **Jogo** (Visão geral). Ligado, o painel deixa o PresentMon rodando e mede o processo da janela em
foco (se estiver desenhando), senão o jogo da Steam aberto, senão quem mais desenha quadros. Mostra FPS, 1% low
(aprox., últimos 60 s), frametime, **CPU e GPU ocupadas** (quanto de cada quadro foi trabalho do jogo em cada uma; perto
de 100% mostra quem segura o FPS) e a latência até a tela. Desligado, o PresentMon é encerrado e nada é medido.

O PresentMon vê qualquer programa que desenhe com a GPU, não só jogos. Se um app aparecer como jogo, marque-o em
**Mural > Apps que não são jogos**. Navegadores, Discord, VS Code, Tabby e parecidos já são ignorados de fábrica.

Por segurança o painel só usa um PresentMon em pasta de administrador (o do winget `--scope machine` ou um
`PresentMon*.exe` copiado para `C:\Program Files\SelfMonitoring\tools`).

O jogo da Steam aberto aparece mesmo sem PresentMon (a Steam grava o AppID em execução no registro). Jogos não-Steam
adicionados à Steam aparecem com o nome do atalho; se o executável está na pasta de um jogo da Steam (ex.: launcher de
mod), aparecem como esse jogo, com a arte dele.

## Desenvolver

```powershell
py -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python server.py --port 8799                   # roda direto do repositório, sem instalar (dados em .\data)
.venv\Scripts\python -X utf8 -m unittest discover -s tests -t .   # testes
```

Rodando da pasta do repositório, o painel usa `.\config.toml` se existir (senão, os padrões do código) e `.\data\`,
separados da instalação, então dá para testar sem mexer no painel instalado. Sem rodar como administrador, alguns
recursos ficam limitados (bytes por conexão, processos do sistema, PresentMon).
O GitHub Actions (`.github/workflows/ci.yml`) roda os testes num Windows, compila o Python e confere a sintaxe do
JavaScript e dos scripts PowerShell a cada push. Registre mudanças no `CHANGELOG.md`.

## Segurança

- Acesso por senha. Só o hash (scrypt com salt) é salvo, em `data\auth.json`; a pasta `data` só é legível por administradores.
- A primeira senha só pode ser criada na própria máquina (localhost), não pela rede.
- Depois de 5 senhas erradas em 5 min, o IP fica bloqueado por 2 min. Trocar a senha encerra as sessões dos outros aparelhos.
- **Todo login aparece como notificação na tela do PC** (aparelho, IP, nome na rede), assim como todo IP bloqueado por
  senha errada. Isso não depende dos alertas: sempre acontece. A aba **Sistema > Quem está acessando** lista as
  sessões abertas e deixa encerrar qualquer uma; logins, saídas, senhas erradas e bloqueios ficam em
  `dataccess-log.json` (últimos 200). Nada é consultado fora da rede: para um IP da internet o painel só oferece um
  link para ver a localização.
- **Registro de ações** (aba Sistema > Ações feitas pelo painel): tudo que muda algo no PC pelo painel (finalizar
  processo, desligar/timer, bloquear, abrir app, volume/saída/mudo, mídia, resolução, brilho, apagar a tela, captura,
  FPS) ou no painel (senha, sessões, alertas, confiáveis, limpar históricos), com aparelho, IP, sessão, hora e resultado
  (inclusive as tentativas recusadas, com o motivo). Ajustes seguidos do mesmo controle viram uma linha só. Fica em
  `dataudit-log.json` (últimas 1000) e no log. Filtra por categoria e por aparelho ("Ver ações" em cada sessão).
- O próprio servidor, o núcleo do Windows e processos críticos (csrss, lsass, winlogon, services...) são protegidos:
  finalizá-los derrubaria o Windows.
- A finalização confere o horário de criação do processo, para não matar um PID reaproveitado (no Windows os PIDs
  são reusados rápido).
- Apps abertos pelo celular rodam como o seu usuário comum, não como administrador (o painel pede ao Explorer).
- `security.allow_*` em `config.toml` desliga cada recurso (finalizar, energia, bloquear, abrir apps, captura, controles).
- Não exponha a porta para a internet: o tráfego é HTTP sem criptografia (feito para a LAN).

## Histórico e análise de processos

O Windows não guarda histórico de processos encerrados. O painel lista os processos a cada `sampling.track_interval`
(0,5 s) e registra quem aparece e quem some. Processos mais curtos que o intervalo podem escapar. O histórico
(últimos `exited_max`) fica em `data\exited.json` e sobrevive a reinícios. Para executáveis em pasta temporária,
Downloads, Público ou ProgramData, o SHA-256 é calculado assim que o processo aparece.

Sinais verificados (`sm\procinspect.py`), em três níveis: **alto**, **médio** e **info**:
nome de processo do Windows (svchost, lsass, csrss...) rodando fora do System32; nome imitando um deles (scvhost.exe);
extensão dupla (fatura.pdf.exe); executável sem assinatura em pasta temporária; executável solto direto no AppData
ou ProgramData; arquivo oculto; assinatura digital inválida; arquivo sem assinatura na pasta do Windows ou rodando
como SYSTEM; interpretador (PowerShell, wscript, mshta, rundll32...) executando script de pasta temporária;
PowerShell com comando codificado; download-e-executa (`IEX ... DownloadString`); `certutil -urlcache`,
`bitsadmin /transfer`, `mshta http:`, `regsvr32 /i:http`; apagar cópias de sombra (ransomware); apagar logs;
mexer nas exclusões do Defender; minerador; shell reverso; porta aberta para a rede por programa sem assinatura.
A assinatura é verificada com o `Get-AuthenticodeSignature` (entende os arquivos do Windows assinados por catálogo) e
fica em cache. **Não é um antivírus**: programas legítimos podem disparar algum sinal. Dá para marcar um executável
como confiável (fica em `data\trusted.json`).

### Simular um processo suspeito

```powershell
powershell -ExecutionPolicy Bypass -File tools\simular-suspeito.ps1        # 120 s; ... 300 para 5 min
```

Cria 4 processos **inofensivos** (cópias do `ping` e um PowerShell que só espera) que disparam sinais de alto risco:
"svchost.exe" rodando da pasta temporária, "fatura.pdf.exe", PowerShell com comando codificado e linha de comando de
minerador. Tudo é encerrado e apagado no fim (ou com Ctrl+C).

## Monitor de rede

Lê a tabela TCP do Windows (com o PID dono) a cada `sampling.net_interval` (2 s) e os sockets UDP pelo psutil.
Mostra para cada conexão o programa, o IP remoto e o nome reverso (PTR), se é internet/rede local/esta máquina,
porta e serviço, direção (entrada/saída), bytes enviados/recebidos e velocidade atual. Conexões encerradas ficam em
`data\connections.json`. Portas em que malware costuma falar (3333, 4444, 14444...) ganham um aviso.

Limites: **o conteúdo não é visível** (quase tudo é HTTPS). Os bytes por conexão TCP vêm das estatísticas por conexão
do Windows, que exigem administrador e contam a partir de quando o painel viu a conexão. UDP (inclui QUIC/HTTP3)
aparece sem contagem de bytes. Sockets UDP em portas dinâmicas (49152+, usados por navegadores e chamadas de voz)
não contam como "porta aberta".

## Serviços

*Serviços* lista os serviços do Windows (EnumServicesStatusEx, uma chamada só) com estado, modo de início e, para os que
estão rodando, memória, CPU e threads do processo. Vários serviços dividem o mesmo `svchost.exe`: o consumo mostrado é do
processo inteiro (aparece "dividido com N"). "Com falha" são os parados com código de saída de erro. "Por usuário" são as
cópias que o Windows cria para cada conta logada (ex.: `CDPUserSvc_1a2b3`).

O detalhe mostra a conta, o executável, as dependências e o que o serviço gravou no log de eventos. Iniciar, parar e
reiniciar usam o próprio Gerenciador de Serviços (o painel roda como administrador); para parar, pede confirmação. Os
essenciais do Windows, o Defender e o Firewall não são parados pelo painel. Para desligar as ações:
`[security] allow_services = false`.

## Logs (log de eventos do Windows)

*Logs* lê os canais **Sistema** e **Aplicativo** do log de eventos (o mesmo do Visualizador de Eventos) pela API nativa
do Windows. Filtros: texto ou regex, nível (críticos, erros, avisos, informação), fonte (ex.: `Service Control Manager`,
`nvlddmkm`), início do Windows (inclusive os anteriores) e período. "Ao vivo" acrescenta as linhas novas a cada 2 s. Por padrão as mais recentes ficam no topo ("Mais recentes primeiro").
Tocar numa linha mostra todos os campos do evento, "Ver ao redor" (tudo 2 min antes e depois) e "Só esta fonte".
Para esconder a aba: `[security] allow_logs = false`.

## Abrir aplicativos pelo celular

A aba **Apps** lista o mesmo que o menu Iniciar (`Get-StartApps`): programas, apps da Loja e jogos da Steam, com os
ícones do Windows (e do cache da Steam para jogos). Tocar abre o app **na tela do PC, como o seu usuário comum**
(o painel pede ao Explorer, então o app não herda o administrador). Se a tela estiver bloqueada, o app abre por trás
do bloqueio. Desative com `security.allow_launch = false`.

## Sessão

*Sistema > Sessão* mostra se a tela está bloqueada e permite **bloquear** pelo celular. Desbloquear pelo celular não
é possível no Windows (a tela de bloqueio só aceita a senha, o PIN ou o Windows Hello no próprio PC).
Se o login automático do Windows estiver ligado, o painel avisa (qualquer um que ligar o PC entra na sua conta).
Se outra conta estiver usando o PC (o painel roda na conta da tarefa agendada), o cartão avisa e mostra como reinstalar;
o próprio instalador confere isso antes de criar a tarefa.

## Áudio e tela

**Áudio** (Core Audio): saída padrão (multimídia e comunicação), volume/mudo de cada saída, volume/mudo **de cada
aplicativo** (como o Mixer de volume), microfone (mudo, volume, entrada padrão) e quais apps estão usando o microfone.

**Saída por aplicativo** (ex.: Chrome pelo monitor, Spotify pelo fone): cada app tem um seletor "Padrão (…)" ou uma
saída fixa. É o "Dispositivo de saída" do Mixer de volume do Windows; como não há API pública, o painel usa a mesma
interface interna (`IAudioPolicyConfigFactory`) do EarTrumpet. O Windows grava a escolha para o executável, então ela
vale nas próximas vezes que o app abrir; "Padrão" desfaz. Se a interface não existir na versão do Windows, o seletor
some. Alguns apps só trocam de saída ao reiniciar a reprodução.

**Tela**: resolução e taxa de atualização de cada monitor (com o nome real dele e a conexão). Toda mudança precisa ser
confirmada em 20 s; sem confirmação volta ao modo anterior (proteção contra tela preta). **Brilho/contraste** via
DDC/CI (o monitor precisa ter DDC/CI ligado no menu). **Apagar tela** põe brilho e contraste em 0: a tela fica
praticamente preta mas o monitor continua ligado e conectado; **Restaurar** volta aos valores salvos. **Espera**
desliga a imagem (qualquer tecla ou mouse acorda), com o aviso de que alguns monitores DisplayPort se desconectam.

## Captura de tela

*Controles > Captura de tela*. **Toda captura mostra antes uma notificação do Windows** ("📸 Captura de tela pelo
painel", com o IP de quem pediu). Se a notificação não puder ser exibida (notificações do "Self Monitoring" desativadas
nas Configurações), a captura é recusada. Captura todos os monitores; jogos em tela cheia exclusiva podem sair pretos.
Não captura com a tela bloqueada. As últimas 30 ficam em `data\screenshots\`. Desative com `security.allow_screenshot = false`.

## No PC agora: janela em foco e mídia

**Janela em foco**: título, aplicativo (nome amigável do executável), PID, há quanto tempo e se está em tela cheia.
Apps da Loja são resolvidos para o processo real (não o "ApplicationFrameHost").

**Mídia**: os mesmos dados do painel de mídia do Windows (o que aparece ao mudar o volume): título, artista, álbum,
capa e posição de cada app que toca algo (Spotify, navegadores, VLC, Media Player...). ⏮ ⏯ ⏭ e ±10 s seguem
`security.allow_control`. Desligar: `collectors.disabled = ["media"]`.

## Downloads da Steam

Igual à versão Linux: o painel junta o `logs\content_log.txt` (estado) e os `steamapps\appmanifest_<id>.acf`
(bytes baixados/total) da instalação da Steam (achada pelo registro). A velocidade é o tráfego do `steam.exe` no
monitor de rede (sem ele, a variação dos bytes baixados). O cartão só aparece com algo na fila.

## Incidentes

O painel lê o log de eventos do Windows a cada 3 s (avisos e piores, mais os relatórios de erro 1001) e passa cada linha
por regras (`sm/rules.py`). Abre um incidente quando:

| O quê | Fonte no log de eventos |
|---|---|
| Driver de vídeo parou de responder / GPU reiniciada | `Display` 4101, `nvlddmkm` 13/14/153, `LiveKernelEvent` 141/117 |
| Programa travou | `Application Error` 1000 (exceção, módulo com falha, versão) |
| Tela azul | `WER-SystemErrorReporting` 1001 |
| Memória virtual esgotada | `Resource-Exhaustion-Detector` 2004 |
| Serviço caiu | `Service Control Manager` 7031/7034/7023/7024 |
| Disco / NTFS | `disk` 7/11/51/153, `stornvme`/`storahci` 129, `Ntfs` 55/98/137/140 |
| Erro de hardware | `WHEA-Logger` 1/18/20/46 |
| Desligamento inesperado | `Kernel-Power` 41 e `EventLog` 6008, conferidos na partida |

Gatilhos próximos (2 min) viram um incidente só; o mesmo programa caindo de novo em 15 min conta como repetição. O
relatório junta a linha do tempo (5 min antes até 1 min depois), as métricas (ao vivo ou o resumo por minuto do banco),
os processos e o jogo no momento, o programa ou serviço envolvido, as atualizações do Windows dos 3 dias anteriores e as
mudanças de hardware/driver da semana, e lista hipóteses com confiança e o que verificar. "Copiar para IA" gera um
Markdown com tudo isso. Na aba Logs, "Investigar este momento" faz o mesmo para qualquer linha.

O Windows reenvia relatórios antigos de travamento da GPU (ex.: no logon); o painel lê a data do arquivo de despejo e
não abre incidente por eles. Nos primeiros 2 min depois de ligar, avisos de drivers e serviços não abrem incidente
(disco e hardware abrem).

Tudo fica em `data\store.db` (SQLite): eventos e métricas por minuto por 30 dias, resumo por hora por 1 ano, incidentes
e o inventário de hardware. Configuração em `[history]` (`enabled`, `days`, `incidents`).

## Alertas

Aba **Alertas** (e o sino no topo). Regras: temperatura da CPU/GPU (sustentada por 30 s), disco quase cheio, memória,
processo suspeito novo, porta nova aberta para a rede, login no painel, IP bloqueado por senha errada, atualizações,
FPS baixo. A mesma condição não repete por 30 min. Canais: o painel, a **tela do PC** (notificação do Windows) e
**push no celular via ntfy** (use um tópico com nome longo e aleatório). Há um botão de teste.

## Mural

Uma tela cheia para deixar aberta num monitor, tablet ou celular. Abra pela aba *Mural* ou direto em `/#mural:abrir`. Mexa o
mouse ou toque na tela para trocar o modo, entrar em tela cheia ou sair (também com Esc).

| Modo | O que mostra |
|---|---|
| Automático | Jogo aberto; senão, música tocando; senão, Início. |
| Início | Relógio, data, clima e previsão de 3 dias. |
| Jogo | Arte do jogo de fundo, logo e os itens escolhidos: FPS, 1% low, frametime, CPU, GPU, temperaturas, VRAM, RAM, tempo de jogo e player. |
| Música | Capa de fundo, faixa, artista, álbum, progresso, controles, hora e temperatura. |

- **Clima:** escolha a cidade na aba. A previsão vem da [Open-Meteo](https://open-meteo.com/), sem cadastro; só as coordenadas
  da cidade saem do PC, e nada é consultado antes da escolha.
- **Fundos do Início:** Céu da cidade, Serra, Placa de circuito, Aurora, Capas dos jogos (arte da Steam, trocada a cada 2 min)
  e Sua imagem. Escolha em Mural > Fundo do Início ou pelo botão de imagem na barra do Mural.
- **Capas:** cada jogo usa a arte do cache da Steam (ou da CDN dela, se faltar) ou uma imagem sua, reduzida no navegador antes do envio.
- **FPS** depende da leitura com o PresentMon (ver [FPS dos jogos](#fps-dos-jogos-presentmon)).
- **Cores:** vêm da arte do jogo ou da capa da música; no Início, do céu da cidade na hora atual. Se a imagem não permitir a
  leitura das cores, fica a paleta anterior.
- **Eventos:** avisos passam no canto; alertas críticos ocupam a tela inteira e devolvem a cena anterior. Maior prioridade passa
  na frente, e os demais esperam na fila. As opções ficam na aba Mural.
- **Tela apagando:** impedir a tela de apagar exige HTTPS ou localhost. Pelo IP da rede, ajuste o tempo de tela no próprio aparelho.
- **Tela cheia:** o iPhone não oferece tela cheia para páginas. Adicione o painel à Tela de Início para abrir sem as barras do Safari.

## Notificações por webhook

Outros programas avisam pelo painel. Gere o token na aba *Alertas*: ele aparece uma vez, e o painel guarda só o hash. Sem
token, o webhook fica desligado.

```bash
curl -X POST http://SEU-PC:8765/api/webhook/notify \
  -H "Authorization: Bearer SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Backup concluído", "message": "120 GB copiados.", "icon": "check", "level": "info", "source": "Backup"}'
```

| Campo | Tipo | Descrição |
|---|---|---|
| `title` | texto, obrigatório | Até 120 caracteres. |
| `message` | texto | Até 1000 caracteres. |
| `icon` | texto | Um emoji ou nome de ícone (a lista completa está na aba Alertas). Sem ele, usa o sino. |
| `level` | texto | `info` (padrão), `warning` ou `critical`. |
| `source` | texto | Nome do programa que enviou, até 60 caracteres. |
| `url` | texto | Link `http(s)` aberto a partir do alerta. |
| `display` | texto | Como aparece no Mural: `auto` (padrão; `critical` ocupa a tela), `toast` ou `fullscreen`. |
| `duration` | número | Segundos no Mural, de 3 a 300. Padrão: info 5, warning 8, critical 12, tela inteira 20. |
| `silent` | booleano | `true`: só no painel e no Mural, sem tela do PC nem ntfy. |

Respostas: `200` com `{"ok": true, "id": 12}`; `400` campo inválido (com `error`); `401` token errado; `403` webhook desligado;
`429` limite excedido, com `Retry-After`. Limites: 60 notificações por minuto e 5 tokens errados por IP a cada minuto.

O token dá acesso só a esta rota, não ao resto do painel. O painel usa HTTP: na rede local o token viaja sem criptografia, então
não exponha a porta na internet; use uma VPN.

## Atualizações

*Sistema > Atualizações pendentes*: Windows Update (a mesma busca das Configurações, inclui drivers; leva de
30 s a alguns minutos) e `winget upgrade`. Verifica sozinho a cada 6 h. Atualizações do Windows e drivers aparecem
destacadas. Quando o Windows já instalou algo que só vale depois de reiniciar, aparece "O PC precisa reiniciar" no
topo do cartão. **O painel não instala atualizações**.

### Atualização do painel

A aba *Atualização* compara a versão instalada com a do GitHub e mostra o changelog, o link do repositório e os comandos para
atualizar. Quando há versão maior, a navegação mostra **Nova**. O aviso depende do número da versão: um commit que não
aumenta `__version__` não gera aviso.

O painel só lê dois arquivos públicos do repositório (`raw.githubusercontent.com`), com ele aberto e no máximo a cada 6 h. Não
envia dados do PC, não baixa nem instala nada. Para desligar: `check = false` em `[update]` no `config.toml`. A aba continua
mostrando o changelog da versão instalada.

## Desligar / reiniciar

Aba **Sistema > Energia** (`shutdown.exe`). Pede confirmação. **Forçar finalização** (marcado por padrão) fecha os
programas sem esperar; sem forçar, o Windows pode ficar parado em "Este app está impedindo o desligamento".
**Timer**: desligar ou reiniciar daqui a X minutos, sempre forçado. Avisa na tela do PC ao agendar, 5 min e 1 min
antes; pode ser cancelado. Sobrevive a reinícios do painel. Desative com `security.allow_power = false`.

## Estrutura e como estender

```
server.py                 ponto de entrada (reinicia no modo UTF-8 do Python se preciso)
install.ps1 / install.cmd instalar / atualizar / desinstalar / trocar senha
config.example.toml       modelo de configuração (vira config.toml na instalação; config.toml não vai pro git)
requirements.txt          dependências (psutil, pycaw/comtypes, winrt)
tests/                    testes (python -X utf8 -m unittest discover -s tests -t .)
tools/simular-suspeito.ps1  processos inofensivos que imitam malware (para testar a aba Suspeitos)
tools/diagnostico.ps1     junta o que é preciso para entender um problema (não muda nada); cole a saída
sm/
  win.py                  subprocessos sem janela, PowerShell, admin, versão do .exe, encoder PNG
  config.py               leitura da configuração
  sampler.py              roda os coletores periodicamente; Context compartilhado
  history.py              séries temporais em memória (gráficos)
  server.py               HTTP: estáticos, API JSON, SSE (/api/stream), autenticação
  auth.py                 senha (hash scrypt), sessões e limite de tentativas
  actions.py              finalizar/forçar/pausar processo
  proctrack.py            histórico de processos encerrados, serviços do Windows por PID
  procinspect.py          análise de processo, assinatura digital, hash, heurísticas de suspeita
  netmon.py, winnet.py    monitor de conexões (tabela TCP do Windows + estatísticas por conexão)
  power.py                desligar/reiniciar e timer
  desktop.py              sessão (bloquear), notificação na tela, apps do menu Iniciar e ícones, Steam
  audio.py                saídas/entradas/apps de áudio (Core Audio)
  display.py              resolução com reversão, nomes dos monitores, brilho DDC/CI, espera
  game.py                 jogo atual (Steam) e FPS (PresentMon)
  alerts.py               regras de alerta, histórico e canais (PC, ntfy)
  screenshot.py           captura de tela com aviso obrigatório no PC
  updates.py              Windows Update + winget
  focus.py                janela em foco
  media.py                o que está tocando (controles de mídia do Windows) e controles
  steamdl.py              downloads/atualizações da Steam (content_log + appmanifest)
  api/routes.py           endpoints REST
  collectors/             UM ARQUIVO POR COLETOR (carregados automaticamente)
  hw/
    pdh.py                contadores de desempenho do Windows (PDH)
    gpu.py                GPUs: DXGI (lista), contadores "GPU Engine" (uso por processo), NVML (NVIDIA)
    lhm.py                sensores do LibreHardwareMonitor
web/                      frontend (sem build; fonte Archivo, licença OFL, e ícones Phosphor, licença MIT, incluídos)
```

**Novo coletor**: crie `sm\collectors\docker.py`:

```python
from . import Collector, register

@register
class Docker(Collector):
    name = "docker"
    interval = 5            # segundos (None = padrão)
    def collect(self):
        self.record("docker.running", 3)   # opcional: vira série para gráfico
        return {"running": 3}
```

Os dados aparecem em `snapshot.data.docker` no frontend e `app.history["docker.running"]` nos gráficos.
**Novo endpoint**: `@route("POST", "/api/algo/<id>")` em `sm\api\routes.py`.
**Nova aba**: crie `web\js\modules\minha.js` exportando `{ id, title, icon, mount(el, app), show(), hide() }` e adicione
em `web\js\modules\index.js`.

## API

A mesma da versão Linux, exceto: sem `/api/fan`; `POST /api/session/unlock` responde 501 (o Windows não permite);
`POST /api/audio` com `op: "move"` aceita `sink: ""` para voltar à saída padrão, e cada app em `GET /api/audio` traz
`routed` (saída escolhida ou `null`); modos de tela têm id `LARGURAxALTURA@HZ` (ex.: `3440x1440@160`) e
saídas `DISPLAY1`, `DISPLAY2`...; `GET /api/processes/<pid>/inspect` traz `signature` e `version_info` no lugar de
`package`/`package_info`.

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/info` | informações estáticas, coletores ativos, recursos (`features.admin`...) |
| GET | `/api/snapshot` | última amostra de todos os coletores |
| GET | `/api/stream` | Server-Sent Events com um snapshot por segundo |
| GET | `/api/history?keys=cpu.total,gpu.0.busy` | séries temporais |
| GET | `/api/processes` | lista completa de processos |
| POST | `/api/processes/<pid>/signal` | `{"signal": "TERM"\|"KILL"\|"STOP"\|"CONT", "create_time": ...}` (TERM = fechar janela) |
| GET | `/api/processes/exited` | histórico de encerrados (`POST .../exited/clear` limpa) |
| GET | `/api/processes/suspicious` | processos com sinais de atenção (`?info=1` inclui os informativos) |
| GET | `/api/processes/<pid>/inspect` | análise de processo vivo; `/api/processes/record/<id>/inspect` para encerrados |
| POST | `/api/processes/trust` | `{"exe": "C:\\caminho\\app.exe", "trusted": true}` |
| GET | `/api/net` | conexões ao vivo, por programa e portas abertas (`/api/net/closed` encerradas) |
| GET/POST | `/api/power` | permissão; `{"action": "poweroff"\|"reboot", "force": true, "confirm": true}` |
| GET/POST | `/api/power/timer` | `{"action":"poweroff","minutes":60}` agenda (forçado); `{"cancel":true}` |
| GET | `/api/session` | sessão do Windows (bloqueada?), login automático |
| POST | `/api/session/lock` | bloqueia a tela do PC |
| GET | `/api/apps` | aplicativos do menu Iniciar; `/api/apps/<id>/icon`; `POST /api/apps/<id>/launch` |
| GET/POST | `/api/mural/background` | imagem de fundo do Início; `POST` com `{"image": "data:image/jpeg;base64,..."}`; `POST /api/mural/background/reset` remove |
| GET/POST | `/api/game` | jogo atual e FPS; `{"fps_enabled": true}` liga o PresentMon |
| POST | `/api/game/not-game` | `{"key": "exe:tabby.exe", "name": "Tabby", "ignored": true}`; `"ignored": false` desfaz |
| POST | `/api/media` | `{"player": "Spotify.exe", "op": "play_pause"\|"next"\|"previous"\|"seek"\|"shuffle"\|"repeat", "offset": 10}` |
| GET | `/api/media/lyrics?player=Spotify.exe` | letra sincronizada da faixa (`synced: [[segundos, verso], ...]`, do LRCLIB; vazio se o Mural estiver com a letra desligada) |
| GET | `/api/media/audio?player=Spotify.exe` | sessão de áudio do player (volume do app); mudar com `POST /api/audio` `{"op": "volume", "kind": "app", "index", "volume"}` |
| GET/POST | `/api/audio` | estado; `{"op": "set_default"\|"volume"\|"mute"\|"move", "kind": "sink"\|"source"\|"app"\|"rec", ...}` |
| GET | `/api/display` | monitores e modos; `POST /api/display/mode` `{"output":"DISPLAY1","mode":"1920x1080@144"}` ou `{"op":"confirm"\|"revert"}` |
| GET/POST | `/api/display/ddc` | brilho/contraste; `{"bus", "brightness"/"contrast"}` ou `{"bus","op":"dim"\|"restore"}` |
| POST | `/api/display/dpms` | `{"on": false}` desliga a imagem |
| GET | `/api/updates` | atualizações pendentes; `POST /api/updates/refresh` verifica agora |
| GET | `/api/alerts` | histórico; `POST /api/alerts/read`, `/clear`, `/test`; `GET/POST /api/alerts/config` |
| POST | `/api/screenshot` | captura (avisa na tela do PC); `GET /api/screenshots`, `/api/screenshots/<nome>` |
| GET | `/api/services?scope=system\|user` | serviços com estado e consumo; `GET /api/services/<nome>` detalhe e eventos |
| POST | `/api/services/<nome>/start\|stop\|restart` | ações no serviço (`security.allow_services`) |
| GET | `/api/logs?grep=&priority=4&unit=<fonte>&kernel=1&boot=0&since=&until=&limit=300&before=&after=` | log de eventos; `kernel=1` = só o canal Sistema; `before`/`after` = `cursor` de uma linha |
| GET | `/api/incidents?status=open` | incidentes e estado do vigia; `GET /api/incidents/<id>` relatório; `POST /api/incidents/<id>` `{"status": "resolved", "note": "..."}`; `POST /api/incidents/<id>/delete` |
| POST | `/api/incidents/investigate` | `{"at": epoch, "before": 300, "after": 60, "note": "..."}` investiga um momento |
| GET | `/api/history/metrics?keys=cpu.temp&since=&until=&res=60\|3600` | métricas gravadas; `/api/history/hardware` inventário; `/api/history/events` eventos classificados |
| GET | `/api/fleet` | identidade da máquina; `/api/fleet/export?since=<seq>` pacote para um servidor central (futuro) |
| GET | `/api/logs/entry?cursor=` | todos os campos de um evento; `/api/logs/boots` inícios do Windows; `/api/logs/sources` fontes vistas |

Autenticação: cookie de sessão obtido em `POST /login` (campo `password`). Troca de senha:
`POST /api/auth/password` com `{"current": "...", "new": "..."}`. Sair: `POST /logout`.
Sessões: `GET /api/auth/sessions` (sessões abertas e últimos acessos, com origem do IP);
`POST /api/auth/sessions/end` com `{"id": "..."}` (id público da sessão) ou `{"others": true}`.
Ações: `GET /api/audit?cat=audio&session=<id>&limit=100&before=<seq>` (todos os parâmetros opcionais).
