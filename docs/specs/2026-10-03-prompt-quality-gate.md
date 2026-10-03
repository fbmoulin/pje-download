# Prompt Quality Gate para Claude Code (piloto no pje-download)

**Status:** proposta — aguardando o USER VALIDATION GATE. Nenhum código escrito.
**Origem:** review do relatório "JEV + Claude Code + LangGraph"
(`docs/research/2026-10-03-review-jev-claude-code-langgraph.md`).
**Alvo:** `tools/prompt_gate.py` (novo), `.claude/settings.json` (novo),
`.claude/skills/refine-prompt/SKILL.md` (novo), `tests/test_prompt_gate.py` (novo).

## Goal

Antes de o Claude Code começar a trabalhar neste repo, filtrar prompts **vagos** e prompts
com **PII real** (CPF/CNPJ válidos), usando os mecanismos nativos do Claude Code (hooks
`UserPromptSubmit` e `Stop`), sem enviar o prompt a nenhum processador novo. E, no fim da
tarefa, cobrar o *definition of done* que o gate exigiu no início.

Sucesso mensurável:
- Falso bloqueio < 5% num conjunto rotulado de ≥ 40 prompts reais (follow-ups incluídos).
- Latência adicionada pelo hook determinístico < 100 ms (p95).
- Zero prompts com CPF/CNPJ válido chegando ao modelo.

## Non-goals

| Fora de escopo | Motivo |
|---|---|
| Usar o JEV/TypeSafe neste repo | Sem ZDR/DPA cobrindo LGPD; API ainda alpha e diferente da descrita (ver review §1) |
| Wrapper em volta de `claude -p` | Não cobre o modo interativo; hooks nativos cobrem os dois |
| LangGraph no pje-download | O repo não usa LLM; o padrão LangGraph corrigido fica para o kratos-case-pipeline (review §3.2) |
| Reescrever o prompt automaticamente | Hook `UserPromptSubmit` só pode bloquear ou anexar contexto; reescrita fica na skill `/refine-prompt`, sob comando do usuário |
| Classificador local (central-ollama) | Fase 2, depende dos dados de telemetria desta fase |

## Arquitetura

```
prompt ──▶ [T0] tools/prompt_gate.py  (command hook, local, ~ms)
            │  isenções: slash command, pergunta, follow-up curto, prefixo "!!"
            │  PII: CPF/CNPJ válido (pontuado ou nu)  ──▶ exit 2 (bloqueia SEMPRE)
            │  heurísticas: arquivo|sintoma, DoD, multi-tarefa
            │     modo advisory ──▶ exit 0 + additionalContext (aviso ao modelo)
            │     modo block    ──▶ exit 2 + motivo + dica "/refine-prompt"
            ▼
         [T1] (opcional, decisão D1) hook type:"prompt" (Haiku) — ambiguidade semântica
            ▼
         Claude Code trabalha
            ▼
         [Stop] tools/prompt_gate.py --stop: houve .py alterado? ruff + pytest focado
                 falhou ──▶ {"decision":"block","reason":...} (respeita stop_hook_active)
```

Contrato do hook, **verificado empiricamente** com o CLI 2.1.288 (review §4): stdin é JSON
com `prompt`, `cwd`, `session_id`, `transcript_path`, `permission_mode`,
`hook_event_name`; `exit 2` aborta o turno com `num_turns: 0`; hook `type: "prompt"` com
Haiku bloqueou um prompt vago e deixou passar um específico.

### Regras (revisadas a partir das 4 regras do JEV)

| Regra | Origem | Tipo | Ação |
|---|---|---|---|
| `pii_valida` — CPF/CNPJ com dígito verificador válido | nova | determinística | **bloqueia** sempre |
| `objetivo_claro` | JEV regra 1 | T1 (Haiku) | aviso/bloqueio conforme D2 |
| `alvo_ou_sintoma` — cita caminho existente **ou** sintoma/erro reproduzível | JEV regra 2, afrouxada | determinística | aviso |
| `possui_dod` — "pronto quando", "deve passar", `pytest`, "aceite", etc. | JEV regra 3 | determinística | aviso; cobrado no Stop |
| `multi_tarefa` — ≥ 3 verbos imperativos/itens numerados | JEV regra 4 | determinística | aviso sugerindo plano |
| isenções: `/cmd`, termina em `?`, ≤ 6 palavras, prefixo `!!` | nova | determinística | passa direto |

