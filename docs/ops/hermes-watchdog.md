# Watchdog da VPS com Hermes

Avisa no Telegram quando algo importante falha na VPS de produção do TJES. As
checagens vivem neste repositório (`tools/pje_watchdog.py`); o Hermes, no Mac
mini, só agenda e entrega a mensagem. Substitui o stack Prometheus/Grafana do
openclaw, que, pelo que foi medido, não tinha como coletar da VPS atual (ver
"Por que não Prometheus").

```
Mac mini (Hermes, gateway Telegram)
   └─ cron sem LLM, a cada 5 min ── ssh (chave restrita) ──▶ VPS
                                                              └─ tools/pje_watchdog.py   (só lê)
   ◀──────────── saída do script ── Telegram ◀── Hermes
```

Contrato com o Hermes (cron sem LLM, `--no-agent`): saída vazia não envia nada;
saída com texto vai como está; código de saída diferente de zero gera um alerta
de erro. Se o `ssh` não conectar, o wrapper sai com 1: **VPS fora do ar vira
alerta sem checagem extra**.

## O que é checado

| Checagem | Alerta quando |
|---|---|
| Worker `:8006/health` | não responde; Redis inacessível (circuit breaker); sessão expirada; captcha; disco baixo; MNI do TJES fora |
| Dashboard `:8007/healthz` | não responde; Redis inacessível; lote aguardando retomada |
| Contêineres | `pje-redis`, `pje-dashboard`, `pje-worker` parados, inexistentes, `unhealthy` ou reiniciando (crash-loop) |
| Filas do Redis | `jobs` não vazia sem mudar por mais de 15 min; `processing` não vazia **e** `jobs` sem mudar por mais de 15 min (o tamanho de `processing` sozinho não conta: cada item é um job diferente e um lote longo o mantém em 1); itens novos no dead-letter (exceto `invalid_json`, que o teste de fila do deploy injeta a cada deploy) |
| O próprio watchdog | execução anterior presa (lock ocupado há mais de 10 min sem nenhuma execução bem-sucedida): `[ALERTA] watchdog travado`, saída 1 |
| Lotes | lote novo terminando `failed` (alerta) ou `partial` (aviso) |
| Disco da VPS | menos de 5 GB livres |

Regras de envio: um problema é anunciado depois de **2 execuções seguidas** (3
para `unhealthy`), então um reinício de deploy não gera alarme falso; eventos
(lote com falha, dead-letter novo) são anunciados na hora; um problema que
continua gera `[LEMBRETE]` a cada 6 h; quando some, `[NORMALIZOU]`.

Antes da correção de 24/10/2026 o worker respondia `503` com `status:
processing` enquanto executava um job; o watchdog nunca tratou `processing`
como falha, então segue correto com qualquer versão do worker.

Ajustes por variável de ambiente (opcionais, na VPS): `WATCHDOG_STUCK_MINUTES`,
`WATCHDOG_REMIND_HOURS`, `WATCHDOG_STATE`, `WATCHDOG_DEADLINE_SECS` (prazo total
do processo, padrão 90).

## Prazo, lock e falha do próprio watchdog

- **Prazo total de 90 s por execução** (`signal.setitimer`): estourou, imprime
  `WATCHDOG: tempo esgotado` e sai com 1. Cada leitura HTTP também tem prazo
  total (2x o timeout de socket) e teto de 1 MB, para um endpoint que manda um
  byte de cada vez não prender o processo e o lock.
- **Lock ocupado** (outra execução em andamento): sai em silêncio com 0 na
  primeira vez. O instante da última execução bem-sucedida (ou da primeira vez
  que o lock foi visto ocupado) fica em `~/.cache/pje-watchdog/state.json.ok`;
  passados 10 min, imprime `[ALERTA] watchdog travado` e sai com 1.
