# Prompt Quality Gate para Claude Code (piloto no pje-download)

**Status:** **v3**. v1 → premortem `deep` REWORK
(`.premortems/PREMORTEM-2026-10-03T14-58-00Z.md`); v2 → premortem de confirmação REFINE
(`.premortems/PREMORTEM-2026-10-03T16-05-00Z-v2.md`), cujos 8 achados esta v3 incorpora.
Decisões vigentes:
D1=**B** (só T0 determinístico; o usuário escolheu a opção "a" em 2026-10-03, revendo o
D1=A anterior), D2=advisory, D3=sim, D4=só pje-download, D5=sim (aprovado 2026-10-03).
Implementação em andamento.
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
| Qualquer falha *dentro* do processo antes de a PII estar decidida → `exit 2` com mensagem, nunca `exit 0`/`1` | Testes simulando `ImportError`, stdin inválido e exceção (Task 4) |
| Comportamento em subdiretório documentado com evidência | Smoke ponta-a-ponta: `claude -p` iniciado em `tests/` com CPF gerado; resultado registrado no PR e refletido no modelo de ameaça (Task 4) |
| Latência p95 < 150 ms medida **de fora** (inclui startup do Python) | `for i in $(seq 50); do /usr/bin/time ...; done` sobre `python3 tools/prompt_gate.py < sample.json` (Task 4) |
| Promoção advisory→block de uma regra só com ≥ 60 avisos rotulados e ≤ 2 FP (heurística: limite superior do IC 95% ≈ 11%) | `tools/prompt_gate.py --report` (Task 5) na revisão datada (Task 9) |

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
- **Sessão aberta em subdiretório não carrega o `.claude/settings.json` do projeto**
  (verificado 2026-10-03 com o CLI 2.1.288 em modo `-p`: o hook disparou na raiz e não em
  `sub/`; reconfirmar em sessão interativa no WSL na Task 4). Nesse caso não há PII, Stop
  nem deny.
- **Falha aberta fora do processo:** se o hook estoura o `timeout`, se `python3` não está no
  PATH (`exit 127`) ou se o interpretador morre, o Claude Code trata como erro não
  bloqueante e o prompt segue (verificado: hook cancelado por timeout → modelo principal
  rodou). O fail-closed só vale para falhas que o processo consegue capturar.

Regras operacionais (vão para o CLAUDE.md): **não digite PII em prompts** e **abra o Claude
Code na raiz do repo**.

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
   (main() inteiro dentro de try/except BaseException; timeout do hook: 10 s)
   0. PROMPT_GATE_DISABLE=1 ─────────────────────────────▶ exit 0 (kill switch)
   1. PII — antes de qualquer git ou disco; sem isenção, nem "!!"
        CPF/CNPJ válido ──────────────────────────────────▶ exit 2, motivo mascarado
        qualquer exceção até aqui (stdin, import, regex) ─▶ exit 2 (fail-closed)
   2. snapshot do turno, a CADA prompt, fora do repo: {arquivo: sha256} dos arquivos
      sujos e não rastreados (insumo do Stop)
   3. qualidade — só se a vaga de "1º prompt avaliado" da sessão estiver livre;
      isenções (/cmd, "?", ≤ 6 palavras, "!!") passam SEM consumir a vaga
        avisos ─▶ exit 0 + JSON {additionalContext, systemMessage com id e comando --label}
        regra em PROMPT_GATE_BLOCK_RULES ─▶ exit 2 (só após a Task 9)
        erro ───▶ exit 0, sem aviso (fail-open, só para qualidade)

Stop ─▶ python3 "$CLAUDE_PROJECT_DIR/tools/prompt_gate.py" --stop
   stop_hook_active ou PROMPT_GATE_DISABLE=1 ─▶ exit 0
   arquivos = sujos/não rastreados agora cujo sha256 difere do snapshot do turno
              (ou ausentes dele), que ainda existem, + commitados no turno
              (git diff --name-only --diff-filter=d <HEAD do snapshot>..HEAD)
   .py tocado? ruff (0.14.14, como o CI) + pytest em tests/test_<mod>*.py + testes tocados
        falha de teste/lint (pytest exit 1) ─▶ {"decision":"block","reason":...}
        ambiente ausente / coleta (pytest exit 2–5, ruff ausente) ─▶ exit 0 + systemMessage
