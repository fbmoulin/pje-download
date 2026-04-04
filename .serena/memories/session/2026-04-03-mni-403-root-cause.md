# MNI 403 Root Cause — 2026-04-03

## Status
UNRESOLVED (infrastructure block, not code bug)

## Root Cause
VPS IP (191.252.204.250) is blocked by pje.tjes.jus.br at the HTTP level.
- From local/residential IP: WSDL GET → HTTP 200 ✓ (confirmed via curl + Playwright)
- From VPS (cloud hosting): WSDL GET → HTTP 403 ✗
- Brazilian government servers commonly block cloud provider IP ranges (Vultr/DO/etc.)

## WSDL URL
`https://pje.tjes.jus.br/pje/intercomunicacao?wsdl` — confirmed correct, returns valid WSDL XML.
Service endpoint: `https://pje.tjes.jus.br/pje/intercomunicacao` (POST for SOAP calls).
Docstring discrepancy (`sistemas.tjes.jus.br`) is stale — code URL is correct.

## Credentials
- Were empty on VPS (docker-compose used `${MNI_USERNAME:-}` with no .env)
- Fixed: GitHub Secrets MNI_USERNAME + MNI_PASSWORD set (CPF + password)
- deploy.yml now writes .env from secrets on every deploy (commit 73cb682)
- Even with credentials, 403 persists because block is at WSDL fetch level (before auth)

## Code Changes Made This Session
- commit 16f84d1: classify 403/Forbidden as auth_failed, clean error message
- commit 2e3f96b: add Chrome UA (later reverted — not the fix)
- commit f732c32: revert UA change
- commit 73cb682: deploy.yml writes .env from GitHub Secrets
- worker.py: keep mni_client alive after startup health_check failure (don't set to None)
- 73 tests pass

## Solutions
1. **Run locally** (recommended): `python dashboard_api.py --port 8007 --output ./downloads`
2. **Residential proxy**: set `HTTPS_PROXY=socks5://user:pass@ip:port` in .env on VPS
   - requests library reads HTTPS_PROXY natively, zeep inherits it — no code change needed
3. **Contact tribunal**: request whitelist of VPS IP

## What Was NOT the Issue
- WSDL URL (correct)
- User-Agent (not filtered)  
- zeep client configuration
- Credentials format
