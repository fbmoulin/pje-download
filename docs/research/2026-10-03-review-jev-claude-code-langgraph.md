# Review — "Integração JEV + Claude Code + LangGraph"

**Data:** 2026-10-03
**Objeto:** relatório técnico colado na sessão (gerado por outro assistente), que propõe o
modelo JEV (TypeSafe AI) como "gatekeeper" de qualidade de prompt antes do Claude Code.
**Spec derivada:** [`docs/specs/2026-10-03-prompt-quality-gate.md`](../specs/2026-10-03-prompt-quality-gate.md)

## TL;DR

A **ideia** é boa e já existe no seu ecossistema (`router_confidence_gate` no
kratos-case-pipeline, `check_pre` no prompts-gold, `route_status` no LDQG). A
**implementação** proposta tem problemas bloqueantes:

1. O id do modelo e o contrato de API estão errados (`typesafe/jev` → `typesafe/jev-1.13`,
   e a chamada documentada é a Decisions API, não `chat/completions` + `json_schema`).
2. Mandar o prompt para um terceiro sem ZDR é incompatível com dados de processo/LGPD.
3. O código do LangGraph tem um bug que **descarta silenciosamente o prompt corrigido**
   (reproduzido abaixo).
4. O wrapper Node tem injeção de shell e um `ReferenceError`.
5. O ponto de integração certo no Claude Code é um **hook `UserPromptSubmit` nativo**, não
   um wrapper em volta de `claude -p` — e o Claude Code já tem um hook `type: "prompt"`
   que faz o papel do JEV sem código e sem novo processador de dados (testado abaixo).

## 1. Verificação dos fatos sobre o JEV

Fonte: pesquisa web por subagente. O proxy desta sessão bloqueou `openrouter.ai`,
`typesafe.ai` e `youtube.com`, então os números vêm de snippets de busca — **pistas fortes,
não verificação de primeira mão**. Confirmar antes de gastar.

| Afirmação do texto | Achado | Status |
|---|---|---|
| Modelo `typesafe/jev` no OpenRouter | Id é `typesafe/jev-1.13` (alias `~typesafe/jev-latest`); `typesafe/jev-router` é outro produto (roteador) | ❌ errado |
| Chamado via `chat/completions` + `response_format: json_schema` | Fontes descrevem a **Decisions API** alpha `POST /api/alpha/decisions` com array `questions` (tipos `choice`, `score`, `noul`) | ❌ provavelmente errado — confirmar no tutorial do OpenRouter |
| "Não gera texto, retorna probabilidades" | Correto: devolve probabilidades calibradas por pergunta, sem texto | ✅ |
| Barato e rápido | US$ 0,042/M tokens de entrada, US$ 0 saída; P50 ~0,2–0,45 s; contexto 32k | ✅ (não verificado em primeira mão) |
| — (omitido) | ZDR só no plano enterprise; OpenRouter indica ZDR **não** estabelecido para o jev-1.13; nenhuma menção a LGPD/residência | ⚠️ omissão grave |

A TypeSafe AI saiu do stealth em 15/09/2026 (seed de ~US$ 40M) e publica uma skill oficial
para Claude Code. Os 4 vídeos enviados não puderam ser identificados (IDs não indexados,
YouTube bloqueado); há outros vídeos "Jev + Claude Code" públicos.

## 2. Review do desenho

### 2.1 As 4 regras de avaliação

| Regra | Problema | Recomendação |
|---|---|---|
| `objetivo_claro` | OK | Manter |
| `define_arquivo_exato` | **Penaliza exatamente o que o Claude Code faz bem**: "ache por que o worker trava em X", "onde está o bug de Y". Prompts exploratórios não sabem o arquivo | Rebaixar para *aviso*; exigir "arquivo **ou** sintoma reproduzível" |
| `possui_definition_of_done` | Boa regra; é a que mais economiza retrabalho | Manter — e **cobrar no fim** (Stop hook), não só no início |
| `empilha_multiplas_tarefas` | OK, mas é melhor tratado sugerindo decomposição (plano/TaskCreate) do que bloqueando | Aviso, não bloqueio |

