# HANDOFF — pje-download: full review + testes reais a partir do Mac (2026-09-26)

> Tudo abaixo foi medido nesta sessão, exceto onde marcado. Repo PÚBLICO: nada de host, IP ou
> credencial aqui.

## Estado medido

- **Máquina:** macOS (Apple Silicon), clone em `~/Projetos/juridico/pje-download`. Os caminhos WSL
  do `CLAUDE.md` (seção Paths) não existem nesta máquina.
- **`master` = `origin/master` = `4745bd8`** no início (o clone local estava 7 commits atrás; foi
  feito `pull --ff-only`).
- **Branch local `fix/mni-loginfailed-auth`**, NÃO commitada, NÃO empurrada:
  `mni_client.py`, `tests/test_mni_client.py` (+2 testes), `tools/smoke_real_local.sh` (novo) e
  este handoff (novo). Commitar a branch leva os quatro; merge no `master` = deploy.
- **Gate do deploy conferido:** `deploy.yml` falha com exit 1 quando o script imprime
  `MNI credentials are INVALID`, que é a linha que a chamada real passou a imprimir. ⚠️ O passo
  roda DEPOIS de trocar os containers: pinta o deploy de vermelho, não impede a troca.
- **Deixados defasados de propósito:** a Task 4 da spec do zeep e o item 5 do backlog no
  `CLAUDE.md` ainda dizem "verificação ao vivo pendente"; a evidência de que fechou está acima.
- **Testes:** 601 passed no `master` com redis vivo; **603 passed** na branch. `ruff check .` e
  `ruff format --check .` limpos na 0.14.14. `verify_spec.py`: PASSED.
- **Produção:** roda `f2aeeec`. Prova: run de deploy `36210166619`, `success`, com
  "running build matches" (worker) e "dashboard build matches". Os commits #51/#52 são só docs.
- **Task 4 da spec do zeep: FECHADA.** No mesmo deploy, o smoke de credenciais rodou no VPS com
  `forbid_external=True tribunal=TJES` e o `consultarProcesso` real autenticou (`result=valid`).

## Testes reais feitos daqui (sem credencial do Felipe)

| Teste | Resultado |
|---|---|
| WSDL TJES / TJES_2G / TJCE | HTTP 200, 5 imports, **zero `schemaLocation`** |
| WSDL TJBA | 403 de WAF ("Access Denied"), não CloudFront |
| WSDL TJBA_2G / TRT17 | **404** — endpoints do `TRIBUNAL_ENDPOINTS` defasados |
| `MNIClient._get_client()` real com `forbid_external` em TJES, TJES_2G, TJCE | carrega, 6 operações |
| `verify_mni_credentials.py` com CPF/senha FALSOS contra o TJES real | **antes:** `inconclusive` (exit 2) · **depois do fix:** `invalid` (exit 1) |
| `docker compose --profile worker up` sem credenciais | dashboard `healthy`; **worker em crash-loop** (Chromium ausente) |

## Achados (ordem de severidade)

1. **CORRIGIDO NA BRANCH — credencial errada passava no deploy.** O TJES responde senha errada no
   CORPO com `Erro ao realizar login via MNI. exception invoking: loginFailed`. O classificador só
   conhecia "Acesso negado"/"Unauthorized", então virava `mni_error` → `inconclusive` → o passo do
   deploy só avisa. Fix: `_MNI_AUTH_FAILED_MARKERS` com `loginFailed`, usado nos dois ramos.
   Risco residual aceito: se o TJES usar `loginFailed` também para pane de LDAP, o deploy falha
   como "credencial inválida".
2. **Chromium inutilizável no container do worker (desde o Dockerfile original).** `playwright
   install` roda como root → `/root/.cache`, ilegível para `appuser`; e faltam libs
   (`libxcomposite1`, `libxdamage1`, `libxfixes3`, `libxkbcommon0`). Em produção o erro é engolido
   por `_ensure_browser` (`pje.session.lazy_failed`), então as estratégias 2 e 3 **nunca rodam**.
   Patch validado localmente (Chromium abriu como uid 1001) e **deliberadamente NÃO aplicado**,
   por causa do item 3:
   ```diff
   +ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
    RUN pip install --no-cache-dir -r requirements.txt && \
   -    playwright install chromium
   +    playwright install --with-deps chromium && \
   +    rm -rf /var/lib/apt/lists/*
   ```
3. **Regressão do F7, latente.** Em modo MNI com navegador disponível, `_phase_browser_fallback`
   devolve `captcha_required` e o `except` de `download_process` devolve `session_expired` quando
   a página está no login. Os dois estão em `_FATAL_WORKER_STATUSES` → a dashboard cancela o lote
   inteiro, e os arquivos já salvos pelo MNI somem do resultado. Hoje inalcançável só porque o
   item 2 impede o navegador de abrir. **Corrigir 3 antes de aplicar 2.** Repro por subagente.
4. **Recusa do `forbid_external` rotulada como geo-bloqueio 403.** `str(ExternalReferenceForbidden)`
   contém "Forbidden" → status `blocked`. Operador iria atrás do IP em vez do WSDL.
