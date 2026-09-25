# SPEC — zeep SSRF hardening: `Settings(forbid_external=True)` in the MNI client

**Status:** draft, awaiting user validation
**Author:** sessão 2026-09-25
**Target:** `mni_client.py` (`_get_client`, `TRIBUNAL_ENDPOINTS`)
**Research:** CLAUDE.md backlog item 5; live WSDL measurement of TJES (2026-07-25, recorded
in CLAUDE.md); this spec's own measurement plan for the remaining 5 tribunals.

---

## Goal

Pass `zeep.Settings(forbid_external=True)` into the `zeep.Client(...)` construction in
`mni_client.py:_get_client`, so a maliciously crafted or compromised WSDL/XSD response can no
longer make the MNI client fetch an attacker-controlled URL (SSRF via `xsd:import`/
`xsd:include` `schemaLocation`) — **without** breaking WSDL loading for any of the 6
configured tribunals.

### Non-goals (explicitly out of scope)

| Out of scope | Why |
|---|---|
| Bumping `zeep` itself | Already done (`23d5e8a`, `zeep==4.3.3`), closes Dependabot alert #1 / GHSA-4cc2-g9w2-fhf6. This spec is the *remaining* defense-in-depth step, not a dependency bump. |
| Changing the WSDL allowlist | `TRIBUNAL_ENDPOINTS` is already a hardcoded `.jus.br` allowlist (`mni_client.py:45-51`); this spec does not touch it. `forbid_external` defends against what happens *after* a legitimate WSDL fetch — a malicious `schemaLocation` embedded inside an otherwise-trusted response (compromised tribunal server, MITM without TLS pinning, or a future tribunal added to the allowlist without review). |
| Validating XML signatures / response bodies | Separate control; not part of `forbid_external`. |
| Any tribunal not currently in `TRIBUNAL_ENDPOINTS` | Adding a 7th tribunal is out of scope; this spec's Task 1 gate applies only to the 6 endpoints that exist today. |

---

## Why this was blocked, and why it no longer is

CLAUDE.md backlog item 5 previously said this was blocked by "hoje bloqueado por IP cloud" —
refuted 2026-07-18 (the real MNI blocker was AWS CloudFront geo-restriction on the VPS's
outbound IP, unrelated to `forbid_external`, resolved by moving the VPS to the São Paulo
datacenter). Once that was cleared, a second, real prerequisite emerged: **`forbid_external`
is an all-or-nothing flag on the zeep client, and flipping it blind risks a hard failure
(`ExternalReferenceForbidden`) on any tribunal whose WSDL legitimately references an external
schema via `schemaLocation`.** Measuring that is the actual remaining blocker.

One of six tribunals has been measured, live, from a BR-region IP (2026-07-25):

```
curl https://pje.tjes.jus.br/pje/intercomunicacao?wsdl  → HTTP 200, 34.5 KB
grep -c 'xs:import\|xs:include'   → 5
grep -c 'schemaLocation='          → 0
```

Five `xsd:import`/`xsd:include` elements, **zero** carrying a `schemaLocation` attribute — the
imports are namespace-only declarations. Zeep/lxml only performs a network fetch for an
import that has a `schemaLocation` URL; a namespace-only import never triggers one. TJES is
therefore confirmed safe for `forbid_external=True`.

**The other five tribunals are unmeasured**, and this repo's own postmortem history
(`Known Issues`, backlog item 5's own prior wording) shows guessing about tribunal WSDL
behaviour from one data point has already been wrong once. This spec does not repeat that.

### This session cannot take the measurement itself

This spec is being written from a Claude Code Remote cloud sandbox, not the VPS. PJe/TJES (and
presumably its sibling tribunals — same CloudFront pattern is the working hypothesis, not yet
confirmed per-tribunal) sits behind AWS CloudFront with country-level geo-restriction: a
non-BR egress IP gets `403` (`POP BOS50`), a BR IP gets `200` (`POP GRU3`). This sandbox's
egress is not BR. **Task 1 below must run from `pje-vps` (or another BR-IP host)**, not from
wherever this spec gets implemented.

---

## Design

**Task 1 gates Task 3.** The flag is applied globally in the single shared `_get_client`
method — there is no per-tribunal branching today, and adding one purely to carve out a
non-conforming tribunal would be a strictly worse outcome than knowing in advance. So:

- If all 6 tribunals measure `schemaLocation` count `0`, apply
  `Settings(forbid_external=True)` unconditionally in `_get_client`. This is the expected
  and default path.
- If any tribunal has a non-zero `schemaLocation` count, **stop and report back** before
  writing Task 3's implementation — do not silently exclude that tribunal or silently leave
  the flag off. This is a new decision point requiring the user's call (drop that tribunal
  from `forbid_external` coverage with a per-tribunal `Settings`, or accept breaking it, or
  abandon this spec). Recorded explicitly so a future session does not skip the gate.

