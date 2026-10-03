# Prompt Quality Gate para Claude Code (piloto no pje-download)

**Status:** **v2**, revisada após o premortem `deep`
(`.premortems/PREMORTEM-2026-10-03T14-58-00Z.md`, veredito REWORK). Decisões vigentes:
D1=**B** (só T0 determinístico; o usuário escolheu a opção "a" em 2026-10-03, revendo o
D1=A anterior), D2=advisory, D3=sim, D4=só pje-download. D5 pendente. Nenhum código escrito.
**Origem:** `docs/research/2026-10-03-review-jev-claude-code-langgraph.md`.
**Alvo:** `tools/prompt_gate.py`, `.claude/settings.json`,
`.claude/skills/refine-prompt/SKILL.md`, `tests/test_prompt_gate.py`,
`tests/test_prompt_gate_hook.py`, `tests/test_refine_prompt_skill.py`,
`tests/fixtures/prompt_gate_cases.json`, `CLAUDE.md`.

## Goal

Dois objetivos, com escopo declarado honestamente:

1. **PII (bloqueio):** o **modelo principal** nunca processa um prompt *digitado* que
   contenha CPF ou CNPJ com dígito verificador válido. Fail-closed.
2. **Qualidade (aviso):** no **primeiro** prompt de cada sessão, avisar quando faltar alvo
   (arquivo ou sintoma), *definition of done* ou quando houver tarefas empilhadas — sem
   bloquear, até que dados rotulados justifiquem promover uma regra a bloqueio.

E, no fim de cada turno com código alterado, rodar lint e os testes dos arquivos tocados
(Stop hook).

Critérios verificáveis:

| Critério | Como se verifica |
|---|---|
| Prompt com CPF/CNPJ válido → `exit 2` em todas as formas (nu, pontuado, com `?`, `!!`, `/cmd`, ≤ 6 palavras) | Teste parametrizado (Task 2) + smoke: `num_turns == 0` no JSON do `claude -p` (Task 4) |
| Falha interna do checador de PII → `exit 2` com mensagem, nunca `exit 0`/`1` | Teste simulando `ImportError` e exceção (Task 4) |
| Hook funciona com sessão aberta em subdiretório | Teste executando o comando do `settings.json` a partir de outro `cwd` (Task 4) |
| Latência p95 < 150 ms medida **de fora** (inclui startup do Python) | `for i in $(seq 50); do /usr/bin/time ...; done` sobre `python3 tools/prompt_gate.py < sample.json` (Task 4) |
| Promoção advisory→block de uma regra só com ≥ 60 avisos rotulados e ≤ 3 FP | `tools/prompt_gate.py --report` (Task 5) na revisão datada (Task 9) |

## Modelo de ameaça e risco residual

O gate é **rede de segurança, não sanitizador**. Fora do alcance de qualquer hook
`UserPromptSubmit` (verificado no premortem, Finding 1):

- O CLI envia o prompt ao Haiku para **gerar o título da sessão** mesmo quando o hook
  bloqueia, e grava o prompt **por extenso no transcript** (`~/.claude/projects/...`) e no
  campo "Original prompt". Logo, PII digitada sai da máquina mesmo bloqueada.
- Nomes de partes, RG, endereço, e-mail e trechos de autos **não** são detectados
  (`tools/validate_br_pii.py:22-23`: nome "continua sendo o buraco residual"). Número CNJ
  passa de propósito (rotina neste repo), mas CNJ + nome identifica um caso.
- Conteúdo que entra por `@arquivo`, anexo ou ferramenta (`Read` em `downloads/`) nunca
  passa pelo gate — mitigação parcial na Task 7 (D5).
- `UserPromptSubmit` não cobre prompts de subagentes nem chamadas via SDK.

Regra operacional (vai para o CLAUDE.md): **não digite PII em prompts**.

## Non-goals

| Fora de escopo | Motivo |
|---|---|
| Hook `type:"prompt"` (Haiku) | Não tem modo advisory: bloqueia sempre que reprova (premortem F2); não vê a conversa e bloqueia follow-ups reais (F3). Volta na Fase 2 *dentro* do command hook, como aviso |
| JEV/TypeSafe | Sem ZDR/DPA cobrindo LGPD; API alpha diferente da descrita (review §1) |
| Wrapper em volta de `claude -p` | Não cobre o modo interativo |
| Reescrever o prompt automaticamente | Hook só bloqueia ou anexa contexto; reescrita fica na skill `/refine-prompt` |
| Cobrar "o DoD que o prompt declarou" no Stop | Exigiria estado semântico entre hooks; o Stop roda lint + testes dos arquivos tocados, sem fingir mais que isso (F9) |
| CI para mudanças só em `.claude/**` | `ci.yml` ignora `.claude/**`; o teste do settings (Task 4) roda em qualquer mudança Python. Resíduo aceito (F14) |

