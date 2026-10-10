# HANDOFF — pje-download: prompt gate do Claude Code + migração para o Mac mini (2026-10-03 → 2026-10-10)

> Este arquivo é a fonte para a próxima sessão. Tudo abaixo foi medido, exceto onde marcado.

## Estado

- **Repo:** `fbmoulin/pje-download` · **branch default:** `master`.
- **`master`:** após o PR deste handoff (docs). Antes dele, `e37a09f` (#72).
- **Testes (2026-10-10):** `pytest tests/ -q` → **920 passed** com Redis vivo; **918 passed, 2
  skipped** sem Redis (os 2 de socket real). `ruff 0.14.14` limpo em `check .` e `format --check .`.
- **Produção:** deploys #111 (merge do #66) e #114 (merge do #71) com `conclusion: success`.
  #72 e este PR são só `.md`, então não rodam CI nem deploy.
- **PRs abertos:** só o **#70** (dependabot: aiohttp 3.14.4, gdown 6.4.1). Não foi tocado.
- **Cópia de trabalho:** agora no **Mac mini**, `/Users/felipemoulin/pje-download`. O WSL foi
  aposentado; as cópias de lá não são mais de trabalho.

## O que foi feito, em ordem

1. **Review de um desenho colado "JEV + Claude Code + LangGraph"** (gatekeeper de prompt via
   OpenRouter). Rejeitado: id de modelo e contrato de API errados, sem ZDR/LGPD para dados de
   processo, injeção de shell no wrapper Node, e um bug reproduzido no loop HITL do LangGraph
   (`update_state(as_node=...)` descarta o prompt corrigido). Relatório:
   `docs/research/2026-10-03-review-jev-claude-code-langgraph.md`.
2. **Spec SDD** de um gate nativo do Claude Code (`docs/specs/2026-10-03-prompt-quality-gate.md`),
   passada por **dois premortems** (`.premortems/`): v1 → REWORK (15 achados), v2 → REFINE (8).
   Decisões do Felipe: D1=B (só regras determinísticas; o hook `type:"prompt"` com Haiku não tem
   modo de aviso e bloqueia follow-ups), D2=advisory, D3=Stop hook, D4=só pje-download, D5=negar
   leitura de `downloads/`.
3. **#66 — implementação (Tasks 1–8)**, mais `/code-review` (10 achados, todos corrigidos com teste
   de regressão) e `/security-review` (nenhum achado). Detalhe em `CLAUDE.md` §"Prompt gate".
4. **#71 — `.gitignore`** de `.claude/settings.local.json` e `.venv/`, com teste que roda o
   `.gitignore` num repo temporário isolado (sem `info/exclude` nem excludes globais).
5. **Setup no Mac mini** e **#72 — docs**: caminho do Mac no `CLAUDE.md` e confirmação, em sessão
   interativa (macOS, CLI 2.1.296), de que sessão aberta em subdiretório não carrega os hooks.
6. **Este PR:** CHANGELOG (set–out), TODO (seção "Aberto agora"), README (contagens, seção do
   prompt gate), AGENTS.md (comandos do CI), CLAUDE.md (contagens e entrada do prompt gate).

## Armadilhas aprendidas (não repetir)

- **`PROMPT_GATE_PYTHON` com `Path.resolve()` aponta para o Python errado.** `.venv/bin/python` é
  link simbólico; resolvido, vira o interpretador base do `uv`, que não tem pytest, e o Stop hook
  só avisa em vez de testar. Usar `.absolute()`.
- **`uvx ruff@0.14.14` precisa de rede na primeira vez.** No Mac deu timeout no PyPI; a solução foi
  instalar `ruff==0.14.14` no `.venv` e apontar `PROMPT_GATE_RUFF` para `.venv/bin/ruff`.
- **Sessão aberta em subdiretório não carrega `.claude/settings.json`** — nem gate de PII, nem
  Stop hook, nem o deny de `downloads/`. Abrir o Claude Code sempre na raiz.
- **Literais com forma de CPF, CNPJ ou CNJ formatado** são barrados pelo gitleaks mesmo com dígito
  inválido. Testes montam esses valores em tempo de execução.
- **Teste de `.gitignore` não pode usar `git check-ignore` direto no checkout:** um
  `.git/info/exclude` local faz o teste passar sem a mudança.
- **PR que sai de rascunho dispara a revisão do Codex.** No #72 o merge veio 9 s depois e o resumo
  ficou "Running" sem achados; para revisão de verdade, comentar `@codex review`.

## Aberto (prioridade)

1. **Revisão D2 do prompt gate em 2026-10-17** — `python3 tools/prompt_gate.py --report`; regra
   só vira bloqueio com ≥ 60 avisos rotulados e ≤ 2 FP. Rotular avisos até lá.
2. **Job perdido no deploy** (BLPOP sem ack, sem `stop_grace_period`) — maior risco de perda de
   dado hoje, caminho TJES. Precisa de spec + premortem.
3. **Rotação do audit log só no start da dashboard** — e `/data/audit` sem cópia com o sync off.
4. **Dependabot #70** — atualizar o branch e rodar a suíte com Redis vivo antes do merge.
5. **Rotação da chave SSH de deploy e da `DASHBOARD_API_KEY`** — adiada em 2026-07-25; não
   reverificada.

Fonte de verdade dos itens: `TODO.md` §"Aberto agora (revisão de 2026-10-10)".
