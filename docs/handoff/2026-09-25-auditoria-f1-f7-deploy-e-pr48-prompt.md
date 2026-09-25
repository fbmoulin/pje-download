# Prompt de retomada — pje-download, 2026-09-25

Cole o bloco abaixo numa sessão nova. Ele é auto-suficiente: as decisões fechadas estão
repetidas no corpo, não só linkadas.

```
Leia primeiro, na íntegra, o handoff desta sessão no checkout atual:
docs/handoff/2026-09-25-auditoria-f1-f7-deploy-e-pr48.md

Projeto: pje-download (fbmoulin/pje-download no GitHub). Branch default: master.
Branch de trabalho: claude/full-analysis-bgj8hi — NUNCA desenvolva nem empurre para outro
branch sem permissão explícita.

⚠️ ANTES DE QUALQUER COISA:
- Confirme o estado real, não confie em números deste prompt: `git status -sb`,
  `git log --oneline -5`, e (se tiver acesso ao GitHub) o estado do PR #48.
- Suba um redis antes de rodar a suíte: sem ele, 2 testes de socket real PULAM EM
  SILÊNCIO ("588 passed, 2 skipped" parece verde e não prova nada sobre o pin do redis).
- Use ruff 0.14.14 localmente (é o pinado no CI); versões mais novas relatam ~200 erros
  que o CI não pede.
- O validador de PII lê o diff do STDIN: `git diff ... | python tools/validate_br_pii.py`,
  nunca um caminho de arquivo direto.
- Push no master REDEPLOYA produção, exceto diffs puramente em **.md, docs/**, .serena/**
  ou .claude/** (paths-ignore do ci.yml — confirmado de novo nesta sessão via o PR #48).

Estado ao fim da sessão anterior: estável, nada pendente de código.
- Auditoria completa (relatório docs/reports/2026-09-20-full-analysis.md, achados F1–F7)
  fechada em dois PRs, ambos mesclados e DEPLOYADOS em produção: #46 (F1+F2) e #47
  (F3–F7 + follow-up do Codex + rodadas de review). master está em 684b920.
- O deploy do 684b920 (run 36077990351) confirmou build_sha batendo em worker e
  dashboard, e a PRIMEIRA execução ao vivo do smoke test de credenciais MNI (F3) leu
  "result=valid status=mni_error" — ou seja, credenciais corretas de produção são
  reconhecidas. Só falta, por desenho (exigiria deployar segredo errado de propósito), o
  outro lado: senha errada -> INVALID.
- Isso foi registrado num PR docs-only, #48 (branch claude/full-analysis-bgj8hi contra
  master), aberto como draft, mergeable_state clean, inscrito em subscribe_pr_activity,
  com check-in horário armado (trig_017ULHokiCXojncMYbidosuB). Esse PR NUNCA vai rodar
  CI -- o único arquivo do diff bate o paths-ignore do ci.yml (**.md e docs/**) -- e isso
  é o comportamento correto, não uma falha para investigar.
- O branch claude/full-analysis-bgj8hi foi reconstruído do zero em cima de master (fetch +
  checkout -B + cherry-pick do commit de docs + push --force-with-lease) porque ainda
  carregava as ~25 commits pré-squash de #47 de antes do restart pós-merge. Nenhum
  trabalho foi perdido -- tudo já estava em master via #47.

Próxima ação:
1. Nenhuma ação de código pendente. O único item aberto é o PR #48: tirar do draft e
   mergear (é um diff textual de 3 linhas, sem risco), ou deixar o check-in horário
   confirmar sozinho até alguém mergear.
2. Se for retomar trabalho de código, os follow-ups residuais (nenhum bloqueante) estão
   na seção "▶ Próxima ação concreta" do handoff e na seção "Residual follow-ups" do
   relatório docs/reports/2026-09-20-full-analysis.md: pinagem de host key do deploy
   (precisa de secret VPS_HOST_KEY fornecido por fora), TestDocumentSavedAudit flaky
   quando rodado isolado com -k (pré-existente, passa na suíte inteira), threshold do
   alerta de lag vs. tick, e -- operação, não código -- o fallback Playwright (F4) fica
   dormente até alguém gerar /data/pje-session.json na VPS.

Já decidido, NÃO reabra:
- PR #48 foi RECONSTRUÍDO do branch, não incrementado -- decisão técnica desta sessão
  para tirar as 25 commits obsoletas que deixavam o PR mergeable_state: dirty. Não é uma
  perda de trabalho: tudo que elas continham já está em master via #47.
- Não dispare CI manualmente no PR #48 achando que "pending" é uma falha -- é o
  paths-ignore funcionando como desenhado para diffs puramente documentais.

Contexto adicional:
- Relatório técnico completo da auditoria (com SHAs de cada fix): docs/reports/2026-09-20-full-analysis.md
- Handoffs anteriores (2026-07-25): docs/handoff/2026-07-25-ci-pin-guarda-pii-e-revisao.md
  -- ainda tem decisões vivas (repo continua público; rotação de chave SSH/API key
  adiada; 3 números CNJ no repo ficam por serem públicos; .serena/memories/ não foi
  destrancado de propósito). Não repita essas decisões sem fato novo.
```
