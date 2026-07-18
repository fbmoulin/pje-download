# HANDOFF — bug de confiabilidade do Redis (redis.asyncio)

> # ✅ RESOLVIDO — 2026-07-18, PR #32 (`2b6a784`)
>
> **Causa-raiz: regressão do `redis-py 8.0.0`** (bump do Dependabot #24, `4da8899`).
> O default de `socket_timeout` em `AbstractConnection.__init__` mudou de `None` para
> **5** — exatamente o timeout que os dois BLPOP usam. Comando bloqueante com
> timeout **>= `socket_timeout`** nunca termina normalmente: o deadline do socket
> dispara antes do `nil` do servidor, e `read_response` **levanta**
> `TimeoutError` em vez de retornar `None` (`redis/asyncio/connection.py:778`).
>
> Medido no container de produção:
> `BLPOP(3)→None@3.017s` · `BLPOP(5)→raise@5.006s` · `BLPOP(8)→raise@5.008s`.
> O `BLPOP(8)` falhar em **5.008s** prova que o teto é um 5 fixo, não o argumento.
>
> **Fix:** `socket_timeout` explícito e derivado (`config.REDIS_SOCKET_TIMEOUT_SECS`)
> nos dois clientes. Detalhes e verificação ao vivo em `TODO.md`.
>
> ---
>
> ## ⚠️ ERRO DESTE DOCUMENTO — leia antes de reusar o método
>
> A seção **"Já descartado"** abaixo estava **ERRADA** no item mais importante.
> Ela descartava `socket_timeout` com base num teste isolado que usou
> `blpop(timeout=3)` — **o único valor abaixo do novo default de 5** — e por isso
> retornou `None` limpo e pareceu saudável. O teste não estava errado; estava
> medindo o único ponto que passa.
>
> A hipótese principal deste handoff (conexão long-lived envenenada por `blpop`
> cancelado) também foi **refutada**: um cliente **novo, em processo novo**, dentro
> do mesmo container, falhava igual em 5.006s.
>
> **Lição:** ao descartar uma hipótese com um teste, varie o parâmetro suspeito.
> Um único ponto de medição não descarta um teto — ele só mostra um lado dele.
>
> ---
>
> _Conteúdo original abaixo, preservado como registro histórico._

> Handoff para uma sessão focada de debug. **Método: `superpowers:systematic-debugging`.**
> NÃO sonde ad-hoc — capture o traceback real primeiro, confirme a causa-raiz, só então corrija.

## Bug

`Timeout reading from redis:6379` **intermitente** nos processos **em execução**
(`worker.py` e `dashboard_api.py`, ambos usando `redis.asyncio`).

**Sintoma:** às vezes o worker não consome o job (batch fica `queued`/`waiting`), às
vezes a dashboard não lê o resultado da reply-queue e marca o batch `failed` — **mesmo
quando o download funcionou e os arquivos estão em disco**. O plano de controle (Redis)
mente; o download em si funciona.

## Já descartado (NÃO repetir)

- **Firewall:** persiste com o firewall Hostinger desanexado.
- **`socket_timeout` mal configurado:** teste ISOLADO dentro do container do worker —
  `redis.asyncio.from_url(os.environ["REDIS_URL"])` + `await r.ping()` (0.01s) +
  `await r.blpop("x", timeout=3)` retorna `None` **limpo** em 3.01s, sem erro.
- **DNS/rede:** TCP para `redis:6379` conecta na hora (conexão fresh).
- **Restart:** `docker compose restart worker` dá alívio temporário; o erro volta.

## Assinatura → hipótese

Conexão **fresh** funciona; conexões **long-lived** nos processos async apodrecem.

Suspeita principal: um `blpop` **cancelado** por um timeout externo
(`asyncio.wait_for`/`asyncio.timeout`) **envenena a conexão do pool** do
`redis.asyncio` → leituras seguintes levantam `TimeoutError` até a conexão ser
resetada.

Pontos a revisar:

- `worker.py:180` e `dashboard_api.py:201` — `redis.from_url(REDIS_URL, decode_responses=True)`
  (sem `retry_on_timeout`, sem `health_check_interval`, pool compartilhado).
- `worker.py:1582` — `blpop("kratos:pje:jobs", timeout=REDIS_BLPOP_TIMEOUT_SECS=5)`.
- `dashboard_api.py:709` — `blpop(reply_queue, timeout=RESULT_POLL_BLPOP_TIMEOUT_SECS=5)`,
  e o loop `_poll_results_loop` / `RESULT_WAIT_TIMEOUT_SECS=360` que pode envolver o
  `blpop` num `wait_for` e cancelá-lo.
- Circuit breaker `REDIS_CIRCUIT_THRESHOLD=20` (`config.py`) — como interage com os erros.

Candidatos de fix a **AVALIAR** (não aplicar às cegas): `retry_on_timeout=True`,
`health_check_interval`, conexão dedicada por consumidor, **não cancelar** o `blpop`
(usar o timeout nativo do `blpop` em vez de um wrapper externo que o cancela).

## Método

1. Reproduzir e **CAPTURAR o traceback real** do `TimeoutError` no processo rodando
   (não o log resumido) — instrumentar com logging de exceção completo, ou reproduzir
   o padrão de cancelamento num script mínimo.
2. Formular **UMA** hipótese, testar, confirmar a causa-raiz **antes** de corrigir.
3. TDD para o fix; rodar `pytest tests/ -q` (441+ testes) antes de commitar.

## Acesso e ambiente

- Repo: `~/projetos-26-2/pje-download`, branch **master** (⚠️ NÃO `/mnt/c/...` — é STALE).
- Deploy ao vivo: Hostinger VPS São Paulo — `ssh -i ~/.ssh/pje_deploy deploy@91.108.125.85`.
  App em `/opt/pje-download` via `docker compose`. Portas só em `127.0.0.1` (túnel SSH).
  API precisa de `X-API-Key` (em `/opt/pje-download/.env`).
- ⚠️ **PRODUÇÃO** com credenciais MNI reais e autos com PII. **O download FUNCIONA — não
  regredir.** Processo de teste que retorna 13 docs: `5022505-25.2024.8.08.0012`.
- ⚠️ **NÃO re-anexar** o firewall Hostinger (quebra a rede inter-container do Docker;
  por isso as portas foram para `127.0.0.1`).

## Ler antes de começar

`TODO.md` (seção "Bug ativo"), `CLAUDE.md` (backlog item 1), e a memória
`project_pje-download.md`. A causa-raiz deste bug é o único item entre o app e uma
orquestração 100% confiável — o download já foi validado de ponta a ponta.
