# TODO — pje-download

> Itens acionáveis abertos. Nada aqui bloqueia o uso: o app está em produção (São Paulo) com MNI `healthy`. Histórico e backlog completo em `CLAUDE.md`.

## 🔴 Bug ativo — confiabilidade do Redis nos processos em execução

- [ ] **`Timeout reading from redis:6379` intermitente no worker/dashboard rodando** → orquestração de batch não-confiável: às vezes o worker não consome o job (fica `queued`/`waiting`), às vezes a dashboard não lê o resultado da reply-queue e marca o batch `failed` embora os arquivos tenham sido baixados. **O download em si FUNCIONA** (batch `20260718_161950` baixou 13 docs → 3 PDF + 9 HTML reais em disco); o bug é no plano de controle via Redis.
  - **Já descartado:** não é o firewall (persiste com ele desanexado), não é `socket_timeout` mal configurado (teste isolado no container: `redis.from_url(REDIS_URL)` + `blpop(timeout=3)` retorna `None` limpo em 3.01s, `ping` 0.01s), não é DNS/rede (TCP `redis:6379` OK), não é restart (persiste após containers frescos).
  - **Assinatura:** conexão fresh funciona, conexões long-lived nos processos ficam ruins. Suspeita = pool/lifecycle do `redis.asyncio` (ex.: `blpop` cancelado por `asyncio.wait_for` externo envenena a conexão do pool; ou acesso concorrente à mesma conexão). **Investigar com `systematic-debugging`**, não sondagem ad-hoc: capturar o traceback real do `TimeoutError` no processo rodando; revisar wrappers de timeout em `dashboard_api._poll_results_loop` e `worker` blpop; considerar `retry_on_timeout=True`/`health_check_interval`/conexão dedicada por consumidor.
  - Toda vez que rodar: `docker compose restart worker` dá alívio temporário (conexão nova) mas o erro volta.

## ▶ Próximo (recomendado)

- [x] **Validar download MNI real de ponta a ponta** — ✅ FEITO 2026-07-18: `5022505-25.2024.8.08.0012` → MNI autenticou (senha nova), `consultar_processo.success documentos=13`, **3 PDF + 9 HTML reais** em `/data/downloads`. Os 11 "vinculados" o MNI não retorna (precisam do fallback Playwright — limitação do MNI 2.2.2). Bug de placar acima é ortogonal ao sucesso do download.

## Opcionais (hardening / operação)

- [ ] **Sink de auditoria no Railway** — `AUDIT_SYNC_ENABLED=true` + `DATABASE_URL=<audit_writer>` (projeto `pje-audit`). A auditoria JSON-L local já grava no volume; o sink é redundância. Ver `CLAUDE.md` §"Audit Sync".
- [ ] **zeep `forbid_external=True`** (`mni_client.py:178`) — defense-in-depth contra SSRF via `xsd:import`. **Testar antes:** WSDLs do MNI podem importar schemas externos legítimos → pode quebrar com `ExternalReferenceForbidden`. Agora é testável (o MNI é alcançável do VPS).

## Follow-ups pequenos (dívida de tipos, do Sprint 15)

- [ ] Migrar `worker._try_official_api` para construir `ResultMessage` via helper tipado em vez de dict inline (~5 linhas).
- [ ] Adicionar `batchId: NotRequired[str | None]` ao `ProgressMessage` (`worker.py:1494` seta, o tipo não declara).

## Operação — bom saber

- Trocar região do VPS na Hostinger é **1×/30 dias**, só por hPanel, muda o IP e desanexa o firewall. Runbook no `CLAUDE.md` (backlog item 1) e no README (§Produção).
- Todo push no `master` redeploya (`ci.yml` verde → `deploy.yml`). Um dispatch manual pode pegar um blip transiente de SSH no `rsync` — re-disparar resolve.