## Arquitetura

```
UserPromptSubmit ─▶ python3 "$CLAUDE_PROJECT_DIR/tools/prompt_gate.py"
   0. PROMPT_GATE_DISABLE=1 ─────────────────────────────▶ exit 0 (kill switch)
   1. PII (sempre primeiro, sem isenção, nem "!!")
        CPF/CNPJ válido ──────────────────────────────────▶ exit 2, motivo mascarado
        erro no próprio checador ─────────────────────────▶ exit 2 (fail-closed)
   2. grava estado da sessão (HEAD atual, 1º prompt?) fora do repo
   3. qualidade — só no 1º prompt da sessão; isenções: /cmd, "?", ≤ 6 palavras, "!!"
        avisos ─▶ exit 0 + JSON {additionalContext, systemMessage} (modo advisory)
        erro ───▶ exit 0, sem aviso (fail-open, só para qualidade)

Stop ─▶ python3 "$CLAUDE_PROJECT_DIR/tools/prompt_gate.py" --stop
   stop_hook_active ou PROMPT_GATE_DISABLE=1 ─▶ exit 0
   arquivos = git diff --name-only <HEAD salvo> + git ls-files -o --exclude-standard
   .py tocado? ruff (0.14.14, como o CI) + pytest em tests/test_<mod>*.py + testes tocados
        falha de teste/lint (pytest exit 1) ─▶ {"decision":"block","reason":...}
        ambiente ausente / coleta (pytest exit 2–5, ruff ausente) ─▶ exit 0 + systemMessage
```

Contrato **verificado** no CLI 2.1.288 (review §4 e premortem): stdin do
`UserPromptSubmit` traz `prompt`, `cwd`, `session_id`, `transcript_path`, `prompt_id`,
`permission_mode`, `hook_event_name` (e outros); stdin do `Stop` traz `stop_hook_active` e
`last_assistant_message`; `exit 2` aborta o turno (`num_turns: 0`); `additionalContext`
chega ao modelo (fica no transcript como `hook_additional_context`), mas é invisível ao
usuário em `-p`; `--settings` **soma** às configurações do projeto; existem
`--setting-sources`, `--disallowedTools`, `disableAllHooks` e `CLAUDE_PROJECT_DIR`.
**Premissa ainda não verificada:** que `systemMessage` de um `UserPromptSubmit` com
`exit 0` é exibido ao usuário no modo interativo — Task 4 verifica antes de depender dela.

### Contrato `Resultado` (fixo antes da Task 1)

```python
@dataclass(frozen=True)
class Resultado:
    decisao: Literal["passa", "aviso", "bloqueia"]
    regras: tuple[str, ...]   # ids das regras disparadas, ex. ("pii_cpf",)
    motivo: str               # texto para o usuário; NUNCA contém a PII (usa _mascara)
```

### Regras

| Regra (id) | Tipo | Quando avalia | Ação |
|---|---|---|---|
| `pii_cpf`, `pii_cnpj` | DV válido, formas nua e pontuada | **sempre, primeiro** | bloqueia |
| `alvo_ou_sintoma` | caminho que existe em `$CLAUDE_PROJECT_DIR` **ou** sintoma (mensagem de erro, código HTTP, nome de teste) | 1º prompt | aviso |
| `possui_dod` | lista de marcadores fixada na Task 1 ("pronto quando", "deve passar", `pytest`, "critério de aceite", …) | 1º prompt | aviso |
| `multi_tarefa` | ≥ 3 itens numerados/marcados ou ≥ 3 orações imperativas coordenadas | 1º prompt | aviso sugerindo plano |

Isenções (`/cmd`, termina em `?`, ≤ 6 palavras, prefixo `!!`) valem **só** para as regras
de qualidade.

## Agents e skills — quem faz o quê

