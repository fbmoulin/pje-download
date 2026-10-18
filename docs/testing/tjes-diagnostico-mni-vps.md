# TJES: diagnóstico MNI na VPS

## Objetivo

Distinguir readiness, conectividade SOAP, autenticação e acesso documental antes
de reiniciar serviços ou tentar renovar a sessão do navegador.

## Sequência de diagnóstico

1. Consulte `/health` do worker e `/healthz` do dashboard pela interface local.
   Compare `build_sha` com o merge aprovado e confira reinícios dos contêineres.
2. Interprete os indicadores separadamente:

   | Indicador | O que comprova | Limite |
   |---|---|---|
   | `healthy=true` | Readiness operacional | Não comprova acesso ao tribunal |
   | `checks.mni=healthy` | WSDL carregado e operações enumeradas | Não transmite credenciais |
   | `session_valid=false` | Sessão REST/browser não validada | Pode ser normal com MNI e Chromium adiado |
   | `fallback_ready=false` | Fallback ainda não pronto | Não impede sucesso exclusivo pelo MNI |
   | `verify_credentials(): valid` | Resposta reconhecida ao teste de credenciais | Não comprova acesso ao processo escolhido |
   | Consulta real com sucesso | Acesso MNI à consulta autorizada | Não comprova download, legibilidade ou completude |

3. Para credenciais, use o verificador existente, no ambiente do worker:

   ```bash
   docker exec pje-worker python -m tools.verify_mni_credentials
   ```

   Não copie credenciais para comandos, relatórios ou histórico. Resultado
   inconclusivo não equivale a senha inválida; investigue transporte e resposta.
4. Apenas quando autorizado, consulte o processo escolhido com os parâmetros do
   worker: `incluir_documentos=True` e `incluir_cabecalho=True`. Não imprima os
   dados retornados. A tentativa de listar documentos omitindo o cabeçalho
   reproduziu erro de esquema no TJES: `documento` recebido quando `dadosBasicos`
   era esperado. Isso não caracteriza rejeição da credencial. Não afrouxe o
   parser XML nem as proteções contra recursos externos para contornar o erro.
5. Se a consulta funcionar, selecione uma amostra limitada de documentos e use
   `download_documentos`. Confira `failed_ids`, referências documentais, tamanho
   e SHA-256. Guarde arquivos e manifesto em diretório privado fora do Git, com
   diretório `0700` e arquivos `0600`. Não registre o conteúdo de respostas SOAP.
6. Compare depois cada documento com o download oficial. Cabeçalho e término
   PDF não comprovam renderização, assinatura, conteúdo completo ou identidade
   com o arquivo oficial. Anexos vinculados exigem verificação própria.

## Critérios para intervenção

- MNI funcionando e navegador adiado: não reiniciar ou renovar sessão apenas
  pelos dois indicadores de fallback falsos.
- Credencial rejeitada: corrigir pelo fluxo de credenciais aprovado, sem expor
  segredos. Não confundir erro de esquema ou bloqueio de rede com senha errada.
- Peças vinculadas pendentes: verificar o fallback autenticado quando necessário.
- Perdas em `failed_ids`: preservar os arquivos obtidos e tratar como coleta
  incompleta. Não declarar sucesso apenas pela quantidade de arquivos físicos:
  deduplicação pode reunir vários IDs em um blob.

## Evidência operacional desta revisão

Em 18/10/2026 foram conferidos o merge do PR #81, o CI do head do PR e o SHA
declarado pelos serviços implantados. Os hashes de três módulos centrais
coincidiram entre a instalação e o contêiner. A consulta MNI com cabeçalho e
documentos funcionou. O CI consultado não publicou artefatos; a comprovação da
implantação veio do runtime, não de um ZIP presumido. O workflow permite que
alterações exclusivamente Markdown não disparem CI.

Nenhuma dessas verificações substitui a comparação documental oficial. Não
incluir números de processos, IDs, nomes, hashes documentais ou arquivos reais
em PRs públicos. O relatório privado do piloto deve registrar resultados e
limitações separadamente.
