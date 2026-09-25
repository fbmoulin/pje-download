# SPEC — zeep SSRF hardening: `Settings(forbid_external=True)` in the MNI client

**Status:** Tasks 1–3 DONE (2026-09-25, commit pending). Task 4 (live post-deploy check)
pending — needs a real deploy first. Task 5 (this doc + CLAUDE.md) in progress.
**Author:** sessão 2026-09-25
**Target:** `mni_client.py` (`_get_client`, `TRIBUNAL_ENDPOINTS`), `config.py`
**Research:** CLAUDE.md backlog item 5; live WSDL measurement of TJES (2026-07-25, recorded
in CLAUDE.md).

---

## Goal

Pass `zeep.Settings(forbid_external=True)` into the `zeep.Client(...)` construction in
`mni_client.py:_get_client`, so a maliciously crafted or compromised WSDL/XSD response can no
longer make the MNI client fetch an attacker-controlled URL (SSRF via `xsd:import`/
`xsd:include` `schemaLocation`) — **for `TJES` only, for now.** Felipe's decision (2026-09-25):
scope this spec to the one tribunal already measured clean, rather than blocking on measuring
the other 5 from `pje-vps` first. The other 5 tribunals keep today's behavior (no
`forbid_external`) until each is measured and added — see "Future expansion" below.

### Non-goals (explicitly out of scope)

| Out of scope | Why |
|---|---|
| Bumping `zeep` itself | Already done (`23d5e8a`, `zeep==4.3.3`), closes Dependabot alert #1 / GHSA-4cc2-g9w2-fhf6. This spec is the *remaining* defense-in-depth step, not a dependency bump. |
| Changing the WSDL allowlist | `TRIBUNAL_ENDPOINTS` is already a hardcoded `.jus.br` allowlist (`mni_client.py:45-51`); this spec does not touch it. `forbid_external` defends against what happens *after* a legitimate WSDL fetch — a malicious `schemaLocation` embedded inside an otherwise-trusted response (compromised tribunal server, MITM without TLS pinning, or a future tribunal added to the allowlist without review). |
| Validating XML signatures / response bodies | Separate control; not part of `forbid_external`. |
| Hardening `TJES_2G`, `TJBA`, `TJBA_2G`, `TJCE`, `TRT17` now | Explicitly deferred by Felipe's 2026-09-25 decision, not abandoned. Each needs the same `schemaLocation` measurement from a BR-IP host (`pje-vps`) that TJES already got before it can safely join the hardened set. Tracked as follow-up work, not a blocker for this spec. |

---

## Why this was blocked, and how it got unblocked

CLAUDE.md backlog item 5 previously said this was blocked by "hoje bloqueado por IP cloud" —
refuted 2026-07-18 (the real MNI blocker was AWS CloudFront geo-restriction on the VPS's
outbound IP, unrelated to `forbid_external`, resolved by moving the VPS to the São Paulo
datacenter). Once that was cleared, a second, real prerequisite emerged: **`forbid_external` is
per-`Settings`-instance, and flipping it blind for a tribunal risks a hard failure
(`ExternalReferenceForbidden`) if that tribunal's WSDL legitimately references an external
schema via `schemaLocation`.**

TJES has been measured, live, from a BR-region IP (2026-07-25):

```
curl https://pje.tjes.jus.br/pje/intercomunicacao?wsdl  → HTTP 200, 34.5 KB
grep -c 'xs:import\|xs:include'   → 5
grep -c 'schemaLocation='          → 0
```

Five `xsd:import`/`xsd:include` elements, **zero** carrying a `schemaLocation` attribute — the
imports are namespace-only declarations. Zeep/lxml only performs a network fetch for an import
that has a `schemaLocation` URL; a namespace-only import never triggers one. TJES is therefore
confirmed safe for `forbid_external=True`.

