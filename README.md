# PJe Download

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
| `dashboard_api.py` | ~518 | API REST (aiohttp) com rate limiting, validacao CNJ e recuperacao parcial |
| `dashboard.html` | ~161 | Frontend HTML referenciando CSS/JS externos |
| `static/css/style.css` | ~553 | Design system — dark theme, animations, responsive grid |
| `static/js/app.js` | ~500 | Dashboard — adaptive polling, pipeline renderer, toasts, file upload |
| `gdrive_downloader.py` | ~596 | Download de pastas Google Drive (processos antigos escaneados) |
| `config.py` | ~61 | Configuracao centralizada — todas as variaveis env-configuraveis |

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
| `GET` | `/static/*` | Arquivos estaticos (CSS, JS) |

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

Respostas: `201` (criado), `400` (formato CNJ invalido), `409` (batch em execucao), `429` (rate limit).

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

## Integracao com Kratos Case Pipeline

Este projeto e o primeiro estagio do [Kratos Case Pipeline (KCP)](https://github.com/fbmoulin/kratos-case-pipeline) — o orquestrador que conecta download PJe, extracao PDF e analise FIRAC em um pipeline automatizado para gabinetes judiciais.

Fluxo: **pje-download** (MNI SOAP) → **KCP** (organiza, classifica, despacha) → **kratos-pdf-extractor** (extracao) → **kratos-v5** (FIRAC + minuta)
