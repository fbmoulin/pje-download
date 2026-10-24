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
| Filas do Redis | `jobs` ou `processing` sem mudar por mais de 15 min; itens novos no dead-letter (exceto `invalid_json`, que o teste de fila do deploy injeta a cada deploy) |
| Lotes | lote novo terminando `failed` (alerta) ou `partial` (aviso) |
| Disco da VPS | menos de 5 GB livres |

Regras de envio: um problema é anunciado depois de **2 execuções seguidas** (3
para `unhealthy`), então um reinício de deploy não gera alarme falso; eventos
(lote com falha, dead-letter novo) são anunciados na hora; um problema que
continua gera `[LEMBRETE]` a cada 6 h; quando some, `[NORMALIZOU]`.

O worker responde `503` com `status: processing` enquanto executa um job. O
watchdog não trata isso como falha (ver o achado no `TODO.md`).

Ajustes por variável de ambiente (opcionais, na VPS): `WATCHDOG_STUCK_MINUTES`,
`WATCHDOG_REMIND_HOURS`, `WATCHDOG_STATE`.

## Privacidade

A saída vai para o Telegram. Nunca contém número de processo, payload de job,
nome de parte nem credencial: mensagens de erro de lote passam por `scrub`
(número CNJ, formato de CPF e qualquer sequência de 6 ou mais dígitos viram
marcadores) e o dead-letter é resumido só pelo motivo. O resumo diário traz
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
restrict,command="/usr/bin/python3 /opt/pje-download/tools/pje_watchdog.py" ssh-ed25519 AAAA... hermes-watchdog
```

`restrict` desliga terminal, port forwarding, agent forwarding e X11. Com o
`command=`, **essa chave só consegue executar o watchdog**, mesmo que o Mac mini
ou o Hermes sejam comprometidos ou enganados. Não reutilize a chave de deploy.
Confira o caminho do Python com `command -v python3`.

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

## Revogar

Remova a linha `hermes-watchdog` do `authorized_keys` da VPS e rode
`hermes cron remove <job_id>` para os dois jobs.

## Limites

- Sem séries históricas nem painéis: é alerta, não observabilidade. Para
  durações de job e de lote, o `_report.json` de cada lote tem `duracao_s` por
  processo.
- Quem observa mora no Mac mini; se ele ficar offline, ninguém avisa. O resumo
  diário é a única defesa e depende de você notar a ausência.
- O watchdog usa o Docker (`docker inspect`, `docker exec`), então roda como um
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
