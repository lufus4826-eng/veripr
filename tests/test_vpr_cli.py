#!/usr/bin/env python3
"""Automated unit & integration tests for VeriPR CLI engine (scripts/vpr.py)."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
VPR_SCRIPT = REPO_ROOT / "scripts" / "vpr.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import vpr  # noqa: E402


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc


def _init_git_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.name", "VeriPR Test")
    _git(root, "config", "user.email", "test@verihunt.local")


def _run_vpr(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
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
        raise AssertionError(f"vpr.py {' '.join(args)} failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc


class TestVeriPRCli(unittest.TestCase):
    def test_init_diff_scope_and_line_caps(self):
        with tempfile.TemporaryDirectory(prefix="vpr_t1_") as td:
            root = Path(td)
            (root / "app.py").write_text(
                "def calc(x):\n"
                "    try:\n"
                "        return x + 1\n"
                "    except Exception:\n"
                "        return 0\n\n"
                "def api():\n"
                "    return {'ok': True, 'total': 10}\n",
                encoding="utf-8",
            )
            # Add 28 extra functions so diff-scope tests 25-line cap when all change
            many = "\n".join(f"def fn_{i}(a):\n    return a + {i}\n" for i in range(28))
            (root / "bulk.py").write_text(many, encoding="utf-8")
            _init_git_repo(root)
            _git(root, "add", ".")
            _git(root, "commit", "-m", "base")

            # Edit in WORKTREE: return None, rename key, change signature, remove try/except
            (root / "app.py").write_text(
                "def calc(x, y):\n"
                "    if x < 0:\n"
                "        return None\n"
                "    return x + y\n\n"
                "def api():\n"
                "    return {'ok': True, 'amount': 10}\n",
                encoding="utf-8",
            )
            many_mod = "\n".join(f"def fn_{i}(a):\n    return a * {i + 1}\n" for i in range(28))
            (root / "bulk.py").write_text(many_mod, encoding="utf-8")

            init_out = _run_vpr(root, "init", "--root", str(root)).stdout.strip()
            meta = json.loads(init_out)
            self.assertEqual(meta["base_ref"], "HEAD")
            self.assertEqual(meta["head_ref"], "WORKTREE")
            s_dir = root / meta["S"]
            for sub in ("tests", "logs", "tmp", "patches"):
                self.assertTrue((s_dir / sub).is_dir())
            self.assertTrue((s_dir / "logs" / "fs-before.txt").is_file())

            ds_out = _run_vpr(root, "diff-scope", "--root", str(root), "--s", meta["S"]).stdout.strip()
            lines = ds_out.splitlines()
            self.assertLessEqual(len(lines), 25)
            self.assertIn("RETURN_SHAPE_CHANGED", ds_out)
            self.assertIn("CONTRACT_KEY_RENAMED_OR_DROPPED", ds_out)
            self.assertIn("SIGNATURE_CHANGED", ds_out)
            self.assertIn("ERROR_GUARD_REMOVED", ds_out)
            self.assertIn("more symbols in logs/diff_scope.json", ds_out)

    def test_impact_map_ignores_comments_and_caps_25_lines(self):
        with tempfile.TemporaryDirectory(prefix="vpr_t2_") as td:
            root = Path(td)
            (root / "svc.py").write_text("def price(x):\n    return x * 2\n", encoding="utf-8")
            (root / "fake_doc.py").write_text(
                '"""Docstring mentions price(10) but never calls it."""\n'
                "# Comment: price(20)\n"
                "def unrelated():\n    return 1\n",
                encoding="utf-8",
            )
            # Real Python caller + JS caller + 30 extra callers to test <=25 line cap
            (root / "caller.py").write_text(
                "from svc import price\n"
                "def checkout(n):\n"
                "    p = price(n)\n"
                "    return p + 5\n",
                encoding="utf-8",
            )
            (root / "web_caller.js").write_text(
                "// price(99) commented out\n"
                "export function renderTotal(n) {\n"
                "  return price(n) + 1;\n"
                "}\n",
                encoding="utf-8",
            )
            many_callers = "\n".join(f"def caller_{i}(n):\n    return price(n)\n" for i in range(30))
            (root / "bulk_callers.py").write_text("from svc import price\n" + many_callers, encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_web.py").write_text("def test_x():\n    renderTotal(1)\n", encoding="utf-8")

            _init_git_repo(root)
            _git(root, "add", ".")
            _git(root, "commit", "-m", "base")
            (root / "svc.py").write_text("def price(x):\n    return None if x == 0 else x * 2\n", encoding="utf-8")

            meta = json.loads(_run_vpr(root, "init", "--root", str(root)).stdout.strip())
            _run_vpr(root, "diff-scope", "--root", str(root), "--s", meta["S"])
            im_out = _run_vpr(root, "impact-map", "--root", str(root), "--s", meta["S"], "--max-lines", "25").stdout.strip()
            lines = im_out.splitlines()
            self.assertLessEqual(len(lines), 25)
            self.assertIn("checkout (caller.py:3)", im_out)
            self.assertNotIn("unrelated", im_out)
            self.assertNotIn("fake_doc.py", im_out)
            self.assertIn("more callers in logs/impact_map.json", im_out)

            # Verify full JSON recorded both Python and JS callers + tested flags
            im_json = json.loads((root / meta["S"] / "logs" / "impact_map.json").read_text(encoding="utf-8"))
            caller_names = {c["caller_symbol"]: c for c in im_json["callers"]}
            self.assertIn("checkout", caller_names)
            self.assertIn("renderTotal", caller_names)
            self.assertEqual(caller_names["checkout"]["tested"], "NO")
            self.assertEqual(caller_names["renderTotal"]["tested"], "YES")
            self.assertIn("arithmetic_op", caller_names["checkout"]["usage"])

    def test_ab_verify_worktree_ripple_and_preexisting_filter(self):
        with tempfile.TemporaryDirectory(prefix="vpr_t3_") as td:
            root = Path(td)
            (root / "mod.py").write_text("def tax():\n    return 0.0\n", encoding="utf-8")
            (root / "caller.py").write_text("from mod import tax\ndef total(x):\n    return x + tax()\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_legacy.py").write_text(
                "import unittest\n"
                "class LegacyTest(unittest.TestCase):\n"
                "    def test_broken_legacy(self):\n"
                "        self.assertEqual(1, 2)\n"
                "if __name__ == '__main__': unittest.main()\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_combo.py").write_text(
                "import unittest\n"
                "from caller import total\n"
                "class ComboTest(unittest.TestCase):\n"
                "    def test_broken_legacy(self):\n"
                "        self.assertEqual(1, 2)\n"
                "    def test_total_works(self):\n"
                "        self.assertEqual(total(10), 10.0)\n"
                "if __name__ == '__main__': unittest.main()\n",
                encoding="utf-8",
            )
            _init_git_repo(root)
            _git(root, "add", ".")
            _git(root, "commit", "-m", "base")

            # Committed PR mode: test HEAD~1 vs HEAD!
            (root / "mod.py").write_text("def tax():\n    return None\n", encoding="utf-8")
            _git(root, "add", ".")
            _git(root, "commit", "-m", "head: breaks tax")

            meta = json.loads(_run_vpr(root, "init", "--root", str(root)).stdout.strip())
            self.assertEqual(meta["base_ref"], "HEAD~1")
            self.assertEqual(meta["head_ref"], "HEAD")
            s_dir = root / meta["S"]
            (s_dir / "tests" / "test_ripple.py").write_text(
                "import os, sys, unittest\n"
                "sys.path.insert(0, os.environ['VERIPR_TARGET_ROOT'])\n"
                "from caller import total\n"
                "class T(unittest.TestCase):\n"
                "    def test_tot(self): self.assertEqual(total(5), 5.0)\n"
                "if __name__ == '__main__': unittest.main()\n",
                encoding="utf-8",
            )

            ab1 = _run_vpr(root, "ab-verify", "--s", meta["S"], "--id", "t_rip", "--category", "RIPPLE",
                           "--", "python", f"{meta['S']}/tests/test_ripple.py").stdout
            self.assertIn("verdict=GATE1_RIPPLE_CONFIRMED", ab1)

            ab_leg = _run_vpr(root, "ab-verify", "--s", meta["S"], "--id", "t_leg", "--category", "LEGACY",
                              "--", "python", "-m", "unittest", "tests.test_legacy", "-v").stdout
            self.assertIn("verdict=PRE_EXISTING_FAILURE", ab_leg)
            self.assertIn("blocks_pr=False", ab_leg)

            # Combo test has legacy failure on Base AND gains a NEW failure on Head -> must NOT be masked!
            ab_combo = _run_vpr(root, "ab-verify", "--s", meta["S"], "--id", "t_combo", "--category", "REGRESSION",
                                "--", "python", "-m", "unittest", "tests.test_combo", "-v").stdout
            self.assertIn("verdict=GATE1_NEW_REGRESSION", ab_combo)
            self.assertIn("blocks_pr=True", ab_combo)

    def test_recursive_schema_hash_drift_and_additive_tolerance(self):
        base_a = {"order_id": "ORD-1", "total": 100, "items": [{"sku": "A", "price": 50.0}]}
        base_b = {"order_id": "ORD-999", "total": 250.5, "items": [{"sku": "B", "price": 99}, {"sku": "C", "price": 15}]}
        sig_a = vpr.compute_schema_signature(base_a)
        sig_b = vpr.compute_schema_signature(base_b)
        self.assertEqual(vpr.schema_hash(sig_a), vpr.schema_hash(sig_b))

        # Purely additive field should be non-breaking
        head_add = {**base_a, "loyalty_points": 42}
        cmp_add = vpr.compare_schemas(sig_a, vpr.compute_schema_signature(head_add))
        self.assertFalse(cmp_add["breaking"])
        self.assertEqual(cmp_add["added_keys"], ["$.loyalty_points"])

        # Renamed / missing key OR number->null must break!
        head_rename = {"order_id": "ORD-1", "amount": 100, "items": [{"sku": "A", "price": None}]}
        cmp_brk = vpr.compare_schemas(sig_a, vpr.compute_schema_signature(head_rename))
        self.assertTrue(cmp_brk["breaking"])
        self.assertIn("$.total", cmp_brk["missing_keys"])
        self.assertIn("$.items[].price: number -> null", cmp_brk["type_changes"])

    def test_gate2_auto_rollback_feature_broken_and_caller_regression(self):
        with tempfile.TemporaryDirectory(prefix="vpr_t5_") as td:
            root = Path(td)
            (root / "svc.py").write_text("def calc(x, vip=False):\n    return x\n", encoding="utf-8")
            (root / "caller.py").write_text("from svc import calc\ndef bill(x):\n    return calc(x) + 1\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_feature.py").write_text(
                "import unittest\nfrom svc import calc\n"
                "class TF(unittest.TestCase):\n"
                "    def test_vip(self): self.assertEqual(calc(10, vip=True), 15)\n"
                "if __name__ == '__main__': unittest.main()\n",
                encoding="utf-8",
            )
            _init_git_repo(root)
            _git(root, "add", ".")
            _git(root, "commit", "-m", "base")

            # Head adds vip=+5 feature but breaks default non-vip return -> None
            (root / "svc.py").write_text(
                "def calc(x, vip=False):\n    return (x + 5) if vip else None\n",
                encoding="utf-8",
            )
            meta = json.loads(_run_vpr(root, "init", "--root", str(root)).stdout.strip())
            s_dir = root / meta["S"]
            (s_dir / "tests" / "test_caller.py").write_text(
                "import os, sys, unittest\nsys.path.insert(0, os.environ['VERIPR_TARGET_ROOT'])\n"
                "from caller import bill\n"
                "class TC(unittest.TestCase):\n"
                "    def test_bill(self): self.assertEqual(bill(10), 11)\n"
                "if __name__ == '__main__': unittest.main()\n",
                encoding="utf-8",
            )
            _run_vpr(root, "ab-verify", "--s", meta["S"], "--id", "f1", "--category", "RIPPLE",
                     "--", "python", f"{meta['S']}/tests/test_caller.py")
            _run_vpr(root, "sandbox", "--s", meta["S"], "--mode", "fix")
            _run_vpr(root, "sandbox", "--s", meta["S"], "--mode", "save", "--id", "f1")

            fix_svc = s_dir / "sandbox-fix" / "svc.py"
            head_snapshot = fix_svc.read_text(encoding="utf-8")

            # Bad fix 1: reverts vip feature -> REJECTED_FEATURE_BROKEN + auto-rollback
            fix_svc.write_text("def calc(x, vip=False):\n    return x\n", encoding="utf-8")
            bad1 = _run_vpr(
                root, "verify-fix", "--s", meta["S"], "--id", "f1", "--auto-rollback",
                "--caller-cmd", f"python {meta['S']}/tests/test_caller.py",
                "--feature-cmd", "python -m unittest tests.test_feature -v",
                check=False,
            )
            self.assertEqual(bad1.returncode, 1)
            self.assertIn("REJECTED_FEATURE_BROKEN", bad1.stdout)
            self.assertIn("AUTO_RESTORED", bad1.stdout)
            self.assertEqual(fix_svc.read_text(encoding="utf-8"), head_snapshot)

            # Bad fix 2: preserves vip, passes target? No, breaks caller on purpose via wrong value -> REJECTED_CALLER_REGRESSION
            fix_svc.write_text("def calc(x, vip=False):\n    return (x + 5) if vip else (x + 99)\n", encoding="utf-8")
            bad2 = _run_vpr(
                root, "verify-fix", "--s", meta["S"], "--id", "f1", "--auto-rollback",
                "--caller-cmd", f"python {meta['S']}/tests/test_caller.py",
                "--feature-cmd", "python -m unittest tests.test_feature -v",
                check=False,
            )
            self.assertEqual(bad2.returncode, 1)
            self.assertIn("REJECTED_INCOMPLETE_FIX", bad2.stdout)
            self.assertEqual(fix_svc.read_text(encoding="utf-8"), head_snapshot)

            # Good fix: preserves vip AND returns x cleanly -> CONFIRMED_ZERO_RIPPLE_FIX
            fix_svc.write_text("def calc(x, vip=False):\n    return (x + 5) if vip else x\n", encoding="utf-8")
            good = _run_vpr(
                root, "verify-fix", "--s", meta["S"], "--id", "f1", "--auto-rollback",
                "--caller-cmd", f"python {meta['S']}/tests/test_caller.py",
                "--feature-cmd", "python -m unittest tests.test_feature -v",
            )
            self.assertIn("CONFIRMED_ZERO_RIPPLE_FIX", good.stdout)

    def test_verify_fix_filters_verified_preexisting_only(self):
        with tempfile.TemporaryDirectory(prefix="vpr_t6_") as td:
            root = Path(td)
            (root / "core.py").write_text("def val():\n    return 1\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_old_debt.py").write_text(
                "import unittest\nclass OldDebt(unittest.TestCase):\n"
                "    def test_known_broken(self): self.assertEqual(1, 2)\n",
                encoding="utf-8",
            )
            (root / "tests" / "test_core.py").write_text(
                "import unittest\nfrom core import val\nclass CoreT(unittest.TestCase):\n"
                "    def test_val_ok(self): self.assertEqual(val(), 1)\n",
                encoding="utf-8",
            )
            _init_git_repo(root)
            _git(root, "add", ".")
            _git(root, "commit", "-m", "base")

            (root / "core.py").write_text("def val():\n    return None\n", encoding="utf-8")
            meta = json.loads(_run_vpr(root, "init", "--root", str(root)).stdout.strip())
            _run_vpr(root, "ab-verify", "--s", meta["S"], "--id", "legacy1", "--category", "LEGACY",
                     "--", "python", "-m", "unittest", "tests.test_old_debt", "-v")
            _run_vpr(root, "ab-verify", "--s", meta["S"], "--id", "core1", "--category", "REGRESSION",
                     "--", "python", "-m", "unittest", "tests.test_core", "-v")
            _run_vpr(root, "sandbox", "--s", meta["S"], "--mode", "fix")
            _run_vpr(root, "sandbox", "--s", meta["S"], "--mode", "save", "--id", "core1")

            # Fix breaks another non-preexisting test if wrong value 2 -> REJECTED_CALLER_REGRESSION
            fix_core = root / meta["S"] / "sandbox-fix" / "core.py"
            fix_core.write_text("def val():\n    return 2\n", encoding="utf-8")
            res_bad = _run_vpr(
                root, "verify-fix", "--s", meta["S"], "--id", "core1", "--auto-rollback",
                "--test-cmd", "python -m unittest discover -s tests -v",
                check=False,
            )
            self.assertEqual(res_bad.returncode, 1)

            # Correct fix -> full discovery runs test_old_debt (fails) + test_core (passes) -> PASS_FILTERED_PREEXISTING!
            fix_core.write_text("def val():\n    return 1\n", encoding="utf-8")
            res_ok = _run_vpr(
                root, "verify-fix", "--s", meta["S"], "--id", "core1", "--auto-rollback",
                "--test-cmd", "python -m unittest discover -s tests -v",
            )
            self.assertIn("CONFIRMED_ZERO_RIPPLE_FIX", res_ok.stdout)
            self.assertIn("PASS_FILTERED_PREEXISTING", res_ok.stdout)

    def test_incremental_patch_export_and_git_apply(self):
        with tempfile.TemporaryDirectory(prefix="vpr_t7_") as td:
            root = Path(td)
            (root / "api.py").write_text("def payload():\n    return {'id': 1, 'total': 50}\n", encoding="utf-8")
            _init_git_repo(root)
            _git(root, "add", ".")
            _git(root, "commit", "-m", "base")

            (root / "api.py").write_text("def payload():\n    return {'id': 1, 'sum': 50, 'v': 2}\n", encoding="utf-8")
            meta = json.loads(_run_vpr(root, "init", "--root", str(root)).stdout.strip())
            s_dir = root / meta["S"]
            (s_dir / "tests" / "probe.py").write_text(
                "import json, os, sys\nsys.path.insert(0, os.environ['VERIPR_TARGET_ROOT'])\n"
                "from api import payload\nprint('VERIPR_PAYLOAD:' + json.dumps({'id':'p1','payload':payload()}))\n",
                encoding="utf-8",
            )
            ab = _run_vpr(root, "ab-verify", "--s", meta["S"], "--id", "p1", "--category", "CONTRACT",
                          "--", "python", f"{meta['S']}/tests/probe.py").stdout
            self.assertIn("GATE1_CONTRACT_DRIFT", ab)

            _run_vpr(root, "sandbox", "--s", meta["S"], "--mode", "fix")
            _run_vpr(root, "sandbox", "--s", meta["S"], "--mode", "save", "--id", "p1")
            (s_dir / "sandbox-fix" / "api.py").write_text(
                "def payload():\n    return {'id': 1, 'total': 50, 'v': 2}\n",
                encoding="utf-8",
            )
            vf = _run_vpr(root, "verify-fix", "--s", meta["S"], "--id", "p1", "--auto-rollback").stdout
            self.assertIn("CONFIRMED_ZERO_RIPPLE_FIX", vf)

            patch_file = s_dir / "patches" / "veripr-fixes.patch"
            self.assertTrue(patch_file.is_file())
            _git(root, "apply", str(patch_file))
            self.assertIn("'total': 50", (root / "api.py").read_text(encoding="utf-8"))

    def test_summary_and_clean_worktree_pruning(self):
        with tempfile.TemporaryDirectory(prefix="vpr_t8_") as td:
            root = Path(td)
            (root / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
            _init_git_repo(root)
            _git(root, "add", ".")
            _git(root, "commit", "-m", "base")
            (root / "m.py").write_text("def f():\n    return None\n", encoding="utf-8")

            meta = json.loads(_run_vpr(root, "init", "--root", str(root)).stdout.strip())
            s_dir = root / meta["S"]
            (s_dir / "tests" / "t.py").write_text(
                "import os, sys, unittest\nsys.path.insert(0, os.environ['VERIPR_TARGET_ROOT'])\n"
                "from m import f\nclass T(unittest.TestCase):\n    def test_f(self): self.assertEqual(f(), 1)\n"
                "if __name__ == '__main__': unittest.main()\n",
                encoding="utf-8",
            )
            _run_vpr(root, "diff-scope", "--root", str(root), "--s", meta["S"])
            _run_vpr(root, "impact-map", "--root", str(root), "--s", meta["S"])
            _run_vpr(root, "ab-verify", "--s", meta["S"], "--id", "x1", "--category", "RIPPLE",
                     "--", "python", f"{meta['S']}/tests/t.py")
            _run_vpr(root, "sandbox", "--s", meta["S"], "--mode", "fix")
            _run_vpr(root, "log", "--s", meta["S"], "--status", "Complete", "--entry", "Verified x1.")

            sum_out = _run_vpr(root, "summary", "--s", meta["S"], "--snippets").stdout
            self.assertIn("GATE1_RIPPLE_CONFIRMED", sum_out)
            self.assertIn("Compact Evidence Snippets", sum_out)

            clean_out = _run_vpr(root, "sandbox", "--s", meta["S"], "--mode", "clean").stdout
            self.assertIn("cleanliness: CLEAN (0 new/modified files outside .bughunt/)", clean_out)
            self.assertFalse((s_dir / "tmp").exists())
            self.assertFalse((s_dir / "sandbox-fix").exists())

            wt_list = _git(root, "worktree", "list").stdout
            self.assertNotIn("worktree-base", wt_list)
            self.assertNotIn("worktree-head", wt_list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
