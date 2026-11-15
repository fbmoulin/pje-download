# SDD — Retenção dos downloads na VPS

## Goal

Apagar automaticamente os arquivos baixados de lotes já concluídos depois de N dias, para que
`/data/downloads` não cresça sem limite e para que documentos de partes não fiquem guardados
na VPS além do necessário. O `_report.json` de cada lote (que o histórico do dashboard lê) e a
trilha de auditoria (`/data/audit`, CNJ 615/2025) **não** são tocados. Nenhum dado real de
autos entra em código, fixtures, documentação ou mensagens de commit.

## USER VALIDATION GATE

O dono decidiu em 2026-11-15, no chat: "os arquivos depois de analisados não precisam ser
armazenados" e aprovou a **opção A (limpeza por idade)** proposta pelo assistente, com 14
dias de prazo padrão e `0` desativando. O dono não respondeu ainda se copia os arquivos por
`scp`; o prazo é configurável por ambiente. Como a deleção é irreversível, o pre-mortem do plano
(REFINE) exigiu **dry-run ligado por padrão no primeiro deploy**: o laço só registra o que
apagaria; virar o padrão para deleção real é um segundo PR de uma linha, com merge autorizado
pelo dono depois de ele ver o log. Nenhum merge sem autorização.

## Medição (2026-11-15, VPS)

| Fato | Valor medido |
|---|---|
| Disco do host | 12 GB usados de 96 GB |
| `/data/downloads` | 219 MB em 14 lotes; cinco lotes de teste de 37–46 MB somam ~212 MB |
| `/data/audit` | 332 KB, com rotação própria de 120 dias |
| Quem apaga arquivos de download hoje | ninguém (`audit.rotate_logs` só trata `audit-*.jsonl`) |
| Quem lê `_report.json` | `DashboardState._load_history`, `glob("*/_report.json")`; `_progress.json` é lido por `_load_active_batch`; `_active_batch.json` fica na raiz de `/data/downloads` |
| Formato do id do lote | `strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]` (`dashboard_api.py` `submit_batch`) |
| Onde o worker grava | `DOWNLOAD_BASE_DIR/<outputSubdir>`, com `outputSubdir = <lote>/<processo>` quando vem do dashboard; sem `outputSubdir` cai em `DOWNLOAD_BASE_DIR/<processo>` (jobs diretos na fila) |
| Volume | dashboard e worker, ambos `downloads_data:/data`, mesmo usuário (appuser) |
| Primeiro passe com 14 dias (medido 2026-11-15 ~05:06 UTC) | 13 lotes com nome de lote e relatório válido; **6 passam do corte**: quatro de 2026-07-18 (1, 1, 2 e 2 MB, todos `failed`) e dois de 2026-10-18 (1 MB `failed` e 2 MB `done`), ~9 MB no total. Os 7 restantes (2026-11-14 e 2026-11-15) ficam. Fora do padrão na raiz: só `_logs`. Relatório e `completed_at` concordam em todos |

## writing-plans

Os nomes writing-plans, subagent-driven-development e plan-quality-gate atendem ao verificador
estrutural do repositório (`tools/verify_spec.py`); esses pacotes não estão disponíveis como
skills nesta sessão e não se declara sua execução. O desenho foi feito por leitura do código e
revisado por pre-mortem de plano (REFINE; as emendas estão incorporadas abaixo).

Regras da limpeza (módulo novo `retention.py`, função pura `purge_old_downloads`):

