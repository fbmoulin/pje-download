# Handoff — SPEC-D2-001, 14/11/2026

## Estado verificável

| Item | Evidência |
|---|---|
| Repositório / base | `fbmoulin/pje-download`, `master` consultada em `e5ec846` |
| Entrega | [PR103](https://github.com/fbmoulin/pje-download/pull/103), branch `fix/d2-rotulagem-por-regra`, em rascunho |
| Código validado | `e0a5d7233839fde6272f9e4bbaf128da29741b51`, árvore `c784f4062f7b6a5c0f1ae3cfa0e7f31df2e50bba` |
| CI remoto | [43551254323](https://github.com/fbmoulin/pje-download/actions/runs/43551254323), success; **1331 passed, 0 skipped, 1 warning**, Redis real |
| Testes locais | Gate 247 passed; suíte 1317 passed, 14 skipped (Redis ausente), 1 warning |
| Checks locais | Ruff 0.14.14 check/format, 10 specs estruturais, detector PII e diff-check aprovados |
| Integração/produção | Nenhum merge/deploy desta entrega; instalação no Mac e estado da VPS não consultados |

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

## Próxima sequência

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

**T6 e T7 continuam pendentes.** O registro de 17/10 usava uma medição do Mac de 10/10
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