The other five tribunals are unmeasured, and measuring them requires running from `pje-vps` (or
another BR-IP host) — this session's own sandbox sits behind the same CloudFront geo-block that
hid MNI connectivity until 2026-07-18, so it cannot take that measurement itself. **Rather than
block this whole spec on that access, Felipe's call (2026-09-25) is to ship the one tribunal
that's already provably safe now, and treat the other 5 as separate, later follow-up work.**

### Future expansion (not part of this spec's Tasks)

When ready to extend coverage: measure `schemaLocation` on a tribunal's WSDL from `pje-vps`
(see the `curl`/`grep` commands above, one tribunal at a time), and if it comes back zero, add
that tribunal to `MNI_FORBID_EXTERNAL_TRIBUNALS` (Design section below) — no other code change
needed. This is deliberately left as a config change, not a new spec, precisely so expansion
doesn't require another full SDD cycle per tribunal.

---

## Design

`forbid_external` becomes a **per-tribunal** setting, not a global one — the opposite of the
original all-or-nothing design, now that scope is intentionally narrowed to one tribunal at a
time. Add a new config constant, following this repo's existing convention of env-configurable
constants (CLAUDE.md, Sprint 13 Q4: `PLAYWRIGHT_FULL_DOWNLOAD_TIMEOUT_MS` et al.):

```python
# config.py
MNI_FORBID_EXTERNAL_TRIBUNALS: frozenset[str] = frozenset(
    t.strip().upper()
    for t in os.getenv("MNI_FORBID_EXTERNAL_TRIBUNALS", "TJES").split(",")
    if t.strip()
)
```

```python
# mni_client.py, _get_client
from zeep import Client, Settings
from config import MNI_FORBID_EXTERNAL_TRIBUNALS
...
settings = Settings(forbid_external=self.tribunal in MNI_FORBID_EXTERNAL_TRIBUNALS)
self._client = Client(wsdl=self.wsdl_url, transport=transport, settings=settings)
```

`self.tribunal` is already uppercased in `__init__` (`mni_client.py:161`,
`.upper()`), matching `MNI_FORBID_EXTERNAL_TRIBUNALS`'s casing — no extra normalization needed.

