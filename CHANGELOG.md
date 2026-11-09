# Changelog

## 0.12.0-win
Trazido da versão Linux (seção 2 do [PARIDADE.md](PARIDADE.md)):
- **Atualizações: aviso "O PC precisa reiniciar"** no topo do cartão quando o Windows Update já instalou algo que só vale
  depois de reiniciar (as mesmas chaves do registro que o Windows Update consulta).
- **Visão geral: uso da GPU por engine** ("Por engine: 3D 42% · Vídeo decod. 3%").
- **Jogo: CPU e GPU ocupadas e latência**, lidas do PresentMon: quanto de cada quadro foi trabalho do jogo na CPU e na GPU
  (perto de 100% mostra quem está segurando o FPS) e o tempo até o quadro chegar à tela.
- **Jogos não-Steam adicionados à Steam** (ex.: launcher do Seamless Coop do Elden Ring): se o executável está na pasta de um
  jogo da Steam instalado, vira esse jogo (nome e arte do Mural); senão aparece o nome do atalho (`shortcuts.vdf`).
- **Sensores: dica quando nenhuma ventoinha aparece** (LibreHardwareMonitor sem o driver PawnIO, sem administrador, ou
  ventoinhas fora da placa).
- **Sistema > Sessão: aviso de conta errada**: se outra conta está usando o PC, o cartão diz e mostra como reinstalar.
- **Instalador não instala na conta errada**: se foi aberto como outra conta de administrador, avisa e oferece instalar para
  quem está usando o PC.
- **`tools\diagnostico.ps1`**: junta tarefa agendada, sessões, porta e firewall, placa-mãe, LibreHardwareMonitor, PresentMon,
  áudio, monitores e o fim do log, para colar quando algo não aparece. Não muda nada no PC.

## 0.11.1-win
- **Sensores sem precisar clicar em Run**: o instalador agora grava o servidor web do LibreHardwareMonitor na configuração dele
  e o reinicia. Antes, a opção podia se perder ao desligar o PC, e os sensores só voltavam depois de clicar em **Run** de novo.

## 0.11.0-win
- **Estilos do painel**: a janela **Aparência** ganhou três estilos, que mudam todas as abas, a começar pela Visão geral, e
  combinam com qualquer cor, no tema escuro e no claro.
  - **Placa**: o visual de sempre, com LEDs e contatos.
  - **Vidro**: painéis translúcidos e arredondados e medidores contínuos, sobre uma aurora na cor escolhida; no tema claro, sobre
    uma placa clara.
  - **Osciloscópio**: tela de instrumento, com grade nos gráficos, traço luminoso e números em fonte de instrumento; no tema
    claro, fica como papel de registrador.

## 0.10.0-win
- **Cores do painel**: o botão de tema agora abre **Aparência**, com seis cores de placa (verde, azul, vermelha, roxa, preta e
  amarela), cada uma no tema escuro e no claro. A escolha fica salva em cada aparelho.
- **Fundos do Início do Mural**: seis opções. **Céu da cidade**, agora desenhado, com sol e lua na posição real, estrelas,
  nuvens, chuva e neve conforme o clima; **Serra**; **Placa de circuito**, com pulsos de luz nas trilhas e a cor do painel;
  **Aurora**; **Capas dos jogos**, com a arte da Steam mudando a cada 2 minutos; e **Sua imagem**.
- Escolha o fundo em Mural > Fundo do Início, com miniaturas, ou troque pelo botão de imagem dentro do próprio Mural.
- **Barra do Mural redesenhada**: dock de vidro com ícones e um destaque que desliza até o modo escolhido. No celular, só o
  modo atual mostra o nome.

## 0.9.2-win
- **`atualizar.cmd`**: dois cliques baixam a versão nova do GitHub e reinstalam o painel. Antes de instalar, mostra a versão
  instalada, a do GitHub e os commits novos. Com `-Verificar` só mostra o que há de novo; com `-Forcar` reinstala mesmo sem
  novidade. Se houver arquivos alterados na pasta, ele para sem mudar nada.
- A aba Atualização agora indica o `atualizar.cmd`.

## 0.9.1-win
- **Apps que não são jogos**: nova lista na aba Mural. Marque um programa que apareceu como jogo e ele sai do modo Jogo, do
  aviso de jogo aberto, da leitura de FPS e do alerta de FPS baixo. Vale também para apps da Steam, e dá para desfazer.
- Correção: terminais, navegadores e outros programas que desenham com a GPU apareciam como jogo. O Tabby agora é ignorado de
  fábrica, e navegadores, Discord, VS Code e os demais da lista interna deixam de contar mesmo com a janela em foco.

## 0.9.0-win
- **Cores vivas no Mural**: a paleta agora vem da arte do jogo ou da capa da música e muda com transição suave. O texto
  continua legível, porque a paleta garante o contraste.
- **Céu real no Início**: o fundo segue o amanhecer, o dia, o entardecer e a noite da sua cidade, e fica acinzentado com chuva
  ou tempo fechado.
- Telas redesenhadas: painéis de vidro, clima com mais detalhes, anéis de CPU, GPU e RAM, gráfico de FPS, capa com brilho na
  cor da arte, troca de arte com fade e movimento lento de fundo, que respeita "reduzir movimento".
- **Motor de eventos**: cada acontecimento tem prioridade. Avisos simples passam no canto (info 5 s, warning 8 s); alertas
  críticos ocupam a tela inteira por 20 s e devolvem a tela anterior. O mais urgente passa na frente, e os demais esperam na
  fila. Toque em Dispensar ou tecle Esc para fechar.
- O Mural também avisa quando um jogo abre e quando a música muda. Dá para desligar isso na aba Mural.
- Webhook: novo campo `display` (`auto`, `toast` ou `fullscreen`), para uma notificação ocupar a tela inteira; `duration`
  sem valor agora usa o tempo padrão do nível. Rajadas de notificações não se perdem mais no Mural.