| Etapa | Mecanismo | Uso |
|---|---|---|
| Planejamento | skill `writing-plans` | Expandir as Tasks em passos; o contrato `Resultado` acima é entrada fixa |
| Revisão do plano | skill `plan-quality-gate` (ou `plan-review-cycle` do claude-skills, se a primeira não estiver instalada) + skill `premortem-code` em modo plano | Antes da execução — esta v2 já passou por um premortem |
| Execução | skill `subagent-driven-development` | Task 1 → Task 2 **sequenciais** (mesmo arquivo e contrato). Só a Task 3 roda em paralelo, num subagente `general-purpose` em worktree, com arquivo de teste próprio. Tasks 4–9 sequenciais, inline |
| Revisão do diff | skills `/code-review`, `/security-review` e `premortem-code` (modo código) | Antes do merge |
| Uso diário | skill nova `/refine-prompt` | Reescreve um prompt no template Objetivo / Alvo / Definition of Done / Fora de escopo, sem executar |
| Agentes existentes | `cascade-worker`, `soap-downloader` | Inalterados |

## USER VALIDATION GATE

| # | Decisão | Estado |
|---|---|---|
| D1 | Motor semântico (T1) | **B — nenhum nesta fase** (escolhido 2026-10-03, substitui A). Fase 2: classificador dentro do command hook, advisory |
| D2 | Modo inicial | **advisory** para qualidade; PII sempre bloqueia. Promoção por regra, via Task 9 |
| D3 | Stop hook | **sim**, com as salvaguardas da Task 6 |
| D4 | Escopo | **só pje-download** |
| D5 | Negar `Read` em `downloads/`, `downloads_batch/` e `/data/` via `permissions.deny` | **pendente** — recomendação: sim; Task 7 só executa se aprovado |

## Tasks

TDD em todas: teste vermelho primeiro. Commits pequenos e frequentes — um por task, no
mínimo. Nenhum literal de CPF/CNPJ válido entra no git: valores válidos são **gerados em
tempo de teste** (helper que calcula o DV a partir de uma semente). Critério de aceite
comum a toda task: `git diff master | python3 tools/validate_br_pii.py` limpo e o pre-push
passando no branch.

### Task 1 — Núcleo `avaliar` e regras de qualidade (TDD)

`tests/test_prompt_gate.py` primeiro; `tools/prompt_gate.py` só stdlib.
`avaliar(prompt: str, raiz: Path, primeiro_prompt: bool) -> Resultado`.
- Isenções → `passa` sem regras; `primeiro_prompt=False` → `passa` sem regras.
- `alvo_ou_sintoma`: `"corrija worker.py"` (existe em `raiz`) → sem aviso;
  `"corrija xyz.py"` → aviso; `"o /health retorna 503"` → sem aviso.
- `possui_dod`: lista de marcadores definida como constante e testada item a item.
- `multi_tarefa`: lista numerada de 4 itens → aviso.
- Fixture `tests/fixtures/prompt_gate_cases.json` (**`.json`**, não `.jsonl`) com ≥ 40
  prompts *sem PII*, cada um com `esperado` e `turno_anterior` opcional.
Commit.

### Task 2 — PII primeiro (TDD, depende da Task 1)

Reusar `tools/validate_br_pii.cpf_valido`/`cnpj_valido`; acrescentar as formas pontuadas
(`NNN.NNN.NNN-DD`, `NN.NNN.NNN/NNNN-DD`). PII é avaliada **antes** de isenções e de
`primeiro_prompt`. Teste parametrizado: CPF e CNPJ válidos (gerados) × {nu, pontuado} ×
{texto comum, termina em `?`, prefixo `!!`, `/review …`, ≤ 6 palavras, `primeiro_prompt=False`}
→ `bloqueia`. Negativos: 11 dígitos com DV inválido, número CNJ → não bloqueiam. `motivo`
nunca contém os dígitos (assert explícito). Commit.

### Task 3 — Skill `/refine-prompt` (paralela, TDD leve)

`.claude/skills/refine-prompt/SKILL.md` — frontmatter `name`, `description`,
`allowed-tools: Read, Glob, Grep`; devolve o prompt reescrito e **não executa** a tarefa.
`tests/test_refine_prompt_skill.py`: frontmatter válido, `allowed-tools` sem `Edit`, `Write`
nem `Bash`. Evidência manual registrada no PR: uma execução de
`claude -p "/refine-prompt corrige o worker" --max-turns 3`. Commit.

### Task 4 — Ligar o `UserPromptSubmit` (TDD)