## Agents e skills — quem faz o quê

| Etapa | Mecanismo | Uso |
|---|---|---|
| Planejamento | skill `writing-plans` | Expandir esta spec em plano passo-a-passo (o plano de Task abaixo é o esqueleto) |
| Revisão do plano | skill `plan-quality-gate` (ou `plan-review-cycle` do claude-skills, se a primeira não estiver instalada) | Antes da execução |
| Execução | skill `subagent-driven-development` | Tasks 1–3 são independentes (arquivos distintos) → 1 subagente `general-purpose` por task em **worktree** isolada; Tasks 4–6 sequenciais, inline |
| Medição do T1 | subagente `general-purpose` | Roda o conjunto rotulado contra o hook `type:"prompt"` via `claude -p --settings` e devolve a matriz de confusão |
| Revisão | skills `/code-review` e `/security-review` | No diff final; foco em escape de PII em logs e em `subprocess` |
| Uso diário | skill nova `/refine-prompt` | Reescreve um prompt bloqueado no template Objetivo / Alvo / Definition of Done / Fora de escopo |
| Agentes existentes | `cascade-worker`, `soap-downloader` | Não mudam; o gate os beneficia (prompts chegam com arquivo + DoD) |

## USER VALIDATION GATE

Nada é implementado antes destas decisões:

| # | Decisão | Opções | Recomendação |
|---|---|---|---|
| D1 | Motor do T1 (ambiguidade semântica) | A) hook `type:"prompt"` Haiku · B) nenhum, só T0 · C) JEV via OpenRouter | **A** — zero processador novo; B se o custo/latência do Haiku incomodar |
| D2 | Modo inicial | advisory (só avisa) · block | **advisory** por 2 semanas, promover a block se falso bloqueio < 5% |
| D3 | Stop hook cobrando DoD | sim (ruff + pytest focado) · não | **sim**, limitado a testes dos arquivos tocados (suite inteira é lenta demais por turno) |
| D4 | Escopo | só pje-download · também `~/.claude` via claude-skills | **só pje-download** nesta fase; promover depois com dados |

## Tasks

TDD em todas: teste vermelho primeiro, depois implementação. Commits pequenos e
frequentes — um por task, no mínimo.

### Task 1 — Núcleo determinístico `tools/prompt_gate.py` (TDD)

Testes primeiro em `tests/test_prompt_gate.py`, chamando `avaliar(prompt, cwd) -> Resultado`
(função pura, só stdlib):
- isenções: `/review`, `por que X?`, `sim`, `pode seguir`, `!! qualquer coisa` → `passa`.
- `alvo_ou_sintoma`: `"corrija worker.py"` (arquivo existe no `cwd`) → ok;
  `"corrija o arquivo xyz.py"` (não existe) → aviso; `"o /health retorna 503"` → ok (sintoma).
- `possui_dod`: `"... pronto quando pytest tests/test_config.py passar"` → ok.
- `multi_tarefa`: lista numerada com 4 itens → aviso.
- Fixture com 40 prompts rotulados em `tests/fixtures/prompt_gate_cases.jsonl` (sem PII
  real; CPFs de teste gerados com dígito válido ficam só no fixture, que já é caminho
  permitido em `validate_br_pii.CAMINHOS_PERMITIDOS`).
Commit.

### Task 2 — PII no prompt (TDD)

Reusar `tools/validate_br_pii.cpf_valido`/`cnpj_valido`. Acrescentar as formas
**pontuadas** (`NNN.NNN.NNN-DD`, `NN.NNN.NNN/NNNN-DD`), que hoje ficam com o gitleaks e
não se aplicam a prompts. Testes: CPF válido nu e pontuado → bloqueia; 11 dígitos com DV
inválido → passa; **número CNJ** (`NNNNNNN-DD.AAAA.J.TR.OOOO`) → passa (é rotina neste repo).
A mensagem de bloqueio usa `_mascara` — nunca ecoa a PII. Commit.

### Task 3 — Skill `/refine-prompt` (TDD leve)

