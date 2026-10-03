---
name: refine-prompt
description: Reescreve um pedido vago num prompt preciso para o repo pje-download (Objetivo / Alvo / Definition of Done / Fora de escopo), sem executar nada. Use quando o usuário quer transformar um rascunho de pedido em prompt acionável, ou quando o prompt gate sinalizou um prompt (alvo_ou_sintoma, possui_dod, multi_tarefa).
allowed-tools: Read, Glob, Grep
---

# /refine-prompt

Rascunho do usuário: `$ARGUMENTS`

Você **só reescreve** o rascunho. **Não execute a tarefa**, não edite arquivos, não rode
comandos. Nunca edite nem crie nada no repo: sua saída é apenas texto.

## Passos

1. Leia o rascunho e identifique o que ele quer mudar ou investigar.
2. Ache o alvo concreto **neste repo** com Glob/Grep/Read: módulos (`worker.py`,
   `mni_client.py`, `dashboard_api.py`, `batch_downloader.py`, `audit_sync.py`, …),
   símbolos (funções, classes, constantes de `config.py`) e testes
   (`tests/test_<modulo>*.py`). Se o rascunho descreve um sintoma (mensagem de erro,
   código HTTP, nome de teste), confirme onde ele nasce.
3. Escolha um Definition of Done verificável por comando: o teste mais específico
   (`pytest tests/test_x.py -q`), mais `pytest tests/ -q` antes de commit. Para lint,
   lembre que o CI fixa `ruff==0.14.14` (ver CLAUDE.md): `uvx ruff@0.14.14 check .` e
   `uvx ruff@0.14.14 format --check .`.
4. Liste em "Fora de escopo" o que não deve ser tocado (refactors vizinhos, outros
   tribunais, `deploy.yml`, regras de segurança do CLAUDE.md, etc.).

## Regras

- Nunca execute a tarefa nem edite arquivos — devolva só o prompt reescrito.
- Se o rascunho junta 3 ou mais tarefas, separe em prompts numerados, um template por
  tarefa, na ordem sugerida de execução.
- Se não achar o alvo no repo, diga isso e faça **uma** pergunta objetiva ao usuário;
  não invente caminhos, símbolos nem testes.
- Nunca copie PII do rascunho para a saída: CPF/CNPJ, número de processo e nomes de
  partes viram placeholders (`<CPF>`, `<CNPJ>`, `<NUMERO_PROCESSO>`, `<PARTE>`).
- Não acrescente explicações longas: a saída é o template preenchido (ou a pergunta).

## Formato da saída

Devolva **somente** isto, preenchido:

```
**Objetivo:** <uma frase>
**Alvo:** <arquivos/símbolos concretos, ou o sintoma reproduzível>
**Definition of Done:** <comando verificável, ex.: `pytest tests/test_x.py -q` passa; `ruff check .` limpo>
**Fora de escopo:** <o que não mexer>
```