**Ponto cego principal: o gate ignora o contexto da conversa.** Num chat, a maioria dos
prompts é continuação ("sim", "pode seguir", "agora rode os testes", "opção A"). Avaliadas
isoladamente, todas reprovam nas regras 2 e 3. Um gate que não isenta follow-ups, perguntas e
slash commands vira atrito em todo turno e será desligado em uma semana.

**Thresholds:** o texto diz "> 50%" e "< 50%", o código rejeita `< 50` e `> 50` — score
exatamente 50 passa nas duas, contrariando o texto. Além disso, 50 é arbitrário: sem um
conjunto de prompts rotulados não dá para saber a taxa de falso bloqueio. Comece em modo
*advisory* e meça (mesma filosofia data-first da T2.2A).

### 2.2 Economia de tokens — a premissa está superestimada

O custo de uma sessão de Claude Code é dominado pelo loop de ferramentas (leituras,
edições, testes), não pelo prompt inicial. O gate só economiza quando um prompt vago
causaria exploração longa e inútil. Isso existe, mas o ganho real é **qualidade/retrabalho**,
não tokens do prompt. Meça antes de afirmar economia.

### 2.3 Privacidade (bloqueante para os seus repositórios)

Seus prompts de desenvolvimento citam CNJs, nomes de partes, trechos de autos, às vezes CPF
de teste. O desenho envia **todo prompt** a um segundo processador (OpenRouter → TypeSafe)
sem ZDR e sem DPA cobrindo transferência internacional. Com o hook nativo do Claude Code,
o prompt vai só para onde já ia (Anthropic) — **zero processador novo**.

## 3. Bugs no código

### 3.1 Wrapper Node.js

```js
const claudeProcess = spawn('claude', ['-p', prompt], { stdio: 'inherit', shell: true });
```

- **`prompt` não existe** (a variável é `userPrompt`) → `ReferenceError` antes de rodar.
- **Injeção de shell:** com `shell: true`, o Node concatena os argumentos numa string de
  shell. Um prompt contendo `` `rm -rf ~` `` ou `$(...)` é executado. Remova `shell: true`.
- `evaluatePromptWithJEV` nunca é aguardado; sem timeout, sem tratamento de `response.ok`;
  `data.choices[0]` estoura se a API devolver erro.
- Conceitualmente: um wrapper só cobre o modo headless. No uso interativo (onde está a
  maior parte do trabalho) ele não roda. O ponto nativo é o hook `UserPromptSubmit`.

### 3.2 LangGraph — o prompt corrigido é descartado (reproduzido)

Reproduzi o grafo colado **verbatim** (só a chamada HTTP trocada por um fake
determinístico) com `langgraph 1.2.12` + `langgraph-checkpoint-sqlite 3.1.1`:

```
after 1st run next= ('rejection_handler',)
after retry next= () | gatekeeper calls: ['Cria a tela inicial']
final_output= None | scores= {...'objetivo_claro': 10...}
```

`app.update_state(config, {"prompt": new_prompt}, as_node="gatekeeper")` grava o estado
**como se o gatekeeper já tivesse rodado**. O LangGraph então segue as arestas *de saída*
do gatekeeper, roteando com os **scores antigos**: o gatekeeper nunca reavalia o prompt
novo, o grafo cai no `rejection_handler`, termina, e o `while` sai sem imprimir nada. O
usuário digitou um prompt melhor e ele sumiu.

Correção verificada (mesma versão): pausar *dentro* do nó com `interrupt()`, retomar com
`Command(resume=...)` e ter uma aresta de volta ao gate:

```python
from langgraph.types import interrupt, Command

def ask(state):
    novo = interrupt({"scores": state["scores"], "msg": "refine o prompt"})
    return {"aborted": True} if novo == "sair" else {"prompt": novo}

wf.add_conditional_edges("gate", route, ["run", "ask"])
wf.add_conditional_edges("ask", lambda s: END if s.get("aborted") else "gate", ["gate", END])
# ...
app.invoke(Command(resume=novo_prompt), cfg)
```

Saída: `calls= ['Cria a tela inicial', 'Fix tests/test_x.py; ...'] out= RAN: Fix tests/...`.

