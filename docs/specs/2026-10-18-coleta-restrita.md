# SDD — Coleta restrita por IDs no TJES

## Goal

Permitir que o operador escolha documentos de um único processo antes de qualquer
requisição de conteúdo binário, sem ampliar automaticamente para anexos ou inteiro
teor. O fluxo legado sem filtro permanece compatível. Nenhum dado real de autos será
incluído em código, fixtures, documentação pública ou mensagens de commit.

## USER VALIDATION GATE

O usuário determinou em 18/10/2026 promover a coleta restrita com agentes e skills.
A autorização cobre implementar e verificar a funcionalidade necessária ao piloto,
mantendo as proibições de alteração processual, assinatura, protocolo e MinutaIA.
Oito IDs do piloto permanecem apenas no relatório privado e na execução autorizada.

## writing-plans

Pesquisa prévia no código identificou que o dashboard publica um job por processo;
não havia filtro documental. O worker usa MNI, REST e depois browser; este último
possui download integral e varredura de links individuais. Filtrar os arquivos após
esses caminhos não é suficiente. Adotar ramo isolado que falha sem ampliar escopo.
Os nomes writing-plans, subagent-driven-development e plan-quality-gate atendem
ao verificador estrutural do repositório; esses pacotes específicos não estão
disponíveis e não se declara sua execução. A skill Browser governa a etapa local.

| Risco observado | Decisão |
|---|---|
| Lista vazia interpretada como ausência | Campo presente vazio é inválido |
| IDs associados a vários processos | Apenas um processo por lote restrito |
| Anexos automáticos | Só IDs explicitamente selecionados |
| GDrive e browser integral | Bloqueados no ramo restrito |
| Recuperação perde filtro | Persistir campo em progresso, relatório e retomada |
| Duplicação de bytes | Completude por referências de ID, não quantidade de blobs |
| Metadados podem conter conteúdo inline | Não alegar garantia absoluta sobre bytes da resposta do tribunal |

### Task 1 — Contrato e dashboard (TDD)

Validar lista não vazia de até cem strings decimais ASCII, normalizar duplicatas
preservando ordem. Expor documento_ids na API e documentoIds na fila; rejeitar
GDrive, múltiplos processos e formatos inválidos antes de publicar. Preservar
filtro no ciclo de persistência, recuperação e nova tentativa. Interface deve
mostrar claramente quando a coleta está restrita e transmitir o mesmo filtro.

### Task 2 — Execução restrita (TDD)

Revalidar payload no worker. Identificar documentos a partir dos metadados do
processo e requisitar binários apenas dos IDs permitidos. Não chamar caminhos de
download integral ou varredura de links sem IDs auditáveis. Documento ausente,
falha de rede, conteúdo inválido ou anexo não suportado deve produzir falha ou
resultado parcial com arquivos preservados, sem transformar seleção em coleta
integral. Erro de disco mantém tratamento terminal existente.

### Task 3 — Verificação e entrega

Rodar regressões do contrato, dashboard e worker e a suíte completa. Executar
Ruff 0.14.14, verificador de specs e diff-check. Registrar skips ambientais,
sem confundir testes mockados com coleta judicial real. Revisão independente
deve procurar ampliação em retomada, fallback, anexos e deduplicação. Fazer
commits por incrementos validados (frequent commits), PR revisável e conferir CI.

## subagent-driven-development

Um agente mapeia contrato e riscos sem edição; outro implementa dashboard,
protocolo e interface; outro implementa worker e bordas MNI/REST. O coordenador
mantém esta especificação, integra as alterações e executa verificações globais.
Arquivos de implementação não serão editados por agentes concorrentes. A revisão
independente examina o conjunto depois da integração.

## plan-quality-gate

Critérios de aceite: entrada inválida nunca vira coleta integral; filtro atravessa
fila e retomada; binários fora do filtro não são requisitados pelo cliente;
pendências não viram done; referências duplicadas não se perdem; legado continua
aprovado. A ausência de serviço local, autenticação ou captura de download pode
bloquear o piloto real e deve ser reportada separadamente da implementação.

## Referências

- AGENTS.md e CLAUDE.md do projeto.
- protocol.py, dashboard_api.py, worker.py e mni_client.py, master consultada em 18/10/2026.
- docs/testing/2026-10-18-tjes-1g-piloto.md.