## 0.8.0-win
- Nova aba **Mural**: uma tela cheia para deixar aberta, como um mostrador de mesa. Modos **Início** (relógio, data, clima
  e previsão), **Jogo** (arte do jogo de fundo, logo, FPS, 1% low, CPU, GPU, temperaturas, VRAM, tempo de jogo e player de
  música, cada item ligado ou desligado) e **Música** (capa de fundo, faixa, progresso e controles). O modo **Automático**
  escolhe sozinho: jogo, senão música, senão Início.
- Na primeira vez, você busca a sua cidade; dá para trocar depois. O clima vem da Open-Meteo, sem cadastro.
- **Capas dos jogos**: escolha o fundo de cada jogo entre as artes da Steam (fundo, capa vertical, banner) ou envie uma
  imagem. Jogos fora da Steam usam a imagem enviada.
- **Notificações por webhook** (aba Alertas): outros programas enviam título, mensagem, ícone, nível, origem, link e
  duração. O aviso entra nos alertas, vai para a tela do PC e o ntfy, se ligados, e aparece no Mural. Fica desligado até você
  gerar um token. Guia de uso no README.
- Para abrir o Mural direto em um tablet ou monitor: endereço do painel + `/#mural:abrir`.

## 0.7.0-win
- Nova aba **Atualização**: versão instalada e a do GitHub, botão "Verificar agora", como atualizar, link do repositório
  e o changelog de cada versão, com as novas e a instalada marcadas.
- A navegação mostra **Nova** na aba Atualização quando o GitHub tem versão maior que a instalada. No celular, a aba fica
  em "Mais", que ganha um marcador.
- A verificação só consulta o GitHub, não baixa nem instala nada. Roda com o painel aberto, no máximo a cada 6 h, e pode
  ser desligada em `[update] check = false` no `config.toml`.
- Sistema > Monitor: a versão agora leva à aba Atualização.

## 0.6.0-win
- **Interface repaginada**, com as mesmas funções. A aparência lembra a placa do próprio PC: fundo verde de máscara de
  solda, dourado nos botões e na navegação, medidores em LEDs. Temas escuro e claro.
- Visão geral: faixa no topo que diz se está tudo bem ("Tudo normal", "Fique de olho", "Precisa de atenção"), cita o
  motivo, avisa quando faltam dados do PC e mostra uma coluna de LEDs por thread da CPU.
- Navegação: barra lateral no PC. No celular, 4 abas e "Mais", em vez de uma barra que escondia abas ao rolar.
- Ícones em SVG no lugar dos emojis e fonte Archivo incluída no painel: a aparência é a mesma em qualquer aparelho e
  nada é baixado da internet.
- Acessibilidade: contraste mínimo de 4,5:1 nos dois temas, foco de teclado visível, áreas de toque maiores no celular,
  campos de senha com rótulo, link "Ir para o conteúdo", botões de ícone com nome e respeito a "reduzir movimento".
- Ícones de alguns apps não apareciam (erro 500): o handle de 64 bits do bitmap estourava ao ser passado para a API.
- Com muitos pedidos ao mesmo tempo, o servidor recusava conexões e a página carregava pela metade (ex.: logo quebrado).
- Tabela de atualizações não passa mais da largura da tela no celular.
- A interface antiga continua disponível: veja "Voltar para a interface antiga" no [ATUALIZAR.md](ATUALIZAR.md).

## 0.5.3-win
- Sistema > **Ações feitas pelo painel**: registro de tudo que muda algo no PC ou no painel (processos, energia,
  bloqueio, apps, áudio, mídia, tela, captura, FPS, senha, sessões, alertas), com aparelho, IP, sessão, hora e
  resultado, inclusive tentativas recusadas. Ajustes seguidos (arrastar o volume/brilho) viram uma linha.
  Filtros por categoria e por aparelho; "Ver ações" em cada sessão de "Quem está acessando".
- Nome de alguns programas vinha com lixo no fim (ex.: "Spotify" seguido de caracteres estranhos no mixer por app e na análise de
  processos): o tamanho informado pelo executável era maior que o texto.
- [ATUALIZAR.md](ATUALIZAR.md): guia de como atualizar, conferir a versão e voltar à anterior.

## 0.5.2-win
- Sistema > **Quem está acessando**: sessões abertas com aparelho, IP, origem (este PC, rede local, VPN, internet),
  nome na rede (DNS reverso) e MAC (tabela ARP), última atividade e botão para encerrar a sessão de outro aparelho
  (ou todas as outras). Histórico dos últimos acessos: logins, saídas, senhas erradas, bloqueios, sessões encerradas.
- **Todo login no painel mostra uma notificação na tela do PC** (e todo IP bloqueado por senha errada), sempre,
  independente dos alertas. O alerta "Novo acesso ao painel" continua indo para o painel/ntfy e não duplica no PC.
- Captura de tela (e demais avisos na tela do PC) recusada logo após instalar, com "as notificações estão
  desativadas?": antes da primeira notificação o Windows não conhece o app e a consulta da configuração falha
  com "Elemento não encontrado"; agora isso não é tratado como "desativadas" e o aviso é exibido.

## 0.5.1-win
- Controles > Áudio: escolher por qual saída cada app toca (ex.: Chrome no monitor, Spotify no fone), ou "Padrão".
  Usa a interface interna do Mixer de volume do Windows; o Windows lembra a escolha por executável.
- `PARIDADE.md`: o que existe só no Windows ou só no Linux e o que dá para levar de um para o outro.
- Instalador: avisos de programas externos (pip, winget) não interrompem mais a instalação.

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