Implementation shape (contingent on a clean Task 1):

```python
from zeep import Client, Settings
...
settings = Settings(forbid_external=True)
self._client = Client(wsdl=self.wsdl_url, transport=transport, settings=settings)
```

`forbid_external=True` blocks zeep/lxml from dereferencing any `schemaLocation` (or WSDL
`import`/`include`) that points outside the document's own origin, raising
`zeep.exceptions.TransportError` wrapping lxml's `XMLSyntaxError` /
`ExternalReferenceForbidden` at parse time — i.e., at `MNIClient._get_client()` time, not
mid-SOAP-call. This matters for Task 2's regression test: the failure mode to prove is "client
construction fails loudly," not a silent SSRF fetch.

---

## Tasks

Executed with **TDD** and **frequent commits** — one commit per task, tests written before
implementation where the task is code, suite green before moving on. Per
`superpowers:writing-plans`, each task is independently verifiable; execution follows
`superpowers:subagent-driven-development`.

### Task 1 — Measure `schemaLocation` on the remaining 5 tribunal WSDLs

**Not a code task — a manual measurement, run from `pje-vps` (BR IP) via SSH, before any
implementation.**

For each of `TJES_2G`, `TJBA`, `TJBA_2G`, `TJCE`, `TRT17` (`mni_client.py:46-51`):

```bash
ssh pje-vps 'curl -s "<wsdl_url>" | tee /tmp/wsdl_check.xml | wc -c'
ssh pje-vps 'grep -oc "xs:import\|xs:include" /tmp/wsdl_check.xml'
ssh pje-vps 'grep -oc "schemaLocation=" /tmp/wsdl_check.xml'
```

Record every result in this spec's "Research still required" table below (replacing this
task's own text with the filled table) before Task 3 starts. Zero `schemaLocation` hits across
all 6 → proceed as designed. Any nonzero hit → stop, do not proceed to Task 3, raise the
per-tribunal decision above with Felipe.

**Gate:** Task 3 (the actual code change) MUST NOT be written before this table is complete.

| Tribunal | WSDL URL | Measured? | `xs:import`/`include` count | `schemaLocation=` count |
|---|---|---|---|---|
| TJES | `pje.tjes.jus.br/pje/intercomunicacao?wsdl` | ✅ 2026-07-25 | 5 | 0 |
| TJES_2G | `pje.tjes.jus.br/pje2g/intercomunicacao?wsdl` | ❌ pending | — | — |
| TJBA | `pje.tjba.jus.br/pje/intercomunicacao?wsdl` | ❌ pending | — | — |
| TJBA_2G | `pje.tjba.jus.br/pje2g/intercomunicacao?wsdl` | ❌ pending | — | — |
| TJCE | `pje.tjce.jus.br/pje1grau/intercomunicacao?wsdl` | ❌ pending | — | — |
| TRT17 | `pje.trt17.jus.br/pje/intercomunicacao?wsdl` | ❌ pending | — | — |

### Task 2 — Prove the corpus can detect the SSRF window (RED)

Before touching `mni_client.py`, add a **deterministic, offline** regression test that does
not depend on live tribunal WSDLs: build a small local fixture WSDL/XSD
(`tests/fixtures/wsdl_external_schema_location.wsdl`) containing one `xsd:import` with a
`schemaLocation` pointing at an unreachable external host
(e.g. `http://169.254.169.254/malicious.xsd`, the classic SSRF cloud-metadata target — chosen
deliberately as the canonical example, never actually reachable in CI). Load it through real
`zeep.Client` (not mocked — this must exercise the real lxml resolver) with **no**
`forbid_external` setting and assert it attempts the external fetch (raises a connection
error to the bogus host, proving the fetch was attempted — i.e., today's code is exploitable
in principle). This test documents the vulnerability window and must be written and shown
failing/behaving-as-vulnerable against current `master` before Task 3.

Harness: follow the existing zeep-mocking idiom in `tests/test_mni_client.py:526-531`
(`patch("zeep.Client", ...)`, `patch("zeep.transports.Transport", ...)`) for the *unit* tests
in Task 3, but this Task 2 fixture test is intentionally **not** mocked at the zeep layer —
mocking away lxml's resolver would make it incapable of proving anything.

### Task 3 — Implement `Settings(forbid_external=True)` (GREEN)

Only after Task 1's table is fully green. Add `from zeep import Settings` and the `settings=`
kwarg to the `Client(...)` call at `mni_client.py:206-209`, per the Design section.

Tests:
- Unit test (mocked, extending the pattern at `tests/test_mni_client.py:526-531`): assert
  `zeep.Client` is called with a `settings` kwarg whose `forbid_external` is `True`.
