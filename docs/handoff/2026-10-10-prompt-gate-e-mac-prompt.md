# Prompt de retomada — pje-download, 2026-10-10

Abra o Claude Code **na raiz** do repo (`cd /Users/felipemoulin/pje-download && claude`) e cole
o bloco abaixo.

```
Leia primeiro, na íntegra: docs/handoff/2026-10-10-prompt-gate-e-mac.md e TODO.md
(seção "Aberto agora (revisão de 2026-10-10)").

Projeto: pje-download (fbmoulin/pje-download). Branch default: master. Escopo: só TJES.

Antes de qualquer coisa:
- Confira o estado real: `git status -sb`, `git log --oneline -5`, `git fetch origin master`
  e compare com origin/master (o ponteiro local não anda sozinho).
- Suíte: `.venv/bin/python -m pytest tests/ -q`. Sem Redis, 2 testes pulam em silêncio
  (esperado: 918 passed, 2 skipped); com Redis vivo, 920 passed.
- Lint como o CI: `.venv/bin/ruff check .` e `.venv/bin/ruff format --check .` (ruff 0.14.14).
- Push no master redeploya produção, exceto diffs só em **.md, docs/**, .serena/** ou .claude/**.
- Nunca escreva literais com forma de CPF, CNPJ ou CNJ formatado (o pre-push e o gitleaks
  bloqueiam); testes montam esses valores em tempo de execução.

Próximo passo sugerido: a revisão D2 do prompt gate é em 2026-10-17
(`python3 tools/prompt_gate.py --report`). Fora isso, o maior risco aberto no caminho TJES é
o job perdido no deploy (BLPOP sem ack) — exige spec + premortem antes de código.
Pergunte qual item atacar antes de começar.
```
