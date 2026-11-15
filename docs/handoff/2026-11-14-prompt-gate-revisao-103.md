# Handoff — revisão do merge do #103 no prompt gate (14/11/2026)

Sessão no Mac mini, em `/Users/felipemoulin/pje-download`. Tudo abaixo foi medido
nesta sessão com o comando indicado, salvo o que está marcado `NÃO VERIFICADO`.

## 1. Estado verificado

- `git status -sb` em `master`, antes deste handoff: `## master...origin/master`, limpo,
  nem à frente nem atrás.
- `master` = `5f0c7577e08ce098221ec889cad5afc20128fec1`.
- Este arquivo e uma linha do `HANDOFF.md` estão no branch
  `docs/handoff-prompt-gate-revisao-103` (só `.md`).

## 2. Commits desta sessão

| SHA | PR | O que fez |
|---|---|---|
| `219696d` | #105 | Docs: restaura no `CLAUDE.md` a instrução de rodar `pytest` antes de commit e o comando do Redis local; documenta o reparo manual de `EVIDENCIA_INCOMPLETA`; adendo pós-merge na SPEC-D2-001 |
| `5f0c757` | #106 | Código: quatro correções em `tools/prompt_gate.py`, com testes, pré-mortem e CHANGELOG |

O #104 (`68b5507`) entrou na `master` por outra sessão enquanto esta rodava e já
corrigia o estado do PR103 nos documentos; o #105 foi reconciliado com ele.

### O que o #106 mudou

- O leitor estrito de `--report`/`--label` separa registros só em `\n`. Antes, um
  campo com U+2028, U+2029 ou U+0085 gravado pelo próprio hook terminava em
  `EVIDENCIA_INCOMPLETA`.
- O aviso mostra um comando `--label <id> <regra>` por regra, **sem o rótulo**.
  Acrescenta-se `tp` ou `fp`; colado sem editar, cai no erro de uso e não grava.
  O plano aprovado previa `tp` pronto; o pré-mortem
  (`.premortems/PREMORTEM-2026-11-14T08-29-13Z.md`) mostrou que isso enviesaria a
  evidência do D2, e a entrega mudou por isso.
- `--label` recusado não quebra mais em Python anterior ao 3.11.
- `_ler_jsonl`, sem uso, foi removido.

### Decisões do Felipe nesta sessão (14/11/2026)

Manter, sem mudar código: linha ruim bloqueia `--report` e `--label` (reparo manual
documentado no `CLAUDE.md`, seção "Prompt gate"); colisão de id de 8 hex é erro de
integridade; elegibilidade continua "≥60 rotulados e ≤2 FP". Registro: adendo de
14/11 em `docs/specs/2026-11-14-d2-rotulagem-telemetria.md`.

## 3. CI

| Run | Commit | Resultado |
|---|---|---|
| CI #243 (43576227056), `pull_request` | `9d3046d`, head do #106 | sucesso; **1338 passed, 1 warning**, sem skips |
| CI #244 (43576449728), `push` | `5f0c757` = HEAD da `master` | sucesso; **1338 passed, 1 warning**, sem skips |

Comando: `gh run view <id> --json headSha,conclusion` e `gh run view <id> --log`.
Local, sem Redis nesta máquina: 1324 passed, 14 skipped (os testes de socket real),
1 warning. O #105 é só `.md` e não rodou CI.

## 4. Produção

- Deploy #134 (43576637427), `workflow_run`, `headSha` `5f0c757`: sucesso. Passos
  `Sync files to VPS`, `Write .env on VPS`, `Restart services` e
  `Smoke-test MNI credentials` com `success`.
- `tools/prompt_gate.py` é hook local do Claude Code e CLI manual. O #106 não mudou
  nenhum arquivo do worker nem da dashboard.
- `NÃO VERIFICADO`: `build_sha` no `/health` do worker e no `/healthz` da dashboard,
  e o estado dos containers. Esta sessão não acessou a VPS; a evidência é só o
  resultado do workflow.

## 5. Itens em aberto

1. **Conferir o aviso numa sessão interativa.** `NÃO VERIFICADO`: como o Claude Code
   exibe um `systemMessage` de várias linhas. Próximo passo: abrir sessão nova na
   raiz do repo, mandar um primeiro prompt vago e ver se cada comando `--label`
   aparece em linha própria e copia inteiro.
2. **Aviso pendente de rótulo na fonte real do Mac.** `python3 tools/prompt_gate.py
   --report` às 08:37 UTC: telemetria válida, 2 prompts avaliados, 1 aviso pendente
   (`alvo_ou_sintoma` e `possui_dod` do mesmo evento de 10/10), `rotulos.jsonl`
   ausente. Rotular é opcional: o D2 está encerrado em advisory.
3. **Dois achados baixos do pré-mortem, não corrigidos** (já existiam antes do
   #106): linha de JSON muito aninhado derruba o `--report` com `RecursionError`;
   último registro sem `\n` final é aceito e o append seguinte cola dois eventos.
   Só edição manual do arquivo produz os dois. Próximo passo: nenhum, salvo se
   acontecer; cai no reparo manual.
4. **PR #102 (dependabot, python-deps) aberto.** Não foi tocado nesta sessão.
   Próximo passo: atualizar o branch contra a `master` e rodar a suíte com Redis
   real antes de decidir (`CLAUDE.md`, seção de manutenção).
5. **Aceite operacional do TJES** segue como no `HANDOFF.md`: teste de lançamento do
   Chromium na imagem atual, lote pequeno, e só depois a Task 6. Nada disso foi
   executado nesta sessão.

## 6. Prompt de retomada

```text
Retome o pje-download em /Users/felipemoulin/pje-download. Leia primeiro, nesta ordem: HANDOFF.md, docs/handoff/2026-11-14-prompt-gate-revisao-103.md, TODO.md e CLAUDE.md (seções Scope e Prompt gate). Antes de agir, meça de novo: git status -sb, git log --oneline -3, gh run list --limit 5 e gh pr list. O handoff afirma master em 5f0c757 (#106), CI #244 com 1338 passed sem skips e deploy #134 com sucesso; confirme em vez de confiar.

O prompt gate está fechado: D2 em advisory, leitura estrita mantida, reparo manual documentado. Não reabra essas decisões. Pendências dele: conferir numa sessão interativa se o aviso de várias linhas aparece com um comando --label por linha, e o PR #102 do dependabot.

O trabalho principal continua sendo o aceite operacional do TJES descrito no HANDOFF.md: Chromium na imagem atual (Passo 4 do guia do dono), lote pequeno (Passo 2) e depois a Task 6. Guie o Felipe um comando por vez; não presuma acesso à VPS nem execute teste destrutivo, deploy ou mudança de código sem pedido.
```
