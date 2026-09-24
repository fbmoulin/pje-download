# Full analysis — pje-download @ `1bbcdde`

Analysis performed read-only against `1bbcdde`; every claim below was measured in this session,
and where a step could not be executed it says so explicitly instead of asserting the conclusion.

Fixes have since landed in two PRs: **F1** and **F2** in #46 (`e4ca0b2`, deployed), and **F3–F6**,
the Codex lag-baseline follow-up, plus a seventh finding **F7** surfaced during that work, in #47.
The baseline and the findings are recorded as they were *at analysis time* — the 471-test baseline
is the pre-fix number; #47 carries 574.

---

## HEADLINE

**The repo is in good health and its docs are unusually honest — the findings here are not
regressions, they are four places where a stated guarantee is not the guarantee that exists.**

> **Status (2026-09-24):** F1, F2 fixed and deployed (#46). F3, F4, F5, F6 and the Codex lag
> follow-up fixed in #47, each as its own commit with tests verified in both directions.
> **F7** (below) was found while reviewing F4 and is also fixed in #47. Nothing from this report
> remains open; residual follow-ups are listed at the end.

Lint, format, specs and tests are green (471 collected, matching `CLAUDE.md` exactly). The
architecture, the containment work of PRs #32–#35, and the `build_sha` provenance chain all hold
up under inspection.

What does not hold up is a specific class: **controls that are named after something stronger than
what they do.** A lag alert that can never fire, a dependency pin one of the two images never
receives, a deploy step called "Validate MNI credentials" that cannot fail on bad credentials, and
an SSRF guard that accepts arbitrary hosts. Three of the four are invisible precisely because they
report success.

A fifth item is structural rather than a defect: in the deployed configuration the worker's
advertised 3-strategy cascade is a **1-strategy cascade**, and the code that would close that gap
is also the unaudited code (F6).

---

## Baseline measured this session

| Gate | Command | Result |
|---|---|---|
| Lint | `ruff check .` @ **0.14.14** (the CI pin) | ✅ All checks passed |
| Format | `ruff format --check .` | ✅ 39 files already formatted |
| Specs | `python tools/verify_spec.py docs/specs/*.md` | ✅ 2/2, 11/11 checks each |
| Tests | `pytest tests/ -q` (live redis on 6379) | 471 collected · **469 passed, 2 failed** |
| Gitleaks rules | `bash tools/verify_gitleaks_rules.sh` | ⚠️ **not exercised** — `gitleaks` absent; script correctly fails closed (exit 1) |

The 2 failures are an **artifact of this container running as uid 0**, not a defect:
`test_propagates_oserror` and `test_audit_called_on_disk_error` create a `0o444` directory and
expect `OSError`. Root bypasses directory permissions, so the write succeeds and the `pytest.raises`
block fails. Verified directly:

```
$ id -u → 0
ROOT BYPASSED 0o444 -> test premise invalid here
```

CI runs as non-root, so these pass there. **Effective result: green.**

Environment caveat: Python here is **3.11.15**; the project targets 3.12. Nothing in the findings
below depends on the difference.

---

## F1 — `pje_audit_sync_lag_seconds` is never set: the alert cannot fire, and the panel reads *perfect*

> ✅ **FIXED** in `1e788f7` on this branch. Kept below as the diagnosis.

**Severity: high (latent — see reachability).** The gauge is declared in `metrics.py`, consumed by
one alert rule and two Grafana panels, and **never written by any production code path.**

```
$ grep -c 'metrics.audit_sync_lag_seconds' *.py   (excluding metrics.py, tests/)
0        ← every other metric has ≥1 production site
```

`audit_sync.py` *computes* the lag — but only into a dict:

```python
# audit_sync.py:297
lag = (datetime.now(UTC) - self._last_synced_event_ts).total_seconds()
return {"lag_seconds_event_time": lag, ...}   # → /healthz only
```

Meanwhile the code that maintains `_last_synced_event_ts` describes itself as feeding a gauge:

```python
# audit_sync.py:499
# Track newest event timestamp for lag metric. Always coerce to
# tz-aware UTC — ... would raise TypeError ... and silently freeze the lag gauge.
```

The author believed a gauge was on the other end. There isn't one. Sprint 12 B2 fixed the
*computation* (`_coerce_utc`) and the wiring was never there to fix.

### Why this is worse than a missing metric

An unlabelled `prometheus_client` Gauge is materialised at construction, so it **is** scraped — at
a constant zero:

```
$ python -c "... generate_latest(metrics.REGISTRY) ..."
EXPOSED: 'pje_audit_sync_lag_seconds 0.0'
value that will always be scraped: 0.0
```

A missing series would break the panel visibly (`No Data`) and leave the alert in an unknown state.
A constant `0.0` renders as a flat line at zero and reads as **ideal sync health**, forever:

```yaml
# ops/monitoring/pje/alert-rules.yml:10
- alert: PjeAuditSyncLagHigh
  expr: pje_audit_sync_lag_seconds > 60     # 0.0 > 60 is never true
```

Plus two panels in `ops/monitoring/pje/dashboard.json:39,47`.

**Reachability:** `AUDIT_SYNC_ENABLED=false` in production today, so nothing is syncing and there is
no lag to miss. This bites at exactly the moment the open backlog item *"Sink de auditoria no
Railway"* is switched on — i.e. the first moment anyone would rely on the alert.

**Fix:** ~3 lines — `.set(...)` alongside the `_last_synced_event_ts` update, and `.set(0)` /
staleness handling on the no-data path. Consider asserting in `ops/monitoring/verify.sh` that every
metric named in `alert-rules.yml` has a production writer; that check would have caught this.

---

## F2 — The dashboard image ignores `requirements.txt` entirely

> ✅ **FIXED** in `dfbd8a7` on this branch. Kept below as the diagnosis.
>
> Derived from `requirements.txt` by excluding only `playwright` (139 MB measured,
> never imported by the dashboard) rather than adding a second pin list — a parallel
> list is what drifted here in the first place. Guarded by
> `tests/test_image_dependency_pins.py` (9 tests). Docker was unavailable in the
> analysis environment, so **the image was not built**; what was verified is that the
> derivation yields the same 8 packages, now pinned, and that pip resolves them.

**Severity: high.** The worker target installs pinned deps. The dashboard target installs a
**hand-written, completely unpinned list**:

```dockerfile
# Dockerfile — dashboard target
RUN pip install --no-cache-dir \
        aiohttp prometheus_client structlog zeep requests gdown \
        "redis[hiredis]" asyncpg && ...
#   ^ not one version specifier

# Dockerfile — worker target
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium
```

`COPY requirements.txt .` is already in the shared `base` stage — the dashboard target inherits the
file and never uses it.

### Measured drift, today

| package | `requirements.txt` | what a dashboard rebuild installs now |
|---|---|---|
| `redis[hiredis]` | `==8.0.0` | **8.1.0** |
| `structlog` | `==25.5.0` | **26.1.0** (major) |
| `aiohttp` | `==3.14.1` | **3.14.3** |
| `zeep` | `==4.3.3` | 4.3.3 (coincides) |
| `asyncpg` | `==0.31.0` | 0.31.0 (coincides) |

Four consequences, in increasing order of how much they should sting:

1. **CI green proves nothing about the dashboard image.** The suite runs against
   `requirements.txt`; the dashboard container runs a different, untested dependency set. This is
   the same "tested tree ≠ deployed artifact" gap the `build_sha` work exists to close — `build_sha`
   proves *which source* is running and says nothing about which *libraries* are.
2. **The two containers of one deploy run different library versions** of `redis`, `structlog` and
   `aiohttp`.
3. **TODO.md is deliberating a decision that is already made.** The open item *"Mesclar Dependabot
   #36 e #41"* weighs accepting `structlog` 25→26 and `redis` 8.0.0→8.0.1, noting *"Mesclar é outro
   deploy em produção, desta vez carregando versões novas — decisão diferente"*. The dashboard has
   been shipping `structlog` **26** and `redis` **8.1.0** on every rebuild regardless.
4. **This is the exact class of the documented outage.** PR #32's root cause was `redis-py 8.0.0`
   silently changing a default, and the stated defence is the pin. The dashboard — one of the two
   BLPOP sites (`_poll_results_loop`) — does not receive that defence.

**Not alarmist about (4):** the specific historical bug is independently defended, because
`socket_timeout=REDIS_SOCKET_TIMEOUT_SECS` is now passed explicitly at both client-construction
sites. The pin is defence against the *next* one.

**Fix:** one line — `RUN pip install --no-cache-dir -r requirements.txt` in the dashboard target
(plus the existing `curl` apt step). Layer caching is unaffected; `requirements.txt` is already
copied in `base`. It pulls `playwright` into the dashboard image, which is dead weight there — if
that matters, split into `requirements.txt` + `requirements-worker.txt` rather than reverting to a
hand-maintained list, which is the thing that already drifted once (the `COPY` list, fixed in
`237ea55`).

---

## F3 — `Validate MNI credentials` cannot fail on bad credentials

> ✅ **FIXED** in #47 — `7572ddd` (`MNIClient.verify_credentials()` + `tools/verify_mni_credentials.py` +
> the deploy step, failing only on a genuine rejection) and `5884eee` (a bare HTTP 403 is now
> `blocked`, not `auth_failed`: the MNI rejects credentials with a SOAP fault, a 403 is CloudFront
> geo-restriction — the probe reports that as *inconclusive* with the cause named, instead of
> "credentials INVALID" during a geo incident; proven live from this sandbox's geo-blocked egress).
> ⚠️ Still unverified from here: the live classification of a *genuinely wrong password* against
> TJES. Read the "Smoke-test MNI credentials" step on the first real deploy.

**Severity: medium.** The final deploy gate:

```yaml
- name: Validate MNI credentials
  script: |
    if docker compose exec -T dashboard python -c "from mni_client import MNIClient; MNIClient()"; then
      echo "MNI credentials are valid"
```

Measured — both exit 0:

```
MNIClient() with EMPTY credentials -> constructed OK
  username repr: ''  password repr: ''   zeep client built? False (lazy)
MNIClient() with WRONG credentials -> constructed OK, exit 0
```

`MNIClient.__init__` does a dict lookup in `TRIBUNAL_ENDPOINTS` and stores two strings. The zeep
client is lazy (`_get_client` is not called). Credentials are transmitted in exactly one place:

```python
# mni_client.py:368-369  — inside consultar_processo, nowhere else
"idConsultante": self.username,
"senhaConsultante": self.password,
```

So the step fails only on an import error or an unsupported `MNI_TRIBUNAL`. Historically it *did*
catch something real — the `MniClient`/`MNIClient` typo (`461a789`) — but that is an import check
wearing a credential check's name, and `CLAUDE.md` records the intent as *"Task 1.3 — post-deploy
MNI credential smoke test."*

**Second-order, and the more useful half:** `health_check()` would not fix this either. It fetches
the WSDL and enumerates operations — it never sends `idConsultante`/`senhaConsultante`. **Nothing in
the deploy path or in `/health` validates credentials.** `mni: healthy` means *the tribunal's WSDL is
reachable from this IP*, which is what it proved during the geo-blocking work — a genuinely useful
signal, just not this one.

**Fix:** either call `consultar_processo` against a known processo and assert the result is not
`auth_failed`, or rename the step to what it does (`Validate MNI client imports and tribunal
config`). The rename is honest and free; the smoke test is what `CLAUDE.md` actually asked for.

---

## F4 — With MNI enabled, strategies 2 and 3 are unreachable: the cascade is 1 strategy, not 3

> ✅ **FIXED** in #47 — `b3159d9`, landed *after* F6 (`569d94d`) by design. `_ensure_browser()`
> lazily launches a headless Chromium from the saved session file only when a fallback is actually
> needed, never blocking on manual login; without `/data/pje-session.json` behaviour is byte-identical
> to before. ⚠️ Operator action needed for it to be live: produce the session file once via
> `/api/session/login` or `python pje_session.py login`. Not verifiable here against a real PJe session.

**Severity: medium (design gap, not a silent failure).** `self.page` / `self.context` are assigned
in exactly one place — the branch of `load_session` that is skipped whenever MNI is available:

```python
# worker.py:290
if self.mni_client is not None:
    ...
    log.info("pje.session.mni_available", note="playwright_deferred")
    return True          # ← self.page stays None; nothing ever un-defers it
```

Nothing lazily initialises a browser later; `invalidate_session` only sets them back to `None`.
Measured against a `chromium.launch` that raises if called:

```
load_session -> True
  self.page = None   self.context = None   self._browser = None
strategy 2 (_try_official_api)    -> None   [pje.api.browser_unavailable]
strategy 3 (_download_via_browser)-> None   [pje.browser.unavailable]
```

Chromium is never launched. `deploy.yml` writes `MNI_ENABLED=true`, so this is the production
configuration.

**Consequence:** the documented remedy for the 11 `vinculados` that MNI 2.2.2 does not return
(*"precisam do fallback Playwright"*) cannot execute. Those processos take the
`partial_success` → *"anexos pendentes sem sessão PJe disponível"* branch.

**Credit where due:** this is **not** silent. The worker says exactly what happened, in the result
message and in two distinct warning logs. The gap is between the behaviour and the docs —
`README.md:48` describes *"3 estrategias em cascata (MNI > API > browser)"* and `worker.py`'s
docstring says *"Fluxo com 4 estratégias em cascata"*. In the shipped configuration, two of them
cannot run. `playwright_deferred` is permanent, not deferred.

---

## F5 — The `gdrive_map` SSRF guard is substring-based and accepts arbitrary hosts

> ✅ **FIXED** in #47 — `cfc7043`. `extract_folder_id` parses with `urlsplit` and requires `https` +
> `netloc == "drive.google.com"` exactly; `download_gdrive_folder` hands strategies 1 and 3 a URL
> rebuilt from the validated id, so `page.goto` can no longer receive a caller-supplied host. 22 new
> tests failed against the old regex, including all four bypasses measured below.

**Severity: medium** (authenticated-only; see threat model). `dashboard_api.py:1119` comments the
check as *"(prevents SSRF)"*. It is `re.search` — unanchored, host-unaware:

```
PASSES dashboard SSRF guard?   URL
YES -> 1AbC_dEf                https://drive.google.com/drive/folders/1AbC_dEf
YES -> 1AbC                    https://attacker.example/x?u=drive.google.com/drive/folders/1AbC
YES -> 1AbC                    https://evil.test/drive.google.com/drive/folders/1AbC
YES -> 1AbC                    http://169.254.169.254/drive.google.com/drive/folders/1AbC
YES -> 1AbC                    file:///etc/drive.google.com/drive/folders/1AbC
no                             https://drive.google.com.evil.test/drive/folders/1AbC
```

The accepted URL is stored raw in `gdrive_map`, travels to the worker as `gdriveUrl`, and reaches
`download_gdrive_folder(raw_url, ...)`. Of its three strategies:

| strategy | URL used | verdict |
|---|---|---|
| 1 `_try_gdown` | raw `folder_url` | **safe — measured.** gdown re-derives the id and requests `drive.google.com/embeddedfolderview?id=…`; 0 requests reached a local listener I controlled |
| 2 `_try_requests_parse` | `folder_id` | safe by construction — URL rebuilt from the id |
| 3 `_try_playwright_download` | raw `folder_url` | **the sink** — `page.goto(folder_url)` verbatim |

I initially expected strategy 1 to be the hole; measuring it showed gdown normalises the host, so
the finding is narrower than it first looked.

**Strategy 3 is not gated by F4** — it launches its *own* `async_playwright()` browser, independent
of the worker's `self.page`, and the worker image does run `playwright install chromium`. Data flow
confirmed by capturing the argument:

```
URL handed to Chromium page.goto():  http://127.0.0.1:9/drive.google.com/drive/folders/1AbCdEf
host navigated to: 127.0.0.1:9        expected if guard worked: drive.google.com
```

**Not verified:** Chromium actually issuing the request. This container's Playwright build revision
does not match the pinned `playwright==1.60.0` (`chromium_headless_shell-1223` expected,
`-1194` present), so the launch failed before navigation. The data flow above is proven; the final
hop is inference — a reasonable one, but stated as inference.

**Threat model, honestly:** reaching this needs an authenticated `POST /api/download`
(`DASHBOARD_API_KEY` is mandatory when `APP_ENV=production`), and the dashboard is published only on
`127.0.0.1:8007` with 8007 firewalled and reachable via SSH tunnel. This is **not** anonymously
exploitable. It is a defence-in-depth control that does not provide the defence its own comment
claims, on a path whose input is a pasted third-party link.

**Fix:** parse and compare the host (`urlsplit(url).hostname == "drive.google.com"` plus an
`https` scheme check) instead of `re.search` over the whole string, and/or hand strategies 1 and 3
the URL rebuilt from `folder_id` the way strategy 2 already does.

---

## F6 — `worker.py` emits zero CNJ 615/2025 audit entries (coupled to F4)

> ✅ **FIXED** in #47 — `569d94d`, landed before F4 as this section demanded. All four sites now
> emit `document_saved` entries (`fonte="pje_api"` / `"pje_browser"`, success and OSError paths),
> mirroring `mni_client._save_document`; 6 tests read the JSON-L back.

**Severity: low today, high the moment F4 is fixed.** Every other module that writes a judicial
document audits it. `worker.py` does not reference `audit` at all:

```
$ grep -n "audit" worker.py
NO MATCHES IN worker.py
```

Measured — the identical act, two paths:

```
worker._download_document_api -> sentenca.pdf | bytes: 32
  file on disk : True
  AUDIT ENTRIES: 0          ← document written, no CNJ record
mni_client._save_document ->  saved
  AUDIT ENTRIES: 1          ← {"event_type": "document_saved", "fonte": "mni_soap", ...}
```

Four worker sites write documents with no audit entry: `_download_document_api`,
`_try_full_download_button` (two paths), `_download_docs_individually._fetch_one`, and
`_download_docs_sequential`. No test covers it — `tests/test_worker.py`'s only `audit` mention is a
comment naming the *sprint*.

`audit.AuditEntry`'s own docstring enumerates `fonte` as `mni_soap | pje_api | pje_browser |
google_drive | batch`. `pje_api` and `pje_browser` **are** emitted — but from `pje_session.py`,
which is reached only by `batch_downloader.py` (the offline CLI) and the dashboard's login handler.
Per `AGENTS.md`, `worker.py` is the execution plane for the dashboard, and it is not on that path.

**The coupling is the point:** this is latent *only* because F4 keeps those four sites unreachable.
Fixing F4 — giving the worker a browser so the `vinculados` anexos can finally be fetched — silently
turns on a document-download path with no compliance trail. **F4 and F6 should be one change, in
that order.**

---

## F7 — In MNI mode, worker uptime > 60 min turned every MNI miss into a batch-fatal `session_expired`

> ✅ **FIXED** in #47 — `11c4955`. Found on 2026-09-24 while reviewing F4; **pre-existing on `e4ca0b2`,
> i.e. in production at the time.**

**Severity: high.** `load_session`'s MNI branch stamps `session_started_at` at boot for a browser that
never exists. `download_process` then checked `is_session_expired()` before the fallback strategies,
regardless of whether any browser existed. After `SESSION_TIMEOUT_MINUTES` (60) of *worker uptime* —
days, in production — every processo where MNI returned nothing came back `session_expired`:

```
uptime=   59 min  MNI=no documents  ->  status=failed           dashboard aborts batch: False
uptime=   61 min  MNI=no documents  ->  status=session_expired  dashboard aborts batch: True
uptime= 1440 min  MNI=no documents  ->  status=session_expired  dashboard aborts batch: True
```

`session_expired` is in `dashboard_api._FATAL_WORKER_STATUSES`: the dashboard LREMs the remaining
jobs from `kratos:pje:jobs` and fails the whole batch. **One wrong CNJ, one empty processo or one
transient MNI error aborted every job queued behind it.**

**Fix:** the operational timeout is about a browser session. Browser-primary mode (no MNI) keeps the
original, unconditional semantics verbatim. In MNI mode it applies only if a helper browser (F4)
actually exists, and then closes it *without* deleting the operator's session file (new
`_close_browser`; the timeout is operational, not proof the cookie is dead — `_ensure_browser`
re-validates against `login.seam`). With no browser there is nothing to expire, and the fallbacks
report `failed` / `partial_success` — never a fatal status. 6 tests; 5 fail on the pre-fix code
(all MNI-mode cases, up to 7-day uptime), the browser-primary guard passes either way.

---

## Minor

- **Stale `HEALTHCHECK` in the Dockerfile dashboard target** — `CMD curl -sf .../api/status`, which
  Sprint 8 put behind `X-API-Key`; it would 401-loop forever. `docker-compose.yml:95` overrides it
  to `/healthz`, so the deployed path is correct and this is dead text — but it is a live trap for
  anyone running the image outside compose, and a leftover of the bug fixed in `461a789`.
- **`redis.close()` is deprecated** since redis-py 5.0.1; measured on the pinned 8.0.0:
  `Call to deprecated close. (Use aclose() instead)`. Two sites (`worker.close`,
  `DashboardState.close`). Works today; an eventual removal breaks both shutdown paths — and per F2
  the dashboard is already running 8.1.0.
- **Two TODO.md "open" follow-ups are already done.** (a) `ProgressMessage` *does* declare
  `batchId: NotRequired[str | None]` (`protocol.py`, added in `518c31a`); the cited `worker.py:1494`
  has drifted and now points at `_publish_result`'s retry loop, not the progress publish (line
  1573). (b) `_try_official_api` constructs no `ResultMessage` — it returns `list[dict]` of file
  metadata; the only construction site, `_result()`, is already typed. Both can be struck.
- **`ssh-keyscan` + `StrictHostKeyChecking=no`** in `deploy.yml` are redundant — the keyscan's
  output can never be consulted. Worth noting because the keyscan is the suspected source of the
  documented intermittent `Sync files to VPS` failure; deleting it removes a failure mode without
  weakening anything (the `no` was already the effective policy).
- **`metrics.py`'s module docstring** files `worker.py`'s counters under *"Exposed at GET /metrics
  by dashboard_api.py"*. They are separate registries in separate processes; the worker serves its
  own on `:8006/metrics`. Its handler docstring says so correctly.
- **Rate limiting is POST-only**, so `GET /api/*` is unthrottled. Fine in production (API-key gated,
  loopback-bound); noted only so the asymmetry is a decision rather than an accident.

---

## Verified accurate (no action)

Things I specifically tried to falsify and could not:

- **Test count.** `CLAUDE.md`'s `**471 tests**` — exactly 471 collected.
- **The TTL cross-container invariant is latent, not live** (TODO item 1). Confirmed: neither
  `BATCH_MAX_DURATION_SECS` nor `REDIS_RESULT_QUEUE_TTL_SECS` appears in either service's
  `environment:` block, there is no `env_file:`, and `deploy.yml`'s `.env` writer does not emit them.
- **Metric label cardinality.** No metric carries a CNJ number or any PII as a label — `/metrics`
  being public is safe on that axis.
- **Path traversal in `_resolve_output_dir`.** `.resolve().is_relative_to(...)` correctly rejects
  absolute and `../` subdirs, and resolves symlinks before comparing.
- **`_unique_filename` against hostile document names.** `unique_path(dir / name).name` collapses
  `../x` and `/etc/passwd` to a bare basename.
- **`tools/verify_gitleaks_rules.sh` fails closed** when `gitleaks` is missing (exit 1, explicit
  message) — as designed. Its 12 rule assertions were therefore **not** exercised here.

---

## Suggested order

Each is independently shippable; `AGENTS.md` asks that these not be mixed into one PR.

| # | Finding | Effort | Why this order |
|---|---|---|---|
| ~~1~~ | ~~**F2** dashboard `-r requirements.txt`~~ | ✅ `dfbd8a7` (#46) | Largest blast radius per character; makes CI mean something for both images |
| ~~2~~ | ~~**F1** wire the lag gauge~~ | ✅ `1e788f7` (#46) | Must land *before* the Railway sink is enabled, not after |
| ~~3~~ | ~~**F3** rename or implement the credential gate~~ | ✅ `7572ddd` + `5884eee` (#47) | Implemented, not renamed; 403 split out as `blocked` |
| ~~4~~ | ~~**F5** host-compare the gdrive URL~~ | ✅ `cfc7043` (#47) | |
| ~~5~~ | ~~**F4 + F6** together~~ | ✅ `569d94d` → `b3159d9` (#47) | F6 first, as required |
| ~~6~~ | ~~Minor cleanups + strike the two stale TODOs~~ | ✅ #47 | healthcheck, `aclose()`, keyscan, metrics docstring, TODO |
| — | **F7** (found during 5) | ✅ `11c4955` (#47) | |

### Review round on #47 (2026-09-24) — `code-review` + `security-review` over the assembled branch

`code-review` returned seven findings; all were real, none had been caught by the per-finding
both-directions tests because each sat *between* two fixes:

- **The dashboard image never contained `tools/`** — so F3's smoke test would have failed with
  "can't open file" → exit 2 → warning → a deploy with wrong credentials would pass. The exact false
  assurance F3 replaced, one layer down. Fixed (`6cc2912`) with a guard that reads `deploy.yml` and
  demands every path exec'd in the dashboard container be in its COPY sources.
- `docker compose exec`'s own exit 1 (service restarting) read as "credentials rejected" — fixed
  (`6b9c0ac`): only the script's sentinel line makes exit 1 mean *invalid*.
- "Acesso negado" carried in the SOAP *body* (`sucesso=false`) read as `mni_error` → *valid* — fixed
  (`a9fff50`): same rule as the fault branch, one classifier.
- A permanently torn tail in an older audit file hid a 3-hour backlog in a newer one as lag `2e-06 s`
  — fixed (`55362d3`): the probe keeps looking past a partial tail.
- In `worker.py`: F7's expiry block ran *after* `_ensure_browser()` (expired helper closed but not
  relaunched in the same job); nine near-identical `audit.log_access` blocks; `_current_tribunal()`
  without `.upper()` diverging from `mni_client` — fixed by the worker specialist (see #47).

`security-review` (identify → false-positive filter → report): **no HIGH/MEDIUM finding at ≥0.8
confidence.** The branch tightens the only third-party-URL → `page.goto` path and its new audit
records carry no secrets; the one hardening note (probe exception text reaching the CI log) was
rated ~2/10 for an actual leak and left as is.

### Residual follow-ups (not defects of this report; recorded so they are not lost)

- **F3, live:** confirm on the first real deploy that a *wrong* password classifies as `INVALID`
  (SOAP fault "Acesso negado"); a different fault text means `consultar_processo`'s classifier needs
  one more case. Correct credentials should read `valid`.
- **F4, operator:** the fallback stays dormant until `/data/pje-session.json` exists
  (`/api/session/login` or `python pje_session.py login`).
- **Deploy host-key pinning:** `deploy.yml` never verified the VPS host key (neither rsync nor the
  `appleboy/ssh-action` steps); the dead `ssh-keyscan` was removed rather than promoted. Real pinning
  is a `VPS_HOST_KEY` secret written to `known_hosts` with `StrictHostKeyChecking=yes` — needs the key.
- **Lag alert threshold vs. tick:** the gauge is deliberately tick-granular; a continuous per-scrape
  lag would need `PjeAuditSyncLagHigh`'s threshold raised above `AUDIT_SYNC_INTERVAL_SECS` first.