Outros problemas do script:
- `node_claude_execution` é um stub — **não executa o Claude**. Para executar de verdade
  dentro de Python use o Claude Agent SDK (`claude-agent-sdk`), não `subprocess` de `claude -p`.
- `requests.post` sem `timeout`, sem `raise_for_status`; uma falha de rede derruba o grafo
  em vez de cair num caminho definido (fail-open ou fail-closed tem que ser decisão explícita).
- `SqliteSaver` exige o pacote separado `langgraph-checkpoint-sqlite` (não citado).
- `thread_id` fixo (`sessao_devops_1`): toda execução retoma a mesma thread.
- Schema sem `minimum/maximum` nos inteiros e sem `strict: true`.

## 4. O que o Claude Code já oferece (verificado nesta sessão, CLI 2.1.288)

Testado com `claude -p ... --settings <arquivo>`:

| Teste | Resultado |
|---|---|
| Hook `command` em `UserPromptSubmit`, stdin | JSON com `session_id`, `transcript_path`, `cwd`, `prompt_id`, `permission_mode`, `hook_event_name`, **`prompt`** |
| `exit 2` + stderr | Turno abortado, `num_turns: 0` — modelo principal **não** é chamado |
| Hook `type: "prompt"`, prompt vago "cria a tela inicial" | Bloqueado pelo Haiku: *"não cita um arquivo/componente… E não define critério de pronto"*, `num_turns: 0` |
| Mesmo hook, prompt específico com arquivo + DoD | Aprovado, seguiu para o modelo principal |

Ou seja: o JEV + wrapper é substituível por **~10 linhas de `settings.json`**, sem
dependência nova e sem processador de dados novo. O custo é uma chamada Haiku por prompt.

> Nota: o relatório do subagente de documentação afirmou que o campo era `user_message` e
> deu um exemplo de SDK com hooks como comandos de shell. Ambos estavam errados — o teste
> empírico acima prevalece.

## 5. Adequação ao seu ecossistema

| Repo | Onde o gate entra | Peça reaproveitável |
|---|---|---|
| **pje-download** | Hook `UserPromptSubmit` em `.claude/settings.json` (piloto — esta spec) | `tools/validate_br_pii.py` (`cpf_valido`, `cnpj_valido`) |
| **claude-skills** (`~/.claude`) | Promover o hook para nível usuário, ao lado de `hooks/analysis-loop-detector.ts` (já usa `UserPromptSubmit`) | Plugin `li` |
| **claude-agents** | Gate antes de `router.py:classify_project` → Opus | Já usa Haiku; o gate pode ser a mesma chamada |
| **kratos-case-pipeline** | Nó pré-roteador no LangGraph, no padrão de `router_confidence_gate` (`graph.py:51`) | Use `interrupt()`, não `update_state(as_node=...)` |
| **central-ollama** | Classificador local 1.5B (`lex-prompt-gate`) treinado como o `lex-router` → gate 100% local | `src/eval/gate.py` (go/no-go vs baseline) |
| **legal-data-quality-gateway** | Mascaramento antes de qualquer LLM externo | `anonymize_text` (`ldqg/anonymizers/pii.py:87`), `route_status` (`scoring.py:32`) |
| **prompts-gold** | Já tem gate de intenção | `guardrails.check_pre`, `evalgate.py` |

**Onde o JEV ainda pode fazer sentido:** classificação em alto volume de texto **não
sensível** (ex.: triagem de issues públicas, roteamento de prompts genéricos), depois de
confirmar a API e com ZDR contratado. Não para autos, partes ou CPF.

## References

- Spec: `docs/specs/2026-10-03-prompt-quality-gate.md`
- Claude Code hooks: https://code.claude.com/docs/en/hooks
- OpenRouter Jev (não acessado diretamente): https://openrouter.ai/typesafe/jev-1.13 ,
  https://openrouter.ai/docs/guides/community/jev-tutorial
- TypeSafe legal/DPA (não acessado diretamente): https://docs.typesafe.ai/legal
- InfoQ (não acessado diretamente): https://www.infoq.com/news/2026/10/typesafe-ai-jev-released/
- LangGraph interrupts: https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/
