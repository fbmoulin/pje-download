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
| `worker.py` | ~880 | Worker PJe com 3 estrategias em cascata (MNI SOAP, REST API, Playwright) |
| `mni_client.py` | ~710 | Cliente SOAP para MNI — consulta processos e download de documentos em 2 fases |
| `batch_downloader.py` | ~610 | Download em lote via CLI com progresso, retomada e relatorio |
| `dashboard_api.py` | ~440 | API REST (aiohttp) para submissao, progresso e historico |
| `dashboard.html` | ~160 | Frontend HTML referenciando CSS/JS externos |
| `static/css/style.css` | ~550 | Design system — dark theme, animations, responsive |
| `static/js/app.js` | ~490 | Logica da dashboard — polling, pipeline, toasts, file upload |
| `gdrive_downloader.py` | ~540 | Download de pastas Google Drive (processos antigos escaneados) |
| `config.py` | ~20 | Loader compartilhado de .env |

## Estrategias de Download

**Em ordem de preferencia:**

1. **MNI SOAP** (sem browser, mais rapido)
   - Fase 1: `consultarProcesso` retorna metadados dos documentos
   - Fase 2: `consultarProcesso` com IDs especificos retorna conteudo binario em batches
   - Endpoint TJES: `https://pje.tjes.jus.br/pje/intercomunicacao?wsdl`

2. **API REST** (via browser autenticado)
   - Usa sessao Playwright para chamadas REST ao PJe
   - Endpoint: `/api/processos/{id}/documentos`

3. **Browser Automation** (Playwright, fallback)
   - 3a: Botao "full download" nos autos digitais (baixa tudo de uma vez)
   - 3b: Download individual de cada documento

4. **Google Drive** (processos antigos pre-PJe)
   - Detecta processos antigos (numero nao comeca com "5")
   - Baixa pasta do Google Drive com docs escaneados (gdown > requests > Playwright)

## Setup

```bash
# Dependencias
pip install -r requirements.txt
playwright install chromium

# Variaveis de ambiente
export MNI_USERNAME="12345678900"   # CPF sem pontos
export MNI_PASSWORD="senha"
export MNI_TRIBUNAL="TJES"         # TJES, TJES_2G, TJBA, TJBA_2G, TJCE, TRT17
```

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
| `POST` | `/api/download` | Submeter processos para download |
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
  _progress.json             # Progresso em tempo real
  _report.json               # Relatorio final do batch
```
