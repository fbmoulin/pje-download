# Auditoria Final — pje-download v1.5 (pos-sprint P0/P1)

> Data: 2026-04-04 | Metodologia: 4 agentes especializados em paralelo
> Auditors: Adversarial Critic, Security Reviewer, Code Quality Reviewer, Coverage Explorer

---

## 1. Resumo Executivo

O projeto **pje-download** e um sistema Python 3.12 (~6.000 linhas) que automatiza o download de documentos judiciais do PJe via SOAP (MNI), API REST e browser automation (Playwright). Apos o sprint P0/P1 (12 bugs corrigidos, 73->101 testes), foi realizada uma auditoria final com 4 agentes independentes.

### Veredicto

| Dimensao | Nota | Status |
|----------|------|--------|
| Seguranca | **4/10** | CRITICO — credenciais no .env, zero auth na API, info disclosure |
| Cobertura de Testes | **37%** | INSUFICIENTE — 33/88 simbolos publicos testados |
| Qualidade de Codigo | **7/10** | BOM — arquitetura clara, 4x duplicacao de sanitize filename |
| Resiliencia/Producao | **5/10** | MEDIO — sem eviction de memoria, health check fragil |
| Documentacao | **9/10** | EXCELENTE — CLAUDE.md e README precisos e completos |

**O app NAO esta pronto para producao sem corrigir os itens CRITICAL.**

---

## 2. Achados por Severidade

### CRITICAL (5) — Bloqueiam deploy

| # | Issue | Arquivo | Impacto |
|---|-------|---------|---------|
| C1 | **Credenciais reais (.env)** — CPF `07175573758`, senha `Scraper@123`, Redis password em plaintext no repo | `.env:2-3` | Comprometimento total da conta PJe. Rotacao imediata. |
| C2 | **Zero auth na Dashboard API** — 0.0.0.0:8007 sem API key, token ou session. Qualquer cliente na rede pode submeter downloads de documentos judiciais | `dashboard_api.py:720` | Acesso nao autorizado a documentos sigilosos |
| C3 | **`_acquire_session_lock` retorna True em falha** — conflata ImportError (sem fcntl) com OSError (lock held), permitindo corrupcao concorrente de session file | `worker.py:107-121` | Corrupcao de sessao entre workers simultaneos |
| C4 | **`DashboardState.batches` sem eviction** — cresce indefinidamente na memoria, sem limpar batches antigos | `dashboard_api.py:71-99` | OOM kill do container apos centenas de batches |
| C5 | **`_report.json` escrito nao-atomicamente** — `_progress.json` usa tmp+rename, mas `_report.json` usa write_text direto | `batch_downloader.py:587-591` | Historico corrompido em crash durante finalizacao |

### HIGH (15) — Devem ser corrigidos antes do proximo release

**Seguranca (7):**
| # | Issue | Arquivo |
|---|-------|---------|
| H1 | Session file (`pje-session.json`) escrito com permissoes 0644 — qualquer usuario local le cookies auth | `pje_session.py:101` |
| H2 | Path traversal via `dl.suggested_filename` do Playwright — sem sanitizacao | `pje_session.py:299` |
| H3 | Path traversal via Content-Disposition filename do GDrive | `gdrive_downloader.py:225` |
| H4 | Information disclosure — `/api/status` retorna path absoluto do filesystem | `dashboard_api.py:280` |
| H5 | Information disclosure — `/api/session/status` retorna path do session file | `dashboard_api.py:484` |
| H6 | Exception strings completas retornadas ao cliente em `/api/session/verify` | `dashboard_api.py:503` |
| H7 | Worker health em 0.0.0.0:8006 sem auth, expoe `last_error`, disk usage | `worker.py:954` |

**Resiliencia (5):**
| # | Issue | Arquivo |
|---|-------|---------|
| H8 | Health endpoint faz SOAP check live por request — 503 em lentidao do tribunal causa restart do container | `worker.py:967-978` |
| H9 | `download_documentos` metrics sempre grava `status="success"` mesmo com 0 docs salvos | `mni_client.py:694-699` |
| H10 | `_save_document` swallows disk-full silently — `OSError: No space` logado como WARNING, batch continua | `mni_client.py:741-748` |
| H11 | `_stream_to_disk` sem timeout total — servidor que para de enviar bloqueia thread indefinidamente | `gdrive_downloader.py:241-243` |
| H12 | Rate limiter usa `request.remote` — ineficaz atras de Docker bridge (todos IPs iguais) | `dashboard_api.py:638` |

**Qualidade (3):**
| # | Issue | Arquivo |
|---|-------|---------|
| H13 | 4x implementacoes de sanitize filename com character sets diferentes | `mni_client:814`, `pje_session:323`, `batch_downloader:362`, `worker:315` |
| H14 | `pje_session.py:129` — `read_text()` sem encoding, falha com UTF-8 no Windows | `pje_session.py:129` |
| H15 | `load_session()` double-acquire lock sem release — fd leak | `worker.py:190,236` |

