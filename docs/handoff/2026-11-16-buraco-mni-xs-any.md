# Handoff 2026-11-16 — buraco do MNI (xs:any) e auditoria

## Estado verificado
- `master` = `60e8fe8` (#121). Produção: dashboard e worker com `build_sha` `60e8fe8c6401440479964b848384e0bfe77065c9`, worker `consuming`, `mni: healthy`, Redis healthy (medido com `curl` em `/healthz` e `/health` na VPS após o deploy). CI do #117, #118, #120 e #121: Lint e Test verdes antes do merge; deploys `success`. O #119 só mexe em `.md` (sem CI).

## Commits da sessão
- #117 `4e03346` — compose repassa `DOWNLOAD_RETENTION_*` ao dashboard; Redis `volatile-lru`; `X-API-Key` não-ASCII dá 401.
- #118 `749b9ec` — `mni_client.strip_documento_xs_any`: o `xs:any` do `tipoDocumento` no WSDL do TJES fazia o zeep engolir o 1º anexo de cada documento.
- #119 `61cd2b8` — documentação.
- #120 `1675da0` — nova tentativa por documento, `failed_ids` sem arquivo salvo, parser recursivo.
- #121 `60e8fe8` — relatório do dashboard mais honesto (total esperado, contadores em timeout, teto em relógio de parede, `audit` no status, `include_anexos` bool).

## Medido
- Lote real `20261116_054751_2e7fbc` (3 processos de teste, build `749b9ec`): 3 `done`, `wall_s` 183 s. 5007491: 11/11 documentos do PJe; 5019644: 16/16; 5007796: 76 arquivos, 13 duplicatas por checksum (de propósito) e **5 ids que o MNI não entrega** (25340922, 28967649, 28967652, 28968353, 93643718; ~58 páginas, ids anteriores ao ajuizamento).
- Não rodei de novo o lote depois do #120/#121; eles só têm testes com mocks.

## Em aberto
1. Os 5 documentos que o MNI não entrega: consultar o processo de origem pelo MNI ou usar a sessão PJe (dormente). Decisão do dono.
2. Divergência de páginas no 5007796 (94383092, 94383093, 94383095): ver `TODO.md`.
3. Medir o Redis na VPS (`evicted_keys`, memória).
4. Rodar um lote real depois do #120/#121 para ver a nova tentativa e os contadores em produção.
5. Os itens antigos do `TODO.md` (watchdog, rotação de chaves, etc.) seguem como estavam.

## Prompt de retomada
Leia `docs/handoff/2026-11-16-buraco-mni-xs-any.md`, `TODO.md` (seção "Aberto agora") e a nota de memória `mni-tjes-omite-documentos`. Meça de novo `git status`, `gh run list --branch master` e o `build_sha` em `/healthz` na VPS antes de confiar neste handoff. Depois pergunte ao dono qual caminho seguir nos 5 documentos que o MNI não entrega.
