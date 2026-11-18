# Contributing to VeriPR

Thank you for contributing to **VeriPR** (`veripr`)!

## Core Philosophy (Do Not Violate)

1. **Differential Truth Over Blame:** Gate 1 (`ab-verify`) must execute identically against isolated `Base` and `Head` worktrees so pre-existing branch debt (`PRE_EXISTING_FAILURE`) never blocks a clean PR.
2. **Zero-Ripple Fix & Feature Preservation:** Gate 2 (`verify-fix`) must confirm the ripple/contract defect is fixed, 1-hop callers pass, AND the developer's new PR feature (`--feature-cmd`) still works — auto-rolling back naive reverts via `REJECTED_FEATURE_BROKEN`.
3. **1-Hop Caller & Schema-Hash Visibility:** Keep `diff-scope` and `impact-map` compact (`<=25` lines) and detect silent API contract changes via recursive `schema_hash`.
4. **Zero External Dependencies:** `scripts/vpr.py` uses Python 3.9+ standard library only across Windows (PowerShell) and Linux/macOS (Bash).
5. **Deterministic Worktree Cleanup:** Always clean temporary Git worktrees (`git worktree remove --force` + `git worktree prune`) so `cleanliness: CLEAN` holds after every run.

## Verification Checklist
```bash
python -B scripts/vpr.py --help
python -B tests/test_vpr_cli.py
python -B examples/checkout_pr_demo/run_demo.py
```
