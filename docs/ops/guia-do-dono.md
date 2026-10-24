# Guia do dono: o que só você pode executar

Passo a passo para iniciantes das ações que dependem do dono do sistema (VPS, Mac
mini e decisões). Escrito em 2026-10-24, com o sistema no ar na versão `f23eeed`
(deploy #128). O estado atual dos itens abertos fica em `TODO.md`; confira lá antes
de seguir se já passou algum tempo.

## Antes de começar

- **Mac mini**: o computador onde você trabalha e onde roda o Hermes, o robô do Telegram.
- **VPS**: o servidor em São Paulo onde o sistema roda. Você entra nele com `ssh pje-vps`.
- **Terminal**: a janela onde se digita comando. No Mac: Spotlight (⌘ + Espaço), digite "Terminal".
- **Sair do servidor**: dentro da VPS, digite `exit` para voltar ao seu Mac.

### Como saber onde estou

**Todo comando deste guia é digitado no Terminal do Mac.** O que muda é *onde ele roda*:

- **No Mac mini**: você está no Terminal normal. O início da linha mostra o nome do seu
  Mac e a sua pasta.
- **Na VPS**: você digitou `ssh pje-vps` e entrou no servidor. O início da linha passa a
  mostrar `deploy@...`. Tudo o que você digitar agora roda no servidor, até digitar `exit`.

Cada bloco de comando abaixo vem com a marca **No Mac mini** ou **Na VPS**.

**Regra de ouro:** copie e cole **uma linha por vez**. Se o resultado for diferente do
que o guia diz, pare e peça ajuda com o texto, **sem números de processo, senhas,
chaves ou IP**.

## Ordem sugerida

| # | O que fazer | Onde | Tempo | Precisa? |
|---|---|---|---|---|
| 0 | Criar o apelido `pje-vps` no Mac | Mac mini | 10 min | Só se `ssh pje-vps` falhar |
| 1 | Conferir a versão no ar e o arquivo de senhas | VPS | 5 min | Sim |
| 2 | Rodar um lote pequeno e ler as durações | Navegador + VPS | 15 min | Sim |
| 3 | Instalar ou atualizar o alarme no Telegram | Mac mini + VPS | 40 min | Sim |
| 4 | Testar se o Chromium abre | VPS | 5 min | Antes de decidir o #84 |
| 5 | Decidir o #84 (playwright) | Com o Claude | 1 min | Depois do 4 |
| 6 | Limpezas e prazos | VPS / com o Claude | 10 min | Quando der |
| 7 | Teste do "job perdido no deploy" | VPS | 20 min | Opcional |

---

## Passo 0: criar o apelido `pje-vps` (Mac mini, uma vez só)

`pje-vps` é um atalho: sem ele, o Mac não sabe qual servidor, usuário e chave usar. Se
`ssh pje-vps` já conecta, pule para o Passo 1. O apelido mora só no seu `~/.ssh/config`
e **não fica no repositório** de propósito (o repositório é público e o IP é reservado).

1. **Descubra o IP da VPS.** No painel da Hostinger, abra a sua VPS: o painel mostra o
   "Endereço IP". (O GitHub não deixa ler de volta o secret `VPS_HOST`.) Trate o IP como
   reservado: não cole em chat nem no repositório.
2. **Veja se a chave de deploy está neste Mac** (**No Mac mini**):
   ```bash
   ls -l ~/.ssh/pje_deploy
   ```
   - **Existe:** rode `chmod 600 ~/.ssh/pje_deploy` e use `IdentityFile ~/.ssh/pje_deploy`
     no passo 3.
   - **Não existe** (ela ficou na máquina antiga, e a cópia no GitHub não pode ser lida de
     volta): crie uma chave nova só do Mac mini e autorize-a na VPS:
     ```bash
     ssh-keygen -t ed25519 -f ~/.ssh/pje_mac -C mac-mini
     cat ~/.ssh/pje_mac.pub
     ```
     Copie a linha impressa. Agora, **Na VPS**: no painel da Hostinger, abra o **terminal
     pelo navegador** da VPS (procure "Terminal" no painel da VPS), entre como `root` e
     rode, trocando o final pela linha que você copiou:
     ```bash
     echo 'ssh-ed25519 AAAA... mac-mini' >> /home/deploy/.ssh/authorized_keys
     ```
     No passo 3, use `IdentityFile ~/.ssh/pje_mac`. (Copiar a chave antiga de outra
     máquina, por meio seguro, também serve.)
3. **Crie a entrada no arquivo de configuração do ssh** (**No Mac mini**). O bloco final
   tem 5 linhas, e **só duas coisas mudam**: o IP e, talvez, o nome da chave. Exemplo
   preenchido (`203.0.113.10` é um IP de exemplo, **falso**):
   ```
   Host pje-vps
       HostName 203.0.113.10
       User deploy
       IdentityFile ~/.ssh/pje_deploy
       IdentitiesOnly yes
   ```

   | Linha | O que fazer |
   |---|---|
   | `Host pje-vps` | Deixe igual. É o apelido que você digita depois. |
   | `HostName ...` | **Troque pelo IP real** do item 1. Só números e pontos, sem `http://` e sem espaço. |
   | `User deploy` | Deixe igual. |
   | `IdentityFile ...` | Deixe `~/.ssh/pje_deploy` se o arquivo existe. Se você criou a chave nova, troque por `~/.ssh/pje_mac`. |
   | `IdentitiesOnly yes` | Deixe igual. |

   **Jeito mais fácil, sem editar arquivo.** Troque só o número entre aspas na primeira
   linha (e `pje_deploy` por `pje_mac`, se for o caso) e cole as duas linhas:
   ```bash
   IP="203.0.113.10"
   printf 'Host pje-vps\n    HostName %s\n    User deploy\n    IdentityFile ~/.ssh/pje_deploy\n    IdentitiesOnly yes\n' "$IP" >> ~/.ssh/config
   ```
   Se preferir editar à mão: `mkdir -p ~/.ssh && chmod 700 ~/.ssh` e `nano ~/.ssh/config`;
   cole o bloco no fim, salve com Ctrl+O e Enter e saia com Ctrl+X.

   Confira o resultado e proteja o arquivo:
   ```bash
   cat ~/.ssh/config
   chmod 600 ~/.ssh/config
   ```
   **Deve aparecer** o bloco `Host pje-vps` com o seu IP. Se você rodou o comando duas
   vezes, o bloco aparece repetido: apague a repetição com `nano ~/.ssh/config`.
4. **Primeira conexão** (**No Mac mini**). Ela aceita a identidade do servidor; o vigia do
   Passo 3 exige isso:
   ```bash
   ssh pje-vps
   ```
   Responda `yes` à pergunta da impressão digital. **Deve aparecer** o prompt da VPS
   (`deploy@...`). Saia com `exit`.
5. **Teste curto:**
   ```bash
   ssh pje-vps 'echo ok'
   ```
   **Deve aparecer:** `ok`.

*Se der errado:*

- `Permission denied (publickey)`: a chave não está autorizada na VPS (refaça o passo 2)
  ou o `IdentityFile` aponta para o arquivo errado.
- `Connection timed out`: IP errado, ou o firewall da Hostinger foi alterado ou
  desanexado (depois de uma mudança de região ele se desanexa). Peça ajuda.
- `Could not resolve hostname pje-vps` de novo: a entrada `Host pje-vps` não foi salva.
  Confira com `grep -A4 'Host pje-vps' ~/.ssh/config`.

## Passo 1: versão no ar e arquivo de senhas (VPS)

O último deploy mudou como o arquivo `.env` (que guarda as senhas) é gravado. Você só
confirma que ficou protegido e que o servidor está na versão certa.

1. **No Mac mini**, abra o Terminal e entre no servidor:
   ```bash
   ssh pje-vps
   ```
   A partir daqui você está **na VPS** (o início da linha mostra `deploy@...`) até o `exit`
   do item 5.
2. **Na VPS.** Veja quem pode ler o arquivo de senhas:
   ```bash
   stat -c '%a %U' /opt/pje-download/.env
   ```
   **Deve aparecer:** `600 deploy`. Quer dizer que só o dono do arquivo lê. Se aparecer
   outro número, como `644`, avise.
3. **Na VPS.** Veja se o servidor está na versão certa (uma linha de cada vez):
   ```bash
   curl -s localhost:8007/healthz | jq -r .build_sha
   ```
   ```bash
   curl -s localhost:8006/health | jq -r .build_sha
   ```
   **Deve aparecer**, nas duas: um código que **começa com `f23eeed`** (ou com o
   commit mais recente em produção, se já houve outro deploy).
4. **Na VPS.** Veja se os três serviços estão de pé:
   ```bash
   cd /opt/pje-download && docker compose ps
   ```
   **Deve aparecer:** `pje-redis`, `pje-dashboard` e `pje-worker` com `Up` e `(healthy)`.
5. Saia do servidor: `exit`. Você volta ao Mac mini.

*Se der errado:* "Could not resolve hostname" significa que o apelido `pje-vps` não
existe neste Mac. Faça o **Passo 0**, acima, e volte aqui.

## Passo 2: lote pequeno de teste (navegador + VPS)

Prova, com um caso real, duas melhorias: o sistema agora **avisa** linhas inválidas e
repetidas, e cada processo e cada lote registram **quanto tempo levaram**.

1. **No Mac mini.** Abra o túnel e deixe essa janela aberta. Ele liga o servidor ao seu
   navegador:
   ```bash
   ssh -L 8007:localhost:8007 pje-vps
   ```
2. **A chave de API.** No navegador, abra `http://localhost:8007`. A página abre sem
   chave, mas, se o campo **API Key** (no topo) estiver vazio, a tela mostra **OFFLINE** e
   "Serviço indisponível" (ou "Chave de API", depois da correção da tela). O campo diz
   "Opcional", mas em produção ele é **obrigatório**.
   Leia a chave numa **segunda** janela do Terminal (**No Mac mini, depois na VPS**):
   ```bash
   ssh pje-vps
   grep '^DASHBOARD_API_KEY=' /opt/pje-download/.env
   ```
   A chave é o que vem depois do `=`. Cole no campo **API Key**, clique fora do campo e
   recarregue a página com ⌘ + R. O selo deve mudar para **IDLE**. Essa chave é uma
   senha: não cole em chat nem em mensagem. Depois, digite `exit` nessa segunda janela.

   **"Sessão PJe: Sem sessão salva" é normal.** Essa linha é da sessão do navegador de
   reserva, que fica dormente em produção; os downloads usam o usuário e a senha do MNI.
   **Não clique em "Fazer Login" nem em "Verificar"**: eles tentam abrir um navegador no
   servidor, que não tem tela.
3. No campo de processos, cole **2 ou 3 números de processos reais do TJES que você já
   pode baixar** e acrescente uma linha de lixo (por exemplo `abc123`) e um dos números
   reais **repetido**.
4. Clique em baixar. **Deve acontecer:** um aviso de que 1 linha de formato inválido não
   foi enviada e continua na caixa; outro aviso de que 1 número repetido foi removido; na
   caixa de texto sobra **só** `abc123`; o lote começa com os processos válidos e únicos.
5. Quando o lote terminar, **Na VPS** (a segunda janela, depois de `ssh pje-vps`), liste
   as pastas dos lotes:
   ```bash
   cd /opt/pje-download
   docker compose exec -T dashboard ls /data/downloads
   ```
   Copie o nome da pasta mais recente e rode, trocando `<ID_DO_LOTE>` por esse nome:
   ```bash
   docker compose exec -T dashboard cat /data/downloads/<ID_DO_LOTE>/_report.json | jq '.timing, [.processos[].duracao_s]'
   ```
   **Deve aparecer:** um bloco com `wall_s`, `sum_s`, `p50_s`, `p95_s` e `max_s`, e abaixo
   uma lista de números: os segundos de cada processo.

Como ler: `sum_s` é o tempo de trabalho de verdade; `wall_s` inclui esperas. Com menos de
20 processos, `p95` é igual ao máximo.

**Privacidade:** não cole o `_report.json` inteiro em lugar nenhum: ele tem números de
processo. O bloco `.timing` e a lista de segundos são seguros.

## Passo 3: alarme no Telegram, com o Hermes (Mac mini + VPS)

Sem isso, **ninguém avisa** se o sistema cair. O vigia pergunta ao servidor a cada 5
minutos se está tudo bem e só manda mensagem quando algo vai mal, mais um resumo todo dia
às 8h. Detalhes técnicos: `docs/ops/hermes-watchdog.md`.

Pré-requisito: o `ssh pje-vps` precisa funcionar neste Mac (Passo 0).

**Onde rodar cada etapa** (todas no Terminal do Mac; as da VPS exigem entrar antes com
`ssh pje-vps` e sair com `exit`):

| Etapa | Onde |
|---|---|
| 1. Atualizar o repositório e criar a chave do vigia | **No Mac mini** |
| 2. Colocar a chave no `authorized_keys` | **Na VPS** (`ssh pje-vps`, editar, `exit`) |
| 3. Testar a chave | **No Mac mini** |
| 4. Copiar os dois scripts | **No Mac mini**, na pasta do repositório |
| 5. Agendar no Hermes | **No Mac mini** |
| 6. Manter o Mac acordado | **No Mac mini** |
| Teste de ponta a ponta | **Na VPS** |

**Primeiro descubra se já instalou.** No Mac mini: `hermes cron list`. Se aparecerem
`pje-watchdog` e `pje-watchdog-digest`, vá para "Se já estava instalado", mais abaixo.

### Instalação nova (seis etapas, nesta ordem)

1. **Mac mini.** Atualize o repositório e crie a chave só do vigia:
   ```bash
   cd /Users/felipemoulin/pje-download && git pull
   ssh-keygen -t ed25519 -f ~/.ssh/pje_watchdog_ed25519 -N '' -C hermes-watchdog
   cat ~/.ssh/pje_watchdog_ed25519.pub
   ```
   A última linha é a chave pública (começa com `ssh-ed25519 AAAA...`). Copie a linha inteira.

2. **Na VPS.** Autorize a chave, **limitada** a rodar só o vigia. Entre com `ssh pje-vps`
   (**No Mac mini**) e, já dentro (**Na VPS**), abra o arquivo:
   ```bash
   nano ~/.ssh/authorized_keys
   ```
   Vá ao fim e acrescente **uma linha**, trocando o final pela chave que você copiou:
   ```
   restrict,command="/usr/bin/python3 -I /opt/pje-download/tools/pje_watchdog.py" ssh-ed25519 AAAA... hermes-watchdog
   ```
   Salve com Ctrl+O e Enter, saia com Ctrl+X. **Não apague** as outras linhas. Essa chave
   só consegue rodar o vigia, e é por isso que ela é segura. Depois digite `exit` para
   voltar ao Mac mini.

3. **Mac mini.** Teste a chave. Se este Mac nunca entrou na VPS, rode antes `ssh pje-vps`
   e responda `yes`.
   ```bash
   ssh -i ~/.ssh/pje_watchdog_ed25519 deploy@pje-vps
   ```
   ```bash
   ssh -i ~/.ssh/pje_watchdog_ed25519 deploy@pje-vps digest
   ```
   **Deve aparecer:** no primeiro, **nada** (silêncio quer dizer que está tudo certo; ele
   só grava a linha de base); no segundo, um resumo curto com contagens.

4. **Mac mini.** Instale os dois scripts, dentro da pasta `/Users/felipemoulin/pje-download`:
   ```bash
   mkdir -p ~/.hermes/scripts
   cp ops/hermes/pje-watchdog.sh ~/.hermes/scripts/pje-watchdog.sh
   cp ops/hermes/pje-watchdog.sh ~/.hermes/scripts/pje-watchdog-digest.sh
   chmod +x ~/.hermes/scripts/pje-watchdog*.sh
   ```

5. **Mac mini.** Agende. Antes, veja a sintaxe da sua versão com
   `hermes cron create --help`, porque ela muda entre versões.
   ```bash
   hermes cron create "every 5m" --no-agent --script pje-watchdog.sh --deliver telegram --name pje-watchdog
   hermes cron create "0 8 * * *" --no-agent --script pje-watchdog-digest.sh --deliver telegram --name pje-watchdog-digest
   hermes cron list
   ```
   No chat do Telegram com o bot, mande `/sethome` uma vez, para definir onde as
   mensagens chegam.

6. **Mac mini.** Mantenha-o acordado. Se ele dormir, **os alertas somem em silêncio**:
   ```bash
   hermes gateway install
   sudo pmset -a sleep 0
   ```
   O resumo das 8h é o seu sinal de vida. Se um dia ele não chegar, o Hermes parou.

### Teste de ponta a ponta (recomendado, 10 min)

Faça com **nenhum lote em andamento**: você vai parar o worker de propósito. Na VPS:
```bash
cd /opt/pje-download && docker compose --profile worker stop worker
```
**Deve aparecer**, em 5 a 10 minutos, no Telegram: `[ALERTA] Contêiner pje-worker está
exited` e `[ALERTA] Worker não responde em :8006`. Depois religue:
```bash
docker compose --profile worker up -d worker
```
E espere a mensagem `[NORMALIZOU]`.

### Se já estava instalado (atualização, 5 min)

- **VPS.** Em `~/.ssh/authorized_keys`, na linha `hermes-watchdog`, troque `python3 /opt/...`
  por `python3 -I /opt/...`. É só acrescentar o `-I`.
- **Mac mini.** Rode `cd /Users/felipemoulin/pje-download && git pull` e repita **só a
  etapa 4** (copiar os dois scripts de novo). O script novo limita o tempo do `ssh` e nunca
  manda o erro cru dele ao chat.
- O script novo foi testado só no Linux. Rode uma vez `hermes cron run <id_do_job>` e veja
  se a execução termina sem erro.

### Para desligar o alarme

Apague a linha `hermes-watchdog` do `authorized_keys` da VPS e rode
`hermes cron remove <id>` para os dois jobs.

*Se der errado:* sem mensagem e sem erro, confira `hermes cron list` e se o Mac estava
acordado. Mensagem `WATCHDOG: falha ao consultar a VPS` quer dizer que o `ssh` não chegou
(chave errada, VPS fora do ar ou sem rede).

## Passo 4: o Chromium abre? (VPS)

Uma correção antiga do Dockerfile nunca foi provada na prática. É um teste de um comando,
e ele vem antes de decidir o #84.

**Na VPS:** entre antes com `ssh pje-vps` (**No Mac mini**) e saia com `exit` no fim.
```bash
cd /opt/pje-download
docker compose exec worker python -c "
import asyncio
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        print(b.version)
        await b.close()
asyncio.run(main())"
```
**Deve aparecer:** um número de versão, como `145.0.xxxx`. Se aparecer
"executable doesn't exist", guarde as últimas linhas para pedir ajuda.

## Passo 5: decidir o #84 (playwright 1.64.0)

Atualização do navegador automático. O navegador de reserva está dormente em produção,
então não há pressa. Se o passo 4 deu certo, peça "Merge #84" (atualiza a branch, roda a
suíte com Redis e mergeia; o deploy refaz a imagem, cerca de 3 minutos) ou diga "deixa o
#84".

## Passo 6: limpezas e prazos

- **Tailscale sem uso na VPS** (instalado em 24/10/2026 e deslogado). Se não for usar:
  `ssh pje-vps` e `sudo snap remove tailscale`.
- **Pasta `ops/monitoring/`** (painéis antigos do openclaw). Quando confirmar que o
  openclaw foi desligado, peça a remoção em um PR próprio.
- **Revisão do prompt gate, até 31/10/2026.** No Mac mini:
  `cd /Users/felipemoulin/pje-download && python3 tools/prompt_gate.py --report`.
  Decidir entre manter, estender (a última vez, até 14/11) ou remover a regra.
- **Trocar a chave SSH de deploy e a `DASHBOARD_API_KEY`.** Adiado desde julho; envolve
  secrets do GitHub e a VPS.
- **Logs de auditoria.** São apagados todo dia quando passam de 120 dias, e **não existe
  cópia em lugar nenhum**, porque o envio ao Postgres está desligado. Se isso importa, a
  decisão é ligar `AUDIT_SYNC_ENABLED`.

## Passo 7: teste do "job perdido no deploy" (opcional)

A prova final de que um deploy no meio de um lote não perde processos. Só faça com 20
minutos livres e um lote de teste de uns 5 processos em andamento. Os comandos exatos
estão em `TODO.md`, no item "Job perdido no deploy". Se não quiser, deixe: as duas
proteções já estão no ar.

## Se algo der errado

1. Não tente consertar apagando coisas.
2. Copie as últimas linhas do que apareceu, sem números de processo, senhas, chaves ou IP,
   e peça ajuda.
3. Em dúvida sobre por onde começar, faça os passos 1 e 3: são os que mais protegem.