```

Contrato **verificado** no CLI 2.1.288 (review §4 e premortem): stdin do
`UserPromptSubmit` traz `prompt`, `cwd`, `session_id`, `transcript_path`, `prompt_id`,
`permission_mode`, `hook_event_name` (e outros); stdin do `Stop` traz `stop_hook_active` e
`last_assistant_message`; `exit 2` aborta o turno (`num_turns: 0`); `additionalContext`
chega ao modelo (fica no transcript como `hook_additional_context`); `systemMessage` com
`exit 0` chega ao usuário como notice (`{"type":"system","subtype":"informational"}`);
`--settings` **soma** às configurações do projeto; `env` do settings **vence** o shell;
`claude -p` lançado de dentro de uma sessão herda `CLAUDE_CODE_SESSION_ID`; o hook dispara
também para slash commands; existem `--setting-sources`, `--disallowedTools`,
`disableAllHooks`, `CLAUDE_PROJECT_DIR` e `uvx ruff@0.14.14` (1,3 s).

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
| `multi_tarefa` | ≥ 3 itens numerados/marcados em linhas, ou ≥ 3 itens inline `1) … 2) … 3)` (orações imperativas coordenadas ficaram de fora: sem heurística confiável) | 1º prompt | aviso sugerindo plano |

Isenções (`/cmd`, termina em `?`, ≤ 6 palavras, prefixo `!!`) valem **só** para as regras
de qualidade e **não** consomem a vaga de primeiro prompt avaliado. Promoção a bloqueio é
por regra, via `PROMPT_GATE_BLOCK_RULES=<id>,<id>` (vazio por padrão), definido em
`.claude/settings.local.json` — nunca no shell, porque o `env` do settings vence.

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
| D5 | Negar `Read` em `downloads/`, `downloads_batch/` e `/data/` via `permissions.deny` | **sim** (aprovado 2026-10-03) |

## Tasks

TDD em todas: teste vermelho primeiro. Commits pequenos e frequentes — um por task, no
mínimo. Nenhum literal de CPF/CNPJ válido entra no git: valores válidos são **gerados em
tempo de teste** (helper que calcula o DV a partir de uma semente). Critério de aceite
comum a toda task: `git diff master | python3 tools/validate_br_pii.py` limpo e o pre-push
passando no branch.

### Task 1 — Núcleo `avaliar` e regras de qualidade (TDD)

`tests/test_prompt_gate.py` primeiro; `tools/prompt_gate.py` só stdlib.
`avaliar(prompt: str, raiz: Path, primeiro_prompt: bool, bloquear: frozenset[str]) -> Resultado`.
- Isenções → `passa` sem regras; `primeiro_prompt=False` → `passa` sem regras.
- Regra disparada que está em `bloquear` → `bloqueia`; senão `aviso`.
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

`main()` **inteiro** dentro de `try/except BaseException`; até a PII estar decidida,
qualquer exceção → `exit 2`. Ordem e saídas:
- `PROMPT_GATE_DISABLE=1` → `exit 0`.
- Ler stdin e checar PII **antes** de qualquer `git` ou escrita em disco. PII → stderr com
  `motivo`, `exit 2`. Exceção → `exit 2` com "gate de PII falhou — saia e relance com
  `PROMPT_GATE_DISABLE=1 claude --continue`".
- Snapshot do turno (todo prompt) em
  `${XDG_STATE_HOME:-~/.local/state}/pje-prompt-gate/<session_id>/turno.json`: HEAD e
  `{arquivo: sha256}` dos sujos e não rastreados. Estados com mais de 30 dias são apagados.
- Vaga de primeiro prompt: arquivo `<session_id>/avaliado` criado só quando um prompt **não
  isento** passa pelas regras de qualidade.
- Qualidade: `PROMPT_GATE_BLOCK_RULES` decide aviso × bloqueio por regra; aviso →
  `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":…},"systemMessage":"gate [<id>] <regras> — rotule: python3 tools/prompt_gate.py --label <id> fp|tp"}`;
  exceção → `exit 0` sem aviso.
`.claude/settings.json`: comando `python3 "$CLAUDE_PROJECT_DIR/tools/prompt_gate.py"`,
`timeout: 10`; `env` **sem** `PROMPT_GATE_DISABLE` e sem `PROMPT_GATE_BLOCK_RULES`.
`tests/test_prompt_gate_hook.py` (via `subprocess`, stdin JSON real; asserts só nas
chaves usadas): cada caminho de saída; `ImportError`, stdin inválido e exceção simulados;
PII decidida sem tocar `git` (git fora do PATH no teste); o `systemMessage` contém o `id` e
o comando `--label`; prompt isento não consome a vaga; um teste que **carrega o
`.claude/settings.json`**, valida o JSON, garante que `PROMPT_GATE_DISABLE` não está em
`env` e executa o comando nele definido aplicando o `env` do settings e
`CLAUDE_PROJECT_DIR` apontado para o repo.
Smokes manuais (registrados no PR), sempre com `env -u CLAUDE_CODE_SESSION_ID`: na raiz,
prompt com CPF gerado → `num_turns == 0`; o mesmo iniciado em `tests/` → registrar o
resultado (esperado hoje: hook não carrega) e reconfirmar em sessão interativa no WSL;
aviso visível no modo interativo. Medir a latência p95 (Goal). Commit.

