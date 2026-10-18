# SDD — Integridade de documentos e retomada CLI TJES

## Goal

Corrigir os defeitos reproduzidos no commit b5d7693: HTTP 200 com HTML gravado como PDF,
Base64 inválido gravado como vazio, perda de identidade lógica ao deduplicar bytes e
coleta CLI parcial marcada done e ignorada na retomada. Foco exclusivo no caminho TJES.
Preservar os contratos de filas/dashboard e a distinção entre erro de rede e disco cheio.

## USER VALIDATION GATE

Autorizado pelo usuário em 18/10/2026: pesquisa e plano prévios, seguidos da correção
das falhas reproduzidas. O processo de teste real será do primeiro grau e ainda será
definido pelo usuário. Esta autorização cobre código e testes fictícios, sem coleta
judicial, envio ao MinutaIA, deploy ou alterações no PJe. Não é necessário pedir nova
aprovação para implementar as correções aqui delimitadas.

## Pesquisa e decisões anteriores ao fixing

| Evidência | Decisão |
|---|---|
| Python 3.12 descarta caracteres não alfabéticos em b64decode por padrão. | Normalizar apenas whitespace ASCII do transporte e usar validate=True; não reparar padding nem descartar caracteres arbitrários. |
| Playwright expõe headers/body/status separadamente. | HTTP 200 não prova que o corpo é documental; rejeitar vazio, autenticação e PDF incompatível. |
| os.replace é atômico se a operação for bem-sucedida no mesmo filesystem. | Usar temporário vizinho e promover somente após validação; limpar temporário na falha. Não prometer durabilidade a power loss. |
| MNI pode entregar peça HTML legítima. | Não rejeitar todo HTML: distinguir documento de formulário de login; critérios explícitos e testes negativos. |
| SHA-256 deduplica conteúdo, não identidade da peça. | Preservar uma lista aditiva de referências documentais no blob único; não inflar a contagem de arquivos/bytes. |
| CLI e seus relatórios conhecem done/failed. | Usar failed existente para perda conhecida, preservando os arquivos e bytes obtidos; reabrir legado done com erro. |

Validação PDF inicial é deliberadamente limitada a identificação e término do formato;
não é certificação de assinatura digital nem prova de validade semântica de todas as páginas.
Evitar introduzir parser pesado ou alegar validação estrutural completa nesta correção.
Regressões anteriores com payloads fictícios que se diziam PDF devem usar fixtures com
formato coerente, sem afrouxar o validador para satisfazer testes.

## writing-plans

O fluxo de planejamento descrito aqui segue requisitos do verificador local. As skills
writing-plans, subagent-driven-development e plan-quality-gate não estão disponíveis
como pacotes nesta sessão; sua menção é identificação do fluxo exigido pelo projeto,
não declaração de execução de ferramentas ausentes. A coordenação por agentes e a
revisão independente usam as capacidades disponíveis.

### Task 1 — Regressões e validação de conteúdo (TDD)

Ver testes falharem no código atual para HTML-login/HTTP200, PDF vazio, Base64 !!!! e
padding inválido. Implementar helper compartilhado e usar nas bordas REST/MNI, com escrita
atômica e hashes contabilizados só depois da publicação. Manter audit de erro e DiskWriteError.
Testar whitespace Base64 do transporte, peça HTML legítima, PDF válido e falha de disco.
Na revisão independente, ampliar o teste até o chamador da listagem REST: um erro de
disco deve escapar do catch genérico e um documento rejeitado deve sobreviver como
failed_ids até o resultado partial_success, inclusive quando todos os itens REST falham.

### Task 2 — Estado CLI e recuperação (TDD)

Perda MNI ou anexos pendentes devem aparecer como failed em memória, progress e report,
com métrica coerente, arquivos obtidos preservados e saída CLI não bem-sucedida. Resume
reabre done legado com erro e uma nova tentativa limpa os avisos/contadores anteriores.
Sucesso com checksum duplicado continua sucesso. Nenhuma mudança no caminho dashboard.

### Task 3 — Identidade documental e deduplicação (TDD)

Acrescentar metadados estruturados de ID/MIME/parentalidade às saídas. Manter todos os IDs
em referências aditivas quando dois documentos compartilham um blob; merge deve preservar
essas referências sem mutar entradas ou contar bytes duas vezes. Preservar API de lista.
Manifesto completo e retomada por documento são incrementos posteriores, não alegar sua
implementação a partir desta mudança mínima.

### Task 4 — Plano eficiente de piloto TJES primeiro grau

Pesquisar fontes oficiais e definir amostra, baseline manual, sequência serial, métricas,
paradas e condições de envio ao MinutaIA. Falta apenas a escolha do processo e o escopo
de execução autenticada. Não executar rede judicial nesta task.

## subagent-driven-development

Um agente pesquisa o protocolo; outro cuida exclusivamente da CLI e seus testes; outro
valida conteúdo REST/MNI. O coordenador implementa preservação de identidade depois da
edição MNI pelo agente, revisa diffs e executa checks integrados. Não editar o mesmo arquivo
simultaneamente. Não fazer commits dos outros agentes em paralelo.

## plan-quality-gate

Premortem: rejeitar toda peça HTML perderia documentos válidos; testes antigos com bytes
aleatórios poderiam mascarar validação; adicionar hash antes da escrita poderia ocultar
falha de disco; limpar aviso apenas no final manteria estado anterior; dedup por ID apenas
poderia misturar fontes. Cada risco tem teste de regressão/compatibilidade. Retomar um
processo CLI inteiro é recuperação existente, não resumir por documento.

Definition of Done: testes direcionados e suite completa com Redis local quando disponível;
skips e falhas ambientais reportados; ruff==0.14.14 check/format no repo inteiro;
tools/verify_spec.py para todas specs; git diff --check; alterações revisáveis em branch
isolada. Commits frequentes (frequent commits) por incremento quando todos os checks
requeridos estiverem disponíveis; não anunciar commit, push, merge ou deploy não realizados.

## Referências

- https://docs.python.org/3.12/library/base64.html
- https://docs.python.org/3.12/library/os.html#os.replace
- https://playwright.dev/python/docs/api/class-apiresponse
- AGENTS.md e CLAUDE.md do projeto; auditoria precedente e probes offline.
