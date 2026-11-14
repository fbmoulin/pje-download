# Retomada — pje-download

**Sessão pausada em 14/11/2026.** Retomar pela validação operacional TJES, sem novo desenvolvimento iniciado.

## Estado confirmado

[PR103](https://github.com/fbmoulin/pje-download/pull/103) integrado em 14/11/2026 às 05:16:02 UTC, squash `987e2089f3aca2ddaf3cb209ceacbe60317e6c2f`. [CI pós-merge 43560698713](https://github.com/fbmoulin/pje-download/actions/runs/43560698713) concluído com sucesso às 05:18:22 UTC. [Deploy 43560885678](https://github.com/fbmoulin/pje-download/actions/runs/43560885678) concluído com sucesso às 05:22:00 UTC. Sucesso do workflow não substitui o aceite funcional com processos reais.

- CI do head final do PR, `778e70a`: [43560485828](https://github.com/fbmoulin/pje-download/actions/runs/43560485828), **1331 passed, sem skips, 1 warning preexistente de AsyncMock**, lint aprovado. Essa contagem pertence ao head indicado.
- Mac mini: saída de Felipe confirmou `master...origin/master`, limpa, após fast-forward para `987e208`. Não houve acesso remoto ao Mac nesta sessão; esta documentação posterior ainda não foi confirmada na cópia dele.
- D2/Task9/T7 encerradas por Felipe: três regras advisory, sem promoção/remoção ou extensão retroativa; PII/Stop preservados. [Evidência e histórico D2](docs/handoff/2026-11-14-d2-rotulagem-telemetria.md).
- Relatório real gerado às 05:07:24 UTC: 1 evento de 10/10, nenhum rótulo. Smokes isolados excluídos. Não prova cobertura de todas as sessões.
- Nenhum lote real, teste de Chromium na imagem atual ou teste de interrupção foi executado nesta etapa. Nenhuma ação KCP.

## Ordem de retomada e aceite

1. Revalidar branch/HEAD e estado local; ler o [guia do dono](docs/ops/guia-do-dono.md).
2. Repetir lançamento do Chromium na imagem atual (Passo 4): guardar resultado e versão ou erro, sem concluir sucesso apenas pelo build.
3. Rodar lote de 3–5 processos TJES conhecidos (Passo 2): conferir arquivos, legibilidade, feedback de entrada e `duracao_s`/`timing` no `_report.json`.
4. Com fluxo normal aprovado, preparar Task 6 da [spec de recuperação](docs/specs/2026-10-16-job-perdido-no-deploy.md): interrupção forçada e restart gracioso, somente em janela controlada sem outros lotes ativos. Falta aceite operacional, não implementação.
5. Depois, priorizar watchdog no Mac. Riscos adicionais no [TODO](TODO.md): evicção Redis sob pressão não medida, retenção de auditoria sem cópia e rotação de credenciais adiada.

Não alterar código para “sanear” pendências que ainda dependem de medição. Se houver defeito reproduzível, elaborar spec SDD antes da correção. Não publicar dados judiciais, segredos ou identificação de infraestrutura em evidências públicas.

## Prompt para a próxima sessão

```text
Retome o pje-download (fbmoulin/pje-download) a partir de HANDOFF.md, TODO.md, AGENTS.md e docs/ops/guia-do-dono.md. Estamos pausados após o merge do PR103, commit 987e208, CI pós-merge e deploy aprovados em 14/11/2026. Revalide o HEAD e as evidências atuais antes de agir. No Mac mini, a cópia está em /Users/felipemoulin/pje-download; a última saída fornecida mostrou master sincronizada com origin/master e limpa em 987e208. Pode haver documentação posterior: confira git status antes de atualizar, sem descartar alterações.

D2/Task9/T7 estão encerradas: manter alvo_ou_sintoma, possui_dod e multi_tarefa apenas como avisos. Preservar PII/Stop. A medição real de 14/11 às 05:07:24 UTC continha um evento de 10/10, nenhum rótulo e nenhuma regra elegível. Smokes isolados não contam como uso real; não inventar rótulos nem reabrir calendário.

Próximo passo: guiar Felipe no Mac, um comando por vez, para repetir na VPS o teste de lançamento do Chromium da imagem atual (Passo 4 do guia). Verifique o comando no runbook antes de fornecê-lo e aguarde a saída. Não presuma acesso SSH, credenciais ou browser funcionando; nunca solicite que publique segredos. Depois, orientar lote de 3–5 processos TJES conhecidos, conferindo integridade/legibilidade, feedback de entrada e duracao_s/timing no _report.json. Não publicar números de processos ou documentos reais no repositório público.

Somente após o lote normal aprovado, preparar aceite operacional da Task 6 (recuperação de job), com janela controlada e confirmação de que não há outros lotes ativos antes de interromper o worker. Não executar teste destrutivo, novo deploy, alteração de código ou ação KCP automaticamente. Se surgir defeito, documentar evidências e preparar spec SDD antes de alterar código. Atualizar o checklist apenas com resultados observados.
```

Handoffs anteriores preservam estados históricos. Esta página distingue evidência do workflow, saída fornecida pelo dono e aceite operacional ainda pendente.
