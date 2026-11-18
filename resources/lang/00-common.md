# 00 — Common Differential, 1-Hop Ripple & Schema-Hash Protocol

Used with `resources/lang/<lang>.md`. Resolve `<VPR>` as `<skill_dir>/scripts/vpr.py`. Execute commands from `ROOT`.

## 1. CLI Workflow (`scripts/vpr.py`)
`vpr.py` sets `VERIPR_TARGET_ROOT` (`S/tmp/worktree-base` in `base`, `S/tmp/worktree-head` in `head`, `S/sandbox-fix` in `fix`), `VERIPR_PHASE=base|head|fix`, prepends `VERIPR_TARGET_ROOT` to `PYTHONPATH`, forces `PYTHONDONTWRITEBYTECODE=1`, and isolates caches inside `.bughunt/.cache/`:
- `python "<VPR>" init [--root .] [--base REF] [--head REF|WORKTREE]` -> initializes `S/{tests,logs,tmp,patches}`, captures `fs-before.txt` and `worklog.md`, prints JSON.
- `python "<VPR>" diff-scope [--root .] [--s "$S"]` -> parses Git diff + AST/brace symbols for `RETURN_SHAPE_CHANGED`, `CONTRACT_KEY_RENAMED_OR_DROPPED`, `SIGNATURE_CHANGED`, `ERROR_GUARD_REMOVED` (`<=25` lines).
- `python "<VPR>" impact-map [--root .] [--s "$S"] [--max-lines 25]` -> scans repo (ignoring comments/docstrings) for 1-hop callers + test coverage (`<=25` lines).
- `python "<VPR>" ab-verify --s "$S" --id <ID> --category <CAT> -- <cmd...>` -> executes identical check in `worktree-base` vs `worktree-head`, computes recursive API `schema_hash`, separates `GATE1_RIPPLE_CONFIRMED` / `GATE1_CONTRACT_DRIFT` / `GATE1_NEW_REGRESSION` from `PRE_EXISTING_FAILURE` (`<=10`-line digest).
- `python "<VPR>" verify-fix --s "$S" --id <ID> [--auto-rollback] [--test-cmd "..."] [--caller-cmd "..."] [--feature-cmd "..."]` -> Gate 2: confirms defect fixed + schema compatible + callers & non-legacy suite pass + new PR feature preserved, exports `S/patches/<id>.patch` and `S/patches/veripr-fixes.patch`, and auto-rolls back on `REJECTED_FEATURE_BROKEN` / `REJECTED_CALLER_REGRESSION` / `REJECTED_INCOMPLETE_FIX`.
- `python "<VPR>" sandbox --s "$S" --mode init-worktrees|fix|save|rollback|diff|clean [--id <ID>]` -> manages worktrees, `sandbox-fix`, checkpoints, unified diff, and `git worktree remove/prune` + cleanliness audit.
- `python "<VPR>" log --s "$S" [--status "..."] [--entry "..."]` -> atomic `worklog.md` update.
- `python "<VPR>" summary --s "$S" [--snippets]` -> compact Markdown tables and Base/Head/Fix snippets.

## 2. Two Supported Check & Probe Formats
### Mode A — Standard Test Runner (`unittest` / `pytest` / `node:test` / `jest` / `go test`)
- Point imports dynamically at `VERIPR_TARGET_ROOT` so the same check runs against `worktree-base`, `worktree-head`, and `sandbox-fix`.
- Invoke the 1-hop caller directly with inputs covering the modified edge behavior.

### Mode B — `VERIPR_PAYLOAD:` / `VERIPR_CHECK:` Schema Probe
Print one single-line JSON marker for any API response or DTO to verify structural parity (`schema_hash`):
```text
VERIPR_PAYLOAD:{"id":"pr2","endpoint":"POST /checkout","payload":{"order_id":"ORD-1","total_cents":9000,"discount_cents":1000}}
```
- `vpr.py` converts all nested dicts/lists/scalars into canonical structure/type signatures (ignoring dynamic values of the same type like order timestamps or UUIDs) and fails Gate 1 (`GATE1_CONTRACT_DRIFT`) whenever keys are removed/renamed or types change incompatibly (e.g., `number -> null`).

## 3. Rules to Prevent False Fixes
1. **Never revert the developer's new PR feature** just to silence a ripple failure — `--feature-cmd` catches this and rolls back via `REJECTED_FEATURE_BROKEN`.
2. **Never drop `PRE_EXISTING_FAILURE` onto the developer's plate** — verify Base vs Head first via `ab-verify`; once logged as `PRE_EXISTING_FAILURE`, `verify-fix --test-cmd` ignores that exact legacy failure automatically.
