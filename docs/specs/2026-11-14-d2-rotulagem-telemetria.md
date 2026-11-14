# SPEC-D2-001 — Revisão confiável do Prompt Quality Gate

**Versão:** 0.1.1 — proposta aprovada por Felipe em 14/11/2026; implementação validada localmente; CI remoto pendente.
**Data:** 13/11/2026, referência temporal da sessão.
**Projeto:** fbmoulin/pje-download.
**Método:** Spec-Driven Design: evidências → requisitos → contratos → pré-mortem → tarefas → testes → decisão.
**Estado:** implementação validada localmente; nenhuma alteração de política ou decisão D2 realizada.

## 1. Objetivo e decisão proposta

Tornar verificável a decisão D2 de manter, ajustar, remover ou promover cada regra de qualidade do prompt. Corrigir a atribuição de rótulos por regra e distinguir ausência de telemetria de uma amostra efetivamente vazia, reutilizando o programa stdlib existente.

Recomendação: nenhuma promoção de qualidade nesta entrega. A promoção depende de dados locais válidos, rotulagem individual e decisão expressa. A proteção existente de CPF/CNPJ e o Stop hook não são alterados.

O caso KCP com revisão expirada exige decisão operacional separada; não será usado como justificativa para alterar retenção ou executar uma nova geração. Seu roteiro está no apêndice A.

## 2. Evidências e limites

Baseline consultada: pje-download `e5ec846713959d99d3c9bcc669a83f6ed55bf2f3`. Revalidar HEAD e diff antes da futura implementação.

| ID | Evidência por inspeção | Consequência |
|---|---|---|
| E01 | `_rotular` grava `{ts,id,rotulo}`; `_relatorio` usa esse rótulo em todas as regras do evento | Um aviso misto não pode receber TP numa regra e FP em outra |
| E02 | `_ler_jsonl` retorna lista vazia para arquivo ausente e ignora JSON inválido | Relatório pode parecer vazio ou completo sem demonstrar integridade da fonte |
| E03 | Estado em `${XDG_STATE_HOME:-~/.local/state}/pje-prompt-gate` | Relatório de outra máquina não demonstra o uso do Mac |
| E04 | Qualidade avalia o primeiro prompt elegível; perguntas, comandos e pedidos curtos têm isenções | Amostra é selecionada; não mede todas as interações nem taxa de falsos negativos |
| E05 | Task 9 exige ≥60 avisos rotulados e ≤2 FP por regra; até duas extensões de 14 dias | Critério depende de unidade de contagem inequívoca e decisão registrada |
| E06 | Registro histórico: um aviso e zero rótulos, medição de 10/10, extensão decidida em 17/10 até 31/10 | Não é medição atual. A extensão possível até 14/11 não está automaticamente concedida |

