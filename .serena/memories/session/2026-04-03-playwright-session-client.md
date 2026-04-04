# Playwright Session Client — 2026-04-03

## Status
IMPLEMENTED — pje_session.py + batch_downloader.py fallback (commit f55beb9)

## PJe TJES Protection Stack (discovered via live testing)
Three layers of protection in sequence:
1. **Cloudflare Turnstile CAPTCHA** — blocks headless browsers at pje.tjes.jus.br
2. **Keycloak SSO** — sso.cloud.pje.jus.br/auth/realms/pje (client_id: pje-tjes-1g)
3. **MFA email OTP** — 5-minute expiry, sent to fbm*****@tjes.jus.br

Login URL:
`https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth?response_type=code&client_id=pje-tjes-1g&redirect_uri=https://pje.tjes.jus.br/pje/login.seam&login=true&scope=openid`

## Architecture: pje_session.py
- `interactive_login()`: opens non-headless browser → user solves CAPTCHA+MFA → saves `context.storage_state()` → `pje_session.json`
- `PJeSessionClient`: loads saved session → tries REST API first (`/api/v2/processos/{numero}/documentos`) → falls back to browser scraping
- CLI: `python pje_session.py login|test|download`
- Session file: `pje_session.json` in project root (gitignored)

## batch_downloader.py Integration
When MNI health_check fails:
- Auto-detects `pje_session.json`
- If exists: uses PJeSessionClient as drop-in replacement
- If not: error "Execute: python pje_session.py login"

## Credentials (GitHub Secrets)
- MNI_USERNAME = CPF (stored as GitHub Secret)
- MNI_PASSWORD = stored as GitHub Secret  
- DO NOT store raw credentials in memory

## Other Changes This Session
- MNI_PROXY env var added (config.py, mni_client.py, docker-compose.yml) — routes SOAP through proxy
- Worker init: keeps mni_client alive after startup health_check failure
- deploy.yml: writes .env from GitHub Secrets on each deploy

## Session Workflow (one-time setup)
```bash
python pje_session.py login   # local machine only (non-headless, solve CAPTCHA+MFA)
python pje_session.py test    # verify session valid
scp pje_session.json root@191.252.204.250:/opt/pje-download/
```
Session lasts days. Re-run `login` when expired.

## API Endpoints Discovered
- Documents list: `{PJE_BASE_URL}/api/v2/processos/{numero}/documentos`
- Document binary: `{PJE_BASE_URL}/api/v2/documentos/{id}/conteudo`
(v2 endpoints — may need to verify against actual PJe API response)
