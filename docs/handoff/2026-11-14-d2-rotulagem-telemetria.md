# Handoff — SPEC-D2-001, 14/11/2026

## Decisão D2 ratificada por Felipe — 14/11/2026

Felipe aprovou expressamente nesta conversa **manter as três regras de qualidade
em modo advisory**, sem promover bloqueios nem remover regras:
`alvo_ou_sintoma`, `possui_dod` e `multi_tarefa`.

**Fonte:** relatório real do Mac fornecido por Felipe, gerado em
14/11/2026 às 05:07:24 UTC (02:07:24 BRT), na fonte local padrão
`~/.local/state/pje-prompt-gate`. Telemetria válida, 1 evento de 10/10/2026
às 07:33:38 UTC e nenhum rótulo. O mesmo evento disparou as duas primeiras
regras; a terceira teve zero avisos. Não há 60 classificações por regra.

| Regra | Avisos | Rotulados | Decisão |
|---|---:|---:|---|
| alvo_ou_sintoma |1|0| Manter aviso; não promover |
| possui_dod |1|0| Manter aviso; não promover |
| multi_tarefa |0|0| Manter aviso; não promover |

A justificativa é insuficiência de evidência para bloqueio; não houve classificação
inventada do evento antigo. Os smokes isolados não compõem a amostra real.
A decisão **encerra a revisão D2 datada/Task9 e T7**, substituindo o calendário
anterior de extensões. É continuidade advisory expressamente aprovada, **não**
prorrogação retroativa nem satisfação dos critérios estatísticos. Não se cria
novo prazo ou tarefa agendada.

A proteção existente de CPF/CNPJ e o Stop permanecem inalterados. Uma futura
promoção/remoção exige nova revisão e autorização específicas; o relatório não
altera configurações. A rotulagem contextual do evento antigo fica opcional para
investigação futura e não bloqueia esta decisão. A consulta da fonte e os smokes
foram concluídos; isso não atesta todas as sessões, fontes ou comportamento futuro.

Esta aprovação trata da política D2; **não autoriza merge ou deploy do PR103**,
nem ações no caso KCP. Nenhum settings ou código foi alterado para esta decisão.

## Medição do Mac e teste integrado — atualização posterior em 14/11/2026

Esta seção atualiza o estado de T6 e prevalece sobre as pendências históricas abaixo.
**Fonte de evidência:** saídas do Terminal e do Claude Code fornecidas por Felipe
nesta conversa; não houve acesso remoto direto ao Mac ou à VPS.

- Cópia no Mac na branch do PR103, commit `7b4cbfa`, sem alterações locais
  conforme `git status --short --branch`.
- Relatório real gerado em **14/11/2026 às 05:07:24 UTC** (02:07:24 BRT), na
  fonte local padrão `~/.local/state/pje-prompt-gate`.
- Telemetria válida, arquivo de rótulos ausente; **1 prompt avaliado**, exclusivamente
  de **10/10/2026 às 07:33:38 UTC**; zero bloqueios de PII registrados nessa fonte.
- `alvo_ou_sintoma`:1 aviso,0 rotulados; `possui_dod`:1 aviso,0 rotulados;
  `multi_tarefa`:0 avisos. As duas primeiras contagens pertencem ao mesmo evento.
  **Nenhuma regra mecanicamente elegível para bloqueio.**
- Configurações do projeto e local mostraram UserPromptSubmit/Stop no projeto,
  sem disableAllHooks ou PROMPT_GATE_DISABLE ativos nesses dois arquivos.
  Isso não constitui auditoria de todas as configurações possíveis do cliente.
- Smoke direto do programa: exit0, aviso esperado, sem erro; estado temporário.
- Smoke integrado: Claude Code exibiu o aviso UserPromptSubmit e respondeu sem
  ferramentas/alterações, conforme o transcript fornecido. A sessão usou outro
  XDG_STATE_HOME temporário. Não foi executado um teste do Stop nesta sessão.
- Relatório real após o smoke preservou exatamente a amostra antiga: os testes
  sintéticos **não entram** na revisão D2. IDs e texto dos prompts não são publicados.

**Conclusão técnica:** CLI e ligação UserPromptSubmit funcionaram nesse teste local.
A pequena amostra não explica por si só o baixo uso registrado; pode haver outras
pastas, sessões ou fontes. Não afirmar que todos os usos do Claude foram cobertos.
T6: consulta da fonte fornecida concluída; rotulagem contextual do único evento
permanece disponível para investigação, sem bloquear a decisão. T7 foi concluída pela ratificação no início deste documento.
Não houve promoção, remoção, nova extensão, merge ou deploy.

**Proposta histórica, posteriormente ratificada acima:** não promover nenhuma regra; se Felipe desejar
manter os avisos, registrar decisão explícita de continuidade advisory que substitua
o calendário de revisão anterior. Não apresentar essa escolha como extensão
automática ou como cumprimento dos critérios estatísticos.

## Estado anterior à medição (histórico desta entrega)

