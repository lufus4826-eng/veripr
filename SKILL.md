---
name: veripr
description: Use before committing or opening a PR to catch 1-hop caller ripple effects, silent API schema/contract drift, and new regressions across Python, JS/TS, Go, Java/Kotlin, C#, PHP, or Rust in 1-2 minutes using Base vs Head Git worktree differential verification and Zero-Ripple Fix Gate checks. (ใช้ก่อน commit หรือส่ง PR เพื่อตรวจผลกระทบลูกโซ่ต่อไฟล์ข้างเคียงในระยะ 1 ขั้น จับสัญญาข้อมูล API เพี้ยนเงียบ และคัดกรองเทสต์เก่าที่พังค้างอยู่บนสาขาหลักออกด้วยด่านตัดสิน 2 ชั้น — pr check, pre-commit check, ripple effect, caller impact, contract drift, differential test, ตรวจผลกระทบลูกโซ่, ตรวจ PR ก่อนส่งโค้ด)
---

# VeriPR

Goal: Catch **1-hop caller ripple bugs**, **silent API contract drift**, and **new PR regressions** in **1–2 minutes** before commit/PR — without blaming the developer for pre-existing broken tests on the Base branch, and without modifying host source files before explicit approval.

**Supreme Principle (2-Layer Differential & Zero-Ripple Gates):**
1. **Gate 1 — Differential Gate (`ab-verify` on Git Worktrees `Base` vs. `Head`):**
   - Executes identical tests, 1-hop caller checks, and `VERIPR_PAYLOAD:` schema-hash probes against isolated `Base` and `Head` worktrees (`S/tmp/worktree-base` vs. `S/tmp/worktree-head`).
   - Classifies accurately:
     - `GATE1_RIPPLE_CONFIRMED`: `Base=PASS` -> `Head=FAIL` on a 1-hop caller (e.g. `None`/null return breaking caller arithmetic/attribute access).
     - `GATE1_CONTRACT_DRIFT`: Recursive API `schema_hash` breaks backward compatibility (`missing_keys` / `renamed_candidates` / breaking `type_changes` such as `number -> null`).
     - `GATE1_NEW_REGRESSION`: Existing test passed on `Base` and now fails on `Head`.
     - `PRE_EXISTING_FAILURE`: Fails identically on **both** `Base` and `Head` — filtered out automatically (`blocks_pr=false`) so developers are never falsely blocked!
2. **Gate 2 — Zero-Ripple Fix Gate (`verify-fix` on `S/sandbox-fix/`):**
   - Minimal fixes applied inside `S/sandbox-fix/` MUST simultaneously:
     1. Fix the confirmed ripple/contract defect and restore compatible `schema_hash` with `Base`.
     2. Pass 100% of 1-hop caller checks (`--caller-cmd`) and project tests (`--test-cmd`, automatically filtering verified `PRE_EXISTING_FAILURE` items).
     3. **Preserve the Developer's New Feature (`--feature-cmd`):** Naively reverting the PR or breaking new feature tests fails immediately with `REJECTED_FEATURE_BROKEN` + `--auto-rollback`.
     4. Export clean incremental unified patches relative to `Head` (`S/patches/<id>.patch` and combined `S/patches/veripr-fixes.patch`).

## Default Parameters
`TARGET_RUNTIME=1-2 min` | `MAX_IMPACT_LINES=25` | `S=.bughunt/pr-<YYYYMMDD-HHMMSS>/` | `VPR=<skill_dir>/scripts/vpr.py` | **Language:** Auto-match user prompt language (Thai or English; keep standard technical terms like *Base*, *Head*, *Git Worktree*, *1-hop Caller*, *Schema Hash*, *Differential Gate*, *Zero-Ripple Fix* in English) for chat, `S/worklog.md`, and `S/pr-report.md`.

