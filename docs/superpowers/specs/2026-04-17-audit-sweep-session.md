# Session memo — 2026-04-17: Audit sync + full audit sweep

Resumo de uma sessão longa que fechou o backlog acionável da auditoria técnica v1.7 → v2.0 (5 PRs mergeados em master, +74 tests).

## Timeline

| PR | Commit | Escopo | Δ tests |
|---|---|---|---|
| #3 | 6612135 | feat(audit-sync): Phase 2 CNJ 615/2025 → Railway Postgres | 303 → 348 |
| #6 | dd27556 | fix(security): api_key em todo /api/* + torn-read logging | 348 → 353 |
| #7 | de4d57f | fix(reliability): pool lifetime + rpush retry + log hygiene | 353 → 359 |
| #8 | 3f9dde7 | test(p0.2): characterization para browser fallback + gdrive | 359 → 371 |
| #9 | 574c1fb | fix(polish): circuit breaker + PJe retry + cursor cleanup | 371 → 377 |

## Decisões arquiteturais

### 1. Pivô Supabase → Railway Postgres para audit sink

Por custo pré-lançamento. Plain `DATABASE_URL`, asyncpg direto (sem SQLAlchemy nem Alembic, overkill para uma tabela write-only). Gravado em `~/.claude/projects/-home-fbmoulin/memory/feedback_railway-over-supabase-prelaunch.md` para cascata em futuros projetos pré-launch.

### 2. 3 bugs descobertos apenas testando contra Postgres real

Todos corrigidos ainda no PR #3:

- **Expressão geradora não-`IMMUTABLE`** — `ts::text || '|' || ...` depende de `DateStyle` (session-mutable). PG 18.3 rejeita. Trocado por `UNIQUE NULLS NOT DISTINCT (ts, event_type, processo_numero, documento_id)`.
- **`ON CONFLICT (cols) DO NOTHING` exige `SELECT` na tabela** — o arbiter-index precisa ler as linhas candidatas. Doc atualizada: role `audit_writer` tem INSERT+SELECT; sem UPDATE/DELETE/TRUNCATE continua sendo "append-only prático".
- **TLS `verify-full` estrito quebra em Railway TCP proxy** — `nozomi.proxy.rlwy.net` serve self-signed chain. `_ensure_pool` agora respeita `sslmode` da URL; só monta `SSLContext` estrito para `verify-full`/`verify-ca`.

### 3. Dockerfile dashboard estava incompleto (pré-existente)

Target `dashboard` listava deps explicitamente para evitar Playwright (pesado). Mas faltava `redis` (bug antigo) **e** `asyncpg` (novo). `COPY` também não incluía `audit.py`, `audit_sync.py`, `pje_session.py`, nem `migrations/`. Corrigido no PR #3.

### 4. `api_key_middleware` guardava apenas POST

Auditoria P0.1 descobriu que GETs vazavam lista de CNJs, mensagens de erro e status de login sem auth. Middleware agora exige `X-API-Key` em todo `/api/*`; apenas `/`, `/healthz`, `/metrics`, `/static/*` permanecem públicos via whitelist.

## Infraestrutura provisionada

**Railway project**: `pje-audit`, id `3c561ec2-27d4-4278-aa49-9f7187a49e2b`, host `nozomi.proxy.rlwy.net:27048/railway`.
- Schema: `audit_entries` (migration 001) com UNIQUE NULLS NOT DISTINCT composite + 3 indexes (ts, processo_numero, event_type+ts).
- Role: `audit_writer` append-only (INSERT + SELECT — SELECT é exigido pelo arbiter-index do ON CONFLICT).
- Credenciais em `.env` local (admin URL para migrations, audit_writer URL para runtime).

## Audit agent validation

4 agentes paralelos (arch, database, security, quality) rodaram contra o repo e levantaram findings em 7 dimensões. Meta-insight: **2 findings foram falsos-positivos detectados apenas quando tentei implementar**:

- **P2.1 indexes em audit_entries** — migration 001 já criava `(processo_numero, ts DESC)` e `(event_type, ts DESC)`. Agente não inspecionou migration existente.
- **P2.5 hset pipelining** — zero chamadas `hset/hgetall/lrange` no repo. Agente especulou números de linha.

Lição: sempre validar findings de agentes contra o código atual antes de implementar.

## Backlog restante (não-código)

1. **Deploy prod** — SSH na VPS, `AUDIT_SYNC_ENABLED=true` + `DATABASE_URL=<audit_writer URL>`, restart do container `pje-dashboard`. Testado localmente via docker-compose + Playwright.
2. **Grafana dashboard** (fecha P0.4) — provisionar Grafana (reusar VPS openclaw). Consumir `/metrics`. Alertas:
   - `pje_audit_sync_lag_seconds_event_time > 60` → atraso de sync
   - `pje_audit_sync_batches_total{status="failed"}` → Railway caiu
   - `pje_worker_dead_letters_total > 0` → jobs malformados
   - Liveness via `/health` detecta `_health_status=redis_unreachable` (circuit breaker)