Default (`MNI_FORBID_EXTERNAL_TRIBUNALS=TJES`, or the env var unset) hardens only TJES;
every other tribunal gets `Settings(forbid_external=False)`, which Task 3 must confirm is
behaviorally identical to passing no `settings` at all (zeep's own default) — i.e. this change
must be a true no-op for the other 5 tribunals, not just "probably fine."

`forbid_external=True` blocks zeep/lxml from dereferencing any `schemaLocation` (or WSDL
`import`/`include`) that points outside the document's own origin, raising
**`zeep.exceptions.ExternalReferenceForbidden`** directly — confirmed empirically against the
installed `zeep==4.3.3` (`zeep/loader.py`'s `ImportResolver.resolve()` raises it *before*
calling `transport.load()`, i.e. before any network attempt). This is a plain `zeep.exceptions.Error`
subclass, **not** wrapped in `TransportError`/`XMLSyntaxError` as originally guessed here —
corrected once Task 1/2 actually ran it.

---

## Tasks

Executed with **TDD** and **frequent commits** — one commit per task, tests written before
implementation, suite green before moving on. Per `superpowers:writing-plans`, each task is
independently verifiable; execution follows `superpowers:subagent-driven-development`.

### Task 1 — Prove the corpus can detect the SSRF window (RED) — ✅ DONE

Added `tests/fixtures/wsdl_external_schema_location.wsdl`: a minimal valid WSDL whose `<types>`
carries one `xsd:import` with `schemaLocation="http://192.0.2.1/malicious.xsd"` — RFC 5737
TEST-NET-1, never routable, chosen over the originally-suggested `169.254.169.254` once this
session confirmed that address risks hitting a real cloud metadata service on some CI
providers (Azure IMDS). Real, unmocked `zeep.Client`/lxml load this fixture; only
`requests.Session.get` is mocked (to a sentinel-raising `MagicMock`, via
`unittest.mock.patch.object`), so the resolver's own logic is exercised for real, without any
actual network I/O or CI-provider dependence.

`tests/test_mni_client.py::TestForbidExternalSSRFFixture::test_without_forbid_external_the_fetch_is_attempted`
confirms the vulnerability window: with no `Settings`, `zeep.Client(wsdl=FIXTURE)` reaches the
mocked `session.get` (i.e., attempts the external fetch) — proven empirically, not assumed.

### Task 2 — Implement per-tribunal `Settings(forbid_external=...)` (GREEN) — ✅ DONE

Added `MNI_FORBID_EXTERNAL_TRIBUNALS` to `config.py` and the `settings=` kwarg to the
`Client(...)` call in `mni_client.py:_get_client` (now `mni_client.py:213-217`), per the Design
section — `Settings` imported alongside `Client` from `zeep`.

Tests (all in `tests/test_mni_client.py`):
- `TestForbidExternalSettings::test_hardened_tribunal_gets_forbid_external_true` — mocked
  `zeep.Client`, constructs `MNIClient(tribunal="TJES", ...)`, asserts the `settings` kwarg's
  `forbid_external` is `True`.
- `TestForbidExternalSettings::test_unhardened_tribunal_gets_forbid_external_false` — same,
  `tribunal="TJBA"`, asserts `False` — the negative case proving this is scoped, not global.
- `TestForbidExternalSSRFFixture::test_forbid_external_blocks_the_fetch_before_any_network_attempt`
  — re-runs Task 1's fixture with `Settings(forbid_external=True)`: asserts
  `zeep.exceptions.ExternalReferenceForbidden` is raised **and** the mocked `session.get` was
  never called (`mock_get.assert_not_called()`) — the load-bearing assertion, empirically
  confirmed against the real `zeep==4.3.3`/lxml resolver (see Design section's correction).

### Task 3 — Confirm no regression on existing MNI client tests — ✅ DONE

Full `tests/test_mni_client.py` run: 69 passed (73 with the 4 new tests), the same 2
pre-existing failures as on `master` (`test_propagates_oserror`,
`test_audit_called_on_disk_error` — both fail because this sandbox runs as root, so `chmod
0o444` doesn't block a root write; confirmed by reproducing them against unmodified `master`
via `git stash`). No `assert_called_with(wsdl=...)`-style assertion existed to break. Full repo
suite (`pytest tests/ -q`, redis reachable): 592 passed, same 2 pre-existing failures, 594
total (was 590 per CLAUDE.md; +4 from this spec's new tests). `ruff check`/`format --check`
clean on the pinned `0.14.14`.

### Task 4 — Live verification against TJES, post-deploy

**Not a code task — manual, from `pje-vps`.** After Task 2 merges and deploys, exercise the
real MNI health check — `worker.py`'s `/health` handler (`checks["mni"]`, which calls
`mni_client.health_check()` and round-trips through `_get_client()`) — against `TJES` and
confirm it still reports healthy, i.e. no `ExternalReferenceForbidden`/`TransportError`. This is
the production-fidelity check that Task 1's static `grep` measurement cannot fully replace (a
`schemaLocation` could theoretically appear in a nested, dynamically-referenced schema that a
flat `grep` on the top-level WSDL body misses). No live check is needed for the other 5
tribunals — their code path is unchanged by this spec.

### Task 5 — Documentation

Update CLAUDE.md backlog item 5: mark the TJES-only hardening done, record the exception class
pinned in Task 2, and note the other 5 tribunals as explicit deferred follow-up (with the
one-line "Future expansion" recipe from this spec). Update
`docs/reports/2026-09-20-full-analysis.md`'s residual follow-ups if this item is referenced
there.

---

## Risks the implementation must not get wrong

| Risk | Mitigation |
|---|---|
| `Settings(forbid_external=False)` isn't actually a no-op vs. no `settings` argument at all | ✅ Confirmed by Task 3: existing `tribunal="TJBA"`-style tests pass unchanged. |
| Task 1's fixture test is mocked away and proves nothing | ✅ Real `zeep.Client`/lxml resolver used, unmocked; only `requests.Session.get` (the actual network call) is a sentinel. |
| The SSRF fixture target is a real, answering cloud-metadata IP on the CI runner's provider (e.g. Azure IMDS) | ✅ Used RFC 5737 `192.0.2.1` (TEST-NET-1) instead of `169.254.169.254`, and the network call itself is mocked, so this is moot regardless of CI provider. |
| `forbid_external` breaks TJES WSDL loading in a way only visible in production, not in Task 2's mocked unit tests | Task 4 (pending) is a mandatory live post-deploy check against TJES, not optional. |
| Casing mismatch between `self.tribunal` and `MNI_FORBID_EXTERNAL_TRIBUNALS` | Both sides are `.upper()`d — `self.tribunal` already in `__init__` (`mni_client.py:161`), the config constant's comprehension explicitly too. |
| Confusing this flag with authentication/authorization hardening | Non-goals table states plainly this is transport-layer SSRF defense only; it does not touch credentials, the allowlist, or response validation. |
| Someone later "helpfully" expands `MNI_FORBID_EXTERNAL_TRIBUNALS`'s default without measuring that tribunal first | "Future expansion" section states the measurement step explicitly; Task 5's CLAUDE.md update records it as a prerequisite, not just a suggestion. |

---

## USER VALIDATION GATE

1. **Scope — ANSWERED 2026-09-25.** TJES only, for now. The other 5 tribunals are deferred
   (see "Future expansion"); no VPS measurement is required to start Task 1.
2. **Fallback if a tribunal fails measurement later** — moot for this spec's Tasks, since only
   TJES (already measured clean) is in scope. Applies only when someone picks up the deferred
   expansion work.
3. **Scope of Task 4's live check** — now just TJES's existing `/health` MNI check
   (`worker.py`, `checks["mni"]` → `mni_client.health_check()`), not a new endpoint and not all
   6 tribunals. Confirm this is acceptable, or say if a different verification path is
   preferred.

Implementation (Task 1 onward) can start once Felipe confirms question 3 — or explicitly says
to proceed with the stated default.

---

## Referências / References

- CLAUDE.md, Backlog item 5 (zeep SSRF hardening) — prior blocker history and the 2026-07-25
  TJES measurement this spec builds on.
- CLAUDE.md, Known Issues — CloudFront geo-restriction finding (why this sandbox can't measure
  the other 5 tribunals itself; not a blocker for this spec's now-narrower scope).
- CLAUDE.md, Sprint 13 (Q4) — the env-configurable-constants convention this spec's
  `MNI_FORBID_EXTERNAL_TRIBUNALS` follows.
- `mni_client.py:45-51` (`TRIBUNAL_ENDPOINTS`), `:157-178` (`__init__`, `.upper()` at `:161`),
  `:181-210` (`_get_client`).
- `worker.py`, `checks["mni"]` in the `/health` handler — the real MNI health check Task 4
  reuses (there is no separate `/api/mni/health` route).
- `tests/test_mni_client.py:526-552` — existing `zeep.Client`/`Transport` mocking idiom.
- Dependabot alert #1 / GHSA-4cc2-g9w2-fhf6 — the zeep CVE this is defense-in-depth for
  (already mitigated by the `zeep==4.3.3` bump, commit `23d5e8a`).
- `docs/specs/2026-07-18-worker-publish-error-containment.md` — sibling spec this one follows
  in structure (research → design → gated tasks → risks → USER VALIDATION GATE).
- Skills governing execution: `superpowers:writing-plans`,
  `superpowers:subagent-driven-development`, `superpowers:test-driven-development`, and the
  `plan-quality-gate` review before execution.