`main()`: todos os imports **dentro** do `try`; caminhos de saída explícitos:
- `PROMPT_GATE_DISABLE=1` → `exit 0`.
- PII → stderr com `motivo`, `exit 2`. Qualquer exceção na etapa de PII → `exit 2` com
  "gate de PII falhou — PROMPT_GATE_DISABLE=1 desliga".
- Estado da sessão em `${XDG_STATE_HOME:-~/.local/state}/pje-prompt-gate/<session_id>.json`
  (HEAD atual via `git rev-parse HEAD`; existência do arquivo = não é o 1º prompt).
- Qualidade: `PROMPT_GATE_MODE` (`advisory` padrão | `block`); advisory →
  `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":…},"systemMessage":…}`;
  exceção → `exit 0` sem aviso.
`.claude/settings.json`: comando `python3 "$CLAUDE_PROJECT_DIR/tools/prompt_gate.py"`,
`timeout: 5`, `"env": {"PROMPT_GATE_MODE": "advisory"}`.
`tests/test_prompt_gate_hook.py` (via `subprocess`, stdin JSON real; asserts só nas
chaves usadas): cada caminho de saída; `ImportError` simulado; execução a partir de outro
`cwd`; um teste que **carrega o `.claude/settings.json`**, valida o JSON e executa o comando
nele definido com `CLAUDE_PROJECT_DIR` apontado para o repo.
Smoke manual (registrado no PR): prompt com CPF gerado → `num_turns == 0`; verificar se
`systemMessage` aparece ao usuário no modo interativo — se não aparecer, a rotulagem da
Task 5 usa o id impresso pelo `--report` em vez do aviso. Medir a latência p95 (Goal).
Commit.

### Task 5 — Telemetria rotulável, fora do repo (TDD)

Append em `${XDG_STATE_HOME:-~/.local/state}/pje-prompt-gate/telemetria.jsonl`:
`ts`, `id` (uuid4 curto, **sem hash do prompt**), `regras`, `decisao`, `latencia_ms`.
Registros de PII guardam só `regras` e `decisao`. Nunca texto, nunca hash.
`--label <id> fp|tp` grava o rótulo; `--report` mostra, por regra: avisos, rotulados, FP.
Testes: o texto do prompt e os dígitos do CPF não aparecem no arquivo; o arquivo fica fora
do repo. Commit.

### Task 6 — Stop hook (TDD, D3)

`--stop`: `stop_hook_active` ou `PROMPT_GATE_DISABLE=1` → `exit 0`. Arquivos =
`git diff --name-only <HEAD salvo na Task 4>` + `git ls-files -o --exclude-standard`
(sem estado salvo → `HEAD`). Se há `.py`:
- ruff: `uvx ruff@0.14.14 check <arquivos>`; sem `uvx` → pula com `systemMessage`.
- pytest: `python3 -m pytest -q <tests/test_<mod>*.py e testes tocados>` com `timeout` de
  120 s no hook; exit 1 → `{"decision":"block","reason":<resumo de até 20 linhas>}`;
  exit 2–5, módulo ausente ou timeout → `exit 0` + `systemMessage` ("ambiente sem deps").
Testes em repositório git temporário: arquivo staged, não rastreado e já commitado no turno
são detectados; mudança anterior ao turno não é; mapeamento `worker.py` → todos os
`test_worker*.py`; exit 2 do pytest não bloqueia; `stop_hook_active` encerra. Commit.

### Task 7 — Negar leitura de autos (D5, só se aprovado)

`.claude/settings.json` → `permissions.deny`: `Read(./downloads/**)`,
`Read(./downloads_batch/**)`, `Read(//data/**)`. Teste estende o do settings (Task 4).
Smoke: pedir ao Claude para ler um arquivo em `downloads/` → negado. Commit.

### Task 8 — Documentação

Seção "Prompt gate" no `CLAUDE.md`: modelo de ameaça resumido, "não digite PII",
`!!`, `PROMPT_GATE_DISABLE=1`, `disableAllHooks` em `.claude/settings.local.json`,
**PR de fork se revisa com os hooks desligados** (o hook e os testes do PR rodariam
sozinhos), `--label`/`--report`. Sem números de teste fixos. Rodar
`python tools/verify_spec.py docs/specs/*.md`. Commit.

### Task 9 — Revisão D2 (datada: merge + 14 dias)

