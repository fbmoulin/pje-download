# Spec — Job em execução não se perde quando o worker para (deploy, OOM, crash)

**Status:** rascunho, 2026-10-16. Nada implementado. Falta o premortem e a aprovação no
**USER VALIDATION GATE** (abaixo).
**Origem:** `TODO.md` §"Aberto agora (revisão de 2026-10-10)", item "Job perdido no deploy";
`CLAUDE.md` §Test Patterns ("Still open: the job the worker is running when the container stops
is lost").
**Escopo:** caminho TJES. A fila `kratos:pje:jobs` é única e independe de tribunal.
**Alvo:** `worker.py` (`consume_queue`, `main`), `dashboard_api.py` (`_purge_pending_jobs`),
`docker-compose.yml` (serviço `worker`), `config.py`, testes novos em
`tests/test_worker_job_redelivery.py` e `tests/test_compose_worker_grace.py`, `CLAUDE.md`.

## Goal

Quando o container do worker para no meio de um job, o processo que estava sendo baixado
**termina ou é refeito**. Hoje ele some da fila e só reaparece como falha 6 minutos depois.

## O que acontece hoje (medido no código em `001c08b`)

| Passo | Onde | Efeito |
|---|---|---|
| Worker tira o job com `BLPOP kratos:pje:jobs` | `worker.py:2062` | O job sai do Redis antes de começar. Não há ack nem lista de processamento. |
| Deploy roda `docker compose --profile worker up -d --build dashboard worker` | `deploy.yml:152` | O compose manda SIGTERM ao worker. Sem `stop_grace_period`, o Docker espera **10 s** e manda SIGKILL. |
| SIGTERM chega ao Python | `worker.py:2368-2373`; `Dockerfile:95` usa a forma exec, então o Python é o PID 1 e recebe o sinal | O handler só marca `shutdown_event`. O loop consulta o evento **entre** jobs, então o job atual continua até o SIGKILL. |
| SIGKILL | — | O job em execução é perdido. Os arquivos parciais ficam em `/data/downloads`. |
| A dashboard também é recriada e retoma o lote | `dashboard_api.py:441` (`resume_active_batch`, `enqueue_jobs=False`) | Os resultados casam por `numeroProcesso` (`dashboard_api.py:721-726`). O processo perdido fica em `pending` para sempre. |
| Sem resultado por `RESULT_WAIT_TIMEOUT_SECS` (360 s) | `dashboard_api.py:779-787` | O processo é marcado `failed` com "Worker timeout" e o lote fecha. |

**Impacto real:** em cada deploy no meio de um lote, **um processo** sai como falha espúria. O
lote termina pelo menos 6 minutos depois do necessário, e alguém precisa reenviar o processo.
Não há perda silenciosa: o processo aparece como `failed`. O mesmo vale para OOM (o worker tem
`mem_limit: 512m` e roda Chromium) e para qualquer crash que escape do `except Exception` do loop.

**Dado que falta:** não sabemos quanto um job dura em produção. Não há métrica de duração de
job (`metrics.py` não tem nenhuma). Isso decide o D2.

## Desenho proposto

Duas partes independentes. A parte A é barata e cobre o caso comum (deploy). A parte B cobre o
que a A não cobre: job mais longo que o prazo de graça, OOM e crash.

### Parte A — prazo de graça no stop

- `stop_grace_period: <D2>` no serviço `worker` do `docker-compose.yml`.
- O comportamento do SIGTERM continua o mesmo: termina o job atual, publica o resultado e sai. O
  resultado vai para a reply queue, que a dashboard recriada drena ao retomar (a reply queue
  sobrevive ao cancelamento, ver `CLAUDE.md` §"A cancelled batch keeps its reply queue").
- Custo: cada deploy pode demorar até D2 a mais no passo de restart.

### Parte B — lista de processamento com reentrega no boot

- O consumo passa de `BLPOP` para `BLMOVE kratos:pje:jobs kratos:pje:jobs:processing LEFT RIGHT`
  (Redis ≥ 6.2; produção usa `redis:7.4-alpine`, e o redis-py 8.1 tem `blmove`).
- Depois de publicar o resultado, ou de mandar para a dead-letter, ou no `except Exception` do
  loop: `LREM kratos:pje:jobs:processing 1 <job_json>` com os **bytes exatos** retirados.
- No boot, antes de `consume_queue`: cada item que ficou em `processing` volta para a **cabeça**
  de `kratos:pje:jobs` (`LMOVE processing jobs RIGHT LEFT`) e conta uma tentativa em
  `HINCRBY kratos:pje:jobs:attempts <jobId> 1`.
- Com mais de `JOB_MAX_REDELIVERIES` (D3) tentativas, o job vai para a dead-letter com motivo
  `redelivery_exhausted`, e o worker publica um resultado `failed` na `replyQueue` do job, para
  a dashboard não esperar 360 s. Isso impede que um job que derruba o worker (OOM) entre em loop.
- A dashboard, ao desistir de um lote (`_purge_pending_jobs`), faz o `LREM` também em
  `kratos:pje:jobs:processing`, para que um job de lote abandonado não seja reentregue no próximo
  boot.
- **Premissa: um único worker.** O `container_name: pje-worker` é fixo. Com dois workers, a
  reentrega no boot de um roubaria o job em andamento do outro. A premissa fica documentada e
  vira teste (se surgir um segundo worker, a lista passa a ser por worker).

### Reprocessar é seguro?

Sim, com custo. `_save_document` grava em nome determinístico (`{nome}_{doc.id}{ext}`), então
refazer sobrescreve o mesmo arquivo em vez de duplicar. O dedupe por checksum vale só dentro de
uma execução (`seen_checksums` é local, `mni_client.py:700`), então o reprocessamento baixa tudo
de novo e grava de novo `document_saved` no audit. Isso é correto para a CNJ 615/2025: houve um
segundo acesso, e ele fica registrado.

## Decisões para o Felipe

| # | Pergunta | Recomendação |
|---|---|---|
| D1 | Só a parte A, ou A + B? | **A + B.** A sozinha não cobre OOM, crash, nem job mais longo que o prazo. Se o D2 mostrar que quase todo job cabe no prazo, dá para entregar a A primeiro, num PR próprio. |
| D2 | Qual `stop_grace_period`? | Medir antes de escolher: duração dos jobs nos logs da VPS (intervalo entre `pje.queue.job_received` e o resultado publicado). Valor provisório: **120 s**. |
| D3 | `JOB_MAX_REDELIVERIES` | **2.** O deploy reentrega uma vez. A segunda reentrega já indica um job que derruba o worker. |
| D4 | Redis com `maxmemory-policy allkeys-lru` (`docker-compose.yml`) pode descartar `kratos:pje:jobs` e a lista de processamento sob pressão de memória. Tratar aqui? | **Não neste PR.** É um risco anterior a este trabalho e independente dele. Fica registrado como item separado no `TODO.md`. |

## Non-goals

| Fora de escopo | Motivo |
|---|---|
| Vários workers em paralelo | A produção tem um. A premissa vira teste. |
| Retomar o job do ponto em que parou | Reprocessar é idempotente no disco. Retomar exigiria estado por documento. |
| `_purge_pending_jobs` de um lote **retomado** | Hoje ele não casa os payloads antigos porque o resume gera ids novos (`CLAUDE.md` §Test Patterns). Corrigir isso é casar por `batchId` + `numeroProcesso` em vez de bytes, e fica como follow-up. |
| Política de memória do Redis | D4. |
| Jobs vindos do n8n (`kratos:pje:results` sem sufixo) | Usam a mesma fila e ganham a reentrega de graça, mas não têm a espera de 360 s da dashboard. Nada muda para eles além disso. |

## Plano (TDD, commits frequentes)

Cada task começa com o teste visto **falhando** e termina com `pytest tests/ -q` verde com Redis
vivo (920 + novos), `ruff check .` e `ruff format --check .` (ruff 0.14.14). Commits frequentes:
um commit por task.

### Task 1 — Medir a duração dos jobs (D2)

No VPS, extrair dos logs do worker (`docker compose logs worker --since 720h`) o intervalo entre
`pje.queue.job_received` e o evento terminal do mesmo job (`pje.download.mni_success`,
`pje.download.mni_partial`, `pje.download.api_success`, `pje.download.browser_success` ou
`pje.download.failed`), e registrar p50, p95 e máximo nesta spec. A publicação bem-sucedida do
resultado não gera log, então o evento terminal do download é o melhor marcador que existe. O
`docker logs` só guarda o que o driver de log reteve; se a janela for curta, registrar quantos
jobs entraram na amostra. Sem código. Quem roda é o Felipe, porque precisa de SSH no `pje-vps`.

### Task 2 — `stop_grace_period` (parte A)

Teste estático em `tests/test_compose_worker_grace.py`: o serviço `worker` declara
`stop_grace_period` maior ou igual ao valor escolhido no D2. Mudança de uma linha no
`docker-compose.yml`. Conferir que o `deploy.yml` não tem timeout menor que o novo prazo no
passo de restart.

### Task 3 — Consumo com `BLMOVE` e `LREM` no fim do job

Testes contra Redis real, no padrão de `tests/test_result_queue_ttl.py`:
- (a) durante `download_process`, o job está em `processing` e não está em `jobs`;
- (b) depois do resultado publicado, `processing` fica vazia;
- (c) idem depois de dead-letter (JSON inválido, campos faltando) e do `except Exception`;
- (d) um `CancelledError` no meio do job **deixa** o job em `processing`.

### Task 4 — Reentrega no boot com limite de tentativas

Testes:
- (a) um item deixado em `processing` volta para a cabeça de `jobs` e é o próximo a ser consumido;
- (b) a terceira reentrega (com D3 = 2) vai para a dead-letter com `redelivery_exhausted`, e a
  reply queue recebe um resultado `failed` com o `numeroProcesso` do job;
- (c) a reentrega não altera os bytes do payload (o `LREM` posterior precisa casar).

### Task 5 — Purge da dashboard também na lista de processamento

Teste em `tests/test_batch_timeouts_and_redis_blips.py` ou num arquivo novo: depois do idle
timeout, o payload do processo pendente não está em `jobs` nem em `processing`. Um erro de Redis
no segundo `LREM` é logado e ignorado, como no primeiro.

### Task 6 — Docs e verificação em produção

Atualizar `CLAUDE.md` (§Test Patterns: tirar o "Still open"; documentar a premissa de um único
worker e as chaves novas) e `TODO.md`. Depois do deploy: enfileirar um processo de teste
conhecido, rodar `docker compose restart worker` no meio do job e conferir que o resultado chega
ao lote, sem "Worker timeout".

## Riscos já vistos (insumo para o premortem)

1. **`LREM` por valor depende dos bytes exatos.** Se algum caminho reserializar o job, o item
   fica preso em `processing` e é reentregue em todo boot até a dead-letter. Mitigação: guardar
   o `job_json` cru retirado pelo `BLMOVE`; teste 4(c).
2. **Reentrega de job cujo resultado já foi publicado.** O worker morre entre o `RPUSH` do
   resultado e o `LREM`. O job é refeito e publica um segundo resultado. A dashboard ignora
   resultado de processo fora de `pending` (`dashboard_api.py:726`), então o efeito é só
   retrabalho.
3. **O prazo de graça atrasa todo deploy.** Um job travado (SOAP sem `operation_timeout`
   efetivo, `asyncio.wait_for` que não mata a thread) segura o stop até o fim do prazo, e aí vem
   o SIGKILL. A parte B cobre esse caso.
4. **Health durante o prazo de graça.** O worker continua respondendo `/health` enquanto termina
   o job, e o compose já está subindo o container novo. Com `container_name` fixo, o compose
   espera o antigo parar antes de criar o novo, então não há dois consumidores ao mesmo tempo.
   Conferir no teste da Task 6.
5. **Chave `attempts` sem TTL.** Pôr `EXPIRE` igual a `REDIS_RESULT_QUEUE_TTL_SECS` e apagar o
   campo quando o job termina.

## USER VALIDATION GATE

Antes de qualquer código:
1. O Felipe responde D1–D4.
2. Rodar o premortem desta spec (catálogo de 10 categorias) e incorporar os achados.
3. Task 1 medida.

Depois disso, o plano detalhado sai pela skill writing-plans, a execução por
subagent-driven-development (uma task por subagente, revisão entre elas) e a revisão do plano
pela plan-quality-gate.

## References

- `worker.py:2047-2160` (`consume_queue`), `worker.py:2348-2380` (`main`, sinais)
- `dashboard_api.py:441-455` (`resume_active_batch`), `:598-622` (`_purge_pending_jobs`),
  `:715-790` (`_poll_results_loop`)
- `docker-compose.yml` (serviços `redis` e `worker`), `Dockerfile:95`, `.github/workflows/deploy.yml:152`
- `mni_client.py:700, 880-920` (nome determinístico, dedupe por execução)
- Redis: `BLMOVE` (≥ 6.2), padrão "reliable queue" da documentação de `LMOVE`
- Docker Compose: `stop_grace_period` (padrão 10 s)
