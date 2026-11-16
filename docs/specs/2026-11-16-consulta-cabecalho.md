# SDD — Consulta só de cabeçalho pelo MNI

## Goal

Dar ao gabinete um jeito de ler, pelo MNI do TJES, só o cabeçalho de uma lista de processos
(classe, assuntos pelo código nacional da TPU, partes com o tipo de pessoa e movimentações),
**sem baixar nem gravar documento nenhum**. O uso imediato é a checagem diária que cruza a fila
do PJe com os temas repetitivos do STJ que suspendem o 1º grau: o export do PJe só traz o
assunto principal, e o polo passivo (por exemplo, o banco num cartão de crédito consignado)
separa melhor os candidatos. Nenhum dado real de autos entra em código, fixtures, documentação
ou mensagens de commit.

## USER VALIDATION GATE

O dono aprovou em 2026-11-16, no chat, "criar o modo que traz só o cabeçalho do processo
(assuntos, partes e movimentos, sem baixar documentos)" com PR no repositório para revisão e
merge por ele. O merge é um deploy em produção: nenhum merge sem autorização dele. O uso real
contra o MNI do TJES (lote pequeno de aceite) também depende dele.

## Medição (2026-11-16, leitura do código no `4129542`)

| Fato | Valor medido |
|---|---|
| Quem já sabe pedir só cabeçalho | `MNIClient.consultar_processo(incluir_documentos=False, incluir_cabecalho=True, incluir_movimentacoes=...)`; nenhum chamador usava `incluir_documentos=False` |
| O que o parser guardava das partes | só o nome, em `polo_ativo`/`polo_passivo`; o tipo de pessoa (`pessoa.tipoPessoa`) era descartado |
| Assuntos no TJES | vêm com `codigoNacional` (e `principal`), sem descrição: `cod:<código> (principal)` |
| Superfície da API do dashboard | só `/api/download` (baixa documentos); uma rota nova mexeria na fila e no worker |
| Imagem do worker | `COPY . .` (e a do dashboard copia `tools/`), então `tools/consultar_cabecalho.py` chega às duas |

## writing-plans

Os nomes writing-plans, subagent-driven-development e plan-quality-gate atendem ao verificador
estrutural do repositório (`tools/verify_spec.py`); esses pacotes não estão disponíveis como
skills nesta sessão e não se declara sua execução. O desenho foi feito por leitura do código.

| Decisão | Escolha |
|---|---|
| Forma | ferramenta de linha de comando `tools/consultar_cabecalho.py`, rodada dentro do contêiner do worker (`docker exec pje-worker python -I tools/consultar_cabecalho.py ...`); nenhuma rota nova, nenhuma mudança na fila Redis, no worker ou no dashboard |
| Chamada | `consultar_processo(..., incluir_documentos=False, incluir_cabecalho=True, incluir_movimentacoes=not --sem-movimentos)`, um processo por vez, `--pausa` de 1 s padrão entre consultas |
| Parser | campo aditivo `MNIProcesso.partes` (`polo`, `nome`, `tipo_pessoa` em minúsculas, `""` quando ausente); `polo_ativo`/`polo_passivo` ficam iguais para os chamadores antigos |
| Privacidade | `--so-pessoa-juridica` tira as pessoas físicas das listas e deixa só a contagem (`partes_omitidas`); é o modo para saída que sai da VPS. Erros de construção do cliente imprimem só o tipo da exceção |
| Limites | até 50 processos por chamada; números inválidos não são consultados (só contados em `invalidos`); repetidos caem |
| Auditoria (CNJ 615/2025) | uma entrada `cabecalho_consultado` por processo (`fonte=mni_soap`, `status` success/error, `duracao_s`) |
| Saída | JSON em stdout ou em `--saida` (criado com 0600). Códigos: 0 tudo ok, 1 algum processo falhou, 2 uso inválido ou configuração |

### Task 1 — Parser com tipo de pessoa (TDD)

Escrever primeiro os testes de `tests/test_consultar_cabecalho.py::TestParserTipoPessoa`
(partes com polo e tipo; `polo_ativo`/`polo_passivo` e assuntos inalterados; tipo ausente vira
texto vazio), vê-los falhar e então acrescentar `partes` em `MNIProcesso` e em
`_parse_processo`. Prova de mutação: sem a mudança do parser os testes de tipo falham.

### Task 2 — Ferramenta de linha de comando (TDD)

Testes de `TestFerramenta` antes do código: não pede documentos (mutação: trocar para
`incluir_documentos=True` quebra o teste), JSON com classe/assuntos/polos/movimentos,
`--so-pessoa-juridica` sem nomes de pessoas físicas, `--sem-movimentos`, inválidos não
consultados, falha de um processo dá código 1, auditoria por processo, limite de 50, arquivo de
entrada e saída 0600, erro de cliente sem vazar mensagem, pausa negativa recusada.

### Task 3 — Documentação e verificação

Registrar a ferramenta no `CLAUDE.md` e no `TODO.md` (aceite real pendente). Rodar
`uvx ruff@0.14.14 check .`, `uvx ruff@0.14.14 format --check .`,
`python tools/verify_spec.py docs/specs/*.md` e a suíte completa. Fazer frequent commits pequenos,
um por tarefa.

## Riscos e não-objetivos

Não testado contra o MNI real: o TJES pode ignorar `incluirDocumentos=False` e devolver a lista de
documentos (só metadados, sem conteúdo; a ferramenta não os usa nem grava) ou não informar
`tipoPessoa` (aí `--so-pessoa-juridica` omite todos, o que é o lado seguro). O aceite é um lote de
3 processos públicos da vara, rodado pelo dono ou com autorização dele. Não-objetivos: rota HTTP no
dashboard, fila no worker, descrição textual dos assuntos (a TPU fica com quem consome) e qualquer
outro tribunal (`CLAUDE.md` §Scope).

## References

`mni_client.py` (`consultar_processo`, `_call_consultar_processo`, `_parse_processo`, `MNIProcesso`),
`audit.py` (`AuditEntry`, `log_access`), `config.py` (`is_valid_processo`, `load_env`),
`tools/verify_mni_credentials.py` (molde de ferramenta), `Dockerfile` (cópia de `tools/`),
`CLAUDE.md` (Env Loading, Scope, Prompt gate).