### Task 5 — Telemetria rotulável, fora do repo (TDD)

Append em `${XDG_STATE_HOME:-~/.local/state}/pje-prompt-gate/telemetria.jsonl`:
`ts`, `id` (uuid4 curto, **sem hash do prompt**), `session_id`, `prompt_id`, `regras`,
`decisao`, `latencia_ms`. `session_id` + `prompt_id` permitem ao próprio usuário abrir o
transcript local e julgar o aviso, sem que a telemetria guarde texto.
Registros de PII guardam só `regras` e `decisao`. Nunca texto, nunca hash.
`--label <id> fp|tp` grava o rótulo; `--report` mostra, por regra: avisos, rotulados, FP.
Testes: o texto do prompt e os dígitos do CPF não aparecem no arquivo; o arquivo fica fora
do repo. Commit.

### Task 6 — Stop hook (TDD, D3)

`--stop`: `stop_hook_active` ou `PROMPT_GATE_DISABLE=1` → `exit 0`. Arquivos = os que
mudaram **durante o turno**, comparando com o snapshot da Task 4: sujos/não rastreados cujo
sha256 difere do snapshot (ou ausentes dele) e que ainda existem, mais os commitados no turno
(`git diff --name-only --diff-filter=d <HEAD do snapshot>..HEAD`). Sem snapshot → não checa
nada. Hook `Stop` com `timeout: 180` explícito no settings. Se há `.py`:
- ruff: `uvx ruff@0.14.14 check <arquivos>`; sem `uvx` → pula com `systemMessage`.
- pytest: `python3 -m pytest -q <tests/test_<mod>*.py e testes tocados>` com `timeout` de
  120 s no hook; exit 1 → `{"decision":"block","reason":<resumo de até 20 linhas>}`;
  exit 2–5, módulo ausente ou timeout → `exit 0` + `systemMessage` ("ambiente sem deps").
Testes em repositório git temporário: arquivo staged, não rastreado e já commitado no turno
são detectados; edição não commitada e rascunho não rastreado **feitos antes do prompt** não
são; arquivo apagado no turno não vai ao ruff; mapeamento `worker.py` → todos os
`test_worker*.py`; exit 2 do pytest não bloqueia; `stop_hook_active` encerra. Commit.

### Task 7 — Negar leitura de autos (D5)

`.claude/settings.json` → `permissions.deny`: `Read(/downloads/**)`,
`Read(/downloads_batch/**)`, `Read(//data/**)` (`/x` é relativo ao arquivo de settings;
`//` é absoluto). Teste estende o do settings (Task 4). Smoke: pedir ao Claude para ler um
arquivo em `downloads/` → negado (da raiz; de subdiretório vale o modelo de ameaça). Commit.

### Task 8 — Documentação

Seção "Prompt gate" no `CLAUDE.md`: modelo de ameaça resumido, "não digite PII",
"abra o Claude Code na raiz do repo", `!!`, `PROMPT_GATE_DISABLE=1`,
`PROMPT_GATE_BLOCK_RULES` em `.claude/settings.local.json`, `disableAllHooks`,
**PR de fork se revisa com os hooks desligados** (o hook e os testes do PR rodariam
sozinhos), `--label`/`--report`. Sem números de teste fixos. Rodar
`python tools/verify_spec.py docs/specs/*.md`. Commit.

### Task 9 — Revisão D2 (datada: merge + 14 dias)

`--report`. Para cada regra: ≥ 60 avisos rotulados **e** ≤ 2 FP → promover a bloqueio
(acrescentar o id a `PROMPT_GATE_BLOCK_RULES` no `env` do `.claude/settings.json`, com o
teste do settings atualizado); FP > 2 → ajustar a regra ou removê-la; menos dados → estender
14 dias, **no máximo duas vezes** — depois disso, decidir com os dados que houver ou remover a
regra. Registrar a decisão nesta spec. Commit.

## Correções vindas do premortem

