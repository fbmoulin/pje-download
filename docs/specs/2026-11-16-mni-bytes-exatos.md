# SDD — MNI TJES: bytes exatos dos anexos MTOM e checagem do hash declarado

## Goal

Os documentos baixados pelo MNI do TJES devem ser byte a byte iguais aos que o tribunal
tem. Hoje o zeep descarta o CR/LF inicial e final de cada parte MTOM `binary`, e nada
confere o resultado. Esta mudança (1) devolve os bytes exatos e (2) recusa gravar como
`ok` um documento cujo MD5 difere do atributo `hash` que o próprio MNI declara. Nenhum
dado real de autos entra em código, fixtures, specs ou commits: os fixtures são sintéticos,
montados em tempo de execução.

## USER VALIDATION GATE

O dono autorizou, no pedido desta tarefa (2026-11-16), implementar, abrir PR e fazer merge
com CI verde; escopo TJES. Nenhum lote real é rodado por esta tarefa: a prova de re-download
fica para um passo posterior ao deploy (item aberto em `TODO.md`).

## Medição (2026-11-16, processo de teste do TJES, números sem identificar o processo)

| Fato | Valor medido |
|---|---|
| zeep instalado | 4.3.3 |
| Código | `zeep/wsdl/attachments.py`, `Attachment.content`: ramo `encoding == "binary"` faz `return content.strip(b"\r\n")`; `content` é `functools.cached_property` |
| Servidor | TJES (Apache CXF) manda partes MTOM com `Content-Transfer-Encoding: binary`; `MultipartDecoder` já entrega a parte sem o CRLF do delimitador, então o `strip` come CR/LF que pertencem ao arquivo |
| Lote medido | 66 PDFs: 57 com MD5 diferente do `hash` (MD5) do XML do MNI (24 sem `\r\n` final, 33 sem `\n` final); devolvendo o sufixo, o MD5 bate em todos |
| Controles | 9 PDFs, 9 HTML e 1 mp4 que não terminam em CR/LF já estavam corretos |
| Efeito visível | `pdfinfo` do PDF truncado e do correto dão as mesmas 41 páginas (por isso ninguém notou) |
| Replay offline | pelo caminho real (`MNIClient._get_client` → `consultar_processo` → `_parse_processo` → `decode_document_base64`) reproduz N-2 bytes |
| Onde o hash é exposto | `tipoDocumento` do WSDL do TJES tem `<xs:attribute name="hash" type="xs:string"/>`; o zeep o expõe como `doc.hash`, também nos `documentoVinculado` |

## writing-plans

Os nomes writing-plans, subagent-driven-development e plan-quality-gate atendem ao verificador
estrutural do repositório (`tools/verify_spec.py`); esses pacotes não estão disponíveis como
skills nesta sessão e não se declara sua execução.

| Decisão | Escolha |
|---|---|
| Causa | `mni_client.install_exact_binary_attachments()`, chamada uma vez na importação, substitui `Attachment.content` por uma reimplementação idêntica à original, exceto que o ramo `binary` devolve `self._part.content` sem `strip`. Base64 e demais ramos iguais. Reimplementa em vez de embrulhar o original e marca a função com um atributo sentinela, para ser idempotente (nunca encadeia) |
| Descritor | o novo `cached_property` recebe `__set_name__` explícito, porque atribuir depois da criação da classe não o chama |
| Hash | `MNIDocumento.hash_declarado: str or None`, preenchido em `_parse_documento` (principal e vinculados) só se `doc.hash` for `str` não vazia |
| Checagem | em `_save_document`, depois do decode e da validação, antes do dedup e da gravação: hash com 32 hex e MD5 dos bytes diferente (sem diferenciar caixa) → `outcome["status"]="failed"`, log `mni.download.hash_mismatch` (`doc_id`, `tamanho`, sem nomes), auditoria `status="error"` com `erro="hash_mismatch"` (valores já existentes do schema), retorno `None`, arquivo não gravado e checksum fora de `seen_checksums` |
| Hash ausente ou de outro formato | comporta-se como hoje (grava) e registra `mni.download.hash_unchecked` em nível info com o motivo (`absent` ou `format`) |
| Duplicata por checksum | continua não sendo falha (ver CLAUDE.md); a checagem do hash vem antes e não a altera |
| Risco aceito | se o patch deixar de valer, todo PDF terminado em CR/LF vira `failed`: comportamento barulhento desejado |

### Task 1 — Testes primeiro (TDD)

`tests/test_mni_mtom_binary_exact.py`: multipart/related sintético, partes `binary` terminadas em
`\r\n`, em `\n`, iniciadas por `\r\n` e terminada em 0x0A binário, pelo caminho real do zeep
(`MultipartDecoder` + `MessagePack` + `process_xop`); `_parse_documento` + `decode_document_base64`
com MD5 igual ao `hash` sintético; `_save_document` com hash correto, divergente e ausente; idempotência do instalador;
vinculados com hash. Ver cada um falhar antes da implementação.

### Task 2 — Implementação e mutações

Implementar em `mni_client.py`. Provar três mutações: voltar ao `strip(b"\r\n")`, remover a
chamada do instalador e remover a comparação de hash, cada uma quebrando o teste indicado.

### Task 3 — Entrega com frequent commits

Commits pequenos com evidência no corpo; `ruff` 0.14.14 no diretório; suíte completa com Redis
real; PR; merge só com CI verde; acompanhar o deploy até o `build_sha` do merge.

## Referências

`zeep/wsdl/attachments.py`, `zeep/wsdl/messages/xop.py`, `zeep/wsdl/bindings/soap.py`;
`docs/specs/2026-10-18-tjes-integridade-download.md`; CLAUDE.md, seção "MNI TJES".