`--report`. Para cada regra: ≥ 60 avisos rotulados **e** ≤ 3 FP → promover a bloqueio
(nova chave em `PROMPT_GATE_MODE` por regra, com teste); menos dados → estender 14 dias;
FP > 3 → ajustar a regra ou removê-la. Registrar a decisão nesta spec. Commit.

## Correções vindas do premortem

| Finding | Mudança nesta v2 |
|---|---|
| F1 título/transcript recebem PII | Goal reescrito (modelo principal); seção de modelo de ameaça |
| F2 `type:"prompt"` não tem advisory | D1=B; hook Haiku vira non-goal |
| F3 follow-ups bloqueados | Qualidade só no 1º prompt; fixture com `turno_anterior` |
| F4 isenções antes da PII | PII primeiro; teste parametrizado com todas as isenções |
| F5 tasks não independentes | Contrato `Resultado` fixo; Task 1 → 2 sequenciais; Task 3 com teste próprio |
| F6 fail-open em PII / caminho relativo | `$CLAUDE_PROJECT_DIR`; imports no `try`; PII fail-closed; testes de cwd e `ImportError` |
| F7 metas não falsificáveis | Critérios em tabela; rotulagem `--label`; regra ≥ 60 / ≤ 3 FP; Task 9 datada; p95 medido de fora |
| F8 fixtures barradas pelo pre-push | `.json`; CPFs gerados em teste; pre-push como critério de aceite |
| F9 Stop no conjunto errado / ambiente | HEAD salvo + não rastreados; exit 2–5 não bloqueia; ruff 0.14.14; mapeamento por glob; sem promessa de DoD semântico |
| F10 telemetria no repo / hash reversível | Fora do repo; uuid, sem hash; PII sem detalhes |
| F11 sem kill switch / fork | `PROMPT_GATE_DISABLE`; `disableAllHooks`; regra de fork no CLAUDE.md |
| F12 sem modelo de ameaça | Seção dedicada; D5 para `downloads/` |
| F13 medição não isolada | Task 7 antiga (medição Haiku) removida; smoke da Task 4 só observa `num_turns` |
| F14 `.claude/**` sem CI | Teste que carrega e executa o settings; resíduo declarado |
| F15 contrato do stdin | Lista completa; testes só exigem chaves usadas |

## Fase 2 (fora desta spec)

- **Classificador semântico advisory dentro do command hook** — Haiku via API ou
  `lex-prompt-gate` 1.5B local (central-ollama, treinado como o `lex-router`, com os rótulos
  da Task 5), recebendo a última mensagem do assistente lida do `transcript_path`.
- **claude-skills:** promover a nível usuário, ao lado de `hooks/analysis-loop-detector.ts`.
- **kratos-case-pipeline / claude-agents:** gate antes de chamadas caras com
  `interrupt()` + `Command(resume=)` (review §3.2) e Claude Agent SDK.

## Riscos

| Risco | Mitigação |
|---|---|
| Gate vira atrito e é desligado | Qualidade só avisa e só no 1º prompt; `!!`; kill switch |
| PII sai mesmo bloqueada (título, transcript) | Declarado; regra "não digite PII"; o gate reduz, não elimina |
| Checador de PII quebra e trava o trabalho | Mensagem diz como desligar (`PROMPT_GATE_DISABLE=1`) |
| Stop hook lento ou ruidoso | Só testes dos módulos tocados; timeout 120 s; ambiente ausente não bloqueia |
| Hooks versionados executam código de PR de fork | Regra no CLAUDE.md: revisar forks com hooks desligados |
| CLI muda o contrato do hook | Testes com stdin real e só chaves usadas; re-smoke ao atualizar o CLI |

## References

- Premortem: `.premortems/PREMORTEM-2026-10-03T14-58-00Z.md`
- Review: `docs/research/2026-10-03-review-jev-claude-code-langgraph.md`
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- PII: `tools/validate_br_pii.py`, `.gitleaks.toml`, `tools/git-hooks/pre-push`
- Precedentes: `kratos-case-pipeline/src/langgraph/graph.py:51` (`router_confidence_gate`),
  `prompts-gold/scripts/propagate/guardrails.py:58` (`check_pre`),
  `legal-data-quality-gateway/ldqg/scoring.py:32` (`route_status`)
- Método data-first: `docs/specs/2026-09-29-phase2-sprint2-playwright-telemetry.md`