- **Estado não gravável** (disco cheio): o alerta já calculado é impresso com
  `[AVISO] não consegui gravar o estado`, e o `.tmp` é removido. Enquanto durar,
  a mensagem se repete a cada execução e eventos já anunciados podem repetir.
- O modo `digest` usa o mesmo lock: se uma checagem está rodando, ele sai vazio
  com 0 (aquele resumo diário se perde; aceito).

## Privacidade

A saída vai para o Telegram. Nunca contém número de processo, payload de job,
nome de parte nem credencial: mensagens de erro de lote passam por `scrub`
(o texto é normalizado antes: NFKC, sem caracteres invisíveis, todo traço Unicode
vira `-`; número CNJ e qualquer sequência de 6 ou mais dígitos, mesmo separados
por espaço, ponto, traço ou barra, viram marcadores; `senha=`/`token=`/`key=`/
`cookie=`, `Bearer ...` e URLs com `usuario:senha@` também) e o dead-letter é
resumido só pelo motivo. O wrapper nunca repassa o stderr do `ssh` ao chat (traz
IP da VPS e caminhos); em falha, a mensagem é fixa, com o código de saída, mais
as linhas `WATCHDOG: ...` que o próprio watchdog imprimiu. O resumo diário traz
apenas contagens. Não coloque um modelo (LLM) para ler relatórios de lote: eles
têm números de processo.

## Instalação (uma vez)

Pré-requisito: o código chega à VPS no próximo deploy depois do merge
(`/opt/pje-download/tools/pje_watchdog.py`). A VPS precisa de `python3`
(Ubuntu 24.04 já tem); o watchdog usa só a biblioteca padrão.

**1. Chave no Mac mini.** Sem senha de chave, porque o Hermes roda sem
interação. O risco é contido pelo comando forçado do passo 2.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/pje_watchdog_ed25519 -N '' -C hermes-watchdog
cat ~/.ssh/pje_watchdog_ed25519.pub
```

**2. Autorizar a chave na VPS**, no usuário `deploy`, com comando forçado.
Acrescente uma linha ao `~/.ssh/authorized_keys` (uma só linha; troque o final
pelo conteúdo do `.pub`):

```
restrict,command="/usr/bin/python3 -I /opt/pje-download/tools/pje_watchdog.py" ssh-ed25519 AAAA... hermes-watchdog
```

`restrict` desliga terminal, port forwarding, agent forwarding e X11. Com o
`command=`, **essa chave só consegue executar o watchdog**, mesmo que o Mac mini
ou o Hermes sejam comprometidos ou enganados. Não reutilize a chave de deploy.
Confira o caminho do Python com `command -v python3`. O `-I` (modo isolado)
ignora `PYTHON*` e o diretório do script no `sys.path`. Quem já instalou a linha
antiga deve acrescentar o `-I`.

**3. Testar a chave** (no Mac mini). Se o host ainda não estiver no
`known_hosts`, conecte uma vez com `ssh pje-vps` antes: o wrapper usa
`StrictHostKeyChecking=yes`.

```bash
ssh -i ~/.ssh/pje_watchdog_ed25519 deploy@pje-vps            # 1ª vez: vazio (linha de base)
ssh -i ~/.ssh/pje_watchdog_ed25519 deploy@pje-vps digest     # resumo diário
ssh -i ~/.ssh/pje_watchdog_ed25519 deploy@pje-vps 'id'       # não imprime o uid: roda o watchdog (normalmente vazio)
```

**4. Wrappers do Hermes** (no Mac mini). O modo vem do nome do arquivo:

```bash
cp ops/hermes/pje-watchdog.sh ~/.hermes/scripts/pje-watchdog.sh
cp ops/hermes/pje-watchdog.sh ~/.hermes/scripts/pje-watchdog-digest.sh
chmod +x ~/.hermes/scripts/pje-watchdog*.sh
```

**5. Agendar.** Confira a sintaxe exata na versão instalada com
`hermes cron create --help` (a documentação varia entre versões).

```bash
hermes cron create "every 5m" --no-agent --script pje-watchdog.sh \
  --deliver telegram --name pje-watchdog