## Token Efficiency Rules (Mandatory)
1. **Grep-First & Ranged Reads:** Run `vpr.py diff-scope` and `vpr.py impact-map` first, then `read_file` with `StartLine`/`EndLine` (`<=150` lines/call) only on modified symbols and flagged 1-hop callers. **Never** read `S/logs/*.log` or `S/logs/*.json` directly (neither via `read_file` nor `Get-Content`/`cat`).
2. **CLI-First (`scripts/vpr.py`):** Use `python "<VPR>"` (`init`, `diff-scope`, `impact-map`, `ab-verify`, `verify-fix`, `sandbox`, `log`, `summary`). Rely on `<=25`-line maps and `<=10`-line digests.
3. **Batch & Turn-Fusion:** Never spend a standalone turn calling only `vpr.py log`. Fuse `vpr.py log` into the next turn's test writes, sandbox actions, or Phase 4 report prep.
4. **Lazy-Load Heavy Templates:** Load `resources/report-template-th.md` (Thai) or `resources/report-template.md` (English) **ONLY in Phase 4** (never load both!). Load ONLY the matching `resources/lang/<lang>.md`.

## Reference Files (Lazy-Load by Phase)
- `scripts/vpr.py` — Zero-dependency Git worktree differential runner, AST/brace diff-scope, 1-hop impact mapper, schema-hash comparator & patch exporter.
- `resources/ripple-taxonomy.md` — Phase 0–1: 4 ripple/contract failure categories & minimal fix patterns.
- `resources/lang/00-common.md` — Phase 0–1: `VERIPR_TARGET_ROOT`, `VERIPR_PAYLOAD:` schema-hash protocol, and Gate 1/2 rules.
- `resources/lang/<lang>.md` — Phase 0–1: load **only** matching language (`python`, `js-ts`, `go`, `jvm`, `dotnet`, `rust`).
- `resources/report-template.md` — **Phase 4 ONLY (English)**.
- `resources/report-template-th.md` — **Phase 4 ONLY (Thai)**.

## Core Rules & Isolation (`.bughunt/`)
1. Execute Phases `0-1 -> 2 -> 3 -> 4` cleanly without skipping Gate 1 or Gate 2 checks.
2. All agent files live strictly inside `.bughunt/.cache/` and `S/` (`tests/`, `logs/`, `tmp/`, `patches/`, `sandbox-fix/`, `worklog.md`, `pr-report.md`).
3. **NEVER** modify host source files, configs, dependencies, or lockfiles before explicit user approval; **NEVER** run `git checkout|restore|reset|clean` on the host repository.
4. Physical `S/pr-report.md` is mandatory (chat summaries alone do not count).

---

## Phase 0–1 — Init, Diff-Scope & 1-Hop Impact Map
1. **Single Fused Init + Scope + Impact Command:**
   - `python "<VPR>" init --root .` -> capture `{S, SNAME, CACHE, ROOT, base_ref, head_ref}`
   - `python "<VPR>" diff-scope --root . --s "$S"` -> detects changed symbols + semantic risks (`RETURN_SHAPE_CHANGED`, `CONTRACT_KEY_RENAMED_OR_DROPPED`, `SIGNATURE_CHANGED`, `ERROR_GUARD_REMOVED`) in `<=25` lines.
   - `python "<VPR>" impact-map --root . --s "$S" --max-lines 25` -> lists 1-hop callers + test coverage status in `<=25` lines.
   Load `resources/ripple-taxonomy.md`, `resources/lang/00-common.md`, and ONLY matching `resources/lang/<lang>.md`.
2. **Targeted Inspection:** Use ranged `read_file` (`<=150`L) on changed functions and high-risk/untested 1-hop callers to plan focused checks (`PR-1`, `PR-2`, ...), including any pre-existing failing project test (`PR-3`) so Gate 1 proves whether it is legacy branch debt (`PRE_EXISTING_FAILURE`).

## Phase 2 — Differential Gate (`ab-verify` on Base vs. Head Worktrees)
1. **Fuse Log + Batch Write Targeted Checks (One Turn):**
   - Fuse `python "<VPR>" log --s "$S" --status "Phase 2 - Differential Gate" --entry "..."` with parallel `write_file` calls writing `S/tests/test_pr1.<ext>`, `S/tests/probe_pr2.<ext>`, ...
   - Every test/probe MUST read `VERIPR_TARGET_ROOT` (`worktree-base` during Base run, `worktree-head` during Head run, `sandbox-fix` during Gate 2) so the exact same code runs against both versions.
   - For JSON/API payloads, emit `VERIPR_PAYLOAD:{"id":"prN","payload":...}` so `vpr.py` checks recursive `schema_hash` parity automatically.