`.claude/skills/refine-prompt/SKILL.md` com frontmatter `name`, `description`,
`allowed-tools: Read, Glob, Grep`. Lê o prompt, procura os arquivos citados, devolve o
prompt reescrito no template e **não executa** a tarefa. Teste: parsing do frontmatter
(campos obrigatórios presentes) em `tests/test_prompt_gate.py`. Commit.

### Task 4 — Ligar o hook `UserPromptSubmit` (TDD)

`main()` lê o JSON do stdin, chama `avaliar`, e:
- PII → stderr com motivo mascarado, `exit 2`;
- advisory → stdout `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"..."}}`, `exit 0`;
- erro interno → **fail-open** (`exit 0`, log), para o gate nunca travar o trabalho.
Testes via `subprocess` com stdin JSON real (formato capturado na review §4).
`.claude/settings.json` com o hook `command` (`timeout: 5`). Smoke manual:
`claude -p "<prompt>" --settings .claude/settings.json < /dev/null`. Commit.

### Task 5 — Telemetria sem PII (TDD)

Append em `.claude/prompt-gate.jsonl` (gitignored): `ts`, `sha256(prompt)[:12]`, regras
disparadas, decisão, latência. **Nunca o texto do prompt.** Teste garante que o texto não
aparece no arquivo. Script `tools/prompt_gate.py --report` resume taxa de aviso/bloqueio por
regra — insumo para D2 e para a Fase 2 (classificador local). Commit.

### Task 6 — Stop hook cobrando DoD (TDD, condicionado a D3)

`tools/prompt_gate.py --stop`: se `stop_hook_active` for true → `exit 0` (evita loop). Senão,
se `git diff --name-only` tem `.py`: `ruff check` nos arquivos + `pytest` nos testes
correspondentes (`tests/test_<modulo>.py` se existir). Falha → `{"decision":"block",
"reason":"<saída resumida>"}`. Testes com repositório git temporário. Commit.

### Task 7 — T1 Haiku + medição (condicionado a D1 = A)

Adicionar o hook `type:"prompt"` ao `settings.json`, com instrução explícita de aprovar
follow-ups, perguntas e slash commands. Subagente roda os 40 casos via `claude -p` e
reporta a matriz de confusão e o custo (`total_cost_usd`). Se falso bloqueio ≥ 5%, o T1
fica em advisory. Fora do CI (custa dinheiro e precisa de credenciais). Commit.

### Task 8 — Documentação

Seção "Prompt gate" no `CLAUDE.md`: como bypassar (`!!`), como ler o relatório, por que o
JEV não é usado. Rodar `python tools/verify_spec.py docs/specs/*.md`. Commit.

## Fase 2 (fora desta spec, depende da telemetria)

- **central-ollama:** treinar `lex-prompt-gate` 1.5B como o `lex-router`, com os casos
  rotulados + telemetria; gate go/no-go por `src/eval/gate.py`. Substitui o Haiku por
  inferência local.
- **claude-skills:** promover o hook a nível usuário (`~/.claude`), ao lado de
  `hooks/analysis-loop-detector.ts`.
- **kratos-case-pipeline / claude-agents:** gate antes de chamadas caras usando o padrão
  `interrupt()` + `Command(resume=)` (review §3.2) e o Claude Agent SDK em vez de
  `subprocess` de `claude -p`.

## Riscos

| Risco | Mitigação |
|---|---|
| Gate vira atrito e é desligado | Advisory primeiro; isenções de follow-up; bypass `!!` |
| Hook quebra e trava todo prompt | Fail-open + `timeout: 5` |
| Telemetria vira vazamento | Só hash + flags; teste garante |
| `settings.json` versionado afeta outros contribuidores | Repo de um mantenedor; documentar no CLAUDE.md |
| Contrato do hook muda em versões futuras do CLI | Teste da Task 4 usa o JSON real capturado; re-smoke ao atualizar o CLI |

## References

- Review: `docs/research/2026-10-03-review-jev-claude-code-langgraph.md`
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- Precedentes no ecossistema: `kratos-case-pipeline/src/langgraph/graph.py:51`
  (`router_confidence_gate`), `prompts-gold/scripts/propagate/guardrails.py:58`
  (`check_pre`), `legal-data-quality-gateway/ldqg/scoring.py:32` (`route_status`)
- PII: `tools/validate_br_pii.py`
- Spec anterior com o mesmo método data-first: `docs/specs/2026-09-29-phase2-sprint2-playwright-telemetry.md`
