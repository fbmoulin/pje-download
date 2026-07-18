# TODO — pje-download

> Itens acionáveis abertos. Nada aqui bloqueia o uso: o app está em produção (São Paulo) com MNI `healthy`. Histórico e backlog completo em `CLAUDE.md`.

## ✅ Bug do Redis — RESOLVIDO 2026-07-18 (PR #32, `2b6a784`)

- [x] **`Timeout reading from redis:6379`** — causa-raiz = **regressão do redis-py 8.0.0**, não pool/cancelamento.
  - `redis-py 8.0.0` (bump do Dependabot #24, `4da8899`) mudou o default de `socket_timeout` em `AbstractConnection.__init__` de `None` para **5** — exatamente o valor que os dois BLPOP usam. Um comando bloqueante com timeout **>= `socket_timeout`** nunca termina normalmente: o deadline do socket dispara antes do `nil` do servidor chegar, então `read_response` **levanta** `TimeoutError` em vez de retornar `None` (`redis/asyncio/connection.py:778`).
  - Medido no container de produção: `BLPOP(3)→None@3.017s`, `BLPOP(5)→raise@5.006s`, `BLPOP(8)→raise@5.008s`. O `BLPOP(8)` falhar em 5.008s é a prova: o teto é um 5 fixo, não o argumento do blpop.
  - **Por que parecia intermitente:** job que chega enquanto o BLPOP já espera retorna na hora e funciona. Só a fila **vazia** levantava — daí o circuit breaker latchar `redis_unreachable` e o backoff dormir até 60s (jobs parados em `queued`), e a dashboard marcar `failed` batches cujos arquivos já estavam em disco.
  - **⚠️ Por que a investigação anterior "descartou" `socket_timeout`:** o teste isolado usou `blpop(timeout=3)` — o único valor **abaixo** do novo default — então retornou `None` limpo e pareceu saudável. A conclusão "não é `socket_timeout`" estava errada.
  - **Fix:** `socket_timeout` explícito nos dois sites de construção do cliente, **derivado** das constantes de BLPOP (`config.REDIS_SOCKET_TIMEOUT_SECS`) para subir em lockstep. Nunca `None` (conexão TCP morta penduraria para sempre e o circuit breaker nunca dispararia); nunca hardcoded (o bug foi confiar num default de biblioteca para comportamento load-bearing).
  - **Verificado ao vivo** pós-deploy: worker `unhealthy`→`healthy`, status `redis_unreachable`→`consuming`, **0** `redis_error`. 446 testes verdes no CI (era 441).

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