2. **Execute Parallel Worktree Differential Checks:**
   - Run `python "<VPR>" ab-verify --s "$S" --id prN --category <CAT> -- <cmd>` (chain multiple `PR-N` IDs in one turn).
   - Inspect compact verdicts:
     - `GATE1_RIPPLE_CONFIRMED` / `GATE1_CONTRACT_DRIFT` / `GATE1_NEW_REGRESSION` -> real PR defect (`blocks_pr=true`), proceed to Phase 3.
     - `PRE_EXISTING_FAILURE` -> legacy failure (`blocks_pr=false`); filtered out from blocking the PR and ignored during Gate 2 full-suite checks.
     - `CLEAN_PASS` -> verified safe across Base and Head.

## Phase 3 — Zero-Ripple Fix Gate (`verify-fix` on `S/sandbox-fix/`)
1. **Create Sandbox-Fix:** Run `python "<VPR>" sandbox --s "$S" --mode fix`, fused with `vpr.py log --status "Phase 3 - Zero-Ripple Fix Gate" --entry "..."`.
2. **Checkpoint, Patch Minimally & Verify Gate 2:**
   - Before patching `prN` in `S/sandbox-fix/`, snapshot via `python "<VPR>" sandbox --s "$S" --mode save --id prN`.
   - Apply a **minimal, additive/backward-compatible fix** in `S/sandbox-fix/` that preserves every new PR feature!
   - Run Gate 2 with automatic rollback:
     ```text
     python "<VPR>" verify-fix --s "$S" --id prN --auto-rollback --test-cmd "<project suite>" --caller-cmd "<1-hop caller cmd>" --feature-cmd "<new PR feature cmd>"
     ```
   - Outcomes:
     - `CONFIRMED_ZERO_RIPPLE_FIX`: Target fixed, schema compatible, 1-hop callers + non-legacy project tests PASS, new PR feature PASS, and incremental `.patch` exported to `S/patches/prN.patch` & `S/patches/veripr-fixes.patch`.
     - `REJECTED_FEATURE_BROKEN` / `REJECTED_CALLER_REGRESSION` / `REJECTED_INCOMPLETE_FIX`: `S/sandbox-fix/` auto-restored to checkpoint; adjust fix to keep both callers and new features working.

## Phase 4 — PR Impact Report, Patch Export & Cleanliness
1. **Single Fused Prep Turn:** Run one chained shell command:
   ```text
   python "<VPR>" summary --s "$S" --snippets ; python "<VPR>" sandbox --s "$S" --mode diff ; python "<VPR>" sandbox --s "$S" --mode clean
   ```
   **AND** in the same turn call `read_file` on **ONLY** `resources/report-template-th.md` (Thai) or `resources/report-template.md` (English).
2. **Write `S/pr-report.md` & Log (Single Turn):** Populate the loaded template using `vpr.py summary` tables/snippets and `S/patches/veripr-fixes.patch` unified diff (all 7 sections; `"None"`/`"ไม่มี"` if empty), fused with `python "<VPR>" log --s "$S" --status "Phase 4 Complete" --entry "VeriPR report & incremental patches finalized; worktrees & sandbox cleaned."`.
3. **Handoff:** Present a concise Ready/Blocked PR verdict, ripple & contract fixes, filtered `PRE_EXISTING_FAILURE` tests, exported `.patch` paths (`S/patches/veripr-fixes.patch`), `cleanliness` status, and `S/pr-report.md` path in chat (matching prompt language), and ask before applying the `.patch` to the host working tree.

## Post-Report Host Application (Explicit Approval Only)
Apply `S/patches/veripr-fixes.patch` (`git apply <S>/patches/veripr-fixes.patch`) -> run confirmation check -> append Section 7 `"Applied Patches Log"` in `S/pr-report.md`.
