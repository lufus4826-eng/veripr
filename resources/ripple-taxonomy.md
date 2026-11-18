# VeriPR Ripple & Contract Drift Taxonomy

Load during **Phase 0–1**. Focus on bugs that pass unit tests in the edited file yet break 1-hop callers, frontend/API consumers, or existing flows.

---

## 1. `RETURN_NULL_OR_SHAPE_DRIFT` — Empty/`None` Return Breaking Caller Operations
- **Pattern:** A modified function returns `None` / `null` / `undefined` / wrong shape on an edge path (e.g., missing coupon, empty input) where Base previously returned `0.0`, `""`, `[]`, or `{}`.
- **Why Local Unit Tests Miss It:** The modified function's own unit test checks valid inputs or tests `assertIsNone(calculate_discount(100, None))`, passing 100%, while a 1-hop caller runs `subtotal - discount` or `res.strip()` and crashes with `TypeError` / `AttributeError` / `NullPointerException`.
- **Gate 1 Proof (`ab-verify`):** Call the 1-hop caller (`generate_receipt`, controller, handler) with the edge condition on `Base` (`PASS`) vs. `Head` (`FAIL: TypeError`) -> `GATE1_RIPPLE_CONFIRMED`.
- **Zero-Ripple Fix:** Restore safe default return contract (`0.0` / empty collection) without breaking any new PR feature added in the function.

---

## 2. `API_SCHEMA_CONTRACT_DRIFT` — Silent Key Rename, Drop, or Type Shift
- **Pattern:** Response builder, DTO, or serializer renames a JSON key (`"total_cents"` -> `"amount_cents"`), drops a field, or changes field types (`number` -> `null` or `string`), breaking frontend clients or downstream consumers silently.
- **Why Local Tests Miss It:** Unit tests often assert only `res is not None` or check the newly renamed key while omitting full schema parity.
- **Gate 1 Proof (`ab-verify`):** Emit `VERIPR_PAYLOAD:{"id":"pr2","payload":...}` in Base and Head -> `vpr.py` detects `base_schema_hash != head_schema_hash` (`missing_keys` / `renamed_candidates` / `type_changes`) -> `GATE1_CONTRACT_DRIFT`.
- **Zero-Ripple Fix:** Restore backward-compatible key(s)/type(s) (keep `"total_cents"`, optionally emit additive fields if strictly needed without removing Base keys) until `compare_schemas(base_schema, fix_schema)["breaking"] == False`.

---

## 3. `SIGNATURE_OR_DEFAULT_MISMATCH` — Parameter / Guard Change Breaking 1-Hop Callers
- **Pattern:** Function signature adds a required parameter without a default, reorders positional arguments, changes default value semantics, or removes an error-handling guard (`try/except`, `if err != nil`).
- **Why Local Tests Miss It:** Edited call sites inside the same PR were updated, while untouched 1-hop callers elsewhere in the repo (`impact-map`) still invoke the old signature.
- **Gate 1 Proof (`ab-verify`):** Invoke each untested 1-hop caller flagged by `impact-map` on `Base` (`PASS`) vs. `Head` (`FAIL: TypeError/ValueError`) -> `GATE1_RIPPLE_CONFIRMED`.
- **Zero-Ripple Fix:** Add sensible default values or preserve backward-compatible keyword/positional signature and validation guards while keeping new PR behavior intact.

---

## 4. `PRE_EXISTING_VS_NEW_REGRESSION` — Separating Legacy Main-Branch Debt from PR Bugs
- **Pattern:** Full project test run reports a failing legacy test (`test_legacy_tax_export.py`), tempting developers or tools to waste time debugging unrelated old debt or block a clean PR.
- **Gate 1 Proof (`ab-verify`):** Run the suspect suite on both `Base` and `Head` worktrees:
  - `Base=FAIL` AND `Head=FAIL` (same failing test(s)) -> **`PRE_EXISTING_FAILURE`** (`blocks_pr=false`). Automatically filtered out by `verify-fix --test-cmd`!
  - `Base=PASS` AND `Head=FAIL` -> **`GATE1_NEW_REGRESSION`** (`blocks_pr=true`). Must be fixed before merging.
