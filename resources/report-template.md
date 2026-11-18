# VeriPR Pre-PR Impact Report Template (English — Lazy-Load in Phase 4 Only)

> **Instructions:** Copy below the divider to `S/pr-report.md`. Complete all 7 sections; write `"None"` if empty. **NEVER delete sections.** Use exact measured values from `vpr.py summary --snippets` and exported patches from `S/patches/`.

---

# VeriPR Pre-Commit & PR Differential Impact Report

## Session Metadata
- **Session ID (`S`):** `.bughunt/<SNAME>/`
- **Project Root (`ROOT`):** `<path>`
- **Compared Refs:** `Base: <base_ref>` vs. `Head: <head_ref>`
- **Dual-Gate Protocol:** Gate 1 Differential Worktree Check (`ab-verify`) | Gate 2 Zero-Ripple Fix + Feature Preservation (`verify-fix`)
- **PR Verdict:** <READY_TO_MERGE / BLOCKED_PENDING_FIX_APPLICATION>

---

## 1. Executive Summary & Dual-Gate Metrics

| Metric | Value | Notes |
|---|---:|---|
| Changed Symbols (`diff-scope`) | <N> | AST / semantic symbol & contract scan |
| 1-Hop Callers Mapped (`impact-map`) | <N> | Capped `<=25` lines; prioritizes untested callers |
| **Confirmed Ripple / Contract Defects** | **<N>** | Gate 1 (`GATE1_RIPPLE_CONFIRMED` / `GATE1_CONTRACT_DRIFT`) |
| **Zero-Ripple Fixes Verified & Exported** | **<N>** | Gate 2 `CONFIRMED_ZERO_RIPPLE_FIX` (Callers + Feature `PASS`) |
| **Filtered Pre-Existing Failures** | **<N>** | `PRE_EXISTING_FAILURE` (Failed on both Base & Head; non-blocking) |
| **Auto-Rolled-Back Naive Fixes** | **<N>** | Rejected via `REJECTED_FEATURE_BROKEN` / `REJECTED_CALLER_REGRESSION` |

---

## 2. Diff-Scope & 1-Hop Caller Impact Matrix

| Target Symbol | File:Line | Semantic Risk Tags | 1-Hop Callers | Tested? | Priority |
|---|---|---|---|---|---|
| `<symbol>` | `<file:line>` | `<risk_tags>` | `<caller (file:line)>` | `<YES/NO>` | `<HIGH/MED>` |

---

## 3. Confirmed Ripple / Contract Defects & Zero-Ripple Fixes (Gate 1 + Gate 2)

<!-- Repeat per CONFIRMED_ZERO_RIPPLE_FIX defect -->

### <ID>: <Short Defect Title> (`<VERDICT>`)
- **Affected Target / Caller:** [`<relative/path:line>`](<relative/path>) `<symbol>`
- **Root Cause & Ripple Mechanism:** <Why the PR edit broke a 1-hop caller or API contract while local tests passed>
- **Dual-Gate Proof Summary:**
  - **Gate 1 (Differential Base vs. Head):** Base = `<PASS>` | Head = `<FAIL / CONTRACT_DRIFT>` (`schema_hash`: `<base_hash>` vs. `<head_hash>`)
  - **Gate 2 (Zero-Ripple Fix in `S/sandbox-fix/`):** Target = `PASS` | 1-Hop Callers = `PASS` | Project Suite = `<PASS / PASS_FILTERED_PREEXISTING>` | New PR Feature Preserved = `PASS`
- **Verification Script:** [`<S>/tests/<file>`](<S>/tests/<file>)
- **Exported Incremental Patch:** [`<S>/patches/<id>.patch`](<S>/patches/<id>.patch)

#### Compact Evidence Snippet (Base vs. Head vs. Fix)
```text
<paste compact <=6-line snippet from vpr.py summary --snippets>
```

#### Incremental Unified Patch (Relative to Head)
```diff
<paste diff from S/patches/<id>.patch>
```

---

## 4. Filtered Pre-Existing Failures & Auto-Rolled-Back Fix Attempts

> VeriPR never blames the current PR for legacy broken tests on Base (`PRE_EXISTING_FAILURE`), and automatically rolls back fixes that break the developer's new PR feature (`REJECTED_FEATURE_BROKEN`).

| ID | Outcome | Base vs. Head / Rollback | Details / Rationale |
|---|---|---|---|
| <ID or None> | <PRE_EXISTING_FAILURE / REJECTED_FEATURE_BROKEN / REJECTED_CALLER_REGRESSION> | <Base=FAIL,Head=FAIL / AUTO_RESTORED> | <Details> |

---

## 5. Exported Incremental Patch Summary
- **Per-Finding Patches:** `<S>/patches/<id>.patch`
- **Combined Cumulative Patch (Apply on Head):** `<S>/patches/veripr-fixes.patch`
- **Apply Command:** `git apply <S>/patches/veripr-fixes.patch`

---

## 6. Workspace Cleanliness & Worktree Pruning Log
- **Snapshot Files:** `<S>/logs/fs-before.txt`, `<S>/logs/fs-after.txt`
- **Cleanliness Verdict:** `<cleanliness: CLEAN (0 new/modified files outside .bughunt/)>`
- **Deliverables Retained:** `<S>/pr-report.md`, `<S>/worklog.md`, `<S>/tests/`, `<S>/patches/`, `<S>/logs/`

---

## 7. Applied Patches Log
> Updated only after explicit user approval to apply exported `.patch` files to the host repository.

| Timestamp | Patch File | Host Verification Suite | Status |
|---|---|---|---|
| None | - | - | Pending User Approval |
