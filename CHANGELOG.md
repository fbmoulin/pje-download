# Changelog

Todas as mudanças notáveis do **pje-download** são documentadas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
o projeto segue versionamento semântico.

## [Unreleased]

### Added — identidade de build no `/health` (2026-07-27)

- **`build_sha` no corpo do `/health` do worker e do `/healthz` da dashboard**, assado na imagem
  como build arg (`config.build_identity()`). Até aqui o deploy provava **função** — que *alguma
  coisa* responde — e nunca **proveniência** — *qual commit* está respondendo. Todas as asserções
  existentes (`"healthy": true`, `worker_status` em `ready|consuming`, o smoke da dead-letter) são
  satisfeitas **igualmente bem pelo build ANTERIOR**: uma imagem que não foi reconstruída, ou um
  container que nunca foi substituído, produzia um deploy inteiramente verde. É o modo de falha
  que em 2026-07-18 fez o pdf-graph rodar código `v1.7.3` sob a tag `1.7.6` e invalidar três
  sessões de medição.
- **`deploy.yml` passa a falhar quando o SHA reportado difere do que ele acabou de deployar**, e
  trata `"unknown"`/ausente como **falha**, não como aprovação — no momento da asserção, valor
  ausente é indistinguível de imagem velha. Dashboard e worker são checados **separadamente**, por
  serem imagens distintas: o worker bater não diz nada sobre a dashboard.
- **`ARG BUILD_SHA` fica no FIM de cada target do `Dockerfile`, deliberadamente.** Um `ARG`
  invalida toda camada abaixo dele; no topo, forçaria `pip install` e `playwright install
  chromium` a rodar em **todo** deploy. Medido: com um SHA diferente, a camada cara volta como
  `Using cache` e o valor ainda assim muda. `ARG` também é escopado por stage — declarar só na
  `base` não alcançaria nenhum dos dois targets.
- ⚠️ **Limite honesto:** como a imagem é construída **no host de produção a partir de uma árvore
  rsyncada**, o `build_sha` prova *"esta imagem foi construída a partir da árvore que o workflow
  rotulou X"*, não que a árvore é byte-a-byte o commit X. Isso cobre o modo de falha real (imagem
  não reconstruída / container não substituído), não uma edição manual na máquina.
- Spec: `docs/superpowers/specs/2026-07-27-build-sha-health-identity.md`. Suíte: **471 testes**
  (463 + 8), 0 skipped, contra um redis vivo.

### Security — identificadores de infraestrutura no gate de PII (2026-07-27)

- **Três regras novas no `.gitleaks.toml`:** `infra-public-ipv4`, `infra-jusbr-email` e
  `infra-ssh-invite`. Em 2026-07-25 quatro linhas rastreadas publicaram o IP do VPS vivo, uma
  delas junto do usuário de deploy e do nome da chave SSH, e o gate reportou **zero**: as regras
  existentes procuram credencial e PII de pessoa, e um IP não é nenhuma das duas. O comentário do
  próprio arquivo já dizia que **nome de pessoa** é o buraco residual insolúvel; identificador de
  infraestrutura era um **segundo** buraco — e, ao contrário de nome, ele **é** regexável.
- **`tools/verify_gitleaks_rules.sh`** — verifica as regras **nas duas direções** (12 casos: 4 que
  devem disparar, 8 que não devem). Uma regra de gate erra de dois jeitos opostos, e medir só um
  lado não distingue "regra boa" de "regra desligada". O script **falha fechado** se o gitleaks
  faltar: verificador que pula reporta verde sem ter verificado nada.
- ⚠️ **A colisão real não era a prevista.** O alerta registrado era sobre os dois IPs desativados
  mantidos de propósito nos docs — e o allowlist **por valor** (nunca por caminho, porque foi em
  `docs/` que o IP vivo estava) resolveu isso. O que de fato gerou 6 dos 12 falsos positivos
  iniciais foi **`Chrome/120.0.0.0`**: versão de 4 partes em User-Agent casa com qualquer regex de
  dotted-quad. Tratado com `regexTarget = "line"`, já que o RE2 não tem lookbehind para expressar
  "não precedido de barra".
- Medido: **0 falso positivo** na árvore rastreada; o total do repositório continua nos mesmos
  **251** achados de número CNJ pré-existentes.
- ▶ O verificador ainda **não roda no CI** (o runner não instala gitleaks) — follow-up no `TODO.md`.

### Infraestrutura de CI e guardas (2026-07-25)

