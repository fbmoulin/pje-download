# SPEC — zeep SSRF hardening: `Settings(forbid_external=True)` in the MNI client

**Status:** draft, awaiting final user validation (scope narrowed 2026-09-25)
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
`zeep.exceptions.TransportError` wrapping lxml's `XMLSyntaxError` / `ExternalReferenceForbidden`
at parse time — i.e., at `MNIClient._get_client()` time, not mid-SOAP-call. This matters for
Task 2's regression test: the failure mode to prove is "client construction fails loudly," not
a silent SSRF fetch.

---

## Tasks

Executed with **TDD** and **frequent commits** — one commit per task, tests written before
implementation, suite green before moving on. Per `superpowers:writing-plans`, each task is
independently verifiable; execution follows `superpowers:subagent-driven-development`.

### Task 1 — Prove the corpus can detect the SSRF window (RED)

Before touching `mni_client.py`, add a **deterministic, offline** regression test that does not
depend on live tribunal WSDLs: build a small local fixture WSDL/XSD
(`tests/fixtures/wsdl_external_schema_location.wsdl`) containing one `xsd:import` with a
`schemaLocation` pointing at an unreachable external host
(e.g. `http://169.254.169.254/malicious.xsd`, the classic SSRF cloud-metadata target — chosen
deliberately as the canonical example, never actually reachable in CI). Load it through real
`zeep.Client` (not mocked — this must exercise the real lxml resolver) with **no**
`forbid_external` setting and assert it attempts the external fetch (raises a connection error
to the bogus host, proving the fetch was attempted — i.e., today's code is exploitable in
principle). This test documents the vulnerability window and must be written and shown
failing/behaving-as-vulnerable against current `master` before Task 2.

⚠️ If CI runs on Azure-hosted runners (GitHub Actions' default), `169.254.169.254` is Azure's
real IMDS endpoint and may return an actual HTTP response instead of a connection error —
confirm the CI runner's cloud provider first; if it's Azure (or any provider serving that IP),
use a different unreachable target instead (e.g. an address in `TEST-NET-1`, `192.0.2.0/24`,
reserved by RFC 5737 for documentation and never routable).

Harness: follow the existing zeep-mocking idiom in `tests/test_mni_client.py:526-531`
(`patch("zeep.Client", ...)`, `patch("zeep.transports.Transport", ...)`) for the *unit* tests in
Task 2, but this Task 1 fixture test is intentionally **not** mocked at the zeep layer — mocking
away lxml's resolver would make it incapable of proving anything.

### Task 2 — Implement per-tribunal `Settings(forbid_external=...)` (GREEN)

Add the `MNI_FORBID_EXTERNAL_TRIBUNALS` constant to `config.py` and the `settings=` kwarg to
the `Client(...)` call in `mni_client.py:_get_client` (currently `mni_client.py:206-209`), per
the Design section.

Tests:
- Unit test (mocked, extending the pattern at `tests/test_mni_client.py:526-531`): construct an
  `MNIClient(tribunal="TJES", ...)` and assert `zeep.Client` is called with a `settings` kwarg
  whose `forbid_external` is `True`.
- Companion test: construct an `MNIClient(tribunal="TJBA", ...)` (or any tribunal not in the
  default set) and assert `forbid_external` is `False` — the negative case that proves this is
  actually scoped, not accidentally global.
- Re-run Task 1's fixture test with the new code path, using a tribunal in
  `MNI_FORBID_EXTERNAL_TRIBUNALS`: assert it now raises `zeep.exceptions.TransportError` /
  lxml's `XMLSyntaxError` (whichever `forbid_external` surfaces as, per the installed zeep/lxml
  versions — pin the exact exception class found during implementation) **before** any network
  attempt reaches the bogus host. This is the load-bearing assertion — the SSRF window from
  Task 1 must now be closed for TJES.

### Task 3 — Confirm no regression on existing MNI client tests

Run the full `test_mni_client.py` suite. The existing `Client(...)` mocks
(`tests/test_mni_client.py:526-531` and similar) construct `MagicMock` return values, so they
are expected to keep passing untouched — but confirm explicitly, since a `settings=` kwarg
added to a call under test can silently break a mock's `assert_called_with` if one exists. Grep
for any existing `assert_called_with(wsdl=...)`-style assertion that would need updating to
include `settings=`. Also confirm the other 5 tribunals' existing tests (constructed with
`tribunal="TJBA"` etc.) show zero behavior change.

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
| `Settings(forbid_external=False)` isn't actually a no-op vs. no `settings` argument at all | Task 3 explicitly asserts zero behavior change for a non-hardened tribunal's existing tests. |
| Task 1's fixture test is mocked away and proves nothing | Task 1 explicitly uses the real `zeep.Client` / lxml resolver, unmocked, against a local fixture file. |
| The `169.254.169.254` fixture target is a real, answering cloud-metadata IP on the CI runner's provider (e.g. Azure) | Task 1 explicitly calls out checking the runner's provider and swapping to an RFC 5737 `TEST-NET` address if needed. |
| `forbid_external` breaks TJES WSDL loading in a way only visible in production, not in Task 2's mocked unit tests | Task 4 is a mandatory live post-deploy check against TJES, not optional. |
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
