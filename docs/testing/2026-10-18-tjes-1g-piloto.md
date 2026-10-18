# Piloto comparativo — PJe TJES de primeiro grau

Data de pesquisa: 18/10/2026, America/Sao_Paulo. Estado: protocolo preparado; execução autenticada e transmissão ao MinutaIA não realizadas. Escopo exclusivo: TJES primeiro grau. Este protocolo não homologa extensões nem substitui a validação de acesso e finalidade do caso selecionado.

## Objetivo e resultado mensurável

Comparar o download oficial do PJe com o caminho já existente no `pje-download`, usando a mesma amostra de cinco a dez documentos. Somente depois, se houver autorização específica para transmissão e consumo, avaliar o reconhecimento e as respostas do MinutaIA Conecta. A conclusão deve indicar: cobertura documental, integridade, tempo ativo do usuário, tempo total, falhas explícitas, consumo observado e referências verificáveis. Um piloto pequeno serve para decidir o próximo incremento; não estima capacidade de produção nem percentis confiáveis.

Resultado mínimo: uma tabela comparativa anonimizada no relatório de trabalho e um manifesto de execução guardado localmente fora do repositório. Todo documento selecionado deve estar contabilizado como íntegro, duplicata identificada, indisponível na origem ou falha explícita. Não publicar identificadores, nomes, conteúdo, URLs assinadas ou hashes de documentos reais no repositório público.

## Decisões prontas, independentes do processo

| Decisão | Definição para o piloto |
|---|---|
| Ambiente | Computador local autorizado do usuário e pasta exclusiva do piloto. Preferir a sessão Chrome existente via extensão de controle autorizada, sem copiar o perfil; se for necessário Playwright externo, usar perfil dedicado e login humano. Sem alterar produção, VPS, credenciais ou auditoria existente. |
| Amostra | Cinco a dez documentos do mesmo processo autorizado, preferencialmente sem sigilo. Incluir PDF textual, peça do editor, documento escaneado e ao menos um conjunto principal/anexo quando disponíveis. Ausência de um tipo é anotada, não preenchida com outro processo sem ampliar o escopo. |
| Sequência | Baseline oficial → downloader → MinutaIA. Nenhuma comparação com outros tribunais. |
| Concorrência | Um processo e um fluxo por vez. Paralelismo reservado à preparação e análise de código, sem sessões judiciais concorrentes. |
| Extensões | Baseline sem extensão de terceiros atuando sobre o PJe. Habilitar somente a extensão avaliada na etapa correspondente, se já autorizada; instalação ou mudança do perfil depende do escopo aprovado. |
| Dados | Originais e manifesto completo locais, em diretório privado ignorado pelo Git. Relatório compartilhável contém rótulos de amostra, contagens e métricas agregadas. |
| Orçamento | Nenhuma mensagem de IA sem teto expressamente aprovado. Proposta de teto operacional: até três mensagens, sem compra ou renovação de plano; isso ainda não constitui autorização de gasto. |

As instruções `AGENTS.md` e `CLAUDE.md` foram lidas. O dashboard continua plano de controle e o worker plano de execução; o piloto não deve recolocar o batch downloader no caminho HTTP nem criar uma segunda orquestração. Literais com formato de CPF/CNPJ/CNJ são proibidos no repositório, inclusive como exemplo ou fixture inválida. Não há skill específica de ensaio judicial sendo alegada como utilizada nesta pesquisa.

## Etapa A — preparar sem tocar no PJe

1. Registrar versão do código, navegador e extensão por metadados públicos/locais permitidos; construir planilha de observações vazia, sem identificador real.
2. Verificar offline o manifesto e a reconciliação de resultados com arquivos fictícios. As amostras sintéticas não devem imitar números de processos literais.
3. Reservar diretório local privado para downloads, manifestos e eventual estado autenticado. Não usar pasta sincronizada automaticamente com Drive ou outro serviço.
4. Não alterar limites de timeout de produção. Registrar valores efetivos no momento do ensaio e medir cada tentativa; ajuste futuro requer evidência própria.

## Etapa B — referência oficial do TJES

Após autorização de leitura e download da amostra, o usuário confirma perfil, unidade e processo na sessão local. Senhas, certificado, MFA e eventual CAPTCHA ficam sob intervenção do titular. Registrar apenas a origem efetiva e a família da interface, sem copiar parâmetros de sessão. Não fixar um hostname por memória: endereço legado, redirecionamento e frontend atual devem ser conferidos no portal e na tela autorizada.

Inventariar primeiro a amostra, com rótulos internos e correspondência local aos IDs reais. Abrir e baixar os mesmos itens pelo fluxo oficial. O manual publicado pelo TJES descreve download de autos em PDF, seleção por tipo, intervalo de ID/data e ordem crescente/decrescente [1]; a interface atual deve ser observada, pois a documentação histórica não prova seletores vigentes.