hermes cron create "0 8 * * *" --no-agent --script pje-watchdog-digest.sh \
  --deliver telegram --name pje-watchdog-digest
hermes cron list
hermes cron run <job_id>        # teste manual
```

`--deliver telegram` usa o canal inicial do gateway (`/sethome` no chat).

**6. Manter o Mac mini acordado.** O agendador mora no gateway do Hermes: se o
Mac dormir ou o gateway parar, **os alertas somem em silêncio**. Instale o
gateway como serviço (`hermes gateway install`) e desligue a suspensão do
sistema (`sudo pmset -a sleep 0`). O resumo das 8h é o sinal de vida: se ele
deixar de chegar, o Hermes parou.

## Teste de ponta a ponta

Numa janela sem lote rodando, na VPS:

```bash
cd /opt/pje-download
docker compose --profile worker stop worker
```

Em 5 a 10 minutos deve chegar `[ALERTA] Contêiner pje-worker está exited` e
`[ALERTA] Worker não responde em :8006`. Suba de novo
(`docker compose --profile worker up -d worker`) e confirme o `[NORMALIZOU]`.
Para a queda da VPS, basta o `ssh` falhar: o wrapper alerta sozinho.

## Durações dos lotes

Cada lote grava, no volume de dados, `/data/downloads/<batch_id>/_report.json`
com `duracao_s` por processo (medido pelo worker) e um bloco `timing`. Na VPS:

```bash
cd /opt/pje-download
docker compose exec -T dashboard ls /data/downloads            # ids dos lotes
docker compose exec -T dashboard cat /data/downloads/<batch_id>/_report.json \
  | jq '.timing, [.processos[].duracao_s]'
```

`timing` traz `wall_s` (relógio do lote), `sum_s` (soma do trabalho), `p50_s`,
`p95_s`, `max_s`, `with_duration` e `seconds_per_processo`. Leia assim:
`wall_s` inclui a espera ociosa de um timeout e o tempo parado de um lote
retomado, então `seconds_per_processo` **não** é tempo de trabalho: use `sum_s`.
Com menos de 20 processos, `p95` é igual ao máximo. Um job reentregue depois de
um deploy mede só a última tentativa. Lotes anteriores a esta versão não têm
`timing` nem `duracao_s`.

## Revogar

Remova a linha `hermes-watchdog` do `authorized_keys` da VPS e rode
`hermes cron remove <job_id>` para os dois jobs.

## Limites

- Sem séries históricas nem painéis: é alerta, não observabilidade. A duração
  vem do `_report.json` de cada lote (ver "Durações dos lotes" abaixo).
- Quem observa mora no Mac mini; se ele ficar offline, ninguém avisa. O resumo
  diário é a única defesa e depende de você notar a ausência.
- O wrapper limita o `ssh` a 120 s (`PJE_WATCHDOG_TIMEOUT`, alarm do perl, sem
  depender do `timeout` do GNU); estourou, sai com 1 (código 142).
- O watchdog usa o Docker (`docker inspect --type container`, `docker exec`), então roda como um
  usuário do grupo `docker`. A chave restrita limita o que isso permite.
- `invalid_json` no dead-letter é ignorado de propósito; um job malformado de
  verdade com esse motivo não gera aviso.

## Por que não Prometheus

Medido em 24/10/2026: as portas 8006 e 8007 só escutam em `127.0.0.1` na VPS e
o Tailscale não estava instalado nela, então o Prometheus do openclaw, que
raspa o IP do Tailscale, não tinha como conectar. O dono relatou, na mesma
data, que não recebe mensagens do `@kaiOpsBot`. O lado do openclaw não foi
inspecionado. O diretório
`ops/monitoring/` ficou como histórico e não está em uso.
