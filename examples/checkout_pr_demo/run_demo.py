#!/usr/bin/env python3
"""One-command live VeriPR v1.0 E2E showcase using an isolated Git copy of checkout_pr_demo."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


EXAMPLE_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_DIR.parents[1]
VPR_SCRIPT = REPO_ROOT / "scripts" / "vpr.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import vpr  # noqa: E402


HEAD_DISCOUNT_SERVICE = '''"""Discount service (Head PR: adds GOLD loyalty tier bonus, accidentally returns None when no coupon)."""

from typing import Optional


COUPONS = {
    "SAVE10": 10.0,
    "VIP20": 20.0,
}


def calculate_discount(
    subtotal: float,
    coupon_code: Optional[str] = None,
    loyalty_tier: Optional[str] = None,
) -> Optional[float]:
    """Return discount amount; PR adds GOLD tier +5.0 bonus but breaks empty coupon contract (None)."""
    if subtotal <= 0:
        return 0.0
    bonus = 5.0 if loyalty_tier == "GOLD" else 0.0
    if not coupon_code and bonus == 0.0:
        return None
    rate = COUPONS.get(coupon_code.upper(), 0.0) if coupon_code else 0.0
    return round(min(subtotal, rate + bonus), 2)
'''

HEAD_CHECKOUT_API = '''"""JSON checkout API serializer (Head PR: silently renames total_cents -> amount_cents)."""

from typing import Dict, List, Optional

from .discount_service import calculate_discount


def build_checkout_response(
    order_id: str,
    items: List[Dict[str, float]],
    coupon_code: Optional[str] = None,
    loyalty_tier: Optional[str] = None,
) -> Dict[str, object]:
    """Return frontend API payload with inadvertently renamed key."""
    subtotal = round(sum(float(it["price"]) * float(it.get("qty", 1)) for it in items), 2)
    discount = calculate_discount(subtotal, coupon_code=coupon_code, loyalty_tier=loyalty_tier) or 0.0
    final_total = round(max(0.0, subtotal - discount), 2)
    return {
        "order_id": order_id,
        "subtotal_cents": int(round(subtotal * 100)),
        "discount_cents": int(round(discount * 100)),
        "amount_cents": int(round(final_total * 100)),
        "currency": "USD",
    }
'''

HEAD_TEST_DISCOUNT = '''"""Unit tests targeting discount_service directly (passes on Head, covers GOLD feature)."""

import unittest

from checkout_app.discount_service import calculate_discount


class TestDiscountService(unittest.TestCase):
    def test_valid_coupon_discount(self):
        self.assertEqual(calculate_discount(100.0, coupon_code="SAVE10"), 10.0)

    def test_unknown_coupon_zero(self):
        self.assertEqual(calculate_discount(100.0, coupon_code="NOPE"), 0.0)

    def test_gold_loyalty_tier_adds_bonus(self):
        self.assertEqual(calculate_discount(100.0, coupon_code="SAVE10", loyalty_tier="GOLD"), 15.0)
        self.assertEqual(calculate_discount(100.0, loyalty_tier="GOLD"), 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
'''

TEST_PR1_RIPPLE = '''"""PR-1: 1-hop caller test on receipt_generator.generate_receipt without coupon."""
import os, sys, unittest
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.environ.get("VERIPR_TARGET_ROOT", str(DEFAULT_ROOT)))

from checkout_app.receipt_generator import generate_receipt


class TestPr1ReceiptRipple(unittest.TestCase):
    def test_receipt_without_coupon_uses_numeric_discount(self):
        rec = generate_receipt("ORD-100", [{"name": "Keyboard", "price": 50.0, "qty": 2}], coupon_code=None)
        self.assertEqual(rec["subtotal"], 100.0)
        self.assertEqual(rec["discount"], 0.0)
        self.assertEqual(rec["final_total"], 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
'''

PROBE_PR2_CONTRACT = '''"""PR-2: API payload schema-hash probe for build_checkout_response."""
import json, os, sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, os.environ.get("VERIPR_TARGET_ROOT", str(DEFAULT_ROOT)))

from checkout_app.checkout_api import build_checkout_response

resp = build_checkout_response("ORD-200", [{"name": "Mouse", "price": 25.0, "qty": 2}], coupon_code="SAVE10")
print("VERIPR_PAYLOAD:" + json.dumps({"id": "pr2", "endpoint": "build_checkout_response", "payload": resp}, sort_keys=True))
'''

FIX_DISCOUNT_CLEAN = '''"""Discount service (Zero-Ripple Fix: returns 0.0 on empty coupon while preserving GOLD tier bonus)."""

from typing import Optional


COUPONS = {
    "SAVE10": 10.0,
    "VIP20": 20.0,
}


def calculate_discount(
    subtotal: float,
    coupon_code: Optional[str] = None,
    loyalty_tier: Optional[str] = None,
) -> float:
    """Return numeric discount amount; preserves GOLD tier +5.0 bonus and 0.0 empty-coupon contract."""
    if subtotal <= 0:
        return 0.0
    bonus = 5.0 if loyalty_tier == "GOLD" else 0.0
    if not coupon_code and bonus == 0.0:
        return 0.0
    rate = COUPONS.get(coupon_code.upper(), 0.0) if coupon_code else 0.0
    return round(min(subtotal, rate + bonus), 2)
'''

FIX_CHECKOUT_API_CLEAN = '''"""JSON checkout API serializer (Zero-Ripple Fix: restores total_cents key)."""

from typing import Dict, List, Optional

from .discount_service import calculate_discount


def build_checkout_response(
    order_id: str,
    items: List[Dict[str, float]],
    coupon_code: Optional[str] = None,
    loyalty_tier: Optional[str] = None,
) -> Dict[str, object]:
    """Return frontend API payload preserving Base schema contract."""
    subtotal = round(sum(float(it["price"]) * float(it.get("qty", 1)) for it in items), 2)
    discount = calculate_discount(subtotal, coupon_code=coupon_code, loyalty_tier=loyalty_tier) or 0.0
    final_total = round(max(0.0, subtotal - discount), 2)
    return {
        "order_id": order_id,
        "subtotal_cents": int(round(subtotal * 100)),
        "discount_cents": int(round(discount * 100)),
        "total_cents": int(round(final_total * 100)),
        "currency": "USD",
    }
'''


def run_cli(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, "-B", str(VPR_SCRIPT), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): vpr.py {' '.join(args)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def run_git(args: list[str], cwd: Path) -> None:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")


def main() -> int:
    vpr.configure_stdio()
    with tempfile.TemporaryDirectory(prefix="veripr_demo_") as tmp:
        work_root = Path(tmp) / "checkout_pr_demo"
        shutil.copytree(EXAMPLE_DIR / "checkout_app", work_root / "checkout_app")
        shutil.copytree(EXAMPLE_DIR / "tests", work_root / "tests")

        # 1. Initialize Git repo on Base commit (legacy test already broken on Base)
        run_git(["init"], work_root)
        run_git(["config", "user.name", "VeriPR Demo"], work_root)
        run_git(["config", "user.email", "demo@verihunt.local"], work_root)
        run_git(["add", "."], work_root)
        run_git(["commit", "-m", "base: initial checkout service"], work_root)

        # 2. Apply Head PR edits in working tree
        base_discount_src = (work_root / "checkout_app" / "discount_service.py").read_text(encoding="utf-8")
        (work_root / "checkout_app" / "discount_service.py").write_text(HEAD_DISCOUNT_SERVICE, encoding="utf-8")
        (work_root / "checkout_app" / "checkout_api.py").write_text(HEAD_CHECKOUT_API, encoding="utf-8")
        (work_root / "tests" / "test_discount.py").write_text(HEAD_TEST_DISCOUNT, encoding="utf-8")

        print("=== Phase 0-1: Init, Diff-Scope & 1-Hop Impact Map ===")
        init_res = run_cli(["init", "--root", str(work_root)], cwd=work_root)
        init_json = json.loads(init_res.stdout.strip())
        s_rel = init_json["S"]
        s_dir = work_root / s_rel
        print(f"Session: {s_rel} (base={init_json['base_ref']} vs head={init_json['head_ref']})")

        ds_res = run_cli(["diff-scope", "--root", str(work_root), "--s", s_rel], cwd=work_root)
        print(ds_res.stdout.strip())
        assert "RETURN_SHAPE_CHANGED" in ds_res.stdout
        assert "CONTRACT_KEY_RENAMED_OR_DROPPED" in ds_res.stdout
        assert len(ds_res.stdout.strip().splitlines()) <= 25

        im_res = run_cli(["impact-map", "--root", str(work_root), "--s", s_rel, "--max-lines", "25"], cwd=work_root)
        print(im_res.stdout.strip())
        assert "generate_receipt" in im_res.stdout
        assert len(im_res.stdout.strip().splitlines()) <= 25

        # Write targeted checks inside S/tests/
        pr1_test = s_dir / "tests" / "test_pr1_receipt_ripple.py"
        pr2_probe = s_dir / "tests" / "probe_pr2_api_contract.py"
        pr1_test.write_text(TEST_PR1_RIPPLE, encoding="utf-8")
        pr2_probe.write_text(PROBE_PR2_CONTRACT, encoding="utf-8")
        run_cli(
            ["log", "--s", s_rel, "--status", "Phase 2 - Differential Gate",
             "--entry", "Mapped 1-hop caller generate_receipt and API serializer build_checkout_response."],
            cwd=work_root,
        )

        print("\n=== Phase 2: Gate 1 — Differential Worktree Check (Base vs. Head) ===")
        ab1 = run_cli(
            ["ab-verify", "--s", s_rel, "--id", "pr1", "--category", "RIPPLE",
             "--", "python", f"{s_rel}/tests/test_pr1_receipt_ripple.py"],
            cwd=work_root,
        )
        print(ab1.stdout.strip())
        assert "verdict=GATE1_RIPPLE_CONFIRMED" in ab1.stdout

        ab2 = run_cli(
            ["ab-verify", "--s", s_rel, "--id", "pr2", "--category", "CONTRACT",
             "--", "python", f"{s_rel}/tests/probe_pr2_api_contract.py"],
            cwd=work_root,
        )
        print(ab2.stdout.strip())
        assert "verdict=GATE1_CONTRACT_DRIFT" in ab2.stdout

        ab3 = run_cli(
            ["ab-verify", "--s", s_rel, "--id", "pr3", "--category", "LEGACY",
             "--", "python", "-m", "unittest", "tests/test_legacy_tax_export.py", "-v"],
            cwd=work_root,
        )
        print(ab3.stdout.strip())
        assert "verdict=PRE_EXISTING_FAILURE" in ab3.stdout
        assert "blocks_pr=False" in ab3.stdout

        print("\n=== Phase 3: Gate 2 — Zero-Ripple Fix Gate & Feature Preservation ===")
        run_cli(["sandbox", "--s", s_rel, "--mode", "fix"], cwd=work_root)
        run_cli(["sandbox", "--s", s_rel, "--mode", "save", "--id", "pr1"], cwd=work_root)

        # PR-1 Attempt 1: Naive revert to Base (wipes out GOLD loyalty feature -> REJECTED_FEATURE_BROKEN)
        fix_root = s_dir / "sandbox-fix"
        (fix_root / "checkout_app" / "discount_service.py").write_text(base_discount_src, encoding="utf-8")
        vf1_bad = run_cli(
            ["verify-fix", "--s", s_rel, "--id", "pr1", "--auto-rollback",
             "--test-cmd", "python -m unittest discover -s tests -v",
             "--caller-cmd", f"python {s_rel}/tests/test_pr1_receipt_ripple.py",
             "--feature-cmd", "python -m unittest tests.test_discount -v"],
            cwd=work_root,
            check=False,
        )
        print(vf1_bad.stdout.strip())
        assert vf1_bad.returncode == 1
        assert "verdict=REJECTED_FEATURE_BROKEN" in vf1_bad.stdout
        assert "AUTO_RESTORED" in vf1_bad.stdout

        # PR-1 Attempt 2: Clean Zero-Ripple Fix (returns 0.0 & preserves GOLD feature; filters PR-3 pre-existing test)
        (fix_root / "checkout_app" / "discount_service.py").write_text(FIX_DISCOUNT_CLEAN, encoding="utf-8")
        vf1_ok = run_cli(
            ["verify-fix", "--s", s_rel, "--id", "pr1", "--auto-rollback",
             "--test-cmd", "python -m unittest discover -s tests -v",
             "--caller-cmd", f"python {s_rel}/tests/test_pr1_receipt_ripple.py",
             "--feature-cmd", "python -m unittest tests.test_discount -v"],
            cwd=work_root,
        )
        print(vf1_ok.stdout.strip())
        assert "verdict=CONFIRMED_ZERO_RIPPLE_FIX" in vf1_ok.stdout
        assert "PASS_FILTERED_PREEXISTING" in vf1_ok.stdout

        # PR-2: Restore total_cents JSON key in checkout_api.py
        run_cli(["sandbox", "--s", s_rel, "--mode", "save", "--id", "pr2"], cwd=work_root)
        (fix_root / "checkout_app" / "checkout_api.py").write_text(FIX_CHECKOUT_API_CLEAN, encoding="utf-8")
        vf2_ok = run_cli(
            ["verify-fix", "--s", s_rel, "--id", "pr2", "--auto-rollback",
             "--test-cmd", "python -m unittest discover -s tests -v",
             "--caller-cmd", f"python {s_rel}/tests/test_pr1_receipt_ripple.py",
             "--feature-cmd", "python -m unittest tests.test_discount -v"],
            cwd=work_root,
        )
        print(vf2_ok.stdout.strip())
        assert "verdict=CONFIRMED_ZERO_RIPPLE_FIX" in vf2_ok.stdout

        # Confirm exported incremental patch applies cleanly on Head via git apply --check
        combined_patch = s_dir / "patches" / "veripr-fixes.patch"
        assert combined_patch.is_file() and combined_patch.read_text(encoding="utf-8").strip()
        run_git(["apply", "--check", str(combined_patch)], work_root)
        print(f"Incremental patch check: git apply --check {s_rel}/patches/veripr-fixes.patch -> OK")

        print("\n=== Phase 4: Summary, Diff & Worktree Cleanliness ===")
        sum_res = run_cli(["summary", "--s", s_rel, "--snippets"], cwd=work_root)
        print(sum_res.stdout.strip())
        diff_res = run_cli(["sandbox", "--s", s_rel, "--mode", "diff"], cwd=work_root)
        print(diff_res.stdout.strip())
        clean_res = run_cli(["sandbox", "--s", s_rel, "--mode", "clean"], cwd=work_root)
        print(clean_res.stdout.strip())
        assert "cleanliness: CLEAN" in clean_res.stdout

        # Save sample report alongside demo for documentation
        pr1_patch = (s_dir / "patches" / "pr1.patch").read_text(encoding="utf-8").strip()
        pr2_patch = (s_dir / "patches" / "pr2.patch").read_text(encoding="utf-8").strip()
        sample_report = EXAMPLE_DIR / "sample-pr-report.md"
        sample_report.write_text(
            "# VeriPR Pre-Commit & PR Differential Impact Report (Sample)\n\n"
            "## Session Metadata\n"
            f"- **Session ID (`S`):** `.bughunt/{init_json['SNAME']}/`\n"
            "- **Compared Refs:** `Base: HEAD` vs. `Head: WORKTREE`\n"
            "- **Dual-Gate Protocol:** Gate 1 Differential Worktree Check (`ab-verify`) | Gate 2 Zero-Ripple Fix + Feature Preservation (`verify-fix`)\n"
            "- **PR Verdict:** `BLOCKED_PENDING_FIX_APPLICATION` (2 ripple/contract fixes exported cleanly)\n\n"
            "---\n\n"
            "## 1. Executive Summary & Dual-Gate Metrics\n\n"
            "| Metric | Value | Notes |\n"
            "|---|---:|---|\n"
            "| Changed Symbols (`diff-scope`) | 2 | `calculate_discount`, `build_checkout_response` |\n"
            "| 1-Hop Callers Mapped (`impact-map`) | 2 | Flagged untested `generate_receipt` (`HIGH`) |\n"
            "| **Confirmed Ripple / Contract Defects** | **2** | `PR-1` (`GATE1_RIPPLE_CONFIRMED`), `PR-2` (`GATE1_CONTRACT_DRIFT`) |\n"
            "| **Zero-Ripple Fixes Verified & Exported** | **2** | `CONFIRMED_ZERO_RIPPLE_FIX` (`pr1.patch`, `pr2.patch`) |\n"
            "| **Filtered Pre-Existing Failures** | **1** | `PR-3` (`test_legacy_tax_rounding` failed on both Base & Head) |\n"
            "| **Auto-Rolled-Back Naive Fixes** | **1** | `PR-1 Attempt 1` (`REJECTED_FEATURE_BROKEN` — lost `GOLD` bonus) |\n\n"
            "---\n\n"
            "## 2. Exported Incremental Unified Patches\n\n"
            "### `PR-1` (`S/patches/pr1.patch`)\n"
            "```diff\n"
            f"{pr1_patch}\n"
            "```\n\n"
            "### `PR-2` (`S/patches/pr2.patch`)\n"
            "```diff\n"
            f"{pr2_patch}\n"
            "```\n\n"
            "---\n\n"
            "## 3. CLI Summary & Cleanliness Verification\n"
            "```text\n"
            f"{clean_res.stdout.strip()}\n"
            "```\n",
            encoding="utf-8",
        )
        print(f"\nDemo completed cleanly; sample report updated: {sample_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
