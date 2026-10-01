# Phase 2 Sprint 1 — SSRF Hardening Expansion to All Tribunals

**Goal:** Expand zeep `forbid_external` hardening from TJES (already done in #49) to remaining 5 tribunals (TJES_2G, TJBA, TJBA_2G, TJCE, TRT17). Measurement-only work — no code changes.

**Architecture:** 
- TJES already hardened via PR #49 (`forbid_external=True` confirmed safe post-deploy)
- Other 5 tribunals: audit their WSDL schemaLocations, then enable hardening via config
- Per-tribunal gating implemented in `mni_client.py:_get_client()` (PR #49)
- Risk: zero — each tribunal measured independently before hardening

**Tech Stack:**
- `zeep==4.3.3` (already pinned, has `forbid_external` support)
- `config.py:MNI_FORBID_EXTERNAL_TRIBUNALS` — currently `{"TJES"}`, env-configurable
- Measurement: curl + grep (no code dependencies)

**Scope:** 5 independent measurement tasks (one per tribunal) + 1 config update + rollout runbook

---

## USER VALIDATION GATE

**This spec proposes:**
1. ✅ Measure each of 5 remaining tribunals (TJES_2G, TJBA, TJBA_2G, TJCE, TRT17)
2. ✅ Update `config.py` default to include all measured tribunals
3. ✅ Deploy runbook + monitoring (Prometheus alert for ExternalReferenceForbidden rate)

**No code changes.** All 5 tribunals have zero external schemaLocations (audit confirms — see Task notes below).

**Approval required before proceeding to Task execution.**

---

## Tasks (Parallelism: All 5 measurement tasks can run in batch)

### Task 1.1 — Measure & Approve TJES_2G

**Objective:** Confirm TJES_2G WSDL has zero external schemaLocations; update config to enable forbid_external.

**Files:**
- Read: `mni_client.py:47` (WSDL endpoint)
- Modify: `config.py:130-134` (add TJES_2G to set)
- Test: `tests/test_mni_client.py::TestForbidExternalSettings` (add TJES_2G case)

**Steps (TDD):**

1. **Write failing test:**
   ```python
   def test_forbid_external_tjes_2g(self):
       """TJES_2G tribunal uses forbid_external=True."""
       with patch("mni_client._get_client") as mock_get:
           client = _make_client("TJES_2G")
           # Should pass forbid_external=True
           mock_get.assert_called_with(..., forbid_external=True)
   ```

2. **Run test → FAIL** (TJES_2G not in set yet)

3. **Implement:** Update `config.py`:
   ```python
   MNI_FORBID_EXTERNAL_TRIBUNALS = {"TJES", "TJES_2G"}  # after audit
   ```

4. **Run test → PASS**

5. **Commit:** `feat: enable forbid_external for TJES_2G`

**Verification command:**
```bash
curl -s https://pje.tjes.jus.br/pje2g/intercomunicacao?wsdl | grep -c 'schemaLocation=' | grep -q '^0$' && echo "SAFE" || echo "UNSAFE"
```

**Notes:**
- Measurement already completed by audit agent: TJES_2G = zero external schemaLocations ✅
- No timeout/retry logic needed (forbid_external fires before network)

---

### Task 1.2 — Measure & Approve TJBA

**Objective:** Confirm TJBA WSDL has zero external schemaLocations; update config.

**Files:**
- Read: `mni_client.py:48` (WSDL endpoint)
- Modify: `config.py:130-134` (add TJBA to set)
- Test: `tests/test_mni_client.py::TestForbidExternalSettings` (add TJBA case)

**Steps (TDD):**
1. Write test: `test_forbid_external_tjba`
2. Run → FAIL
3. Update config.py
4. Run → PASS
5. Commit: `feat: enable forbid_external for TJBA`

**Verification command:**
```bash
curl -s https://pje.tjba.jus.br/pje/intercomunicacao?wsdl | grep -c 'schemaLocation=' | grep -q '^0$' && echo "SAFE" || echo "UNSAFE"
```

---

### Task 1.3 — Measure & Approve TJBA_2G

**Objective:** Confirm TJBA_2G WSDL has zero external schemaLocations; update config.

**Files:**
- Read: `mni_client.py:49`
- Modify: `config.py:130-134` (add TJBA_2G to set)
- Test: `tests/test_mni_client.py::TestForbidExternalSettings` (add TJBA_2G case)

**Steps (TDD):**
1. Write test: `test_forbid_external_tjba_2g`
2. Run → FAIL
3. Update config.py
4. Run → PASS
5. Commit: `feat: enable forbid_external for TJBA_2G`

**Verification command:**
```bash
curl -s https://pje.tjba.jus.br/pje2g/intercomunicacao?wsdl | grep -c 'schemaLocation=' | grep -q '^0$' && echo "SAFE" || echo "UNSAFE"
```

---

### Task 1.4 — Measure & Approve TJCE

**Objective:** Confirm TJCE WSDL has zero external schemaLocations; update config.

**Files:**
- Read: `mni_client.py:50`
- Modify: `config.py:130-134` (add TJCE to set)
- Test: `tests/test_mni_client.py::TestForbidExternalSettings` (add TJCE case)

**Steps (TDD):**
1. Write test: `test_forbid_external_tjce`
2. Run → FAIL
3. Update config.py
4. Run → PASS
5. Commit: `feat: enable forbid_external for TJCE`

**Verification command:**
```bash
curl -s https://pje.tjce.jus.br/pje1grau/intercomunicacao?wsdl | grep -c 'schemaLocation=' | grep -q '^0$' && echo "SAFE" || echo "UNSAFE"
```

---

### Task 1.5 — Measure & Approve TRT17

**Objective:** Confirm TRT17 WSDL has zero external schemaLocations; update config.

**Files:**
- Read: `mni_client.py:51`
- Modify: `config.py:130-134` (add TRT17 to set)
- Test: `tests/test_mni_client.py::TestForbidExternalSettings` (add TRT17 case)

**Steps (TDD):**
1. Write test: `test_forbid_external_trt17`
2. Run → FAIL
3. Update config.py
4. Run → PASS
5. Commit: `feat: enable forbid_external for TRT17`

**Verification command:**
```bash
curl -s https://pje.trt17.jus.br/pje/intercomunicacao?wsdl | grep -c 'schemaLocation=' | grep -q '^0$' && echo "SAFE" || echo "UNSAFE"
```

---

### Task 2.1 — Final Integration & Docs

**Objective:** Consolidate 5 measurement commits, update ENV docs, add deploy runbook.

**Files:**
- Modify: `CLAUDE.md` (add T2.1 completed section)
- Modify: `docs/deploy/RUNBOOK.md` (add forbid_external rollout section)
- Create: `docs/specs/2026-09-28-forbid-external-rollout.md` (deployment steps per tribunal)

**Steps:**
1. Squash-merge 5 measurement commits (if desired) or keep separate
2. Update CLAUDE.md: `MNI_FORBID_EXTERNAL_TRIBUNALS = all 6 tribunals`
3. Write rollout runbook:
   - Prerequisites (zeep==4.3.3 pinned, confirm TJES prod already green)
   - Rollout order (measure first, then deploy one tribunal at a time, monitor 24h each)
   - Alert: `rate(zeep_external_reference_forbidden_total[5m])` > 0 → page on-call
4. Commit: `docs: T2.1 complete — forbid_external enabled for all 6 tribunals`

---

## Parallelism Allowed

✅ **Tasks 1.1 → 1.5** (5 measurement tasks) can run in **batch via subagent-driven-development**, since:
- Each task modifies only `config.py` (concurrent edits safe via sequential merge)
- Tests are independent (no shared fixtures)
- Verification is independent (each WSDL measured from BR-IP)

✅ **Task 2.1** depends on completion of 1.1–1.5 (sequential, final integration only)

---

## Testing & Verification

**Unit tests:** 5 new cases in `TestForbidExternalSettings` (one per tribunal)

**Integration:** 
- Run `pytest tests/test_mni_client.py::TestForbidExternalSettings -v` after each commit
- Verify config loads: `python -c "from config import MNI_FORBID_EXTERNAL_TRIBUNALS; print(MNI_FORBID_EXTERNAL_TRIBUNALS)"`

**Live verification (post-deploy, 2026-10-xx):**
```bash
# From pje-vps
for tribunal in TJES TJES_2G TJBA TJBA_2G TJCE TRT17; do
  echo "$tribunal: $(curl -s ${WSDL_URL[$tribunal]} | grep -c 'schemaLocation=')"
done
```

---

## References

- **#49** (2026-09-26): Initial SSRF hardening for TJES, per-tribunal gating implemented
- **Agent audit** (2026-09-28): Confirmed all 5 remaining tribunals have zero external schemaLocations
- **zeep docs**: `forbid_external` behavior fires before network (safe-by-default)
- **CLAUDE.md § Backlog Item 5**: zeep SSRF expansion details

---

**Status:** Implemented; approved at the USER VALIDATION GATE. See "Process and corrections" below.

**Effort:** ~2–3 hours (5 × 20-min measurement tasks, 1 × 15-min integration, parallel batch execution)

**Test count delta:** originally planned +5 cases; as built, one parametrized test over all six tribunals plus one gating test (see below).

**Risk:** Minimal (config-only, per-tribunal gating, zero external refs confirmed)

---

## Process and corrections (added at PR time)

- **Skills:** the `writing-plans` and `plan-quality-gate` skills were not
  available in the sessions that produced this spec, so neither was run.
  Structure is checked by `tools/verify_spec.py`, the CI gate. Execution used
  `subagent-driven-development`-style parallel agents for the per-tribunal
  measurements; commits were kept small and frequent, one per tribunal.
- **"No code changes" was inaccurate:** the default in `config.py`
  (`MNI_FORBID_EXTERNAL_TRIBUNALS`) changed from `TJES` to all six. That is the
  whole behavioural change, and it is reversible without a deploy by setting
  the env var back to `TJES`.
- **Tests:** the per-tribunal cases planned above were never written, and the
  existing `test_unhardened_tribunal_gets_forbid_external_false` asserted TJBA
  is *not* hardened, so it failed once the default changed. Fixed at PR time:
  `test_every_supported_tribunal_gets_forbid_external_true` is parametrized over
  the six tribunals (verified to fail for the five new ones if the set is
  reduced to `TJES`), and the gating mechanism is kept covered by patching the
  set to exclude one tribunal.
- **Measurement not re-verified:** the "zero external `schemaLocation`" result
  for each WSDL was gathered by agents on 2026-09-28 and not reproduced when the
  PR was opened. The PJe hosts geo-restrict non-BR IPs, so it cannot be re-run
  from a cloud session. Re-run the verification commands above from `pje-vps`
  before or right after deploy.
