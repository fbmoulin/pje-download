# Handoff 2026-11-16 (parte 2) — pendências analisadas, bytes exatos do MTOM e lote de prova

Continua `docs/handoff/2026-11-16-buraco-mni-xs-any.md`. Esta parte analisou as pendências abertas com subagents, achou e corrigiu um bug de integridade dos arquivos baixados (#124) e provou a correção em produção.

## Estado verificado
- `master` = `3aea984` (#124), em sincronia com `origin/master`. CI do #124 (Lint e Test, com Redis real) e Deploy `success` (medido com `gh run list --branch master`, `headSha` = `3aea9841935dcc7585e04a84c89e33fcd2b6fdd0`).
- Produção: dashboard e worker com `build_sha` `3aea9841…` (o agente do lote de prova leu `/healthz` e `/health` antes de disparar), worker `consuming`, `mni: healthy`. Redis healthy, fila vazia.
- Este handoff e a atualização do `TODO.md` entram em um PR só de `.md` (sem CI nem deploy).

## Commits da sessão (parte 2)
- #123 `29c5a13` — docs: limpa pendências desatualizadas (TODO, contagem de testes).
- #124 `3aea984` — fix(mni): bytes exatos dos anexos MTOM e checagem do hash declarado.

## O que foi descoberto
1. **Bug de integridade (corrigido, #124).** zeep 4.3.3, `zeep/wsdl/attachments.py`, `Attachment.content`: o ramo `binary` fazia `content.strip(b"\r\n")`. O TJES manda as partes MTOM com `Content-Transfer-Encoding: binary`, então todo arquivo que terminasse (ou começasse) em CR/LF chegava truncado. Medido antes: 57 de 66 PDFs do 5007796 com MD5 diferente do `hash` declarado (24 sem `\r\n`, 33 sem `\n`). O PDF abria igual (41 páginas, texto idêntico), mas o arquivo não era o do tribunal e o `sha256` da auditoria era do conteúdo truncado. Nosso código nunca lia o `hash`. Correção: `mni_client.install_exact_binary_attachments` (patch idempotente na importação) e `_save_document` compara o MD5 declarado (32 hex) com os bytes; divergência = não grava, `failed_ids`, evento `mni.download.hash_mismatch`, auditoria `error`/`hash_mismatch`. Hash ausente ou de outro formato grava como antes e loga `mni.download.hash_unchecked`.
2. **Prova em produção** (build `3aea984`, lote `20261116_104133_aaa514`): 3 `done` (11/11, 16/16, 76/76), `hash_mismatch` 0, `hash_unchecked` 0, `saved` 103, `duplicate_skipped` 13, 0 WARNING/ERROR. No 5007796: PDFs 66/66 com MD5 certo (antes 9/66), HTML 9/9, mp4 1/1. Os 3 PDFs agora têm 471154, 220978 e 3005636 bytes.
3. **Divergência de páginas (explicada, não é bug nosso).** 94383092 (41 x 4), 94383093 (10 x 1), 94383095 (17 x 5): cada um é um PDF único, sem páginas repetidas; 41 = 4+37, 10 = 1+9, 17 = 5+12; o miolo traz rodapés do PJe de outros documentos.
4. **Os 5 documentos "não entregues" (hipótese NÃO verificada).** Os rodapés do miolo dos 3 PDFs citam exatamente os ids 25340922, 93643718, 28967649, 28967652 e 28968353 (37+9+3+7+2 = 58 páginas, o total estimado). A sonda no MNI real mostrou que o cabeçalho do 5007796 não tem `processoVinculado`, `outrosnumeros` nem referência a origem ou migração; a rota "consultar o processo de origem" não existe para este processo. Falta a conferência humana no PJe web.
5. **Lote de verificação do #120/#121** (build `60e8fe8`, lote `20261116_100938_e15c9e`): 3 `done`, 176 s, contagens iguais à linha de base, `audit` em `/api/status`. Experimento com `MNI_TIMEOUT=2` (lote `20261116_101850_787e67`): `timeout` 1, `batch_failed` 1, `batch_retry_individual` 1, 11 `saved`: a nova tentativa funciona no MNI real. Restaurado: `MNI_TIMEOUT` voltou a 60.
6. **Redis (medido):** `volatile-lru`, `evicted_keys` 0, 1,4 MB de 96 MB, fila e `processing` vazios, dead-letter com 61 entradas `invalid_json` (smoke dos deploys). `.env` da VPS em `600 deploy`. Tailscale instalado, deslogado e inativo. A linha restrita do watchdog NÃO está no `authorized_keys`.

## Em aberto
1. **Conferência humana (dono):** no PJe web, comparar as páginas do miolo dos 3 PDFs com os 5 documentos listados. Se bater, não há lacuna de entrega. Usar o artefato "Conferência de Autos" (https://claude.ai/artifact/X3YbmH3khVbS8sexA1ak12) com os arquivos novos do lote `20261116_104133_aaa514`; o comparador visual não foi rodado de novo.
2. **Ramo `failed`/`partial` do #120/#121 não exercitado em produção** (a nova tentativa recuperou tudo): só testes com mocks. Anomalia menor: `current_batch` do `/healthz` não volta a `null` depois de `done`.
3. **Risco do #124:** o MD5 foi comparado em 3 processos de teste. Se alguma classe de documento tiver hash calculado de outro modo, aparece como `hash_mismatch` (processo `failed`, o que foi salvo fica). Nada do tipo em 103 documentos.
4. **Só o dono:** `sudo snap remove tailscale` na VPS; instalar o watchdog (`docs/ops/hermes-watchdog.md`); rotacionar a `REDIS_PASSWORD` (apareceu em claro no transcript de um subagente; risco baixo, adiado por decisão dele) e, no mesmo passo, considerar a chave SSH de deploy e a `DASHBOARD_API_KEY`; decidir o sink de auditoria (hoje a rotação apaga logs com mais de 120 dias sem cópia); confirmar se o openclaw foi desligado para remover `ops/monitoring/`.
5. **Efeito colateral na VPS:** um subagente baixou a imagem `alpine` ao medir o volume (container removido; a imagem pode ter ficado).

## Prompt de retomada
Leia `docs/handoff/2026-11-16-pendencias-bytes-exatos.md`, depois `TODO.md` (seção "Aberto agora") e a memória `mni-tjes-omite-documentos`. Antes de confiar neste handoff, meça de novo: `git status -sb`, `gh run list --branch master --limit 5` e `ssh pje-vps "curl -s localhost:8007/healthz"` (o `build_sha` esperado é o do último merge de código, `3aea984`, ou mais novo). Pergunte ao dono se a conferência dos 3 PDFs no PJe web já foi feita. Não rode lote nem reinicie nada sem autorização.
