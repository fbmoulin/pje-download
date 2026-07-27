# TODO — pje-download

> Itens acionáveis abertos. Nada aqui bloqueia o uso: o app está em produção (São Paulo) com MNI `healthy`. Histórico e backlog completo em `CLAUDE.md`.

## ✅ Bug do Redis — RESOLVIDO 2026-07-18 (PR #32, `2b6a784`)

- [x] **`Timeout reading from redis:6379`** — causa-raiz = **regressão do redis-py 8.0.0**, não pool/cancelamento.
  - `redis-py 8.0.0` (bump do Dependabot #24, `4da8899`) mudou o default de `socket_timeout` em `AbstractConnection.__init__` de `None` para **5** — exatamente o valor que os dois BLPOP usam. Um comando bloqueante com timeout **>= `socket_timeout`** nunca termina normalmente: o deadline do socket dispara antes do `nil` do servidor chegar, então `read_response` **levanta** `TimeoutError` em vez de retornar `None` (`redis/asyncio/connection.py:778`).
  - Medido no container de produção: `BLPOP(3)→None@3.017s`, `BLPOP(5)→raise@5.006s`, `BLPOP(8)→raise@5.008s`. O `BLPOP(8)` falhar em 5.008s é a prova: o teto é um 5 fixo, não o argumento do blpop.
  - **Por que parecia intermitente:** job que chega enquanto o BLPOP já espera retorna na hora e funciona. Só a fila **vazia** levantava — daí o circuit breaker latchar `redis_unreachable` e o backoff dormir até 60s (jobs parados em `queued`), e a dashboard marcar `failed` batches cujos arquivos já estavam em disco.
  - **⚠️ Por que a investigação anterior "descartou" `socket_timeout`:** o teste isolado usou `blpop(timeout=3)` — o único valor **abaixo** do novo default — então retornou `None` limpo e pareceu saudável. A conclusão "não é `socket_timeout`" estava errada.
  - **Fix:** `socket_timeout` explícito nos dois sites de construção do cliente, **derivado** das constantes de BLPOP (`config.REDIS_SOCKET_TIMEOUT_SECS`) para subir em lockstep. Nunca `None` (conexão TCP morta penduraria para sempre e o circuit breaker nunca dispararia); nunca hardcoded (o bug foi confiar num default de biblioteca para comportamento load-bearing).
  - **Verificado ao vivo** pós-deploy: worker `unhealthy`→`healthy`, status `redis_unreachable`→`consuming`, **0** `redis_error`. 446 testes verdes no CI (era 441).

## ✅ Reply-queues órfãs — RESOLVIDO 2026-07-18 (PR #33, `7b4c24f`)

- [x] **4 chaves `kratos:pje:results:*` com `ttl=-1`** (48 mensagens não drenadas) — vazamento ilimitado.
  - **Mecanismo:** a dashboard apaga a reply-queue no `finally` do `_run_batch`, mas `finally` roda **in-process**. Restart/redeploy com o batch ainda no `_poll_results_loop` pula o bloco inteiro e, **sem TTL**, a chave vive para sempre. Foi o que houve quando o bug do BLPOP travou batches até os containers serem redeployados por baixo do poll loop — uma chave por batch interrompido.
  - **Fix:** quem cria a chave é o **worker** (um RPUSH cru a recria mesmo logo após a dashboard apagar), então a expiração foi para o caminho de escrita: `worker.rpush_with_ttl` faz RPUSH+EXPIRE **pipelinado**. Pipeline e não dois `await` de propósito: `_publish_result` tem retry, e uma falha entre um RPUSH cru e seu EXPIRE **republicaria a mensagem** (resultado duplicado); MULTI/EXEC aplica os dois ou nenhum.
  - **TTL derivada** de `BATCH_MAX_DURATION_SECS` (+30min) e **re-armada a cada escrita** — fila abandonada se auto-limpa, fila viva nunca expira. ⚠️ TTL **abaixo** do teto do batch expiraria a fila em pleno voo e descartaria resultados = ressuscitaria o sintoma "batch failed com arquivos em disco".
  - 🔑 **Premortem (`high`, confirmado) pegou o buraco:** a invariante estava só nos **testes**, e os testes importam a config com os **defaults** — um deploy podia invertê-la em silêncio (subir `BATCH_MAX_DURATION_SECS` e esquecer a TTL) com a suíte 100% verde. Agora há guarda **fail-fast no import** (`config.py`, mesmo idioma do `PJE_BASE_URL`).
  - **As 4 órfãs:** conteúdo preservado em `~/orphan-queues-20260718/` no VPS, depois `EXPIRE 5400` — se auto-deletam. ⚠️ o fix é **forward-only**: não varre chaves pré-existentes.
  - ⚠️ **#33 tinha DUAS regressões, corrigidas em #34 (`f8d1358`)** — achadas por review adversarial *depois* do merge:
    1. a TTL era aplicada **a toda** fila que o worker escreve, inclusive a compartilhada `kratos:pje:results` = **a fila do n8n** (consumidor externo). A justificativa de #33 ("0 consumidores no repo") era a busca errada: *sem consumidor no repo* ≠ *sem consumidor*. Agora só as filas por batch (`owns_queue_lifecycle`).
    2. a TTL era dimensionada pelo **teto do batch**, que limita a janela errada. `resume_active_batch` re-entra com `enqueue_jobs=False` e **não re-enfileira** — o resume DEPENDE da fila não drenada existir. Nada re-arma a TTL com a dashboard fora do ar. Em 90 min, qualquer outage noturno perdia todo resultado não drenado (antes a chave era imortal e o resume funcionava). Agora **piso de 24h**.
  - **Verificado ao vivo pós-deploy:** fila por batch `ttl=86400`, fila do n8n `ttl=-1`; 459✓ no CI.
  - 🔴 **Aberto (decisão humana):** (a) o guard de import é **por processo**, e as duas metades da invariante vivem em containers diferentes (`BATCH_MAX_DURATION_SECS` só no dashboard, `REDIS_RESULT_QUEUE_TTL_SECS` só no worker) — divergência cross-container passa nos dois guards; (b) valor ruim virando `raise` no import + `restart: unless-stopped` = **crash-loop**, não recusa; (c) `redis.ResponseError` fora da tupla capturada em `_publish_result`, cujo call site está no `while` do `consume_queue` **sem try** ⇒ derruba o consumo (pré-existente). Detalhe em `.premortems/PREMORTEM-2026-07-18T21-20-00Z-addendum.md`.

## ✅ `ResponseError` derrubava o worker — RESOLVIDO 2026-07-18 (PR #35, `b6e644a`)

- [x] **Item C1 do premortem.** `redis.ResponseError` escapava da tupla capturada em `_publish_result` e **não havia `try` em nenhum call site** — eles ficam direto no `while` do `consume_queue`. A exceção subia até o `main()` (que tem `try/finally` **sem `except`**) e o processo saía não-zero.
  - **Por que era pior que um crash — um triturador de jobs:** o `blpop` já removeu o job atomicamente e não há ack nem lista de processing ⇒ o job **some**; e o `_log_job_result` (fallback durável) mora **dentro** do `except` que não casava ⇒ o resultado **nunca era gravado**. Arquivos em disco, zero rastro. Com `restart: unless-stopped` o container voltava, pegava o **próximo** job e morria igual: **um job destruído por ciclo**.
  - 🔑 **Alcançabilidade é via MISCONF, não OOM/WRONGTYPE.** A pesquisa descartou os dois **corretamente** (`allkeys-lru` despeja em vez de errar; todo comando Redis do repo é `rpush`/`lpush`/`blpop`/`expire`/`delete`, então nenhuma chave muda de tipo) e concluiu "inalcançável". Faltou o MISCONF: o Redis roda snapshot RDB com `stop-writes-on-bgsave-error yes`, e um save que falha faz o servidor **recusar toda escrita**. Provado contra o redis-py 8.0.0 fixado: `MISCONF` não tem entrada em `EXCEPTION_CLASSES` ⇒ vira `ResponseError` genérico ⇒ não capturado. Gatilhos plausíveis aqui: **disco cheio** (a função central do app é baixar arquivos pra disco) e **`fork()` falhando** sob `mem_limit: 128m`.
  - **Fix em 2 camadas:** (1) cada caminho de publish ganhou `except ResponseError` **não-retriável** (retry em MISCONF nunca funciona; só atrasa o fallback ~7s/job) com métrica + log `error` + fallback local no `_publish_result`; (2) `try/except Exception` **por job** no `consume_queue`, que cobre as classes que a camada 1 deliberadamente não pega. ⚠️ **NÃO alargar para `BaseException`** — `CancelledError` deriva dele e precisa continuar propagando, ou o shutdown gracioso quebra em silêncio (há teste guardando).
  - **Causa raiz também removida:** `--stop-writes-on-bgsave-error no` no compose. Escolhido em vez de `--save ""` porque desligar o RDB inteiro perderia a recuperação de jobs enfileirados num restart do Redis. É defesa em profundidade, **não** substituto do fix de código.
  - **Verificado ao vivo:** `stop-writes-on-bgsave-error = no`, os 3 caminhos com tratamento, contenção presente; 463✓ no CI. Spec: `docs/specs/2026-07-18-worker-publish-error-containment.md` (11/11).
  - 🔴 **Fora de escopo (declarado):** entrega at-least-once. O `blpop` continua sem ack ⇒ um crash entre BLPOP e publish ainda **perde o job**. Este fix torna o **resultado** durável, não o job re-executável.

## ▶ Itens 1 e 2 — pesquisados, prontos para spec

Relatórios completos em `docs/research/` (preservados do scratchpad da sessão, que é efêmero).

### Item 1 — invariante da TTL entre containers (`docs/research/2026-07-18-cross-container-ttl-invariant.md`)

- **Estado: LATENTE, não vivo.** Nenhuma das vars está no `environment:` dos serviços, não há `env_file:`, o `.dockerignore` exclui o `.env` da imagem, e o `printf` do `deploy.yml` não as emite ⇒ hoje ambos os containers rodam os defaults do `config.py` (3600/86400) e a invariante vale por construção. Reabre no momento em que alguém fizer a coisa documentada: adicionar `BATCH_MAX_DURATION_SECS` só ao bloco do dashboard.
- **Recomendado (opção ii):** o dashboard carimba a TTL derivada dele no `JobMessage`; o worker arma esse valor nas filas que possui. ~6 linhas, em costuras que **já existem** (`_batch_job_payload` é o único ponto de construção e já carimba `replyQueue`; o `_publish_progress` já recebe o dict do job). **FECHA** a invariante (a guarda de import do dashboard já provou TTL > teto naquele processo) e é aditivo/compatível nas duas direções de versão mista.
- ⚠️ **Armadilhas que a spec não pode errar:** (a) são **DOIS** sites de escrita de TTL — result **e** progress; o progress domina, última escrita vence; (b) usar **segundos relativos, NÃO deadline absoluto** — o caminho de resume não publica job novo, então um deadline carimbado fica obsoleto; (c) o fallback de campo-ausente tem de **logar alto**, senão recria a falha silenciosa que o fix existe pra matar.
- **Opção (iii) REFUTADA** (dashboard armar a TTL): `worker.py:105-113` — um RPUSH cru recria a chave sem expiração; o dashboard não consegue ser atômico com a escrita do worker.
- 🔴 **UNKNOWN a resolver antes da spec:** exercitar `config` DENTRO dos dois containers para confirmar os valores efetivos vivos.

### Item 2 — guarda de config: `raise` no import vs clamp (`docs/research/2026-07-18-config-guard-raise-vs-clamp.md`)

- **Estado: NÃO PODE disparar em produção hoje** (mesmos 3 fatos acima). Risco latente; a prioridade se liga ao momento em que alguém plumbar as vars.
- **Mas o raio de dano é real quando plumbar:** os 3 serviços são `restart: unless-stopped`; o backoff do Docker **cobre até 60s e nunca reseta** quando o processo sai em <10s; `deploy.yml:127` **remove o container antigo ANTES do novo estar saudável**; o gate de health é limitado a 24×5s=120s; e **não há rollback** em lugar nenhum do `deploy.yml`.
- **Recomendado:** trocar o `raise` por **clamp** (reusando o `max()` que o caminho default já faz) + regra de **não usar `raise` no import sob `unless-stopped`** + fechar o buraco do remove-before-healthy.
- 🔑 **Princípio proposto (vale além deste caso):** *clampar* quando um valor correto é derivável, a direção segura é inequívoca e nenhuma outra invariante quebra; *abortar* para valores que codificam verdade externa (credenciais, `PJE_BASE_URL`) — e abortar em **tempo de deploy**, não no import.
- ⚠️ **Caveat crítico:** nem `raise` nem clamp fecham a divergência cross-container (item 1) — isso exige o handshake em runtime.

## ▶ Próximo (recomendado)

- [x] **Validar download MNI real de ponta a ponta** — ✅ FEITO 2026-07-18: `5022505-25.2024.8.08.0012` → MNI autenticou (senha nova), `consultar_processo.success documentos=13`, **3 PDF + 9 HTML reais** em `/data/downloads`. Os 11 "vinculados" o MNI não retorna (precisam do fallback Playwright — limitação do MNI 2.2.2). Bug de placar acima é ortogonal ao sucesso do download.

## ▶ Aberto agora (revisão de 2026-07-25)

Contexto completo, com medições: `~/.claude/docs/handoff/2026-07-25-pje-download-repo-review.md`
(fora do repo, porque cita o host de produção).

- [x] **`BUILD_SHA` no `/health` + assert no deploy** — ✅ **MERGEADO E NO AR 2026-07-27** (PR #40,
  squash `c4469420`). **Verificado por conteúdo, não por rótulo:** `master` HEAD `da4b9f2` e os dois
  serviços na VPS reportam `build_sha = da4b9f2cd1a166…` (worker `/health` e dashboard `/healthz`),
  com ambos os containers recriados (`Up 58 seconds`) e o redis intocado (`Up 9 days`). A asserção
  nova rodou no deploy e passou. Build arg →
  `ENV` → `config.build_identity()` → `build_sha` no `/health` do worker e no `/healthz` da
  dashboard; `deploy.yml` escreve o SHA no `.env` da VPS e falha se o serviço reportar outro
  valor, ou `"unknown"`, ou nada. Spec: `docs/superpowers/specs/2026-07-27-build-sha-health-identity.md`.
  - **Publicar em GHCR e deployar por digest continua ADIADO de propósito** — para app de host
    único o rsync se defende, e um registry privado traz de brinde a falha de auth silenciosa que
    foi um dos dois mecanismos do incidente do pdf-graph. Reabrir só se aparecer um segundo host.
  - ⚠️ **O que essa asserção NÃO prova:** a imagem é construída no host a partir de árvore
    rsyncada, então o `build_sha` prova *"construída a partir da árvore que o workflow rotulou
    X"*, não *"a árvore é byte-a-byte o commit X"*. Edição manual na máquina entre o rsync e o
    build passa batido. O modo de falha real — build que não rodou, container que não trocou —
    esse sim é pego de forma exata.
  - ⚠️ **Não mova o `ARG BUILD_SHA` do fim dos targets do Dockerfile.** Um `ARG` invalida toda
    camada abaixo dele: no topo, todo deploy refaz `pip install` e `playwright install chromium`.
- [x] **Regras de infraestrutura no `.gitleaks.toml`** — ✅ **IMPLEMENTADO 2026-07-27** (PR #42).
  Três regras: `infra-public-ipv4`, `infra-jusbr-email`, `infra-ssh-invite`. Verificador
  permanente em `tools/verify_gitleaks_rules.sh` (**12/12**, nas duas direções), que **falha
  fechado** se o gitleaks faltar — verificador que "pula" reporta verde sem ter verificado nada.
  - **Allowlist por VALOR, como o alerta abaixo pedia.** Os dois IPs mortos seguem nos docs e não
    disparam. Medido: 0 falso positivo na árvore rastreada (o total do repo continua nos mesmos
    251 achados de CNJ pré-existentes).
  - ⚠️ **A colisão real não era com os IPs mortos — era com `Chrome/120.0.0.0`**, versão de 4
    partes em User-Agent, 6 dos 12 falsos positivos iniciais. Tratada com `regexTarget = "line"`,
    porque o RE2 não tem lookbehind para dizer "não precedido de barra".
  - ▶ **Follow-up:** o verificador ainda não roda no CI (o runner não instala gitleaks). Enquanto
    isso, a regra só é exercitada por quem rodar o script à mão.
- [ ] ~~**Regras de infraestrutura no `.gitleaks.toml`.**~~ *(contexto original, mantido para
  leitura)* O gate acha credencial e PII brasileira,
  e por isso reportou **zero** sobre o IP público do VPS, o e-mail institucional e a linha
  `ssh -i` que estavam publicados (ver `fb405e7`). O comentário do próprio config diz que nome de
  pessoa é o buraco residual insolúvel; identificador de infraestrutura é um **segundo** buraco, e
  ao contrário de nome ele **é** regexável: IPv4 fora de RFC1918, e-mail `jus.br`, linha `ssh -i`.
  ⚠️ **Armadilha:** uma regra de IPv4 dispara nos IPs mortos que ficaram de propósito
  (`2.24.126.161`, `191.252.204.250`), inclusive dentro de
  `docs/plans/2026-06-26-vps-deploy-verifier-sdd.md`, onde o IP é o objeto de um passo de
  verificação. Allowlist **por valor**, nunca por caminho — escopo por caminho cegaria a regra na
  árvore `docs/`, que é exatamente onde o IP vivo estava.
- [ ] ⚠️ **`Sync files to VPS` é INTERMITENTE — 2 falhas seguidas e depois sucesso (2026-07-27).**
  O passo falhou duas vezes de forma idêntica (~5 s, que é o timeout padrão do `ssh-keyscan`, com
  **zero stderr**) e, no terceiro disparo, **passou sem nenhuma intervenção**. Não é o "blip único"
  que o histórico registrava, mas também **não é uma quebra permanente** — a conclusão de que
  "não é transitório", tirada das duas primeiras falhas, estava **errada**: duas amostras não
  distinguem falha permanente de falha intermitente com taxa alta.
  - **O que ficou medido:** daqui, com a **mesma chave e o mesmo usuário** do deploy
    (`~/.ssh/pje_deploy`, `deploy`), `ssh-keyscan` devolve 3 chaves em **2,2 s** e
    `rsync --dry-run` sai **0**, com a VPS de pé (uptime 9 dias). Firewall Hostinger `330806`
    **descartado**: regra `accept TCP 22 source=any`, grupo `is_synced: false`.
  - **Não verificado:** `ufw`/`iptables` no host e restrições no `sshd_config` — o usuário `deploy`
    não tem sudo sem senha, e `~/.pje_vps_root_pw` é credencial de emergência.
  - ▶ **Se voltar a doer:** o remédio barato é re-disparar (`gh workflow run deploy.yml`), e o
    diagnóstico só avança acrescentando `-v` ao `ssh-keyscan`/`rsync` no passo — hoje ele falha
    **sem imprimir nada**, e é essa mudez que impede fechar a causa.
  - ✅ **Falha segura:** o passo é anterior a qualquer escrita, então uma falha aqui **não deixa
    estado parcial** — nada sincroniza e produção permanece no código anterior.
- [ ] **Mesclar Dependabot #36 e #41.** ⚠️ **O #37 não existe mais** — o Dependabot o FECHOU e
  abriu o **#41** com 5 updates (um `prometheus_client` a mais), então a validação local registrada
  no handoff de 25/07, que era sobre o #37, **não transfere**. O que vale agora é o CI, que rodou
  nos dois: **463 passed, 0 skipped**. Ambos rebasados sobre o master atual.
- [ ] ~~**Mesclar Dependabot #36 e #37.**~~ *(contexto original)* Estavam vermelhos pelo ruff (agora resolvido) e, por
  `test` depender de `lint`, a suíte **nunca rodou** neles: eram *não validados*, não apenas
  bloqueados. Validados localmente em venvs limpos **contra um redis vivo**: **463 passed, 0
  skipped** tanto nas deps pinadas quanto nas do #37 (que traz `structlog` 25→26, um major, e
  `redis[hiredis]` 8.0.0→8.0.1). ⚠️ Mesclar é **outro deploy em produção**, desta vez carregando
  versões novas — decisão diferente de restaurar o pipeline. O Dependabot precisa rebasear sobre
  o pin antes.
- [ ] **Rotacionar a chave SSH de deploy e a `DASHBOARD_API_KEY`.** Adiado deliberadamente pelo
  Felipe em 2026-07-25. É o único passo que reduz a exposição **já ocorrida** do host — a redação
  dos docs (`fb405e7`) só limita propagação futura, porque o valor permanece no histórico do git.

## Opcionais (hardening / operação)

- [ ] **Sink de auditoria no Railway** — `AUDIT_SYNC_ENABLED=true` + `DATABASE_URL=<audit_writer>` (projeto `pje-audit`). A auditoria JSON-L local já grava no volume; o sink é redundância. Ver `CLAUDE.md` §"Audit Sync".
- [ ] **zeep `forbid_external=True`** (`mni_client.py:178`) — defense-in-depth contra SSRF via `xsd:import`. **Testar antes:** WSDLs do MNI podem importar schemas externos legítimos → pode quebrar com `ExternalReferenceForbidden`. ✅ **Medido 2026-07-25 no TJES:** o WSDL vivo baixa com HTTP 200 (34,5 KB) de IP BR e tem **5 `xs:import`/`xs:include` com ZERO `schemaLocation`** — imports só de namespace, que não disparam fetch externo. ⚠️ Medido **só no TJES**; `TRIBUNAL_ENDPOINTS` tem 6 tribunais e basta um com `schemaLocation` externo para quebrar. Meça os outros 5 antes de ligar.

## Follow-ups pequenos (dívida de tipos, do Sprint 15)

- [ ] Migrar `worker._try_official_api` para construir `ResultMessage` via helper tipado em vez de dict inline (~5 linhas).
- [ ] Adicionar `batchId: NotRequired[str | None]` ao `ProgressMessage` (`worker.py:1494` seta, o tipo não declara).

## Operação — bom saber

- Trocar região do VPS na Hostinger é **1×/30 dias**, só por hPanel, muda o IP e desanexa o firewall. Runbook no `CLAUDE.md` (backlog item 1) e no README (§Produção).
- Todo push no `master` redeploya (`ci.yml` verde → `deploy.yml`). Um dispatch manual pode pegar um blip transiente de SSH no `rsync` — re-disparar resolve.