- Re-run Task 2's fixture test with the new code path: assert it now raises
  `zeep.exceptions.TransportError` / lxml's `XMLSyntaxError` (whichever forbid_external
  surfaces as, per the installed zeep/lxml versions — pin the exact exception class found
  during implementation) **before** any network attempt reaches the bogus host, i.e. the
  external fetch itself must not happen. This is the load-bearing assertion — the SSRF window
  from Task 2 must now be closed.

### Task 4 — Confirm no regression on existing MNI client tests

Run the full `test_mni_client.py` suite. The existing `Client(...)` mocks
(`tests/test_mni_client.py:526-531` and similar) construct `MagicMock` return values, so they
are expected to keep passing untouched — but confirm explicitly, since a `settings=` kwarg
added to a call under test can silently break a mock's `assert_called_with` if one exists.
Grep for any existing `assert_called_with(wsdl=...)`-style assertion that would need updating
to include `settings=`.

### Task 5 — Live verification against all 6 tribunals, post-change

**Not a code task — manual, from `pje-vps`.** After Task 3 merges and deploys, run a real
`MNIClient()._get_client()` (or the existing `/api/mni/health` check, if it already exercises
`_get_client` — confirm which) against each of the 6 `TRIBUNAL_ENDPOINTS` values from the VPS
and confirm none raises `ExternalReferenceForbidden`/`TransportError`. This is the
production-fidelity check that Task 1's static `grep` measurement cannot fully replace (a
`schemaLocation` could theoretically appear in a nested, dynamically-referenced schema that a
flat `grep` on the top-level WSDL body misses).

### Task 6 — Documentation

Update CLAUDE.md backlog item 5: mark done, replace "Este item está desbloqueado" with the
completed state, record Task 1's full measurement table and the exception class pinned in
Task 3. Update `docs/reports/2026-09-20-full-analysis.md`'s residual follow-ups if this item
is referenced there.

---

## Risks the implementation must not get wrong

| Risk | Mitigation |
|---|---|
| Task 1 skipped or measured from a non-BR IP (gets `403`, looks like "WSDL unreachable" rather than a real measurement) | Task 1 explicitly requires running from `pje-vps`; a `403`/CloudFront block is not a valid "0 schemaLocation" result and must not be recorded as one. |
| Flag applied uniformly while one tribunal silently needed an external schema | Task 1 is a hard gate before Task 3; any nonzero count stops the plan for a user decision rather than a silent per-tribunal carve-out. |
| Task 2's fixture test is mocked away and proves nothing | Task 2 explicitly uses the real `zeep.Client` / lxml resolver, unmocked, against a local fixture file. |
| `forbid_external` breaks WSDL loading in a way only visible in production, not in Task 3's mocked unit tests | Task 5 is a mandatory live post-deploy check across all 6 tribunals, not optional. |
| Confusing this flag with authentication/authorization hardening | Non-goals table states plainly this is transport-layer SSRF defense only; it does not touch credentials, the allowlist, or response validation. |

---

## USER VALIDATION GATE

Do not begin Task 1 (or any later task) until Felipe confirms:

1. **Who runs Task 1's measurement, and when?** It requires SSH access to `pje-vps` — should
   this session do it now (if it has that access), a future session, or is this something
   Felipe wants to run himself and paste the results back?
2. **If Task 1 finds a nonzero `schemaLocation` on any tribunal** — confirmed default response
   is "stop and ask" (Design section). Is there a preferred fallback already in mind (e.g.
   drop `forbid_external` for that one tribunal via a per-instance `Settings`, vs. treating it
   as a hard blocker for the whole spec), or should that genuinely wait until it's known
   whether it happens at all?
3. **Scope of Task 5's live check** — is hitting all 6 tribunals' real WSDL endpoints from
   production acceptable for verification, or should it be probed some other way (e.g. via
   the existing `/api/mni/health` endpoint if that already round-trips through
   `_get_client`)?

---

## Referências / References

- CLAUDE.md, Backlog item 5 (zeep SSRF hardening) — prior blocker history and the 2026-07-25
  TJES measurement this spec builds on.
- CLAUDE.md, Known Issues — CloudFront geo-restriction finding (why this sandbox can't take
  Task 1's measurement itself).
- `mni_client.py:45-51` (`TRIBUNAL_ENDPOINTS`), `:164-179` (`__init__`), `:181-210`
  (`_get_client`).
- `tests/test_mni_client.py:526-552` — existing `zeep.Client`/`Transport` mocking idiom.
- Dependabot alert #1 / GHSA-4cc2-g9w2-fhf6 — the zeep CVE this is defense-in-depth for
  (already mitigated by the `zeep==4.3.3` bump, commit `23d5e8a`).
- `docs/specs/2026-07-18-worker-publish-error-containment.md` — sibling spec this one follows
  in structure (research → design → gated tasks → risks → USER VALIDATION GATE).
- Skills governing execution: `superpowers:writing-plans`,
  `superpowers:subagent-driven-development`, `superpowers:test-driven-development`, and the
  `plan-quality-gate` review before execution.