5. **`"não encontrado"` genérico conta como credencial válida.** Um corpo "Usuário não encontrado"
   passaria o deploy. Não observado no TJES; ancorar em "Processo" ou no CNJ sondado fecharia.
6. **gdown (estratégia primária do Drive) não grava auditoria CNJ 615** (`gdrive_downloader.py`,
   `_try_gdown`); as estratégias 2 e 3 gravam.
7. **`MNI_FORBID_EXTERNAL_TRIBUNALS` não chega aos containers** (fora do `environment:` do
   compose). Expandir para TJES_2G/TJCE exige mexer no compose, não só no `.env`.
8. **Auditoria em execução nativa (fora do Docker) falha em silêncio** sem `AUDIT_LOG_DIR`
   (padrão `/data/audit`), e o `.env.example` não traz a variável. O script de smoke já exporta.

## Lacunas para o uso do Felipe

- **Não existe caminho para tirar os PDFs do servidor.** Os arquivos ficam num volume Docker
  nomeado; a dashboard não serve arquivo nem zip. Hoje seria `docker compose cp` + `scp`.
- **Este Mac não alcança produção:** sem chave SSH, sem alias, sem a `DASHBOARD_API_KEY`.
- **Caminho mais curto:** CLI local (`batch_downloader.py`) com as credenciais dele, gravando
  direto numa pasta do Mac. Script pronto: `bash tools/smoke_real_local.sh [CNJ]`.
- **Retenção LGPD dos processos baixados não existe** (só a auditoria é rotacionada, 90 dias).
- **Sigilosos não têm tratamento próprio.** Documentos "vinculados" dependem do fallback de
  navegador, que está morto (itens 2 e 3).
- **Botão "Fazer Login" da dashboard não funciona em Docker** (abre navegador visível numa imagem
  sem Playwright).

## Docs defasados

README com contagem de testes e tamanhos de arquivo antigos; `CLAUDE.md` com caminho de `.env`
(`kratos-master/config/.env`) e caminhos WSL que não valem; API key descrita como "só POST"
(cobre todo `/api/*`).

## Próxima ação concreta

1. Felipe decide sobre a branch `fix/mni-loginfailed-auth`: commit + PR. ⚠️ Merge = deploy.
2. Felipe preenche `.env` e roda `bash tools/smoke_real_local.sh` e depois com um CNJ.
3. Corrigir o item 3, depois aplicar o patch do item 2, no mesmo PR ou em sequência.

## Prompt de retomada

```
Leia docs/handoff/2026-09-26-full-review-mac-testes-reais.md no pje-download e re-meça:
git status, git log -1 origin/master, gh run list -L 3. Depois continue pela "Próxima ação
concreta". Não mescle nada no master sem eu pedir: merge redeploya produção.
```

---

## Decisão posterior (mesma sessão): MVP por login manual, MNI na v2

Felipe decidiu: no MVP o login é manual e depois o app assume a sessão. O MNI volta na v2. A
branch `fix/mni-loginfailed-auth` continua válida para a v2.

### Medido no caminho por navegador (sem credencial)

- **Login:** SSO em `sso.cloud.pje.jus.br` (usuário+senha ou certificado) e depois **código do
  Google Authenticator** (informado pelo Felipe). O humano digita o código; o app não automatiza
  o segundo fator. `pje_session.interactive_login` espera até 5 min pelo login completo.
- **`pje.tjes.jus.br` está atrás da AWS WAF** ("Let's confirm you are human", cookie
  `aws-waf-token`), inclusive com user agent normal. O SSO não tem. O `_detect_captcha` do
  worker reconhece a tela (contém "captcha"), e em modo só navegador isso **encerra o worker**.
- **`painel.seam` devolve 404 público** no PJe 2.6.1. Bug: `pje_session.py test`
  (`PJeSessionClient.is_valid`) checa `"painel" in url` nessa URL e dá sessão válida para
  qualquer arquivo, até vazio.
- **Worker em modo navegador encerra após `SESSION_TIMEOUT_MINUTES` (60)** e **apaga** o arquivo
  de sessão (`invalidate_session`): um novo login com TOTP a cada hora, no mínimo.
- **`MAX_DOCS_PER_SESSION=50` corta por PROCESSO** na estratégia de download individual: processo
  com mais de 50 documentos sai incompleto.
- **Seletores das estratégias 2 e 3 nunca foram validados ao vivo** no PJe atual.

### Pendente com o Felipe presente

```
python tools/mvp_session_probe.py login
python tools/mvp_session_probe.py watch 5
python tools/mvp_session_probe.py watch 5 --headed
```
A primeira linha abre o navegador para o login com TOTP. A segunda mede quanto tempo a sessão
vale num navegador invisível. A terceira repete num navegador visível, caso o invisível caia na
WAF logo de saída. Esse número decide se o app pode trabalhar invisível ou precisa manter a
janela do login aberta, e com que frequência o login se repete.
