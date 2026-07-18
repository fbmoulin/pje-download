# Changelog

Todas as mudanças notáveis do **pje-download** são documentadas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
o projeto segue versionamento semântico.

## [Unreleased]

### Produção (2026-07-18) — primeiro deploy ao vivo

- **Deploy em produção** num Hostinger VPS (KVM 2, datacenter **São Paulo**), com CD contínuo via `deploy.yml` (`workflow_run` após `ci.yml` verde). App no ar: dashboard/worker/redis `Up (healthy)`, `mni check: healthy`.
- **MNI geo-bloqueio resolvido.** Diagnosticado que o PJe/TJES fica atrás do AWS CloudFront com geo-restrição por país (IP fora do BR → `403`); comprovado por `curl` do WSDL (IP BR → `200`/POP GRU3 vs IP US → `403`/POP BOS50). Corrigido movendo o VPS para o datacenter brasileiro — **sem proxy, sem ofício**. O fallback Playwright compartilha o mesmo requisito de IP BR.
- **Segurança de rede:** firewall só com a porta 22; dashboard (8007) e worker (8006) acessíveis apenas por túnel SSH. Usuário de deploy dedicado (`deploy`, não-root) no grupo docker.

### Fixed

- `deploy.yml` **nunca compilava**: a etapa `Validate required secrets` misturava expressão do Actions `${{ }}` com expansão do bash `${VAR:?}` → falha de parse (todo run morria em 0s). Passa a usar `env:` + `${VAR:?}`.
- **Dockerfile**: o alvo `dashboard` copiava uma lista explícita de arquivos que ficou defasada — faltavam `async_retry.py`, `file_utils.py` e `protocol.py` (helpers dos Sprints 13/14) → `ModuleNotFoundError` em crash-loop. Trocado por `COPY *.py`.
- **Health check do deploy**: batia em `/api/status` (protegido por `X-API-Key` desde o Sprint 8) sem a chave → `401` eterno. Agora envia o header `X-API-Key`.
- **Validação MNI no deploy**: importava `MniClient` (classe correta é `MNIClient`) e não fazia `cd /opt/pje-download` antes do `docker compose exec`. Corrigidos.

### Security

- **zeep 4.3.2 → 4.3.3** — corrige GHSA-4cc2-g9w2-fhf6 (SSRF via `forbid_external` não-conectado em 4.0–4.3.2). Fecha o Dependabot alert #1.

### Docs

- `CLAUDE.md` reconciliado com o estado pós-v2.5.0 (contagem de testes 408→441, Spec Verifier, backlog de deploy) e correção do diagnóstico anterior (o "vermelho em 0s" era erro de parse, não gate de secrets).
- README: seção de produção reescrita (deploy ao vivo em SP, requisito de IP BR para o MNI, acesso por túnel SSH, runbook de troca de região na Hostinger).

## [2.5.0] - 2026-05-01

### Added / Changed

- `protocol.py`: TypedDicts de fio (`JobMessage`/`ResultMessage`/`ProgressMessage`/`DeadLetterEntry`) + validação `job_from_json`; migração do `worker._publish_result`. Formato de fio byte-idêntico ao v2.4 (interop mantida).
- `dashboard_api`: 7 globais mutáveis de módulo colapsadas em um `AppContext` (`app[APP_CTX_KEY]`), eliminando o vazamento de estado entre testes.
- 416 → 424 testes.

## Histórico anterior (Sprints 1–14, 2026-04)

Ver a seção **Completed Sprints** do `CLAUDE.md` para o detalhe por sprint. Marcos:

- **Sprint 5** — trilha de auditoria CNJ 615/2025 (`audit.py`, JSON-L append-only). 183 → 248 testes.
- **Sprint 7** — sync de auditoria para Railway Postgres (`audit_sync.py`, dedupe idempotente `UNIQUE NULLS NOT DISTINCT`). 303 → 348.
- **Sprints 8–11** — auditoria P0–P2: auth em GET, circuit breaker do Redis, retries do PJe, higiene de logs. 348 → 377.
- **Sprints 12–14** — 5 bugs de produção + helpers DRY (`file_utils`, `async_retry`) + split de `_run_batch`/`download_process`. 377 → 408.
- **Sprints 1–4** — hardening P0/P1, segurança (API key, path traversal, session lock) e expansão de cobertura. 73 → 183.

[Unreleased]: https://github.com/fbmoulin/pje-download/compare/v2.5.0...HEAD
[2.5.0]: https://github.com/fbmoulin/pje-download/releases/tag/v2.5.0
