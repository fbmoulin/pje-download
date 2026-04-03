# PJe Download

[![CI](https://github.com/fbmoulin/pje-download/actions/workflows/ci.yml/badge.svg)](https://github.com/fbmoulin/pje-download/actions/workflows/ci.yml)

Automacao de download de documentos processuais do PJe (Processo Judicial Eletronico) via MNI SOAP, API REST e browser automation.

## Arquitetura

```
                     +------------------+
                     |  dashboard.html  |  Frontend (CSS + JS)
                     +--------+---------+
                              |
                     +--------v---------+
                     |  dashboard_api   |  aiohttp REST API (:8007)
                     +--------+---------+
                              |
              +---------------+---------------+
              |                               |
    +---------v----------+          +---------v----------+
    |  batch_downloader  |          |      worker.py     |
    |  (CLI / API call)  |          |  (Redis queue consumer)
    +---------+----------+          +---------+----------+
              |                               |
    +---------v----------+          +---------v----------+
    |    mni_client.py   |          |   3 strategies:    |
    |  (SOAP/WSDL zeep)  |          |  MNI > API > Browser
    +--------------------+          +--------------------+
              |
    +---------v-----------+
    | gdrive_downloader   |
    | (processos antigos) |
    +---------------------+
```

## Componentes

| Arquivo | Linhas | Funcao |
|---------|--------|--------|
| `worker.py` | ~1076 | Worker PJe com 3 estrategias em cascata, downloads paralelos, deep health checks |
| `mni_client.py` | ~743 | Cliente SOAP para MNI — download em 2 fases com dedup por checksum |
| `batch_downloader.py` | ~631 | Download em lote via CLI com progresso atomico, retomada e relatorio |
| `dashboard_api.py` | ~701 | API REST (aiohttp) com rate limiting, validacao CNJ, recuperacao parcial e gestao de sessao PJe |
| `dashboard.html` | ~193 | Frontend HTML com Google Fonts, data-animate attrs e card de sessao PJe |
| `static/css/style.css` | ~685 | Design system — glassmorphism, Oswald KPIs, dot-grid bg, staggered animations |
| `static/js/app.js` | ~618 | Dashboard — adaptive polling, pipeline renderer (SVG), toasts, file upload, sessao PJe |
| `gdrive_downloader.py` | ~596 | Download de pastas Google Drive (processos antigos escaneados) |
| `config.py` | ~61 | Configuracao centralizada — todas as variaveis env-configuraveis |
| `metrics.py` | ~60 | Registry Prometheus dedicado — 7 metricas de latencia, throughput e erros |

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
| Adaptive polling | Frontend backoff 1.5s-15s com reset em sucesso |
| Partial failure recovery | Dashboard preserva progresso parcial em erros |
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

## Uso

### Dashboard (recomendado)

```bash
python dashboard_api.py --port 8007 --output ./downloads
# Abrir http://localhost:8007
```

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
# Publica jobs em kratos:pje:jobs, resultados em kratos:pje:results
```

## API Endpoints

| Metodo | Rota | Descricao |
|--------|------|-----------|
| `GET` | `/` | Dashboard HTML |
| `GET` | `/api/status` | Status geral do worker |
| `POST` | `/api/download` | Submeter processos (rate limited: 10/60s) |
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

# Resultado dos processos no batch
pje_batch_processos_total{status="done"} 38.0
pje_batch_processos_total{status="failed"} 4.0

# Volume acumulado
pje_batch_docs_total 1247.0
pje_batch_bytes_total 3.28e+09

# Throughput do ultimo batch
pje_batch_throughput_docs_per_min 8.3
```

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
  "docs_downloaded": 42,
  "uptime_minutes": 15.3
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
curl http://localhost:8007/api/status   # {"status":"running"}
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
| `ci.yml` | push / PR | ruff lint → pytest (69 testes) — badge acima |
| `deploy.yml` | push master | rsync → `docker compose up --build` no VPS |
| `dependabot.yml` | semanal | atualiza actions + pip deps |

Secrets necessarios no repositorio: `VPS_SSH_KEY`, `VPS_HOST`, `VPS_USER`.

## Integracao com Kratos Case Pipeline

Este projeto e o primeiro estagio do [Kratos Case Pipeline (KCP)](https://github.com/fbmoulin/kratos-case-pipeline) — o orquestrador que conecta download PJe, extracao PDF e analise FIRAC em um pipeline automatizado para gabinetes judiciais.

Fluxo: **pje-download** (MNI SOAP) → **KCP** (organiza, classifica, despacha) → **kratos-pdf-extractor** (extracao) → **kratos-v5** (FIRAC + minuta)
