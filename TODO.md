# TODO — pje-download

> Sessão de 2026-11-16: buraco do MNI (`xs:any`, #118), correções #120/#121, auditoria #117 e limpeza desta lista. Retomada em `docs/handoff/2026-11-16-pendencias-bytes-exatos.md` (antes: `docs/handoff/2026-11-16-buraco-mni-xs-any.md` e `docs/handoff/2026-11-15-aceite-tjes-anexos.md`, aceite operacional TJES concluído em produção no build `465b2f1`).

## ▶ Aberto agora (reconciliação documental de 2026-11-14)

Escopo: **só TJES** (decisão de 2026-10-03, `CLAUDE.md` §Scope). Itens de outros tribunais não entram aqui.

**Passo a passo para o dono, com os comandos prontos e onde rodar cada um (Mac mini ou VPS): `docs/ops/guia-do-dono.md`.**

- [x] **Teste de lançamento do Chromium na imagem atual** — feito em 2026-11-14/15: `156.0.8078.4` na imagem `5f0c757` e de novo na `d474025` (depois do #109). Se o #102 (Playwright 1.64.1) entrar, repetir.
- [x] **Lote pequeno de aceite** — feito em 2026-11-15 com 3 processos públicos da 2ª Vara Cível de Cariacica: `done` nos três, `timing.wall_s` 159,7 s (máximo 143,2 s). Antes do #109 os mesmos 3 terminavam `partial` (anexos "pendentes"). Avisos de linha inválida/repetida conferidos em 2026-11-14. Comparação com o PDF do PJe web: só contagem de páginas e de arquivos; **a equivalência de conteúdo depende de conferência do dono** (ver `docs/handoff/2026-11-15-aceite-tjes-anexos.md`).
- [x] **Buraco do MNI (documentos do PJe que o worker não baixava)** — investigado em 2026-11-16 por subagents. Causa de 12 dos 17 documentos faltantes: o `xs:any` do `tipoDocumento` no WSDL do TJES fazia o zeep engolir o primeiro anexo de cada documento; corrigido no #118 (`mni_client.strip_documento_xs_any`, produção `749b9ec`; a sonda em produção passou de 9 para 11 e de 81 para 89 documentos nos dois processos testados). **Os 5 documentos do 5007796 que o MNI "não entrega"** (~58 pág): a hipótese de processo de origem caiu (sem número de origem no cabeçalho); a hipótese viva é que estejam embutidos nos 3 PDFs 94383092/94383093/94383095 (ver o item da hipótese abaixo, que depende de conferência humana). Os 3 processos foram reprocessados em 2026-11-16 (ver "Re-download de prova"); a equivalência de conteúdo com o PJe web depende do dono (artefato "Conferência de Autos").
- [x] **Auditoria de 2026-11-16 (#117):** `DOWNLOAD_RETENTION_*` agora chegam ao container do dashboard (antes o interruptor de dry-run era inoperante), Redis com `volatile-lru` (a fila de jobs não tem TTL e não pode ser despejada), `X-API-Key` não-ASCII dá 401 e não 500. Medido depois, em 2026-11-16 (ver o item do Redis abaixo).
- [x] **Achados da auditoria de 2026-11-16 corrigidos** — #120 (nova tentativa por documento no lote SOAP que falha, `failed_ids` preservados quando nada é salvo, parser recursivo de anexos até profundidade 5; **só testado com mocks**, o TJES pode nem emitir nível 2) e #121 (`total_docs` esperado preservado em `partial`/`failed`, contadores preservados no timeout, teto absoluto em relógio de parede persistido e dimensionado por `len(job.processos)`, `audit` em `/api/status` e warning de rotação sem cópia, `include_anexos` só bool). O relógio ocioso continua reiniciando na retomada, de propósito.
- [x] **Bytes dos anexos MTOM truncados pelo zeep (CR/LF final/inicial)** — corrigido em 2026-11-16 (`mni_client.install_exact_binary_attachments` + checagem do `hash` MD5 em `_save_document`; spec `docs/specs/2026-11-16-mni-bytes-exatos.md`). Medido antes: 57 de 66 PDFs do lote do 5007796 com MD5 diferente do declarado.
- [x] **Re-download de prova depois do deploy** — feito em 2026-11-16 no build `3aea984`, lote `20261116_104133_aaa514` (3 `done`: 11/11, 16/16, 76/76; 0 `hash_mismatch`, 0 `hash_unchecked`, 103 `saved`, 13 `duplicate_skipped`, 0 WARNING/ERROR). No 5007796, 66 de 66 PDFs batem com o `hash` MD5 do XML do MNI (antes 9 de 66), HTML 9/9 e mp4 1/1; os 3 PDFs 94383092/94383093/94383095 passaram a ter 471154, 220978 e 3005636 bytes. 5007491 e 5019644 não têm hash de referência independente (só: o worker não registrou divergência). Os lotes antigos continuam com arquivos 1-2 bytes menores até a retenção (14 dias) apagá-los. Risco que fica: classes de documento não vistas ainda (só 3 processos de teste); um `hash_mismatch` em produção significaria hash calculado de outro modo.
- [x] **Divergência de páginas no 5007796 — explicada em 2026-11-16 (não é bug nosso).** 94383092 (41 x 4), 94383093 (10 x 1) e 94383095 (17 x 5): cada um é um PDF único, sem páginas repetidas, e o MD5 dos bytes recebidos do MNI bate com o declarado (depois do #124). O worker entrega o que o MNI manda; a diferença 41 = 4+37, 10 = 1+9, 17 = 5+12 vem de cópias de peças de outros processos anexadas dentro do PDF, com rodapés de origem. Falta só a conferência humana da equivalência no PJe web (item abaixo).
- [x] **Medir o Redis na VPS** — medido em 2026-11-16: `maxmemory-policy` = `volatile-lru`, `evicted_keys` = 0, 1,4 MB usados de 96 MB, fila de jobs vazia.
- [ ] **Instalar o watchdog (Hermes no Mac mini).** O código está no repositório; nada alerta até o dono fazer os 6 passos de `docs/ops/hermes-watchdog.md` (chave SSH restrita na VPS, wrappers em `~/.hermes/scripts/`, dois `hermes cron create`, Mac mini sem suspensão). Teste de ponta a ponta: parar o worker e esperar `[ALERTA]` em 5 a 10 min. Medido em 2026-11-16: a linha restrita do watchdog NÃO está no `authorized_keys` da VPS. Motivo: o Prometheus do openclaw não coletava (portas em `127.0.0.1`, sem Tailscale na VPS, nenhuma mensagem do `@kaiOpsBot`; medido em 2026-10-24).
- [x] **Lote real de verificação pós-#120/#121** — feito em 2026-11-16 (`20261116_100938_e15c9e`, build `60e8fe8`): 3 `done` com as contagens da linha de base, `audit` presente em `/api/status`, 0 WARNING/ERROR. Experimento com `MNI_TIMEOUT=2` (lote `20261116_101850_787e67`): `mni.consultar_processo.timeout` 1, `batch_failed` 1, `batch_retry_individual` 1 e 11 `saved`, ou seja, a nova tentativa por documento funciona no MNI real. **Não exercitado em produção:** o ramo `failed`/`partial` com `failed_ids` nomeados e o `N/M` do relatório (a nova tentativa recuperou tudo); seguem cobertos só por testes com mocks. Anomalia menor aberta: `current_batch` do `/healthz` não volta a `null` depois do lote `done`.
- [ ] **Decisão do dono: sink de auditoria.** `AUDIT_SYNC_ENABLED` está desligado e a rotação periódica apaga logs com mais de 120 dias sem cópia em lugar nenhum. Ou liga o sink (item "Sink de auditoria no Railway" abaixo) ou aceita a perda após 120 dias.
- [ ] **Rotacionar `REDIS_PASSWORD`:** apareceu em claro no transcript de um subagente em 2026-11-16. Risco baixo (Redis sem porta publicada no host); adiado por decisão do dono.
- [ ] **Hipótese, NÃO verificada: os 5 documentos "não entregues" do 5007796** (ids 25340922, 28967649, 28967652, 28968353, 93643718; ~58 páginas) parecem estar embutidos como cópias dentro dos PDFs 94383092, 94383093 e 94383095 (rodapés de origem; 37+9+3+7+2 = 58 páginas; os 3 PDFs são arquivos únicos sem páginas repetidas; 41 = 4+37, 10 = 1+9, 17 = 5+12). Isso também explica a divergência de páginas do item acima. Reforçado em 2026-11-16 pela sonda no MNI real: o cabeçalho do 5007796 não tem `processoVinculado`, `outrosnumeros` nem referência a origem ou migração (a rota "consultar o processo de origem" não existe para este processo), e os 3 PDFs chegam do MNI íntegros (MD5 igual). **Ainda é hipótese:** falta a conferência humana, comparando no PJe web as páginas do miolo desses 3 PDFs com os 5 documentos listados. Se bater, a "lacuna de 5 documentos" é só cópia embutida e não há o que baixar.
- [x] **Worker `/health` devolvia 503 enquanto processava um job** — corrigido em 2026-10-24 (`HEALTHY_STATUSES` inclui `processing`; `tests/test_worker_health_processing.py`). Efeitos que sumiram: healthcheck do Docker oscilando em jobs longos e `worker_status: unhealthy` no `/api/status` durante o trabalho. O gate do dashboard no `deploy.yml` ainda espera `ready|consuming`: num deploy com job reentregue no boot ele espera o job (até 120 s). Deixado assim de propósito.
- [x] **Permissões do `.env` na VPS** — medido em 2026-11-16: `600 deploy` (`stat -c '%a %U' /opt/pje-download/.env`). O `deploy.yml` grava o arquivo com `umask 077` e `chmod 600` (#88).
- [ ] **Tailscale instalado na VPS e sem uso** (`snap install tailscale`, 2026-10-24). Medido em 2026-11-16: instalado, deslogado e inativo. Só o dono pode remover (precisa de sudo): `sudo snap remove tailscale`.
- [ ] **Remover `ops/monitoring/`** (stack Prometheus/Grafana do openclaw, obsoleto) em PR próprio, quando o dono confirmar que o openclaw foi desligado. Enquanto isso o `CLAUDE.md` o marca como não usado.
- [x] **Integração técnica do PR103:** merge autorizado por Felipe e concluído em `987e208` (14/11/2026). CI pós-merge 43560698713 e deploy 43560885678 concluídos com sucesso. Mac sincronizado e limpo conforme saída do dono. Aceite funcional segue nos itens Chromium, lote pequeno e Task 6.
- [x] **Decisão D2 encerrada por Felipe em 14/11/2026.** Manter `alvo_ou_sintoma`, `possui_dod` e `multi_tarefa` em advisory. Fonte real do Mac consultada às 05:07:24UTC:1 evento de 10/10,0 rótulos; nenhuma regra elegível. Smokes isolados excluídos. Task9/T7 concluídas, sem promoção/remoção nem extensão retroativa; calendário anterior substituído, sem novo prazo. PII e Stop preservados. Registro: spec original, Task9.
- [x] **Validar recuperação de job após interrupção (Task 6)** — aceite feito em 2026-11-15 na VPS, build `d474025`, com um processo de ~121 s: (B) `kill -s KILL` no meio do job → `job_redelivered attempt=1`, mesmo `job_id` reentregue 1 s depois do boot, lote `done=1 failed=0`, sem "Worker timeout" nem `redelivery_exhausted`. (A) `docker compose restart worker` com ~75 s de job: levou 43,6 s (< `stop_grace_period` 60 s), contêiner antigo saiu com `exit=0`, boot limpo sem `job_received`/`job_redelivered`, lote `done=1 failed=0`. ⚠️ Um processo com mais de ~60 s restantes no restart passa do grace e vira reentrega (não testado). Os logs do worker são zerados pelo `restart`; a ausência de reentrega em (A) foi inferida do boot limpo. Spec: `docs/specs/2026-10-16-job-perdido-no-deploy.md` (marcar a Task 6 lá).
- [x] **Pendências do pre-mortem do #109** — tratadas em 2026-11-15 (#111, deploy `7a8e0d3`; análise por 4 subagentes e pre-mortem do diff por mais 2): (1) tetos de tempo: o teto absoluto do lote agora é `min(CAP, max(piso 3600 s, n × 300 s))`, com `BATCH_MAX_DURATION_CAP_SECS=21600` (6 h); `RESULT_WAIT_TIMEOUT_SECS`, reentregas e timeouts do MNI **não** mudaram; (2) memória: medida em 2026-11-15 em dois lotes reais dos 3 processos: pico `memory.peak` 343,5 e 345,4 MiB de 512 (67%), sem `oom_kill`; **`mem_limit` não foi alterado, por decisão do dono (2026-11-15):** os arquivos baixados não precisam ser guardados depois de analisados, então não se aumenta o limite; reabrir só se um job for morto por OOM (`oom_kill` em `memory.events` do cgroup do worker, `OOMKilled` no `docker inspect`); (3) mensagem do ramo sem sessão: `file_utils.no_session_message`, razão e contagens antes do corte de 80 caracteres; (5) `batch_downloader.py` só conta como pendente o anexo que o MNI não entregou; (6) o `.bin` era um vídeo `video/mp4` que o MNI informa corretamente (0 mimetypes ausentes em 81 documentos); `_mimetype_to_ext` ganhou uma lista explícita de tipos (sem `mimetypes.guess_extension`, cuja tabela varia com o sistema), e o vídeo agora sai `.mp4` em produção. **Decididas sem mudança:** (4) anexo recuperado por fallback ainda termina `partial_success`: só ocorre com sessão PJe (inexistente na VPS) e o ID REST não foi verificado contra o do MNI; reabrir se um dia houver sessão; (7) contador 69/81: no caminho worker/dashboard o resultado final já sobrescreve para 69/69 (`dashboard_api._apply_result`); só reabrir se o dono vir 85% numa tela real. **Riscos que sobram:** a retomada de um lote reinicia o relógio do teto (≤ 6 h por retomada); um lote travado que emite progresso bloqueia novas submissões com 409 até o teto; reprocessar um processo já baixado deixa o `.bin` antigo ao lado do `.mp4`; no script CLI um retorno parcial da sessão PJe limpa o aviso de anexos (pré-existente).
- [x] **Retenção dos downloads na VPS** — em produção desde 2026-11-15 (#113 em dry-run, #114 liga a deleção real; build `dbc512c`). Lotes concluídos com mais de 14 dias perdem as pastas dos processos; `_report.json` e `_purged.json` ficam. Primeiro passe real: `candidates=6 purged=6 errors=0 bytes_freed=4799795`. Spec `docs/specs/2026-11-15-retencao-downloads.md`; `CLAUDE.md` §Download retention. Fechado depois: a UI marca lote limpo (`purged` em `/api/history`) e o `_logs` entra na retenção (`purge_old_logs`). Fora do escopo: pastas `<processo>` de jobs diretos na fila (o dashboard sempre define `outputSubdir`; não há nenhuma em produção).
- [ ] **Conferência do dono (conteúdo):** abrir alguns arquivos do lote do worker e comparar com o PDF do PJe web dos mesmos processos. O worker só consegue provar contagens, não conteúdo.
- [x] **#102 (dependabot: Playwright 1.64.1, gdown>=6.5.1)** — mergeado em 2026-11-15 (`39a4fc4`); Chromium repetido na imagem nova: `156.0.8078.4`.
- [x] **Rotação do audit log só no start da dashboard** — corrigido em 2026-10-24 (#91, `AUDIT_ROTATE_INTERVAL_SECS`, padrão 1 dia; `tests/test_audit_periodic_rotation.py`). Continua aberto o risco de fundo: com `AUDIT_SYNC_ENABLED` desligado, `/data/audit` não tem cópia em lugar nenhum e a rotação apaga de forma permanente o que passa de 120 dias (`CLAUDE.md` §Audit Sync → Retention).
- [ ] **Watchdog endurecido (#92): ajustar a instalação.** Medido em 2026-11-16: a linha ainda não está no `authorized_keys`, então este ajuste só vale depois da instalação (item acima). Se a linha `hermes-watchdog` estiver no `authorized_keys` da VPS, trocar o comando por `/usr/bin/python3 -I /opt/pje-download/tools/pje_watchdog.py` (o `-I` é novo). Copiar de novo o wrapper `ops/hermes/pje-watchdog.sh` para `~/.hermes/scripts/` (limite de tempo via perl e stderr do ssh fora do chat; testado só no Linux, então rodar o passo 3 do runbook no Mac mini).
- [x] **Linhas inválidas e repetidas do lote sumiam em silêncio (B1)** — corrigido em 2026-10-24 (#89): `POST /api/download` devolve `invalidos`, `invalidos_total` e `duplicados`, e a UI mantém no textarea só as linhas rejeitadas. A importação de arquivo também foi corrigida pelo #101 (`e5ec846`, 04/11); falta o lote pequeno de aceite, já listado acima.
- [x] **Redis `allkeys-lru` podia descartar `kratos:pje:jobs`** (a fila não tem TTL e é a única cópia dos jobs; visto em 2026-10-16, spec do job perdido, D4) — resolvido no #117: `volatile-lru` em `docker-compose.yml`. Medido na VPS em 2026-11-16: política `volatile-lru`, `evicted_keys` 0, 1,4 MB de 96 MB, fila vazia.
- [x] **Dependabot #70 → #75** — o #70 foi substituído pelo #75 (aiohttp 3.14.5, gdown 6.4.2, asyncpg 0.32.0). Suíte com Redis vivo: 920 passed, 0 skipped. Mergeado em 2026-10-17 (`9ddccd4`), deploy #115 verde.
- [ ] **Rotacionar a chave SSH de deploy e a `DASHBOARD_API_KEY`.** Adiado deliberadamente pelo Felipe em 2026-07-25; status não reverificado desde então. É o único passo que reduz a exposição **já ocorrida** do host: a redação dos docs (`fb405e7`) só limita propagação futura, porque o valor permanece no histórico do git.
- [x] **Prompt gate do Claude Code** — ✅ #66 (2026-10-03, deploy #111), #71 (`.gitignore` de `.claude/settings.local.json` e `.venv/`, deploy #114), #72 (docs). Confirmado em sessão interativa no macOS que sessão aberta em subdiretório não carrega os hooks: **abrir o Claude Code na raiz**.
- [x] **Cópia de trabalho migrada para o Mac mini** (`/Users/felipemoulin/pje-download`, 2026-10-10): `.venv` Python 3.12 via `uv`, `settings.local.json` com `PROMPT_GATE_PYTHON`/`PROMPT_GATE_RUFF`, pre-push com gitleaks do Homebrew; 918 passed, 2 skipped (sem Redis). O WSL foi aposentado.

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

- [x] **Validar download MNI real de ponta a ponta** — ✅ FEITO 2026-07-18: `5000001-00.2024.8.08.0012` → MNI autenticou (senha nova), `consultar_processo.success documentos=13`, **3 PDF + 9 HTML reais** em `/data/downloads`. Os 11 "vinculados" o MNI não retorna (precisam do fallback Playwright — limitação do MNI 2.2.2). Bug de placar acima é ortogonal ao sucesso do download.

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
- [x] ~~**Regras de infraestrutura no `.gitleaks.toml`.**~~ *(contexto original, mantido para
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
- [x] **`Sync files to VPS` intermitente (2026-07-27) — resolvido no #47 (`684b920`).** A causa provável era o `ssh-keyscan` do passo (timeout padrão de 5 s, sem stderr): o `rsync` já usa `StrictHostKeyChecking=no`, então o keyscan não protegia nada e só podia derrubar o passo sob `bash -e`. Foi removido (comentário "No ssh-keyscan here on purpose" em `.github/workflows/deploy.yml`). Causa não provada por reprodução. O passo é anterior a qualquer escrita, então uma falha ali nunca deixou estado parcial. Pinagem real de host key (secret `VPS_HOST_KEY`) segue como mudança separada. Medições originais abaixo.
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
- [x] ✅ **Resolvido 2026-10-01:** #36 mesclado; #41 fechado sem merge (substituído pelo #45, mesclado). ~~**Mesclar Dependabot #36 e #41.**~~ ⚠️ **O #37 não existe mais** — o Dependabot o FECHOU e
  abriu o **#41** com 5 updates (um `prometheus_client` a mais), então a validação local registrada
  no handoff de 25/07, que era sobre o #37, **não transfere**. O que vale agora é o CI, que rodou
  nos dois: **463 passed, 0 skipped**. Ambos rebasados sobre o master atual.
- [x] ~~**Mesclar Dependabot #36 e #37.**~~ *(contexto original)* Estavam vermelhos pelo ruff (agora resolvido) e, por
  `test` depender de `lint`, a suíte **nunca rodou** neles: eram *não validados*, não apenas
  bloqueados. Validados localmente em venvs limpos **contra um redis vivo**: **463 passed, 0
  skipped** tanto nas deps pinadas quanto nas do #37 (que traz `structlog` 25→26, um major, e
  `redis[hiredis]` 8.0.0→8.0.1). ⚠️ Mesclar é **outro deploy em produção**, desta vez carregando
  versões novas — decisão diferente de restaurar o pipeline. O Dependabot precisa rebasear sobre
  o pin antes.

## Opcionais (hardening / operação)

- [ ] **Sink de auditoria no Railway** — `AUDIT_SYNC_ENABLED=true` + `DATABASE_URL=<audit_writer>` (projeto `pje-audit`). A auditoria JSON-L local já grava no volume; o sink é redundância. Ver `CLAUDE.md` §"Audit Sync".
- [x] ✅ **Feito:** TJES em #49 (2026-09-25); default para os 6 tribunais em #54 (2026-09-29) — fora do escopo atual, ver `CLAUDE.md` §Scope. ~~**zeep `forbid_external=True`**~~ (`mni_client.py:178`) — defense-in-depth contra SSRF via `xsd:import`. **Testar antes:** WSDLs do MNI podem importar schemas externos legítimos → pode quebrar com `ExternalReferenceForbidden`. ✅ **Medido 2026-07-25 no TJES:** o WSDL vivo baixa com HTTP 200 (34,5 KB) de IP BR e tem **5 `xs:import`/`xs:include` com ZERO `schemaLocation`** — imports só de namespace, que não disparam fetch externo. ⚠️ Medido **só no TJES**; `TRIBUNAL_ENDPOINTS` tem 6 tribunais e basta um com `schemaLocation` externo para quebrar. Meça os outros 5 antes de ligar.

## ✅ Auditoria completa de 2026-09-20 — 7 achados, todos corrigidos (PRs #46 e #47)

Relatório com medições: `docs/reports/2026-09-20-full-analysis.md`. Classe comum: **controle com nome
mais forte do que o que ele faz** — três dos primeiros quatro só eram invisíveis porque reportavam sucesso.

- [x] **F1** gauge `pje_audit_sync_lag_seconds` nunca escrito (exportava `0.0`; alerta nunca disparava) — #46, refinado em #47 (baseline lida do disco = idade da entrada pendente mais antiga; linha em voo = ~0).
- [x] **F2** imagem da dashboard ignorava todos os pins do `requirements.txt` (drift medido: redis 8.1.0, structlog 26) — #46, derivada do requirements.txt excluindo só o playwright (139 MB).
- [x] **F3** `Validate MNI credentials` saía 0 com credencial vazia E errada — #47: `MNIClient.verify_credentials()` + CLI + step que só falha em rejeição real. **403 agora é `blocked`, não `auth_failed`** (é a geo-restrição do CloudFront, não senha). ⚠️ *Confirmar no primeiro deploy real:* senha errada de verdade deve ler `INVALID` com "Acesso negado".
- [x] **F4** com MNI ligado, estratégias 2 e 3 eram inalcançáveis (`playwright_deferred` era permanente) — #47: browser lazy, headless, só quando um fallback é necessário e só se `/data/pje-session.json` existir. ⚠️ *Operação:* dormente até alguém gerar esse arquivo (`/api/session/login` ou `python pje_session.py login`).
- [x] **F5** guard de SSRF do `gdrive_map` era substring — #47: host-aware + sink recebe URL canônica.
- [x] **F6** `worker.py` gravava documento sem trilha CNJ 615/2025 em 4 sites — #47, pousou ANTES do F4.
- [x] **F7** (achado ao revisar o F4; pré-existente em produção) **uptime > 60 min em modo MNI transformava qualquer processo sem documentos em `session_expired`, e a dashboard abortava o batch inteiro** (LREM dos jobs restantes). Medido: 59 min → `failed`; 61 min → batch abortado. #47: o timeout operacional só vale para um browser que exista; em modo MNI nunca é fatal.
- Follow-ups que sobraram (não são defeitos do relatório): pinagem real de host key no deploy (secret `VPS_HOST_KEY`; o `ssh-keyscan` morto foi removido); threshold do alerta de lag vs. tick, se um dia o gauge virar contínuo.

## ~~Follow-ups pequenos (dívida de tipos, do Sprint 15)~~ — já estavam feitos (verificado 2026-09-20)

- [x] ~~Migrar `worker._try_official_api` para construir `ResultMessage` via helper tipado em vez de dict inline (~5 linhas).~~ **Obsoleto:** `_try_official_api` devolve `list[dict]` de metadados de arquivo e não constrói `ResultMessage` nenhum; o único site de construção, `_result()`, já é tipado.
- [x] ~~Adicionar `batchId: NotRequired[str | None]` ao `ProgressMessage` (`worker.py:1494` seta, o tipo não declara).~~ **Já declarado** em `protocol.py` desde `518c31a`; a referência de linha tinha derivado.

## Operação — bom saber

- Trocar região do VPS na Hostinger é **1×/30 dias**, só por hPanel, muda o IP e desanexa o firewall. Runbook no `CLAUDE.md` (backlog item 1) e no README (§Produção).
- Todo push no `master` redeploya (`ci.yml` verde → `deploy.yml`). Um dispatch manual pode pegar um blip transiente de SSH no `rsync` — re-disparar resolve.