### MEDIUM (6) — Tech debt significativo

| # | Issue | Arquivo |
|---|-------|---------|
| M1 | Sem audit trail para acesso a documentos (CNJ 615/2025) | `worker.py` (todo) |
| M2 | SOAP fault messages passadas raw ao usuario no fallback `else` | `mni_client.py:296-326` |
| M3 | Error messages com paths/credenciais publicadas no Redis job result | `worker.py:396` |
| M4 | `PJE_BASE_URL` aceito sem validacao — pode apontar para servidor malicioso | `config.py:39` |
| M5 | Redis health check password em plaintext no `docker inspect` | `docker-compose.yml:30` |
| M6 | Module-level globals (`state`, `_rate_buckets`, `_login_running`) nao resetados entre testes — flaky risk | `dashboard_api.py:244` |

### LOW (10) — Melhorias incrementais

| # | Issue |
|---|-------|
| L1 | `config.load_env()` usa `setdefault` — env do host tem prioridade silenciosa sobre .env |
| L2 | Batch MNI-unhealthy + sem session file retorna early sem tentar GDrive |
| L3 | `_download_docs_individually` — `dl_page.close()` em finally referencia variavel possivelmente nao definida |
| L4 | Worker Redis retry sem backoff exponencial — 120 erros/hora em partitions longas |
| L5 | `_load_history()` bare `except: pass` sem log — historico perdido silenciosamente |
| L6 | `_purge_stale_buckets` so roda com >50 IPs unicos — single-IP nunca limpa |
| L7 | Playwright download timeout (60s) misaligned com `goto` timeout (30s default) |
| L8 | `_try_api` URL-encode `.` e `-` com `%2E`/`%2D` — pode causar 404 em alguns tribunais |
| L9 | `_stream_to_disk` definido dentro do loop — cria novo function object por iteracao |
| L10 | Dockerfile worker usa `COPY . .` — arquivos sensiveis futuros sao incluidos |

---

## 3. Cobertura de Testes — Mapa Funcional

| Modulo | Simbolos | Testados | Cobertura | Risco |
|--------|----------|----------|-----------|-------|
| config.py | 2 | 2 | 100% | Baixo |
| metrics.py | 0 (declarativo) | 8 objetos | 100% | Nenhum |
| mni_client.py | 10 | 2 | **15%** | **CRITICO** — parser, download, save sem testes |
| batch_downloader.py | 13 | 11 | 78% | Medio — paths de fallback |
| gdrive_downloader.py | 8 | 2 | **25%** | **ALTO** — 3 strategies sem testes |
| pje_session.py | 10 | 6 | 60% | Medio — download pipeline |
| dashboard_api.py | 22 | 10 | **40%** | **ALTO** — session endpoints, rate limiter, CORS |
| worker.py | 23 | 3 | **13%** | **CRITICO** — 20 funcoes sem cobertura |

**Top 5 funcoes mais criticas sem testes:**
1. `MNIClient._parse_processo()` — 140L de parsing de SOAP response, data loss silencioso
2. `MNIClient.download_documentos()` — orquestracao de download em 2 fases
3. `PJeSessionWorker.download_process()` — cascata de 3 estrategias, path traversal guard
4. `rate_limit_middleware()` — protecao de seguranca nao testada
5. `DashboardState._run_batch()` — mapeamento de resultado para status final

---

## 4. Qualidade de Codigo — Notas por Area

| Area | Nota | Diagnostico |
|------|------|-------------|
| Arquitetura | 7/10 | Separacao clara: orchestrator (batch), transport (mni), session (pje), API (dashboard). Worker e pje_session tem overlap de responsabilidade em download. |
| Duplicacao | **4/10** | 4x sanitize filename, 2x unique path, 3x credential read. Maior gap de qualidade. |
| API Design | 8/10 | Endpoints consistentes, JSON errors padronizados, status codes corretos. |
| Testes | 7/10 | Bem estruturados onde existem. Mocks corretos. Gaps criticos nos core paths. |
| Async Patterns | 8/10 | `asyncio.to_thread` usado corretamente em todos os calls bloqueantes. |
| Error Handling | 7/10 | Fallback chains (gdrive strategies, worker 3-strategy) sao bem desenhadas. Inconsistencia: alguns swallow, outros raise. |
| Config | 6/10 | Module-level evaluation documentada mas fragil. `CONCURRENT_DOWNLOADS` re-lido do env em batch_downloader. |
| Logging | 9/10 | structlog consistente, dotted namespaces, keyword args. Excelente. |
| Type Annotations | 8/10 | Presentes em todas as funcoes publicas. Poucos `Any`. |
| Documentacao | 9/10 | CLAUDE.md, README e docstrings precisos e sincronizados com o codigo. |

---

## 5. Roteiro Estruturado de Evolucao

### Sprint 2: Security Hardening (Prioridade IMEDIATA)

