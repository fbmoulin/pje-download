# pje-download — Deploy Prep (2026-04-03, in progress)

## Status: PAUSED — Dockerfile fix applied, not yet committed

## O que foi feito nesta sessão
- Gap #13 (Prometheus metrics) completo e pushed (commit e0c6f45)
- CLAUDE.md atualizado com Commands + Environment sections
- README.md atualizado com /metrics section e tabela de status labels
- 69 testes passando

## Deploy prep — estado atual

### Blocker encontrado no Dockerfile
O target `dashboard` no Dockerfile não incluía `metrics.py` nem `prometheus_client`.
Container quebraria no boot com `ModuleNotFoundError`.

**Fix já aplicado (NÃO commitado ainda):**
```diff
- RUN pip install --no-cache-dir aiohttp structlog zeep requests gdown && \
+ RUN pip install --no-cache-dir aiohttp prometheus_client structlog zeep requests gdown && \
  ...
- COPY --chown=appuser:appuser config.py mni_client.py gdrive_downloader.py \
-      batch_downloader.py dashboard_api.py dashboard.html ./
+ COPY --chown=appuser:appuser config.py mni_client.py gdrive_downloader.py \
+      batch_downloader.py dashboard_api.py dashboard.html metrics.py ./
```

### Próximos passos
1. Validar build Docker (usuário recusou `docker build` — pode não ter Docker ou preferir não rodar)
2. Verificar se `.env` está pronto (`.env.example` existe, vars mínimas documentadas)
3. Commit Dockerfile fix
4. Push e deploy

### Observações
- Worker target já estava OK (usa `pip install -r requirements.txt` que inclui prometheus_client)
- `docker compose up -d` sobe dashboard + redis
- `docker compose --profile worker up -d` sobe também o worker
- Healthcheck do dashboard: `GET /api/status`
- Worker requer Playwright headless + sessão salva (headless=False em dev)