Cronometrar separadamente autenticação, preparação, ações do usuário e espera do download. Confirmar arquivo final, quantidade de páginas e natureza textual/digitalizada. Um PDF consolidado pode reorganizar ou transformar peças: não exigir SHA igual entre consolidado e documento individual. Para comparação de bytes, usar a mesma representação do mesmo documento; para PDFs regenerados, comparar estrutura, páginas e conteúdo conferido.

## Etapa C — downloader

Executar um único caminho já suportado pelo projeto, com a mesma conta e escopo aprovados. Se o downloader só permitir o processo inteiro, interromper antes de enfileirar: autorização de cinco a dez documentos não autoriza baixar automaticamente todos os autos. Adaptar seleção em trabalho offline separado ou pedir ampliação concreta do escopo.

Registrar estratégia efetivamente usada (MNI, endpoint oficial ou navegador), duração, arquivos, falhas e relatório final. Reconciliar por identidade documental, não só por nome de arquivo ou tamanho do PDF. Duplicatas por checksum podem representar IDs distintos: contabilizar vínculo e motivo do descarte, sem chamar de peça ausente. Status final verde não dispensa reconciliação do manifesto.

Teste de retomada: primeiro somente com fixture local. Uma interrupção real da sessão não é necessária na rodada inicial e pode impedir a comparação. Se aparecer uma falha natural, registrar e permitir no máximo uma retomada controlada após confirmação do estado; nunca repetir lote inteiro às cegas.

## Etapa D — MinutaIA Conecta

Separar duas permissões: observar o reconhecimento da interface e efetivamente enviar conteúdo para processamento remoto. Não presumir que abrir o processo com a extensão ativa seja uma operação exclusivamente local. Determinar a ativação e os destinos usando evidência estática e ensaio sintético antes de habilitá-la sobre autos reais.

O fabricante documenta envio dos documentos como contexto, OCR e exportação para a plataforma; declara que abrir processos ou exportar não consome o saldo de interações e que cada mensagem o consome [2]. Exportação sem cobrança de interação ainda pode transmitir dados e persistir conteúdo. Compatibilidade genérica com PJe não comprova suporte TJES. Não há API pública ou exportação independente confirmada nesta pesquisa.

Se o Conecta capturar automaticamente todos os autos e não permitir restringir a amostra, não enviar: escolher ensaio sintético ou obter autorização específica de inteiro teor. Com transmissão aprovada e conteúdo elegível, registrar saldo antes/depois, seleção realmente enviada e três tarefas propostas: inventário das peças, síntese factual com referência documental e resposta a pergunta factual de resposta conhecida. Não inserir texto no editor do tribunal nesta rodada. A qualidade é conferida pelo titular contra os originais; ausência de fonte ou afirmação sem suporte é falha, ainda que a redação seja convincente.

## Métricas e condições de parada

Os limites abaixo são decisões conservadoras deste protocolo, não garantias dos fornecedores.

| Métrica/condição | Critério e ação |
|---|---|
| Cobertura da amostra | Todos os itens reconciliados por ID local; qualquer perda silenciosa reprova a rodada. |
| Integridade | Arquivos parseáveis e páginas verificadas; corpo HTML de login/erro nunca aceito como PDF. |
| Tempos | Tempo ativo e tempo total por etapa, mais taxa de bytes; comparar uma rodada equivalente, sem declarar ganho estatístico. |
| Fidelidade | Nenhuma troca de processo/ID, vínculo anexo correto; toda divergência material explicitada. |
| Extração/OCR | Conferir passagens escolhidas antes do teste e números relevantes; registrar erros por documento, sem estimar precisão global com a amostra pequena. |
| IA | Afirmações factuais conferíveis referenciam documento/página ou trecho equivalente; registrar não suportadas e omissões. |
| Custo | Registrar variação observada do saldo, não preço presumido; parar no teto aprovado ou diante de cobrança inesperada. |
| Autenticação | Login, MFA ou CAPTCHA solicitado: parar a automação e devolver ao titular. Sem repetição automática de credenciais. |
| Rede/serviço | Parar em bloqueio, limitação de taxa, erro de permissão, mudança inesperada de domínio ou erro persistente; não buscar contornar. |
| Escopo | Peça sigilosa inesperada, captura maior que a autorizada ou destino externo não esclarecido: parar antes de transmitir. |
| Duração | Janela proposta de até vinte minutos para a rodada, excluído login humano. Expirar a janela encerra a tentativa e registra pendências; não altera timeouts do projeto. |

## Transporte, perfil e evidências

