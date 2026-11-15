# Handoff 2026-11-15 — aceite operacional TJES e download de anexos pelo MNI

## Estado verificado (medido em 2026-11-15)

- Repo: `master` limpa e em dia com `origin/master`, `465b2f1` (`#108`).
- Produção: `build_sha` do worker e do dashboard = `465b2f1ec9d3e5ae06c899a357689cbad3ac0c0e`
  (`curl localhost:8006/health | jq -r .build_sha` na VPS). Worker `consuming`/`ready`, contêineres saudáveis.
- CI e deploy da master: `d474025` (#109) e `465b2f1` (#108), ambos `success`.
- PRs: #109 mergeado por Felipe a pedido (squash `d474025`); #108 mergeado por Felipe às 02:49:48Z
  (durante o aceite; o deploy dele terminou depois dos testes de Task 6); #102 (dependabot) **aberto**, sem merge.

## Commits desta sessão

- `d474025` — fix(mni): baixa os anexos por ID em vez de deixá-los pendentes (#109).
- `465b2f1` — fix(prompt-gate): JSON aninhado demais e último registro sem fim de linha (#108, mergeado por Felipe).
- Este PR de documentação (`TODO.md` + este arquivo; `.md` não roda CI nem deploy).

## O que foi descoberto

O MNI do TJES **entrega o binário de um anexo** (documento vinculado) quando o ID dele vai em `documento`,
aninhado em `vinculados` do pai. O comentário antigo em `mni_client.py` ("MNI não retorna conteúdo de
vinculados; usar Playwright") estava errado; por isso todo processo com anexos terminava `partial`.

Sonda somente leitura (processo com 19 principais + 62 anexos): anexo por ID volta com PDF; pedir o pai
traz os anexos listados, sem conteúdo.

## Aceite operacional (evidência)

| Item | Resultado |
|---|---|
| Chromium na imagem atual | `156.0.8078.4` (imagem `5f0c757` e de novo `d474025`) |
| Lote de 3 processos públicos, antes do #109 | `partial` nos três (2, 10 e 62 anexos "pendentes") |
| Mesmo lote, depois do #109 | `done` nos três; `wall_s` 159,7; máximo 143,2 s; arquivos 9 / 69 / 14 |
| Task 6 (B) kill forçado | `job_redelivered attempt=1`, mesmo `job_id`, lote `done=1 failed=0`, sem "Worker timeout" |
| Task 6 (A) restart gracioso | 43,6 s (< grace de 60 s), `exit=0`, boot limpo, lote `done=1 failed=0` |

Comparação com o PDF do PJe web (só contagens; o conteúdo não foi lido):

| Processo (final) | PDF do PJe | Worker: PDFs (páginas aprox.) | Arquivos |
|---|---|---|---|
| `491-30` | 48 págs | 4 PDFs (~32) + 5 HTML | 9 |
| `796-14` | 300 págs | 59 PDFs (~228) + 9 HTML + 1 `.bin` | 69 |
| `644-95` | 184 págs | 12 PDFs (~162) + 2 HTML | 14 |

A diferença bate com o que o PJe inclui no PDF dele (capa, despachos e certidões renderizados em HTML),
mas **isso não prova equivalência**. O `.bin` do `796-14` é um vídeo de WhatsApp sem mimetype informado.

## O que NÃO foi verificado (risco)

- Equivalência de conteúdo entre os arquivos do worker e o PDF do PJe: depende do dono.
- Pico de memória do worker durante um job (só foi medido em repouso: 197 MiB de 512 MiB).
- Lote grande (muitos processos com muitos anexos) contra `BATCH_MAX_DURATION_SECS=3600`.
- Restart gracioso com mais de ~60 s de job pela frente (deve virar reentrega; não testado).
- Em (A) os logs do worker foram zerados pelo `restart`; a ausência de reentrega foi inferida do boot limpo
  (sem `job_received`/`job_redelivered`) e da fila vazia.
- Um trecho de log do worker impresso nesta sessão (para diagnosticar a espera do kill) mostrou nomes de
  arquivos de anexos, que incluem nomes de partes de um processo público. Nada foi gravado em arquivo.

## Itens em aberto

Estão no `TODO.md` ("Pendências do pre-mortem do #109", "Conferência do dono", "#102"). Resumo e próximo passo:

1. Dono confere o conteúdo de alguns arquivos do lote contra o PJe web.
2. Medir um lote maior (tempo e pico de memória: `docker stats` durante o job) antes de mexer em timeouts.
3. #102: atualizar a branch, esperar o CI, mergear com autorização, repetir o teste do Chromium.
4. Corrigir a mensagem do ramo "sem sessão" e o tratamento de `.bin`/mimetype ausente, se o dono quiser.
5. Marcar a Task 6 em `docs/specs/2026-10-16-job-perdido-no-deploy.md`.
6. Relatórios antigos `queued`/`running` na VPS (4 órfãos de antes do #78): decidir se limpa.

## Incidente registrado

Uma sonda anterior desta sessão imprimiu um número de processo (sigiloso) no chat porque os logs do
cliente MNI vêm sem máscara. A sonda passou a descartar os eventos do structlog. O dono depois liberou
que números de processos podem aparecer no chat.

## Prompt de retomada

```
Leia docs/handoff/2026-11-15-aceite-tjes-anexos.md e TODO.md. Re-meça antes de confiar:
git status, gh run list --branch master --limit 3, e na VPS (ssh pje-vps) o build_sha do worker.
O aceite operacional TJES (Chromium, lote de 3, Task 6) está feito e o #109 baixa anexos pelo MNI.
Próximo: (1) pedir ao dono a conferência de conteúdo; (2) medir um lote maior com docker stats;
(3) tratar o #102 só com autorização explícita. Não leia arquivos de downloads/ nem /data/.
```
