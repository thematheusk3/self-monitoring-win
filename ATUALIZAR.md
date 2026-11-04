# Como atualizar o self-monitoring (Windows)

O painel que está rodando **não é** a pasta do projeto: o instalador copia tudo para
`C:\Program Files\SelfMonitoring` e é de lá que a tarefa agendada inicia o painel. Por isso, baixar código novo não
muda nada sozinho: depois de baixar, é preciso **rodar o instalador de novo**. Ele para o painel, copia os arquivos
novos, atualiza as dependências e inicia o painel outra vez.

**O que é preservado:** a configuração (`config.toml`), a senha, as sessões abertas (ninguém precisa entrar de
novo), os históricos, os alertas, o registro de ações e as capturas (`data\`). **O que é substituído:** o código do
painel (`sm\`, `web\`, `server.py`) e o Python próprio dele.

## 1. Ver qual versão está rodando

No painel: aba **Sistema**, cartão **Monitor**, linha **Versão** (ex.: `0.6.0-win`). A lista do que mudou em cada
versão está no [CHANGELOG.md](CHANGELOG.md).

## 2. Baixar a versão nova

### Opção A: com git (recomendado)

Se a pasta do projeto foi criada com `git clone` (é o caso de `C:\win-self-monitoring`), no PowerShell:

```powershell
cd C:\win-self-monitoring
git pull
```

Se aparecer erro dizendo que há alterações locais, você mexeu em algum arquivo do projeto. Para ver quais:
`git status`. Para descartar essas alterações e ficar igual ao GitHub: `git checkout -- .` e rode `git pull` de novo.
A sua configuração **não** fica nessa pasta, e sim em `C:\Program Files\SelfMonitoring`, então não se perde.

### Opção B: sem git (download do ZIP)

1. Abra <https://github.com/thematheusk3/self-monitoring-win>, clique em **Code** e depois em **Download ZIP**.
2. Clique com o botão direito no ZIP baixado, abra **Propriedades** e marque **Desbloquear**, se a opção aparecer
   (senão o Windows marca os scripts como "baixados da internet").
3. Extraia o ZIP numa pasta, por exemplo `C:\win-self-monitoring`. Pode substituir a pasta antiga.

## 3. Instalar por cima

Na pasta do projeto, dê dois cliques em **`install.cmd`**, ou rode no PowerShell:

```powershell
.\install.cmd
```

- O Windows pede permissão de administrador: aceite.
- Se você usa o PresentMon ou o LibreHardwareMonitor, não precisa passar `-PresentMon`/`-LHM` de novo: eles já
  estão instalados e o painel continua usando.
- O instalador termina abrindo `http://localhost:8765`. Na aba **Sistema**, confira se a **Versão** mudou.

Quem estava com o painel aberto no celular só precisa **recarregar a página**. Se a tela ficar estranha (layout
quebrado, botão que não responde), o navegador guardou arquivos antigos: recarregue sem cache (no PC, `Ctrl+F5`; no
celular, feche a aba e abra de novo).

## 4. Se algo der errado

| Sintoma | O que fazer |
|---|---|
| O instalador fecha com erro em vermelho | Leia a mensagem: ela diz o passo que falhou (cópia, pip, firewall, tarefa). Rode de novo; se repetir, guarde o texto. |
| Erro no `pip` (dependências) | Precisa de internet. Confira também se o Python instalado é o do python.org (`winget install Python.Python.3.13`). O da Microsoft Store não serve. |
| O painel não abre depois de instalar | Veja o log em `C:\Program Files\SelfMonitoring\data\self-monitoring.log` (abra o Bloco de Notas como administrador). |
| Ver se a tarefa está rodando | `Get-ScheduledTask SelfMonitoring \| Select State` no PowerShell. `Ready` = parada; `Running` = rodando. Para iniciar: `Start-ScheduledTask SelfMonitoring`. |
| "Porta em uso" no log | Outro programa (ou um painel antigo aberto à mão) está usando a 8765. Reinicie o PC ou feche esse programa. |
| Não acessa mais pelo celular | Confira se a rede do PC ainda está como **Privada** (Configurações > Rede e Internet > sua conexão). |

### Voltar para a versão anterior

Com git, cada atualização é um commit. Para ver os últimos e voltar a um deles:

```powershell
cd C:\win-self-monitoring
git log --oneline -10          # lista: <código> <descrição>
git checkout <código>          # ex.: git checkout 2b63229
.\install.cmd
```

Para voltar a acompanhar as atualizações depois: `git checkout main`, `git pull` e `.\install.cmd`.

Sem git: baixe o ZIP de um commit antigo no GitHub (**Commits** > escolha um > **Browse files** > **Code** >
**Download ZIP**) e instale por cima do mesmo jeito.

### Voltar para a interface antiga

A interface anterior à repaginada (0.6.0-win) está guardada na marca `ui-classica-0.5.3`. Este comando troca só a pasta
`web\` e mantém o restante do painel atualizado (as correções do servidor continuam valendo):

```powershell
cd C:\win-self-monitoring
git fetch --tags
git restore --source=ui-classica-0.5.3 -- web
.\install.cmd
```

Para voltar à interface nova:

```powershell
cd C:\win-self-monitoring
git restore -- web
.\install.cmd
```

Depois de um `git restore --source`, o `git status` mostra a pasta `web\` como alterada. Desfaça com `git restore -- web`
antes de um `git pull`. Recursos de tela criados depois da 0.6.0-win não existem na interface antiga.

Para decidir vendo as duas, compare no GitHub:
<https://github.com/thematheusk3/self-monitoring-win/compare/ui-classica-0.5.3...main>

## Resumo

```powershell
cd C:\win-self-monitoring
git pull
.\install.cmd
```