| Item | Evidência |
|---|---|
| Repositório / base | `fbmoulin/pje-download`, `master` consultada em `e5ec846` |
| Entrega | [PR103](https://github.com/fbmoulin/pje-download/pull/103), branch `fix/d2-rotulagem-por-regra`, pronto para revisão (sem merge) |
| Código validado | `e0a5d7233839fde6272f9e4bbaf128da29741b51`, árvore `c784f4062f7b6a5c0f1ae3cfa0e7f31df2e50bba` |
| CI remoto | [43551254323](https://github.com/fbmoulin/pje-download/actions/runs/43551254323), success; **1331 passed, 0 skipped, 1 warning**, Redis real |
| Testes locais | Gate 247 passed; suíte 1317 passed, 14 skipped (Redis ausente), 1 warning |
| Checks locais | Ruff 0.14.14 check/format, 10 specs estruturais, detector PII e diff-check aprovados |
| Integração/produção | Nenhum merge/deploy; execução da branch no Mac reportada acima; VPS não consultada |

O aviso é o RuntimeWarning de AsyncMock em `test_worker_lazy_browser.py`, fora da
fatia. O CI comprova os testes daquela revisão; não comprova telemetria de uso real,
aceite jurídico ou comportamento atual do host.

Esta atualização posterior altera somente Markdown. Pelo `paths-ignore` atual,
um PR com apenas docs pode ser ignorado pelo CI. Uma sincronização do PR103 pode
rodar CI porque seu diff completo inclui Python; conferir a execução real. Pelo
guard de deploy, um CI de pull_request não
implanta produção. Um futuro merge do PR103 inclui Python e pode disparar o fluxo
normal de CI/deploy de master: depende de decisão de integração, não deste handoff.

## O que mudou

- Rótulos v2 append-only por `(id, regra)`, com TP/FP independentes no mesmo aviso.
- Forma curta legada aceita somente evento com uma regra. Legado multirregra fica
  pendente; não copiar o mesmo rótulo para todas as regras.
- Fonte ausente, amostra vazia e evidência inválida têm resultados diferentes.
  O relatório informa caminho, geração, período, contagens e elegibilidade mecânica.
- Histórico preservado; nenhuma promoção de qualidade, remoção ou extensão D2.
  PII, isenções e Stop preservados; apenas o texto de instrução do aviso muda.

## Roteiro histórico de medição e revisão (executado; integração ainda pendente)

1. Conferir estado/HEAD do PR103 e CI antes de decidir sua integração; não inferir
   aprovação de merge a partir do sucesso dos testes.
2. Depois de integração autorizada, atualizar a cópia correta no Mac e confirmar
   o commit instalado, preservando `settings.local.json` e os arquivos de estado.
   Se o PR ainda estiver aberto, não apresentar o CLI antigo como a correção nova.
3. Na raiz da cópia correta, executar `python3 tools/prompt_gate.py --report`.
   Conferir `${XDG_STATE_HOME:-~/.local/state}/pje-prompt-gate`, período e status
   dos arquivos. Não confundir fonte de outro ambiente com o uso do Mac.
4. Revisar localmente cada regra disparada no contexto original, sem enviar o
   transcript nem autos. Exemplos de sintaxe, substituindo os placeholders:

   ```text
   python3 tools/prompt_gate.py --label <id> alvo_ou_sintoma tp
   python3 tools/prompt_gate.py --label <id> possui_dod fp
   python3 tools/prompt_gate.py --report
   ```

5. **T7, Felipe:** decidir por regra, registrar fonte/período, contagens, revisor,
   data real, justificativa e decisão na Task 9. Pelo menos 60 rótulos válidos e no máximo 2 FP é
   elegibilidade mecânica, não autorização automática para bloquear.

**Registro anterior à medição: T6 e T7 estavam pendentes; ver atualização no início.** O registro de 17/10 usava uma medição do Mac de 10/10
(um aviso, zero rótulos); não é resultado atual. 31/10 era a data prorrogada;
14/11 é o limite da última extensão possível, sem concessão registrada. Não criar
extensão retroativa nem encerrar a política com base nos dados deste ambiente.

## Interpretação do relatório

| Estado | Exit | Ação |
|---|---|---|
| `TELEMETRIA_AUSENTE` |1| Conferir máquina, usuário e XDG_STATE_HOME; não criar arquivo vazio para sanar |
| `AMOSTRA_VAZIA` |0| Zero eventos nessa fonte; verificar se o hook foi usado; sem elegibilidade |
| `EVIDENCIA_INCOMPLETA` |1| Não decidir pelos subtotais; conservar originais e investigar diagnóstico |
| Fonte válida |0| Revisar denominadores e pendências; nenhuma configuração é alterada |

Use um rotulador por vez. O relatório é um retrato dos arquivos lidos, não snapshot
transacional de escritas concorrentes; se houver inconsistência temporária,
executar novamente após a escrita terminar. Não apagar linhas para obter resultado
verde. Rótulos específicos prevalecem sobre legados; reclassificação não aumenta
o denominador. O CLI não autentica a procedência da fonte nem separa smokes antigos.

## Rollback e limites

Reverter código não apaga os rótulos v2. O relatório do CLI antigo não interpreta
esses rótulos corretamente para D2 e não deve fundamentar a decisão após rollback.
Não existe migração destrutiva. Nenhum settings, dependência ou workflow mudou.
Permanece em aberto a divergência documental entre knobs locais e promoção no
settings versionado da spec original: resolver em escopo futuro aprovado.

O caso KCP expirado é outro projeto e exige identidade real de juiz e decisão sobre
o documento identificado. Não reabrir, rejeitar, eliminar ou reenfileirar por conta
da aprovação da correção D2.

## Fontes

- [Spec incremental](../specs/2026-11-14-d2-rotulagem-telemetria.md), aprovada por Felipe antes da implementação.
- [Spec original / Task 9](../specs/2026-10-03-prompt-quality-gate.md).
- [README](../../README.md), [AGENTS](../../AGENTS.md), [TODO](../../TODO.md), [CLAUDE](../../CLAUDE.md).
- [Guia do dono](../ops/guia-do-dono.md).