Antes do processo, verificar separadamente o controle do Chrome e o executor Python local. A extensão de controle opera a interface do navegador, mas não equivale a acesso a um terminal do Mac. Este ambiente de desenvolvimento é remoto: os testes unitários aqui executados não comprovam que o worker local do usuário foi atualizado ou está iniciado. Se não houver executor local acessível ou dashboard local já operante, essa preparação deve preceder o piloto; não transferir credenciais/sessões para o ambiente remoto como substituição automática.

Decisão de execução inicial: baseline oficial e downloader, com um processo e execução serial. A etapa de análise no MinutaIA fica desativada nessa primeira rodada. O estudo da extensão já fundamenta o planejamento, mas não exige enviar autos para comparar os downloads. Perfil dedicado é necessário para automação por depuração, não uma exigência universal para controle por extensão no Chrome existente.

O transporte preferido é navegador local → TJES → arquivos locais. Se houver fila Redis/dashboard local, ela recebe somente os metadados necessários conforme contrato existente. Transporte ao VPS brasileiro pode fazer parte da operação atual, mas não é equivalente a execução local e precisa estar explicitamente incluído no ensaio. Não copiar cookies ou perfil autenticado para nuvem como atalho. MinutaIA é destino remoto distinto e constitui etapa própria.

Playwright deve usar diretório de perfil dedicado; Chrome restringe depuração do diretório padrão desde a versão 136 [3, 4]. Não confundir essa restrição de remote debugging com impossibilidade geral de controlar o navegador por uma extensão autorizada. A opção de ferramenta de controle deve seguir a disponibilidade local, sem trocar silenciosamente por navegador remoto.

O estado de autenticação pode permitir personificação da conta e não deve ir ao Git, relatório ou artefato compartilhado [5]. Traces podem conter DOM, imagens, cabeçalhos e corpos de rede; desligá-los por padrão no piloto real e coletar métricas sanitizadas. Em falha específica, propor captura mínima local antes de ativar. O Trace Viewer web declara processar localmente, portanto sua existência não é prova de envio externo do trace [6]; ainda assim, o protocolo prefere inspeção local e proíbe hospedar traces reais em CI público. Omissão de screenshots não elimina todos os dados sensíveis de um trace.

## O que permanece pendente para executar

- Confirmação do processo elegível e dos cinco a dez documentos, comunicados por canal local apropriado, sem publicá-los em código ou relatório.
- Autorização expressa para leitura e download dessa amostra no PJe local; indicar se inclui caminho MNI ou servidor existente.
- Autorização separada para transmissão ao MinutaIA, amostra/inteiro teor, teto de interações e eventual persistência. Aprovação genérica do plano não decide esses parâmetros.
- Instalação/ativação da extensão e qualquer captura de trace, se não abrangidas por autorização já existente.

## Fontes e limites da evidência

1. TJES, manual PJe CIASE, seção de download: https://www.tjes.jus.br/wp-content/uploads/manual-PJe-CIASE.pdf . Trecho indexado descreve filtros e ordem; abertura direta retornou HTTP 403 nesta pesquisa. Portal https://www.tjes.jus.br/pje/ e ajuda https://www.tjes.jus.br/pje/ajuda/ também retornaram 403. Isso limita a verificação documental e não demonstra indisponibilidade no computador do usuário. Nenhuma autenticação ou versão atual do frontend foi confirmada. A pesquisa não encontrou instrução TJES de MFA atual suficiente para fixar fluxo; observar após autorização, sem assumir aplicabilidade de outro tribunal.
2. MinutaIA, anúncio oficial do Conecta, 09/02/2026: https://www.minutaia.com.br/blog/conecta . Privacidade: https://www.minutaia.com.br/politica-de-privacidade . São declarações do fornecedor. A política admite biblioteca opcional, minutas por padrão e histórico opcional; não uso para treinamento não significa ausência de armazenamento. Não é auditoria independente da infraestrutura.
3. Google Chrome, mudanças de remote debugging: https://developer.chrome.com/blog/remote-debugging-port . Fonte primária para perfil não padrão; não comprova que o navegador local já está preparado.
4. Playwright Python, geração de testes e perfil: https://playwright.dev/python/docs/codegen . Suporte documentado não demonstra compatibilidade da extensão comercial com Chromium de teste.
5. Playwright, autenticação: https://playwright.dev/docs/auth . Fonte do tratamento restrito de arquivos de sessão.
6. Playwright Python, Trace Viewer: https://playwright.dev/python/docs/trace-viewer . Documenta DOM/rede/imagens e processamento local do viewer web; a coleta concreta depende da configuração.

Nenhum resultado de desempenho, captura, crédito ou análise judicial é declarado como medido. Esta alteração é apenas documental e não exige execução de testes contra serviços judiciais.
