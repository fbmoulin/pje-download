# PJe Download

[![CI](https://github.com/fbmoulin/pje-download/actions/workflows/ci.yml/badge.svg)](https://github.com/fbmoulin/pje-download/actions/workflows/ci.yml)

Automacao de download de documentos processuais do PJe (Processo Judicial Eletronico) via MNI SOAP, API REST e browser automation.

O caminho principal de execucao agora e desacoplado:
- a dashboard publica um job Redis por processo
- o worker executa os downloads e responde por fila especifica do batch
- a dashboard agrega resultados e persiste `_progress.json` e `_report.json`

## Arquitetura

```
                     +------------------+
                     |  dashboard.html  |  Frontend (CSS + JS)
                     +--------+---------+
                              |
                     +--------v---------+
                     |  dashboard_api   |  aiohttp control plane (:8007)
                     +--------+---------+
                              |
                     +--------v---------+
                     |      Redis       |
                     | jobs + reply q   |
                     +--------+---------+
                              |
                     +--------v---------+
                     |     worker.py    |  execution plane
                     | MNI > API > Browser
                     +--------+---------+
                              |
         +--------------------+--------------------+
         |                    |                    |
 +-------v--------+   +-------v--------+   +-------v--------+
 |  mni_client.py |   | pje_session.py |   |gdrive_downloader|
 |  SOAP / WSDL   |   | API + browser  |   | processos antigos|
 +----------------+   +----------------+   +----------------+

CLI offline:
  batch_downloader.py -> mesmos integradores, sem passar pela dashboard
```

## Componentes

| Arquivo | Linhas | Funcao |
|---------|--------|--------|
| `worker.py` | ~1861 | Worker PJe com 3 estrategias em cascata (MNI > API > browser), reply queues por batch, downloads paralelos, dead-letter, circuit breaker no blpop, health detalhado |
| `dashboard_api.py` | ~1494 | Control plane aiohttp — valida batches, publica jobs Redis com retry, agrega resultados, recupera batch ativo, `api_key` em todo `/api/*`, spawn do audit syncer, expõe status/readiness/metrics |
| `batch_downloader.py` | ~914 | Download em lote via CLI com progresso atomico, retomada e relatorio |
| `mni_client.py` | ~876 | Cliente SOAP para MNI — download em 2 fases com dedup por checksum |
| `gdrive_downloader.py` | ~696 | Download de pastas Google Drive (processos antigos escaneados) + `extract_gdrive_link_from_pje` |
| `audit_sync.py` | ~474 | Background syncer: tails `/data/audit/*.jsonl` → Railway Postgres (CNJ 615/2025 Phase 2), partial-line invariant, cursor atomico com fsync, ON CONFLICT dedupe, TLS sslmode-aware |
| `pje_session.py` | ~440 | Login interativo PJe (Playwright), persistencia de sessao Keycloak, API REST + browser fallback |
| `metrics.py` | ~216 | Registry Prometheus dedicado — MNI, GDrive, worker, control plane, audit sync (6 series) |
| `config.py` | ~129 | Configuracao centralizada — todas as variaveis env-configuraveis (incl. audit sync) |
| `audit.py` | ~100 | CNJ 615/2025 audit trail append-only (JSON-L) + `rotate_logs` com cleanup de sidecars `.cursor` |
| `dashboard.html` | ~193 | Frontend HTML com Google Fonts, data-animate attrs e card de sessao PJe |
| `static/css/style.css` | ~685 | Design system — glassmorphism, Oswald KPIs, dot-grid bg, staggered animations |
| `static/js/app.js` | ~618 | Dashboard — adaptive polling, pipeline renderer (SVG), toasts, file upload, sessao PJe |
| `migrations/` | 2 files | Schema SQL idempotente para `audit_entries` (Railway Postgres) |

## Estrategias de Download

**Em ordem de preferencia:**

1. **MNI SOAP** (sem browser, mais rapido)
   - Fase 1: `consultarProcesso` retorna metadados dos documentos
   - Fase 2: `consultarProcesso` com IDs especificos retorna conteudo binario em batches
   - Timeout configuravel via `MNI_TIMEOUT` (padrao 60s)
   - Deduplicacao automatica por SHA-256

2. **API REST** (via browser autenticado)
   - Usa sessao Playwright para chamadas REST ao PJe
   - Endpoint: `/api/processos/{id}/documentos`

3. **Browser Automation** (Playwright, fallback)
   - 3a: Botao "full download" nos autos digitais (baixa tudo de uma vez)
   - 3b: Download paralelo via paginas concorrentes (configuravel via `CONCURRENT_DOWNLOADS`)

4. **Google Drive** (processos antigos pre-PJe)
   - Detecta processos antigos (numero nao comeca com "5")
   - Baixa pasta do Google Drive com docs escaneados (gdown > requests > Playwright)

## Seguranca e Resiliencia

| Feature | Descricao |
|---------|-----------|
| Path traversal prevention | Sanitizacao + `is_relative_to()` guard |
| CNJ format validation | Regex `NNNNNNN-DD.YYYY.J.TR.OOOO` na API |
| CORS localhost-only | Dashboard restringe `Access-Control-Allow-Origin` a origens localhost (whitelist `_ALLOWED_ORIGINS`) |
| Rate limiting | 10 POST/60s por IP (sliding window + expurgo de IPs inativos apos 5min) |
| MNI credentials fail-fast | Valida `MNI_USERNAME`/`MNI_PASSWORD` antes de qualquer chamada SOAP |
| Thread-safe SOAP client | `_get_client()` usa double-checked locking (`threading.Lock`) para init concorrente |
| Redis resilience | Retry com backoff em `ConnectionError`/`TimeoutError` |
| Job schema validation | Requer `jobId` + `numeroProcesso` antes de processar |
| SOAP timeout | `asyncio.wait_for` previne hangs infinitos |
| Atomic writes | Progress file via tmp+rename (previne corrupcao) |
| Session file lock | Advisory lock (`fcntl`) para multi-instancia |
| Document dedup | SHA-256 checksum, skip duplicatas no MNI client |
| Filename collision | Counter suffix automatico para nomes duplicados |
| Browser cleanup | Fecha page/context/browser na invalidacao de sessao |
| Deep health checks | `/health` verifica MNI, Redis e espaco em disco |
| Dashboard readiness | `/healthz` valida Redis e se ha batch recuperado pendente de retomada |
| Adaptive polling | Frontend backoff 1.5s-15s com reset em sucesso |
| Partial failure recovery | Dashboard preserva progresso parcial em erros |
| Active batch recovery | `_active_batch.json` + `_progress.json` permitem retomar agregacao apos restart |
| Progress file resilience | `BatchProgress.load()` ignora arquivos `_progress.json` corrompidos e reinicia limpo |

## Setup

```bash
# Dependencias
pip install -r requirements.txt
playwright install chromium

# Variaveis de ambiente (minimo)
export MNI_USERNAME="12345678900"   # CPF sem pontos
export MNI_PASSWORD="senha"
export MNI_TRIBUNAL="TJES"         # TJES, TJES_2G, TJBA, TJBA_2G, TJCE, TRT17
```

## Configuracao

Todas as variaveis sao configuradas via ambiente (centralizadas em `config.py`):

| Variavel | Padrao | Descricao |
|----------|--------|-----------|
| `PJE_BASE_URL` | `https://pje.tjes.jus.br/pje` | URL base do PJe |
| `MNI_USERNAME` | *(vazio)* | CPF sem pontos para autenticacao MNI |
| `MNI_PASSWORD` | *(vazio)* | Senha MNI |
| `MNI_TRIBUNAL` | `TJES` | Codigo do tribunal |
| `MNI_TIMEOUT` | `60` | Timeout em segundos para chamadas SOAP |
| `MNI_BATCH_SIZE` | `5` | Documentos por chamada SOAP na fase 2 |
| `SESSION_TIMEOUT_MINUTES` | `60` | Timeout da sessao Playwright |
| `MAX_DOCS_PER_SESSION` | `50` | Limite de documentos por sessao |
| `DOWNLOAD_DELAY_SECS` | `1.5` | Pausa entre downloads sequenciais |
| `CONCURRENT_DOWNLOADS` | `3` | Downloads paralelos via browser |
| `DOWNLOAD_BASE_DIR` | `/data/downloads` | Diretorio de saida |
| `REDIS_URL` | `redis://localhost:6379` | URL do Redis |
| `HEALTH_PORT` | `8006` | Porta do health check do worker |
| `DASHBOARD_PORT` | `8007` | Porta da dashboard API |
| `MNI_ENABLED` | `true` | Habilitar/desabilitar MNI SOAP |
| `BATCH_DELAY_SECS` | `2.0` | Pausa entre processos no batch |
| `APP_ENV` | `development` | Quando `production`, exige `DASHBOARD_API_KEY` |
| `DASHBOARD_API_KEY` | *(vazio)* | Chave obrigatoria para endpoints POST em producao |
| `TRUST_X_FORWARDED_FOR` | `false` | So habilitar atras de proxy confiavel |

## Uso

### Dashboard (recomendado)

```bash
redis-server
python worker.py
python dashboard_api.py --port 8007 --output ./downloads
# Abrir http://localhost:8007
```

Sem `worker.py` ativo, a dashboard aceita o batch mas nao tem plano de execucao.

### CLI Batch

```bash
# Via arquivo
python batch_downloader.py --input processos.csv --output ./downloads

# Via argumentos
python batch_downloader.py -p "5008407-35.2024.8.08.0012,0126923-56.2011.8.08.0012"

# Sem anexos + batch menor
python batch_downloader.py -i processos.txt --skip-anexos --batch-size 3

# Processos antigos com mapa de Google Drive
python batch_downloader.py -i processos.json --gdrive-map gdrive_links.json
```

### Worker (Redis queue)

```bash
# Requer Redis rodando
python worker.py
# Consome jobs em kratos:pje:jobs
# A dashboard usa reply queues por batch: kratos:pje:results:<batch_id>
# Cada job pode informar outputSubdir para manter os arquivos dentro da pasta do batch
```

## API Endpoints

| Metodo | Rota | Descricao |
|--------|------|-----------|
| `GET` | `/` | Dashboard HTML |
| `GET` | `/healthz` | Readiness da dashboard para compose/orquestracao |
| `GET` | `/api/status` | Status operacional da dashboard + resumo do worker |
| `POST` | `/api/download` | Publicar batch na fila Redis/worker (rate limited: 10/60s) |
| `GET` | `/api/progress` | Progresso do batch atual (polling) |
| `GET` | `/api/history` | Historico de todos os batches |
| `GET` | `/api/batch/{id}` | Detalhes de um batch especifico |
| `GET` | `/metrics` | Metricas Prometheus (text/plain) |
| `GET` | `/static/*` | Arquivos estaticos (CSS, JS) |
| `GET` | `/api/session/status` | Estado da sessao PJe salva em disco |
| `POST` | `/api/session/login` | Dispara login interativo no browser local (202 async) |
| `POST` | `/api/session/verify` | Valida sessao salva via browser headless |

### POST /api/download

```json
{
  "processos": ["5008407-35.2024.8.08.0012", "0126923-56.2011.8.08.0012"],
  "include_anexos": true,
  "gdrive_map": {
    "0126923-56.2011.8.08.0012": "https://drive.google.com/drive/folders/ABC123"
  }
}
```

Respostas: `201` (criado), `400` (formato CNJ invalido), `409` (batch em execucao), `422` (>500 processos), `429` (rate limit).

Observacoes operacionais:
- a dashboard cria uma pasta por batch e consolida os resultados do worker nessa pasta
- falhas de sessao/CAPTCHA no worker encerram o restante do batch como falha rastreavel
- resultados incompletos agora sao marcados como `partial`/`partial_success`, nunca como sucesso pleno
- se a dashboard reiniciar no meio do batch, ela reidrata o batch ativo e volta a consumir a `replyQueue`
- em producao, configure `DASHBOARD_API_KEY` e `REDIS_PASSWORD`; o workflow de deploy agora falha se esses secrets estiverem ausentes

### GET /metrics

Formato Prometheus text (`text/plain; version=0.0.4`). Compativel com Prometheus scrape e Grafana.

```
# Latencia e contagem de chamadas SOAP MNI
pje_mni_requests_total{operation="consultar_processo", status="success"} 42.0
pje_mni_requests_total{operation="consultar_processo", status="timeout"} 1.0
pje_mni_latency_seconds_bucket{operation="consultar_processo", le="5.0"} 40.0

# Hit rate das 3 estrategias de download do Google Drive
pje_gdrive_attempts_total{strategy="gdown", status="success"} 5.0
pje_gdrive_attempts_total{strategy="requests", status="success"} 2.0
pje_gdrive_attempts_total{strategy="playwright", status="success"} 1.0

# Resultado agregado dos processos
pje_batch_processos_total{status="done"} 38.0
pje_batch_processos_total{status="failed"} 4.0
pje_batch_processos_total{status="partial"} 2.0

# Volume acumulado
pje_batch_docs_total 1247.0
pje_batch_bytes_total 3.28e+09

# Throughput do ultimo batch
pje_batch_throughput_docs_per_min 8.3

# Runtime real do worker/control plane
pje_worker_results_total{status="success"} 35.0
pje_worker_results_total{status="partial_success"} 2.0
pje_worker_dead_letters_total{reason="invalid_json"} 1.0
pje_dashboard_batches_total{status="done"} 12.0
pje_dashboard_batch_timeouts_total 0.0
pje_dashboard_active_batch_recoveries_total 1.0
pje_dashboard_active_batches 0.0

# Audit sync para Railway Postgres (Phase 2, opcional)
pje_audit_sync_rows_total{status="success"} 128.0
pje_audit_sync_rows_total{status="failed"} 0.0
pje_audit_sync_batches_total{status="success"} 3.0
pje_audit_sync_batches_total{status="retry"} 0.0
pje_audit_sync_lag_seconds 4.2
pje_audit_sync_malformed_lines_total 0.0
pje_audit_sync_files_vanished_total 0.0
pje_audit_sync_latency_seconds_bucket{le="1.0"} 3.0
```

**Audit sync health:** `GET /healthz` inclui a secao `checks.audit_sync` quando habilitado:

```json
{
  "checks": {
    "audit_sync": {
      "enabled": true,
      "lag_seconds_event_time": 4.2,
      "last_error": null,
      "last_tick_at": "2026-04-17T05:00:00+00:00",
      "rows_total": 128,
      "url": "postgres://audit_writer:***@host.railway.app:5432/db"
    }
  }
}
```

Ver `CLAUDE.md#audit-sync-railway-postgres-phase-2` para bootstrap, role insert-only e requisitos de TLS.

**Labels de status para `pje_mni_requests_total`:**

| Status | Causa |
|--------|-------|
| `success` | Chamada SOAP retornou com sucesso |
| `mni_error` | MNI retornou `sucesso=False` na resposta |
| `timeout` | `asyncio.wait_for` excedeu `MNI_TIMEOUT` |
| `not_found` | Processo nao encontrado no tribunal |
| `auth_failed` | Credenciais invalidas (`Acesso negado`/`Unauthorized`) |
| `error` | Excecao generica (rede, parsing, etc.) |

### GET /health (Worker)

```json
{
  "service": "pje-worker",
  "status": "consuming",
  "healthy": true,
  "checks": {
    "mni": "healthy",
    "redis": "healthy",
    "disk": "ok",
    "disk_free_mb": 5432.1
  },
  "mni_enabled": true,
  "session_valid": false,
  "fallback_ready": false,
  "docs_downloaded": 42,
  "uptime_minutes": 15.3
}
```

Campos importantes:
- `status`: estado operacional do worker (`ready`, `consuming`, `session_expired`, etc.)
- `healthy`: readiness do worker para compose/deploy
- `session_valid`: existe sessao PJe autenticada pronta para REST/browser
- `fallback_ready`: o fallback Playwright/PJe esta realmente disponivel
- com MNI disponivel, `session_valid=false` e `fallback_ready=false` podem coexistir com `healthy=true`

### GET /healthz (Dashboard)

```json
{
  "service": "pje-dashboard",
  "ready": true,
  "current_batch": null,
  "checks": {
    "redis": "healthy",
    "active_batch_recovered": false,
    "active_batch_resume_pending": false
  }
}
```

Use `/healthz` para healthcheck/orquestracao. Use `/api/status` para diagnostico operacional.

### GET /api/status

```json
{
  "service": "pje-dashboard",
  "status": "running",
  "current_status": "idle",
  "worker_status": "ready",
  "worker": {
    "status": "ready",
    "healthy": true,
    "checks": {
      "redis": "healthy",
      "disk": "ok",
      "mni": "healthy"
    },
    "session_valid": false,
    "fallback_ready": false
  },
  "recovered_active_batch": null
}
```

## Formatos de Input

| Formato | Exemplo |
|---------|---------|
| **TXT** | Um numero por linha |
| **CSV** | Coluna `numero`, `numeroProcesso` ou `processo` |
| **JSON** | Array de strings ou objetos com campo `numero` |

## Tribunais Suportados

| Codigo | Endpoint |
|--------|----------|
| TJES | `pje.tjes.jus.br/pje/intercomunicacao?wsdl` |
| TJES_2G | `pje.tjes.jus.br/pje2g/intercomunicacao?wsdl` |
| TJBA | `pje.tjba.jus.br/pje/intercomunicacao?wsdl` |
| TJBA_2G | `pje.tjba.jus.br/pje2g/intercomunicacao?wsdl` |
| TJCE | `pje.tjce.jus.br/pje1grau/intercomunicacao?wsdl` |
| TRT17 | `pje.trt17.jus.br/pje/intercomunicacao?wsdl` |

## Estrutura de Output

```
downloads/
  5008407-35.2024.8.08.0012/
    Peticao Inicial_56705862.html
    Contestacao_25393735.pdf
    ...
  0126923-56.2011.8.08.0012/
    escaneados_gdrive/       # Processos antigos
      documento_1.pdf
    Parecer_67249671.html    # Docs do MNI
  _progress.json             # Progresso em tempo real (atomic writes)
  _report.json               # Relatorio final do batch
```

## Frontend

Dashboard em `http://localhost:8007` — design "Precision Judicial Ops".

**Sessao PJe (card no topo):**
- Dot colorido indica estado: verde (sessao salva), cinza (sem sessao), ambar pulsante (login em andamento), vermelho (falha)
- Botao **Fazer Login** — abre browser real (headless=False) para login manual com CAPTCHA/MFA; retorna 202 imediatamente e faz polling ate concluir
- Botao **Verificar** — valida sessao salva via browser headless; exibe resultado com toast

**Tipografia (Google Fonts):**
| Familia | Uso |
|---------|-----|
| `Figtree` 400–800 | Texto UI (labels, botoes, tabelas) |
| `Oswald` 600–700 | Numeros KPI (4rem, condensado) |
| `DM Mono` 400–500 | Numeros de processo, relogio |

**Efeitos visuais:**
- Glassmorphism em todos os cards (`backdrop-filter: blur`)
- Textura dot-grid no background (`radial-gradient` 24px×24px)
- Cards KPI com numero 4rem + faixa laranja gradiente na borda esquerda
- Badge "running" com animacao ripple (`box-shadow` ring expansion)
- Barra de progresso com divisores de segmento + gradiente laranja→ambar→verde
- Pipeline de fases com conectores SVG + dot animado no passo ativo
- Entrada de pagina em cascata (5 secoes, `[data-animate]`, delay 0→320ms)
- Compat. `prefers-reduced-motion` — todas as animacoes desativadas se preferido

## Deploy

### Docker Compose (recomendado)

```bash
cp .env.example .env   # preencher MNI_USERNAME, MNI_PASSWORD
docker compose up -d   # dashboard + redis
# Opcional: docker compose --profile worker up -d  (+ worker Playwright)
```

Verificacao:

```bash
curl http://localhost:8007/healthz      # readiness da dashboard
curl http://localhost:8007/api/status   # status operacional + worker resumido
curl http://localhost:8007/metrics      # metricas Prometheus
```

### Producao (VPS)

| Recurso | Valor |
|---------|-------|
| Host | `191.252.204.250` |
| Dashboard | `http://191.252.204.250:8007` |
| Metrics | `http://191.252.204.250:8007/metrics` |
| Path | `/opt/pje-download` |

### CI/CD (GitHub Actions)

| Workflow | Trigger | Etapas |
|----------|---------|--------|
| `ci.yml` | push / PR | ruff lint → pytest (73 testes) — badge acima |
| `deploy.yml` | CI concluido com sucesso em `master` | rsync → `docker compose up --build` no VPS → healthcheck worker/dashboard → smoke test da fila |
| `dependabot.yml` | semanal | atualiza actions + pip deps |

Secrets necessarios no repositorio: `VPS_SSH_KEY`, `VPS_HOST`, `VPS_USER`.

## Integracao com Kratos Case Pipeline

Este projeto e o primeiro estagio do [Kratos Case Pipeline (KCP)](https://github.com/fbmoulin/kratos-case-pipeline) — o orquestrador que conecta download PJe, extracao PDF e analise FIRAC em um pipeline automatizado para gabinetes judiciais.

Fluxo: **pje-download** (MNI SOAP) → **KCP** (organiza, classifica, despacha) → **kratos-pdf-extractor** (extracao) → **kratos-v5** (FIRAC + minuta)
