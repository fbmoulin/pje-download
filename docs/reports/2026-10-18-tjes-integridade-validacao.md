# Correções de integridade do downloader — validação

Data: 18/10/2026 (America/Sao_Paulo). Base: master b5d7693f1df0e4b45be31807727736d2349ef115.
Escopo autorizado: pesquisa e plano antes do fixing; correção e testes fictícios; protocolo
para futuro teste TJES primeiro grau. Nenhuma operação judicial ou implantação nesta rodada.

## Pesquisa e plano prévios

O plano em `docs/specs/2026-10-18-tjes-integridade-download.md` foi escrito e passou nas
11 verificações locais antes da alteração do código de produção. Foram consultadas as
fontes primárias Python 3.12 (Base64, escrita atômica), Playwright (headers/body/status),
Google Chrome e documentação pública TJES/MinutaIA para o piloto. Três agentes trabalharam
em frentes distintas: protocolo, CLI e validação de conteúdo; o coordenador implementou
preservação de identidade e integrou as correções. Revisão independente encontrou duas
perdas de propagação no REST, corrigidas com novas regressões.

## Comportamento corrigido

| Defeito | Resultado após correção |
|---|---|
| Base64 `!!!!` podia gerar arquivo vazio salvo. | Decode estrito; somente whitespace ASCII do transporte é normalizado; erro auditado sem arquivo final. |
| REST 200 com HTML podia ser gravado como PDF. | Corpo validado antes da escrita: vazio/login e PDF incompatível são recusados. HTML processual legítimo continua permitido. |
| Interrupção de escrita podia deixar arquivo final incompleto. | Temporário vizinho, promoção por replace e limpeza; hash de dedup somente após publicação bem-sucedida. |
| Erro de disco era engolido no chamador REST. | DiskWriteError chega ao job, que falha sem fallback no mesmo disco. Erro de rede continua permitindo fallback. |
| Uma peça REST inválida podia desaparecer entre arquivos válidos. | failed_ids sobrevive à listagem/merge; job termina partial_success. Arquivo consolidado posterior sem correspondência por ID não elimina a perda conhecida. |
| Dois IDs com bytes iguais perdiam associação no retorno. | Um blob físico com lista aditiva de referências documentais; bytes não são contados duas vezes; merge não muta entradas. |
| CLI mantinha done mesmo com perdas e resume pulava. | failed existente, arquivos/bytes preservados, progresso/relatório/métrica coerentes, exit1; legado done+erro é reaberto e nova tentativa limpa aviso antigo. |

Validação PDF é limitada ao cabeçalho de versão e marcador final. Não prova validade de
todas as páginas, autenticidade, assinatura digital nem completude dos autos. Não foi
introduzido parser pesado ou dependência nova de produção. Manifesto completo versionado
e retomada por ID individual continuam incrementos posteriores; a CLI retenta o processo.

## Testes e verificações medidos

- Baseline direcionada antes das correções: **207 passaram**.
- Regressões RED observadas antes dos respectivos patches: validação REST/MNI, identidade
  em merge/MNI e estado CLI; mais quatro falhas de propagação REST na revisão.
- Suite final: **980 passaram, zero skips**, em 82,86 s, com Redis **6.2.14** local isolado
  no mesmo processo de execução. A imagem de produção/CI declara Redis 7.4; não foi
  realizado ensaio de deploy ou certificação dessa imagem.
- Um RuntimeWarning no mock preexistente de CAPTCHA em
  `tests/test_worker_lazy_browser.py`; sem falha da suite. Essa fixture não foi alterada.
- Ruff **0.14.14**: check e format no repositório inteiro passaram.
- Verificador de specs: **8 specs passaram**; `git diff --check` sem erro.
- Fixtures antigas de bytes arbitrários que se diziam PDF foram substituídas por PDFs
  fictícios coerentes; os critérios do validador não foram afrouxados para fazê-las passar.
- O helper de testes worker conserva o singleton de auditoria ao simular sys.modules,
  evitando que o primeiro teste isolado observe outro objeto de audit. Verificação de
  auditoria isolada: sete testes aprovados. A fixture PDF também abriu em leitor independente.
- Nenhum acesso ao PJe, transmissão ao MinutaIA, uso de credencial, mudança de produção
  ou medição de desempenho judicial foi feito por estes testes.

## Piloto definido

Ver `docs/testing/2026-10-18-tjes-1g-piloto.md`. Primeira rodada: um processo de primeiro
grau a ser escolhido pelo titular, cinco a dez documentos, execução serial, baseline
oficial antes do downloader, janela proposta de até vinte minutos e reconciliação por
identidade. Falhas induzidas ficam no ensaio fictício; não interromper a sessão judicial
na primeira comparação. MinutaIA desativado na rodada inicial; análise externa é etapa
posterior com escopo próprio. A ferramenta de navegador não equivale a terminal Python
no Mac: a disponibilidade do executor local deve ser conferida antes da coleta.

Fontes primárias e limitações (inclusive HTTP403 ao abrir páginas públicas TJES nesta
pesquisa) constam do protocolo. O 403 remoto não comprova indisponibilidade no computador
do usuário. Não publicar autos, traces, identificadores ou hashes reais no repositório.