- **`ci.yml` passa a pinar `ruff==0.14.14`** (PR #39). Sem pin, o job seguia os releases do
  upstream: quando o ruff foi para `0.16.0`, passou a reportar **208 erros e 7 arquivos a
  reformatar em código byte-a-byte idêntico**. Como `test` declara `needs: lint` e o `deploy.yml`
  gateia em CI concluir `success`, um lint vermelho não bloqueava só a suíte — **matava o
  pipeline de deploy em silêncio**. O último Deploy verde antes disso foi em **2026-07-19**;
  ficou 6 dias parado. Subir o pin passa a ser tarefa deliberada, com PR próprio.
- **Deploy restaurado e verificado por conteúdo.** Após o merge, CI verde, `deploy.yml` disparou
  e os 7 passos passaram — incluindo `Validate MNI credentials`. Confirmado na máquina, não pelo
  label do run: o pin presente na árvore deployada, dashboard e worker reiniciados (`Up 34s`
  contra o redis intocado em `Up 6 days`), `/health` em `status: consuming`, `mni: healthy`.

### Fixed (2026-07-25)

- **`tools/git-hooks/pre-push` bloqueava TODO primeiro push de branch com acusação falsa de PII**
  (PR #38). O caminho de branch nova montava **uma** string de range em sintaxe de `rev-list`
  (`--not <ref> … <sha>`) e a entregava a dois consumidores com gramáticas incompatíveis:
  `gitleaks --log-opts` aceita, `git diff` **rejeita com exit 129**. Sob `set -uo pipefail`, essa
  falha virava falha de pipeline e o hook anunciava *"PII brasileira com dígito verificador
  VÁLIDO"* sem ter varrido nada — mensagem indistinguível de um achado real (o único sinal era a
  ausência de achado impresso). A metade silenciosa era pior: a mesma string fazia o gitleaks
  receber um token vazio e varrer **0 commits**, falhando **ABERTO** exatamente no caso que o
  tratamento de branch nova existe para cobrir.
  - **Fix:** duas variáveis, cada uma na gramática do seu consumidor (`log_opts` para o gitleaks,
    lista explícita de commits para a Etapa 2). A Etapa 2 passa a varrer **commit a commit** via
    `git show` em vez de diferenciar as pontas do range — o que fecha uma brecha extra: um CPF
    adicionado num commit e removido no seguinte some do `git diff A..B` e permanece no histórico
    para sempre, que é a premissa do arquivo inteiro. `git show` também funciona em commit raiz.
  - **Verificado nas duas direções**, porque um fix que só para de bloquear é indistinguível de
    desligar o guarda: branch limpa agora passa (**1 commit varrido**, era 0) e branch com CPF
    válido no módulo 11 continua bloqueada, agora imprimindo o achado e nomeando o commit.
  - ⚠️ `.git/hooks/` não é atualizado por `git pull` — rode `bash tools/install-git-hooks.sh`.

### Security (2026-07-25)

- **Host de produção removido dos docs versionados** (`fb405e7`). O repositório é público e quatro
  linhas rastreadas carregavam o IP público do VPS vivo, uma delas junto do usuário de deploy e do
  nome da chave SSH, ao lado de notas dizendo o caminho da app e que o firewall do provedor está
  desanexado. Nada disso é segredo no sentido do gitleaks — por isso o gate reportava zero — mas
  lido em conjunto é um mapa operacional do host. Substituído por um alias SSH (`pje-vps`, em
  `~/.ssh/config`, não versionado), o que mantém os comandos documentados executáveis.
  - **Não redigidos de propósito:** os IPs desativados (`2.24.x.x`, `191.252.x.x`). O segundo é o
    *assunto* de `docs/plans/2026-06-26-vps-deploy-verifier-sdd.md`, cujos passos de verificação
    fazem `grep -r` por ele — apagar quebraria um documento executável para proteger um endereço
    morto.
  - ⚠️ **Redação limita propagação futura, não desfaz exposição passada:** o valor segue no
    histórico do git e na blob API. O passo que age sobre o que já vazou é rotacionar a chave SSH
    de deploy e a `DASHBOARD_API_KEY` — **ainda não feito** (adiado deliberadamente).

### Docs (2026-07-25)

- Corrigidos três pontos que ensinavam algo já refutado: `CLAUDE.md` listava "MNI blocked by cloud
  IP" em *Known Issues* enquanto o mesmo arquivo, 19 linhas abaixo, documentava o geo-bloqueio como
  resolvido desde 07-18; o item 5 do backlog dizia que o teste do `forbid_external` estava
  "bloqueado por IP cloud"; e a contagem de testes (441) estava 22 atrás da real.

### Produção (2026-07-18) — primeiro deploy ao vivo

- **Deploy em produção** num Hostinger VPS (KVM 2, datacenter **São Paulo**), com CD contínuo via `deploy.yml` (`workflow_run` após `ci.yml` verde). App no ar: dashboard/worker/redis `Up (healthy)`, `mni check: healthy`.
- **MNI geo-bloqueio resolvido.** Diagnosticado que o PJe/TJES fica atrás do AWS CloudFront com geo-restrição por país (IP fora do BR → `403`); comprovado por `curl` do WSDL (IP BR → `200`/POP GRU3 vs IP US → `403`/POP BOS50). Corrigido movendo o VPS para o datacenter brasileiro — **sem proxy, sem ofício**. O fallback Playwright compartilha o mesmo requisito de IP BR.
- **Segurança de rede:** firewall só com a porta 22; dashboard (8007) e worker (8006) acessíveis apenas por túnel SSH. Usuário de deploy dedicado (`deploy`, não-root) no grupo docker.

### Fixed

- `deploy.yml` **nunca compilava**: a etapa `Validate required secrets` misturava expressão do Actions `${{ }}` com expansão do bash `${VAR:?}` → falha de parse (todo run morria em 0s). Passa a usar `env:` + `${VAR:?}`.
- **Dockerfile**: o alvo `dashboard` copiava uma lista explícita de arquivos que ficou defasada — faltavam `async_retry.py`, `file_utils.py` e `protocol.py` (helpers dos Sprints 13/14) → `ModuleNotFoundError` em crash-loop. Trocado por `COPY *.py`.
- **Health check do deploy**: batia em `/api/status` (protegido por `X-API-Key` desde o Sprint 8) sem a chave → `401` eterno. Agora envia o header `X-API-Key`.
- **Validação MNI no deploy**: importava `MniClient` (classe correta é `MNIClient`) e não fazia `cd /opt/pje-download` antes do `docker compose exec`. Corrigidos.

### Security

- **zeep 4.3.2 → 4.3.3** — corrige GHSA-4cc2-g9w2-fhf6 (SSRF via `forbid_external` não-conectado em 4.0–4.3.2). Fecha o Dependabot alert #1.

### Docs

- `CLAUDE.md` reconciliado com o estado pós-v2.5.0 (contagem de testes 408→441, Spec Verifier, backlog de deploy) e correção do diagnóstico anterior (o "vermelho em 0s" era erro de parse, não gate de secrets).
- README: seção de produção reescrita (deploy ao vivo em SP, requisito de IP BR para o MNI, acesso por túnel SSH, runbook de troca de região na Hostinger).

## [2.5.0] - 2026-05-01

### Added / Changed

- `protocol.py`: TypedDicts de fio (`JobMessage`/`ResultMessage`/`ProgressMessage`/`DeadLetterEntry`) + validação `job_from_json`; migração do `worker._publish_result`. Formato de fio byte-idêntico ao v2.4 (interop mantida).
- `dashboard_api`: 7 globais mutáveis de módulo colapsadas em um `AppContext` (`app[APP_CTX_KEY]`), eliminando o vazamento de estado entre testes.
- 416 → 424 testes.

## Histórico anterior (Sprints 1–14, 2026-04)

Ver a seção **Completed Sprints** do `CLAUDE.md` para o detalhe por sprint. Marcos:

- **Sprint 5** — trilha de auditoria CNJ 615/2025 (`audit.py`, JSON-L append-only). 183 → 248 testes.
- **Sprint 7** — sync de auditoria para Railway Postgres (`audit_sync.py`, dedupe idempotente `UNIQUE NULLS NOT DISTINCT`). 303 → 348.
- **Sprints 8–11** — auditoria P0–P2: auth em GET, circuit breaker do Redis, retries do PJe, higiene de logs. 348 → 377.
- **Sprints 12–14** — 5 bugs de produção + helpers DRY (`file_utils`, `async_retry`) + split de `_run_batch`/`download_process`. 377 → 408.
- **Sprints 1–4** — hardening P0/P1, segurança (API key, path traversal, session lock) e expansão de cobertura. 73 → 183.

[Unreleased]: https://github.com/fbmoulin/pje-download/compare/v2.5.0...HEAD
[2.5.0]: https://github.com/fbmoulin/pje-download/releases/tag/v2.5.0
