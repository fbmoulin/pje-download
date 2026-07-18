# TODO — pje-download

> Itens acionáveis abertos. Nada aqui bloqueia o uso: o app está em produção (São Paulo) com MNI `healthy`. Histórico e backlog completo em `CLAUDE.md`.

## ▶ Próximo (recomendado)

- [ ] **Validar um download MNI real de ponta a ponta** — agora que o MNI responde `healthy` do VPS de SP, rodar uma consulta real (`consultarProcesso` com `documento=<id>`) contra um processo de teste e confirmar que o PDF chega em `/data/downloads`. É a prova final de que a função-fim funciona, não só o health check.

## Opcionais (hardening / operação)

- [ ] **Sink de auditoria no Railway** — `AUDIT_SYNC_ENABLED=true` + `DATABASE_URL=<audit_writer>` (projeto `pje-audit`). A auditoria JSON-L local já grava no volume; o sink é redundância. Ver `CLAUDE.md` §"Audit Sync".
- [ ] **zeep `forbid_external=True`** (`mni_client.py:178`) — defense-in-depth contra SSRF via `xsd:import`. **Testar antes:** WSDLs do MNI podem importar schemas externos legítimos → pode quebrar com `ExternalReferenceForbidden`. Agora é testável (o MNI é alcançável do VPS).

## Follow-ups pequenos (dívida de tipos, do Sprint 15)

- [ ] Migrar `worker._try_official_api` para construir `ResultMessage` via helper tipado em vez de dict inline (~5 linhas).
- [ ] Adicionar `batchId: NotRequired[str | None]` ao `ProgressMessage` (`worker.py:1494` seta, o tipo não declara).

## Operação — bom saber

- Trocar região do VPS na Hostinger é **1×/30 dias**, só por hPanel, muda o IP e desanexa o firewall. Runbook no `CLAUDE.md` (backlog item 1) e no README (§Produção).
- Todo push no `master` redeploya (`ci.yml` verde → `deploy.yml`). Um dispatch manual pode pegar um blip transiente de SSH no `rsync` — re-disparar resolve.