| Task | Esforco | Impacto | Cobre |
|------|---------|---------|-------|
| 2.1 Rotacionar credenciais MNI + verificar git history | 15min | CRITICO | C1 |
| 2.2 API key middleware para POST endpoints | 1h | CRITICO | C2 |
| 2.3 Fix `_acquire_session_lock` — separar ImportError de OSError | 30min | CRITICO | C3 |
| 2.4 Session file permissions 0600 | 15min | HIGH | H1 |
| 2.5 Sanitizar `dl.suggested_filename` e Content-Disposition | 30min | HIGH | H2, H3 |
| 2.6 Remover paths/internals das respostas HTTP | 30min | HIGH | H4, H5, H6, H7 |
| 2.7 Validar PJE_BASE_URL (https + .jus.br) | 15min | MEDIUM | M4 |

**Estimativa: ~3.5h | Resultado: C1-C3 + 7 HIGHs fechados**

### Sprint 3: Resiliencia + DRY (1-2 dias)

| Task | Esforco | Impacto | Cobre |
|------|---------|---------|-------|
| 3.1 Eviction policy para `DashboardState.batches` (LRU, max 100) | 1h | CRITICO | C4 |
| 3.2 Atomic write para `_report.json` (tmp+rename) | 15min | CRITICO | C5 |
| 3.3 Consolidar `sanitize_filename` + `unique_path` em config.py | 1h | HIGH | H13 |
| 3.4 Fix `read_text(encoding="utf-8")` em pje_session | 5min | HIGH | H14 |
| 3.5 Fix double lock acquire em `load_session` | 30min | HIGH | H15 |
| 3.6 Health endpoint: cache MNI check (30s TTL), bind 127.0.0.1 | 1h | HIGH | H8, H7 |
| 3.7 `download_documentos` metric: condicionar status a saved_files > 0 | 15min | HIGH | H9 |
| 3.8 `_save_document`: fail-fast em disk-full (re-raise OSError) | 15min | HIGH | H10 |
| 3.9 Rate limiter: parse X-Forwarded-For | 30min | HIGH | H12 |
| 3.10 Stream timeout: `asyncio.wait_for` em torno de `_stream_to_disk` | 30min | HIGH | H11 |

**Estimativa: ~5.5h | Resultado: C4-C5 + 8 HIGHs fechados**

### Sprint 4: Test Coverage Expansion (2-3 dias)

| Task | Testes | Cobre |
|------|--------|-------|
| 4.1 `test_mni_client.py` — `_parse_processo` com mock SOAP response | ~15 | 37%->45% |
| 4.2 `test_mni_client.py` — `download_documentos`, `_save_document`, `health_check` | ~12 | 45%->55% |
| 4.3 `test_worker.py` — `download_process`, `consume_queue`, `_detect_captcha` | ~15 | 55%->65% |
| 4.4 `test_dashboard_api.py` — session endpoints, rate limiter, CORS, batch detail | ~12 | 65%->75% |
| 4.5 `test_gdrive_downloader.py` — `download_gdrive_folder`, strategies com mocks | ~10 | 75%->82% |

**Estimativa: 2-3 dias | Resultado: 101->165+ testes, cobertura 37%->82%**

### Sprint 5: Production Readiness (1-2 dias)

| Task | Descricao |
|------|-----------|
| 5.1 Audit trail estruturado para CNJ 615/2025 — JSON log separado |
| 5.2 Graceful shutdown completo — cancelar batch em andamento, drain Redis |
| 5.3 Docker Compose health checks alinhados com liveness vs readiness |
| 5.4 Redis retry com backoff exponencial + jitter |
| 5.5 Sanitizar error messages antes de publicar no Redis |
| 5.6 Monitoramento: Grafana dashboard com metricas Prometheus existentes |

---

## 6. Pontos Positivos (consenso dos 4 agentes)

1. **Path traversal guards** em batch_downloader e worker sao corretos e completos
2. **CNJ validation** no boundary da API e rigorosa
3. **CORS localhost-only** implementado corretamente
4. **Atomic writes** para `_progress.json` — padrao exemplar
5. **XSS protection** no frontend via `textContent` + `esc()`
6. **Docker non-root** user (appuser:1001)
7. **structlog** consistente com dotted namespaces — excelente observabilidade
8. **SSRF mitigation** no `gdrive_map` via `extract_folder_id()`
9. **Two-phase MNI download** — design correto para o protocolo SOAP do PJe
10. **Fallback chains** (MNI->API->Browser, gdown->requests->playwright) sao robustas

---

> **Conclusao:** O sprint P0/P1 resolveu os 12 bugs mais urgentes e elevou os testes de 73 para 101. O app tem uma arquitetura solida e boa qualidade de codigo. Porem, **nao esta pronto para producao** sem o Sprint 2 (seguranca) — especialmente a rotacao de credenciais (C1) e autenticacao na API (C2). O roadmap acima, executado em ordem, leva o projeto a production-grade em ~2 semanas.