| Regra | Decisão |
|---|---|
| Alvo | só subpastas diretas de `/data/downloads` cujo nome case `fullmatch` com `\d{8}_\d{6}_[0-9a-f]{6}` (`re.ASCII`). Qualquer outra entrada (`_logs`, pastas `<processo>` de jobs diretos na fila, nomes estranhos) **não é apagada** e é contada em `unmatched_dirs`; ficam **fora do escopo** e visíveis no log |
| Só lotes concluídos | o `_report.json` precisa existir, ser um objeto JSON e ter `status` string em `done`, `failed` ou `partial`. Ausente, ilegível, não-objeto, `status` de outro tipo ou outro valor → intacto (`skipped_active` conta `queued`/`running`) |
| Idade | só apaga se **as duas** medidas passam de N dias: `agora − mtime(_report.json)` e `agora − completed_at` do relatório (quando parseável). `mtime` no futuro conta como "não velho". N mínimo 1 dia |
| Veto de frescor | se **qualquer** arquivo que não começa com `_` dentro do lote tem mtime mais novo que o corte, o lote inteiro é pulado (protege worker ainda gravando depois de um timeout e job reentregue) |
| Lote em andamento | `protect = {current_batch_id} ∪ {lotes em queued/running em memória}`, tirado logo antes do `to_thread`; ausência de relatório terminal já protege por si só |
| O que apaga | dentro do lote: **todo diretório, qualquer que seja o nome** (inclusive `_x`), e todo arquivo que não comece com `_`. Só **arquivos regulares** na raiz do lote cujo nome comeca com `_` (`_report.json`, `_progress.json`, `_purged.json`) ficam. Symlink na raiz do lote é removido como link, nunca seguido |
| Segurança de caminho | o lote e cada alvo precisam resolver para dentro do diretório base; lote que é symlink é ignorado |
| Idempotência | lote sem nenhuma entrada apagável é ignorado; reexecutar não faz nada |
| Marcador | gravado **antes** da primeira remoção com `state: started` e reescrito com `state: done` ao fim (`purged_at`, `bytes_freed`): um passe interrompido por deploy deixa `started` como evidência |
| Dry-run | `DOWNLOAD_RETENTION_DRY_RUN` (padrão **true** no #113; **false** a partir deste PR, autorizado pelo dono depois do dry-run em produção): seleciona e conta, mas **não apaga nem grava o marcador** (ele afirmaria uma limpeza que não houve); só registra contagens |
| Log | uma linha `dashboard.retention.complete` com `candidates`, `purged`, `bytes_freed`, `errors`, `skipped_active`, `unmatched_dirs`, `dry_run`, `retention_days`; nunca nomes de arquivo ou de processo. Mais uma linha por lote candidato (`dashboard.retention.would_purge` em dry-run, `...purging` na deleção real) só com `batch_id` (data+hora+hex), `status`, `age_days` e `bytes`, para conferir a seleção contra a lista medida. `errors > 0` sobe para WARNING com o tipo da primeira exceção |
| Falha | erro por lote capturado (a validação do relatório fica dentro do `try`); o laço segue com o próximo e nunca derruba o dashboard |

Configuração (fail-safe, a deleção é irreversível): `DOWNLOAD_RETENTION_DAYS` padrão 14;
`0`, negativo ou **inválido desativa** a limpeza e registra um ERROR com o nome da variável (nunca o
valor); `DOWNLOAD_RETENTION_INTERVAL_SECS` padrão 86400, mínimo 60; `DOWNLOAD_RETENTION_DRY_RUN`
padrão `false` a partir do PR que liga a deleção real (era `true` no #113); qualquer valor que não seja o literal `false` mantém o dry-run. As variáveis têm padrão em `config.py`; o `.env` que `deploy.yml`
reescreve a cada deploy não as define, então mudar o padrão exige PR (ou uma entrada
`environment:` no compose). O laço roda **uma vez no início** (depois de `resume_active_batch`) e a cada intervalo; não
dorme primeiro, porque deploys mais frequentes que o intervalo impediriam qualquer execução.

### Task 1 — Função pura e testes (TDD)

Escrever primeiro `tests/test_download_retention.py`, vendo-os falhar: apaga pasta de processo
velha e mantém `_report.json`; não toca lote novo, `running`/`queued`, sem relatório, relatório
ilegível, relatório JSON que não é objeto, `status` como lista ou dicionário (não pode levantar
`TypeError` fora do `try`), `_logs`, nome fora do padrão (`20251115_x`, nome com quebra de linha
final), pasta de processo `_x` dentro do lote (é apagada), arquivos `_*` regulares (ficam),
symlink de lote ou de entrada (não segue), `mtime` no futuro, só uma das duas idades velha, arquivo
novo dentro do lote vetando a limpeza, lote em `protect`, idempotência, dry-run (nada some e
nenhum marcador é gravado), `max_days` inválido, contagens e bytes devolvidos. Teste de mutação: remover a
exigência de `_report.json` terminal precisa quebrar o teste do lote `running`. Implementar em
`retention.py`.

### Task 2 — Configuração e laço no dashboard (TDD)

Constantes em `config.py` (com o fail-safe), laço `_download_retention_loop` em
`dashboard_api.py` (`asyncio.to_thread`, exceção capturada por tipo, cancelável), registrado em
`_on_startup` e cancelado em `_on_cleanup`, no molde de `_audit_rotation_loop`. Testes: executa
uma vez ao iniciar sem esperar o intervalo; configuração desativada não cria a tarefa; falha não
derruba o laço; o lote atual entra em `protect`; a linha de conclusão é logada com os campos acima.

### Task 3 — Documentação, evidência e verificação

A evidência do que o primeiro passe selecionaria já está na tabela de medição (feita na VPS, só leitura, por lote: `status`, mtime do relatório, `completed_at` e tamanho); repetir a medição logo antes de virar o padrão para deleção real.
Atualizar `CLAUDE.md` (retenção, dry-run, `_purged.json`, jobs diretos fora do escopo, e que a
UI/relatório de um lote limpo ainda mostra `docs_baixados` e `tamanho_bytes` de arquivos que não
existem mais), `TODO.md` e o handoff. Rodar `ruff 0.14.14` no diretório,
`python tools/verify_spec.py docs/specs/*.md` e a suíte completa; confirmar o CI. Fazer frequent
commits pequenos, um por tarefa, com a evidência no corpo.

## Riscos e não-objetivos

A deleção é irreversível; por isso o padrão é dry-run. Pastas de jobs diretos na fila
(`<processo>` na raiz) e `_logs` **não** são limpas: a meta de privacidade só vale para lotes do
dashboard, e `unmatched_dirs` torna o resto visível no log. Um lote reentregue ou retomado cuja pasta
foi limpa simplesmente a recria; nada trata a pasta antiga como "já baixada". Não-objetivos:
limpar `/data/audit` (retenção própria), apagar o histórico (`_report.json`), baixar-e-apagar
(opção C) e qualquer limpeza no worker.

## References

`dashboard_api.py` (`submit_batch`, `_load_history`, `_load_active_batch`, `_audit_rotation_loop`,
`_on_startup`), `worker.py` (`_resolve_output_dir`), `audit.py` (`rotate_logs`),
`docker-compose.yml` (volumes), `CLAUDE.md` (Audit Sync, Test Patterns),
`docs/handoff/2026-11-15-aceite-tjes-anexos.md`, `TODO.md` (item de retenção).

## Evidência do dry-run em produção (2026-11-15, build `528ff5b`)

O primeiro passe registrou `candidates=6 purged=0 errors=0 skipped_active=0 unmatched_dirs=1
dry_run=True bytes_freed=4799795` e seis linhas `would_purge`: quatro lotes de 2026-07-18 (todos
`failed`, 0, 0, 1,7 e 1,7 MB) e dois de 2026-10-18 (um `failed` com 0 e um `done` com 1,4 MB). Bate
exatamente com a seleção medida antes (as pastas dos processos continuavam no disco e nenhum
`_purged.json` foi criado). Os ~9 MB estimados antes vinham do `du`, que conta também o relatório e
a sobrecarga de blocos; o que seria apagado são 4,8 MB.