| Finding | Mudança nesta v2 |
|---|---|
| F1 título/transcript recebem PII | Goal reescrito (modelo principal); seção de modelo de ameaça |
| F2 `type:"prompt"` não tem advisory | D1=B; hook Haiku vira non-goal |
| F3 follow-ups bloqueados | Qualidade só no 1º prompt; fixture com `turno_anterior` |
| F4 isenções antes da PII | PII primeiro; teste parametrizado com todas as isenções |
| F5 tasks não independentes | Contrato `Resultado` fixo; Task 1 → 2 sequenciais; Task 3 com teste próprio |
| F6 fail-open em PII / caminho relativo | `$CLAUDE_PROJECT_DIR`; imports no `try`; PII fail-closed; testes de cwd e `ImportError` |
| F7 metas não falsificáveis | Critérios em tabela; rotulagem `--label`; regra ≥ 60 / ≤ 3 FP (v3: ≤ 2, ver v2-F7); Task 9 datada; p95 medido de fora |
| F8 fixtures barradas pelo pre-push | `.json`; CPFs gerados em teste; pre-push como critério de aceite |
| F9 Stop no conjunto errado / ambiente | HEAD salvo + não rastreados; exit 2–5 não bloqueia; ruff 0.14.14; mapeamento por glob; sem promessa de DoD semântico |
| F10 telemetria no repo / hash reversível | Fora do repo; uuid, sem hash; PII sem detalhes |
| F11 sem kill switch / fork | `PROMPT_GATE_DISABLE`; `disableAllHooks`; regra de fork no CLAUDE.md |
| F12 sem modelo de ameaça | Seção dedicada; D5 para `downloads/` |
| F13 medição não isolada | Task 7 antiga (medição Haiku) removida; smoke da Task 4 só observa `num_turns` |
| F14 `.claude/**` sem CI | Teste que carrega e executa o settings; resíduo declarado |
| F15 contrato do stdin | Lista completa; testes só exigem chaves usadas |
| v2-F1 subdiretório não carrega o settings | Smoke ponta-a-ponta em `tests/`; modelo de ameaça; regra "abra na raiz" |
| v2-F2 timeout / exit ≠ 2 falham abertos | `main()` inteiro no `try`; PII antes de git; `timeout: 10`; declarado no modelo de ameaça |
| v2-F3 Stop pega WIP anterior e arquivo apagado | Snapshot com sha256 a cada prompt; `--diff-filter=d`; teste de WIP anterior |
| v2-F4 rotulagem sem vínculo | `id` + comando `--label` no `systemMessage` (testado); `session_id`/`prompt_id` na telemetria |
| v2-F5 vaga gasta por isentos / session_id herdado | Vaga só consumida por prompt avaliado; smokes com `env -u CLAUDE_CODE_SESSION_ID`; Task 9 com no máximo 2 extensões |
| v2-F6 `env` vence o shell / modo por regra | `PROMPT_GATE_BLOCK_RULES` em `settings.local.json`; teste garante ausência do kill switch no `env` |
| v2-F7 regra estatística fraca | ≤ 2 FP em ≥ 60 |
| v2-F8 Stop sem timeout | `timeout: 180` explícito |

## Fase 2 (fora desta spec)

- **Classificador semântico advisory dentro do command hook** — Haiku via API ou
  `lex-prompt-gate` 1.5B local (central-ollama, treinado como o `lex-router`, com os rótulos
  da Task 5), recebendo a última mensagem do assistente lida do `transcript_path`.
- **claude-skills:** promover a nível usuário, ao lado de `hooks/analysis-loop-detector.ts`
  — isso também cobre sessões abertas em subdiretório (v2-F1), com guarda por `cwd`.
- **kratos-case-pipeline / claude-agents:** gate antes de chamadas caras com
  `interrupt()` + `Command(resume=)` (review §3.2) e Claude Agent SDK.

## Riscos

| Risco | Mitigação |
|---|---|
| Gate vira atrito e é desligado | Qualidade só avisa e só no 1º prompt; `!!`; kill switch |
| PII sai mesmo bloqueada (título, transcript) | Declarado; regra "não digite PII"; o gate reduz, não elimina |
| Checador de PII quebra e trava o trabalho | Mensagem diz como sair (`PROMPT_GATE_DISABLE=1 claude --continue`) |
| Sessão em subdiretório roda sem nenhum gate | Regra "abra na raiz"; Fase 2 a nível usuário |
| Stop hook lento ou ruidoso | Só testes dos módulos tocados; timeout 120 s; ambiente ausente não bloqueia |
| Hooks versionados executam código de PR de fork | Regra no CLAUDE.md: revisar forks com hooks desligados |
| CLI muda o contrato do hook | Testes com stdin real e só chaves usadas; re-smoke ao atualizar o CLI |

## References

- Premortems: `.premortems/PREMORTEM-2026-10-03T14-58-00Z.md` (v1, REWORK),
  `.premortems/PREMORTEM-2026-10-03T16-05-00Z-v2.md` (v2, REFINE)
- Review: `docs/research/2026-10-03-review-jev-claude-code-langgraph.md`
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- PII: `tools/validate_br_pii.py`, `.gitleaks.toml`, `tools/git-hooks/pre-push`
- Precedentes: `kratos-case-pipeline/src/langgraph/graph.py:51` (`router_confidence_gate`),
  `prompts-gold/scripts/propagate/guardrails.py:58` (`check_pre`),
  `legal-data-quality-gateway/ldqg/scoring.py:32` (`route_status`)
- Método data-first: `docs/specs/2026-09-29-phase2-sprint2-playwright-telemetry.md`