Fontes fixadas:
- [Código do gate](https://github.com/fbmoulin/pje-download/blob/e5ec846713959d99d3c9bcc669a83f6ed55bf2f3/tools/prompt_gate.py).
- [Spec original e Task 9](https://github.com/fbmoulin/pje-download/blob/e5ec846713959d99d3c9bcc669a83f6ed55bf2f3/docs/specs/2026-10-03-prompt-quality-gate.md).
- [Regras do repositório](https://github.com/fbmoulin/pje-download/blob/e5ec846713959d99d3c9bcc669a83f6ed55bf2f3/AGENTS.md).

Não executado nesta elaboração: telemetria do Mac, hooks reais do Claude, testes do produto, consultas ao banco KCP ou operações de produção. Os achados são de código, não frequência operacional medida.

## 3. Escopo e invariantes

Dentro: comandos manuais `--label`/`--report`, leitura de evidências para D2, testes correspondentes e atualização documental.

Fora: classificador semântico, API paga, nova plataforma, dashboards, alteração da geração jurídica, PJe, retenção KCP, importação de transcripts e promoção automática.

Invariantes:
1. Telemetria e rótulos permanecem locais, fora do git, sem texto de prompt, hash do prompt, CPF/CNPJ ou conteúdo dos autos.
2. Nenhuma leitura de relatório escreve estado, cria uma fonte ausente ou altera configurações.
3. Rótulos brutos existentes são preservados; não há migração destrutiva.
4. Idempotência estatística: cada par `(id, regra)` contribui uma única vez.
5. Decisão de qualidade não altera PII, isenções, vaga do primeiro prompt ou comportamento do Stop.
6. Os dados não são tratados como prova de anonimização, correção jurídica ou representatividade de todas as sessões.

## 4. Requisitos verificáveis

| ID | Requisito | Aceite |
|---|---|---|
| RF01 | Rotular por ID e regra | Mesmo evento permite TP para uma regra e FP para outra |
| RF02 | Validar vínculo antes da escrita | ID inexistente, regra desconhecida ou que não disparou: recusa, sem append |
| RF03 | Compatibilidade conservadora | Legado de regra única é aproveitável; multirregra é ambíguo, excluído das métricas rotuladas até revisão |
| RF04 | Última classificação específica prevalece | Reclassificação do mesmo par não aumenta denominador; legado não sobrescreve v2 |
| RF05 | Expor a fonte efetivamente lida | Caminho, intervalo temporal dos eventos, geração do relatório e estados dos arquivos visíveis apenas no relatório local |
| RF06 | Distinguir ausente, vazio, válido e inválido | Ausência nunca é apresentada simplesmente como zero avisos |
| RF07 | Evidência parcial impede elegibilidade | Corrupção, leitura incompleta ou ID duplicado tornam decisão inconclusiva; diagnóstico explícito |
| RF08 | Contar por regra | Avisos, rotulados elegíveis, TP, FP, não rotulados e legados ambíguos separados |
| RF09 | Preservar o contrato operacional | PII e Stop continuam com os testes de regressão existentes aprovados |
| RF10 | Registrar decisão humana datada | Revisor, fonte, números por regra, decisão e justificativa na Task 9; sem conteúdo de prompt |

## 5. Contratos propostos

### 5.1 CLI

Nova forma: `python3 tools/prompt_gate.py --label <id> <regra> tp|fp`.

Manter a forma antiga `--label <id> tp|fp` somente se o evento contém exatamente uma regra de qualidade; nesse caso gravar no formato v2. Se contém várias, recusar com instrução para nomear a regra. PII não recebe rótulo de qualidade. ID sem regras de qualidade também é recusado.

Novo registro append-only em `rotulos.jsonl`:

```json
{"schema_version":2,"ts":"<UTC ISO-8601>","id":"<evento>","regra":"possui_dod","rotulo":"tp"}
```

Regras permitidas: `alvo_ou_sintoma`, `possui_dod`, `multi_tarefa`. Um evento tem identidade apenas dentro da fonte local selecionada. Colisão de ID entre eventos é erro de integridade, não deduplicação silenciosa. Não agregar máquinas nesta fatia.

Precedência: último v2 válido por `(id,regra)` na ordem do arquivo; sem v2, último legado de evento com uma única regra. Legado multirregra não é replicado. Suas classificações específicas podem ser refeitas sem apagar o histórico. Rótulo órfão ou versão desconhecida é diagnosticado e impede elegibilidade global até correção/revisão da evidência.

### 5.2 Relatório manual

`--report` mantém saída textual; não há JSON público ou serviço novo nesta fatia.

| Situação | Resultado |
|---|---|
| Telemetria ausente | `TELEMETRIA_AUSENTE`, decisão inconclusiva, exit 1 |
| Telemetria presente e vazia | `AMOSTRA_VAZIA`, zero observado nessa fonte, exit 0, nenhuma regra elegível |
| Telemetria válida, rótulos ausentes | Avisos contados, zero rotulados, exit 0, nenhuma regra elegível |
| Fonte ou rótulos ilegíveis/corrompidos/incompatíveis | `EVIDENCIA_INCOMPLETA`, exit 1; jamais promover com os subtotais |
| Fonte e rótulos válidos | Contagens por regra e resultado mecânico de elegibilidade, exit 0 |

O exit 0 comprova apenas emissão de relatório íntegro; não aprovação da regra. Avisos legados multirregra ambíguos continuam nos totais e pendências, sem entrar em TP/FP. Havendo ≥60 classificações específicas válidas, a existência desses pendentes não os transforma em classificados.

Leitura para relatório/label usa validação estrita própria, sem alterar o leitor tolerante utilizado pelos hooks. Não imprimir a linha inválida bruta: informar arquivo, número da linha e classe do erro. Linha final parcial também torna o relatório inconclusivo; orientar nova leitura quando a escrita acabar, sem reparar automaticamente o arquivo.

O relatório lê a telemetria antes dos rótulos e valida referências entre os snapshots lidos. É retrato local, não snapshot transacional dos dois arquivos. Escrita concorrente com referência ainda ausente produz resultado inconclusivo e exige nova execução. Exclusividade de múltiplas escritas em `--label` permanece fora da garantia desta fatia: usar um único rotulador por vez, documentado.

Mostrar data mínima/máxima e caminho local não autentica a origem nem prova que o hook esteve ativo. Cabe ao operador confirmar a máquina, a configuração e excluir medições sintéticas do conjunto aceito por procedimento documentado, preservando os originais. Não inventar classificação automática de smokes antigos.

### 5.3 Decisão D2

Elegibilidade mecânica preserva a regra da spec: por regra, pelo menos 60 avisos classificados e no máximo 2 FP. Isso não estima recall, nem certifica segurança. Mesmo elegível, a promoção só ocorre em etapa separada, com aprovação e testes da configuração real. Resolver antes a divergência documental entre configuração local descrita no corpo da spec e configuração versionada citada na Task 9; não alterar nenhuma das duas nesta fatia.

Não prorrogar retroativamente, não remover regras nem promover bloqueios por inferência. Registrar datas reais e decisão de Felipe. O limite histórico de extensões permanece o da spec original.

## 6. Pré-mortem

| Falha | Proteção e prova |
|---|---|
| TP/FP de uma regra contamina outra | Evento misto com rótulos opostos deve produzir duas contagens diferentes |
| Máquina errada parece ter zero ocorrências | Fonte ausente é inconclusiva; caminho e cobertura temporal explícitos |
| Atualização converte os rótulos antigos silenciosamente | Brutos preservados, multirregra em pendência, regra única reutilizada |
| Reclassificação aumenta amostra | Último rótulo por par; repetição não altera denominador |
| Arquivo truncado melhora artificialmente as métricas | Diagnóstico de evidência incompleta e nenhuma elegibilidade |
| Falha do relatório muda comportamento de PII | Separação do leitor estrito manual e regressão integral dos hooks |
| Relatório verde vira promoção automática | CLI somente relata; decisão/configuração em etapa independente |

## 7. Plano de execução futuro — sequencial

| Task | Trabalho | Saída e gate |
|---|---|---|
| T0 | Revalidar baseline, AGENTS, hooks, testes e fonte de telemetria; conferir consumidores de `--report` | Inventário e diff mínimo, sem executar hooks de PR não confiável |
| T1 | Escrever testes de rotulagem por regra e compatibilidade | RED comportamental contra implementação antiga, não só erro de import |
| T2 | Implementar formato v2 e resolução de rótulos | GREEN; nenhuma alteração nos arquivos brutos antigos |
| T3 | Escrever testes de fonte ausente, vazia, parcial e inválida | RED com recusa de elegibilidade |
| T4 | Implementar relatório com proveniência e diagnóstico | GREEN; leitura não escreve estado |
| T5 | Rodar regressões, Ruff pinado e verificador de specs do repo; revisar diff | Resultados por SHA, sem confundir skips com passes |
| T6 | Executar relatório somente na fonte correta do Mac e revisar pendências | Medição real, sem fabricar ou enviar transcripts |
| T7 | Felipe decide D2; atualizar Task 9 e instruções de uso | Decisão datada; promoção/configuração somente em escopo posterior autorizado |

Arquivos prováveis: `tools/prompt_gate.py`, testes de prompt gate/telemetria existentes, spec original, `CLAUDE.md` e `TODO.md`. Escolher os testes concretos após inventário, sem inventar nomes de módulos novos. Não criar dependência, banco ou serviço. Não trabalhar em paralelo nos mesmos contratos/arquivos; esta proposta não requer novos agentes.

## 8. Matriz mínima de testes e aceite

- Evento com duas regras: rótulos TP/FP opostos e denominadores independentes.
- Forma antiga: regra única aceita; múltiplas recusadas sem append.
- Legado multirregra não conta; v2 específico recupera somente a regra revisada.
- Reclassificação TP→FP e repetição exata não duplicam contagem.
- ID/regra/rótulo inválidos, ID duplicado e rótulo órfão produzem recusa explícita.
- Telemetria ausente, vazia, rótulos ausentes, arquivo sem permissão e JSON parcial diferenciados.
- Linha inválida não vaza conteúdo em stderr/stdout.
- Limiares: 59/0 não elegível, 60/2 elegível, 60/3 não elegível.
- Ler relatório não modifica mtime/conteúdo dos arquivos de evidência nem cria diretório ausente.
- Regressões existentes: PII antes das isenções, fail-closed interno, primeiro prompt, isenções e Stop.
- Nenhum texto/hash de prompt ou valor de PII em fixtures persistidas, saída ou diff.

Aceite da implementação: todos os RF demonstrados, matriz aprovada, sem mudanças de bloqueio, histórico preservado e comandos documentados. Só depois executar a medição real e registrar decisão D2. Testes desta spec são planejados, não executados.

Rollback: reverter somente o código/documentação desta fatia; preservar `rotulos.jsonl`. Versão antiga não sabe interpretar corretamente v2: após rollback, não usar seu relatório para decisão D2. Não apagar/converter registros para fazê-lo parecer compatível. Não há configuração de produção a restaurar nesta entrega.

## 9. Validação de Felipe

Proposta concreta para validação: (D1) rótulos v2 por regra; (D2) legados multirregra ficam pendentes; (D3) evidência ausente/corrompida nunca habilita promoção; (D4) nenhuma promoção, extensão ou remoção de regra automática. Aprovar esta spec não significa aprovar alteração de política nem operação no caso KCP.

## Apêndice A — caso KCP expirado: roteiro separado, sem código

Fonte: [handoff de 07/11](https://github.com/fbmoulin/kratos-case-pipeline/blob/9b6278d0f814fc815421468492b6187e1855a752/docs/handoff/2026-11-07-kcp-upload-nativo-e-caso-hitl-expirado.md), [rotas](https://github.com/fbmoulin/kratos-case-pipeline/blob/9b6278d0f814fc815421468492b6187e1855a752/src/routers/cases.py), [retenção](https://github.com/fbmoulin/kratos-case-pipeline/blob/9b6278d0f814fc815421468492b6187e1855a752/src/services/retention.py).

1. Consultar estado atual e identificar PDF/minuta; vínculo histórico ao PDF de Felipe é hipótese, não confirmação.
2. Felipe escolhe reabrir revisão, rejeitar a minuta ou tratar eliminação pelo fluxo próprio, conforme a finalidade. Nenhuma escolha é inferida desta spec.
3. Reabrir com identidade real de juiz preserva minuta e não reexecuta IA; conferir estado/prazo e evento `hitl_reopened` após eventual ação autorizada.
4. Rejeitar preserva texto recusado; não equivale a apagar os dados. Arquivamento frio também não equivale a eliminação.
5. Não incluir `hitl_expired` nos terminais por conveniência, não fazer UPDATE direto, não forjar JWT e não executar requeue para recuperar uma revisão.

Este apêndice descreve o procedimento; não confirma o estado atual nem autoriza escrita no dashboard, banco ou PJe.


## Goal e USER VALIDATION GATE

Felipe aprovou o contrato D1–D4 e autorizou prosseguir em 14/11/2026. Isso autoriza a implementação técnica, sem promover regras, decidir o destino do caso KCP ou registrar telemetria fictícia como real.

### Task — Execução TDD e compatibilidade com o projeto

Tarefas T0–T5 sequenciais, com testes de comportamento em RED e GREEN; commits frequentes quando cada fatia estiver consistente. Os nomes writing-plans, plan-quality-gate e subagent-driven-development fazem parte do método histórico do projeto. Não foram executados como skills nesta etapa; nenhuma delegação foi necessária. A validação estrutural do Markdown não é auditoria técnica nem prova de teste executado.

## References

As fontes fixadas e o apêndice A acima vinculam requisitos à evidência. Revalidado HEAD remoto e local e5ec846 em 14/11/2026. O clone está isolado do Mac e da produção.

## Notas da implementação

- RF01–RF08 implementados no CLI, com leitor estrito separado do leitor tolerante histórico; novos rótulos v2, precedência específica e legado multirregra pendente.
- O relatório mostra somente IDs e regras pendentes; session_id/prompt_id permanecem no arquivo local para consulta humana, sem reprodução automática de campos livres.
- T6 permanece dependente da telemetria real do Mac. Nenhum relatório deste ambiente é medição do uso de Felipe.
- T7 permanece pendente de decisão D2: não se registrou extensão retroativa, remoção de regra nem promoção.
- No aceite de leitura, timestamps significa mtime de arquivos/diretórios. atime pode ser atualizado pelo filesystem em uma leitura normal; não há promessa de congelar atime.


## Evidência de execução — 14/11/2026

Base e5ec846; Python 3.12, ambiente virtual isolado. RED contra o código original:
26 falhas comportamentais e 17 passes no conjunto D2/telemetria. GREEN final do gate:
247 passed (núcleo, hooks, Stop, telemetria, D2 e skill refine-prompt). Uma asserção
inicial de apresentação de data foi corrigida para aceitar ISO-8601 com offset UTC,
sem alterar a semântica do relatório.

Suíte completa: 1317 passed, 14 skipped, 1 warning, 78.54s. Os skips são os testes
com Redis real indisponível neste ambiente; não se declara essa integração aprovada.
O aviso é o RuntimeWarning de AsyncMock em test_worker_lazy_browser.py, fora desta
fatia. Ruff 0.14.14 check e format do repositório passaram; 10/10 specs passaram no
verificador estrutural; diff-check e detector de PII passaram. Comparação AST confirmou
funções históricas preservadas, salvo rotular/relatório e a instrução de uso no aviso.

RF10/T6/T7 continuam pendentes: telemetria real do Mac e decisão humana. Aprovação
da implementação não equivale a promover regras ou encerrar a decisão de política.
