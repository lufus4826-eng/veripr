#!/usr/bin/env python3
"""Deterministic Pre-PR Differential & 1-Hop Ripple-Effect Guard CLI Engine for VeriPR v1.0 (stdlib only).

Subcommands:
  init        Initialize .bughunt/pr-<ts>/ session, detect Base/Head refs, snapshot files, worklog.md
  diff-scope  Parse Git diff + AST/brace symbols to detect return-shape, schema-key, sig & guard changes
  impact-map  Scan repo (comments stripped) for 1-hop callers & usage risk patterns (<=25 lines)
  ab-verify   Run checks in parallel isolated Base vs Head worktrees + API schema-hash drift detection
  verify-fix  Enforce Gate 2 (fix passes + schema restored + callers & feature preserved) + export .patch
  sandbox     Manage worktree-base/head, sandbox-fix, checkpoints, diff, and clean (with worktree prune)
  log         Update worklog.md Status line + append timestamped entries atomically
  summary     Emit compact Markdown tables, pre-existing filter ledger, patch info & log snippets
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
from datetime import datetime, timezone
import difflib
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


IGNORED_SCAN_DIRS = frozenset(
    {
        ".git",
        ".bughunt",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "target",
        "bin",
        "obj",
        "dist",
        "build",
        ".gradle",
        ".idea",
        ".vs",
        "TestResults",
        "vendor",
        "coverage",
        ".next",
        ".nuxt",
        "out",
    }
)

TEST_DIR_NAMES = frozenset({"tests", "test", "__tests__", "spec", "specs", "benches", "pocs"})

SANDBOX_ALWAYS_EXCLUDE = frozenset(
    {
        ".git",
        ".bughunt",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".vs",
        "TestResults",
        ".idea",
        ".gradle",
    }
)

SNAPSHOT_FS_SKIP_DIRS = frozenset({".bughunt", ".git", "node_modules"})

EXT_LANG = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".cs": "C#",
    ".php": "PHP",
    ".rs": "Rust",
}

BINARY_EXTS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
        ".gz", ".bz2", ".xz", ".7z", ".whl", ".exe", ".dll", ".so", ".dylib",
        ".class", ".jar", ".pyc", ".pyo", ".o", ".a", ".wasm", ".ttf", ".woff",
        ".woff2", ".db", ".sqlite", ".sqlite3",
    }
)

CHECK_PREFIX = "VERIPR_CHECK:"
PAYLOAD_PREFIX = "VERIPR_PAYLOAD:"
MAX_CLI_LINES = 25


def configure_stdio() -> None:
    """Force UTF-8 stdout/stderr so Unicode logs never crash on Windows cp1252."""
    for stream in (sys.stdout, sys.stderr):
        try:
            reconf = getattr(stream, "reconfigure", None)
            if callable(reconf):
                reconf(encoding="utf-8", errors="replace")
        except OSError:
            pass


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def trunc(text: str, max_len: int = 140) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."


def sanitize_id(raw_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_id.strip()).strip("._")
    if not cleaned:
        raise SystemExit(f"[vpr.py] ERROR: Invalid id: {raw_id!r}")
    return cleaned


def resolve_root(root_arg: Optional[str]) -> Path:
    base = Path(root_arg) if root_arg else Path.cwd()
    if not base.exists() or not base.is_dir():
        raise SystemExit(f"[vpr.py] ERROR: Root directory not found: {base}")
    return base.resolve()


def resolve_session(s_arg: str, root_arg: Optional[str] = None) -> Tuple[Path, Path, str, Path]:
    p = Path(s_arg)
    if p.is_absolute():
        s_dir = p.resolve()
        root = resolve_root(root_arg) if root_arg else s_dir.parent.parent.resolve()
    else:
        root = resolve_root(root_arg)
        s_dir = (root / p).resolve()
    if not s_dir.is_dir():
        raise SystemExit(f"[vpr.py] ERROR: Session directory not found: {s_dir}")
    cache_dir = root / ".bughunt" / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return root, s_dir, s_dir.name, cache_dir


def rel_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _load_session_meta(s_dir: Path) -> Dict[str, Any]:
    meta_path = s_dir / "logs" / ".session.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_session_meta(s_dir: Path, meta: Dict[str, Any]) -> None:
    meta_path = s_dir / "logs" / ".session.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Git Helpers & Snapshot / Cleanliness
# ---------------------------------------------------------------------------


def _run_git(root: Path, git_args: Sequence[str], timeout_s: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(root), *git_args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )


def is_git_repo(root: Path) -> bool:
    if not shutil.which("git"):
        return False
    try:
        proc = _run_git(root, ["rev-parse", "--is-inside-work-tree"], timeout_s=10)
        return proc.returncode == 0 and proc.stdout.strip().lower() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _is_bughunt_git_line(line: str) -> bool:
    path_part = line[3:].strip() if len(line) > 3 else line.strip()
    if " -> " in path_part:
        path_part = path_part.split(" -> ")[-1].strip()
    path_part = path_part.strip('"').replace("\\", "/")
    parts = [p for p in path_part.split("/") if p]
    return ".bughunt" in parts


_TRUNK_BRANCH_NAMES = frozenset({"main", "master", "develop", "trunk", "HEAD", ""})
_TRUNK_CANDIDATES = ("main", "master", "origin/main", "origin/master", "develop", "origin/develop")


def _detect_base_head_refs(root: Path, base_arg: Optional[str], head_arg: Optional[str]) -> Tuple[str, str]:
    if not is_git_repo(root):
        return base_arg or "BASE", head_arg or "WORKTREE"
    has_head = _run_git(root, ["rev-parse", "--verify", "HEAD"], timeout_s=10).returncode == 0
    if base_arg and head_arg:
        return base_arg, head_arg

    dirty = False
    st_proc = _run_git(root, ["status", "--porcelain", "--", "."], timeout_s=15)
    if st_proc.returncode == 0:
        dirty = any(not _is_bughunt_git_line(ln) for ln in st_proc.stdout.splitlines() if ln.strip())

    resolved_head = head_arg or ("WORKTREE" if dirty else "HEAD")
    if base_arg:
        return base_arg, resolved_head

    # On a multi-commit feature branch, compare against merge-base with trunk instead of only HEAD~1
    if has_head:
        br_proc = _run_git(root, ["symbolic-ref", "--short", "-q", "HEAD"], timeout_s=10)
        curr_branch = br_proc.stdout.strip() if br_proc.returncode == 0 else ""
        head_sha_proc = _run_git(root, ["rev-parse", "HEAD"], timeout_s=10)
        head_sha = head_sha_proc.stdout.strip() if head_sha_proc.returncode == 0 else ""
        if curr_branch and curr_branch not in _TRUNK_BRANCH_NAMES and head_sha:
            for trunk in _TRUNK_CANDIDATES:
                if _run_git(root, ["rev-parse", "--verify", trunk], timeout_s=10).returncode != 0:
                    continue
                mb_proc = _run_git(root, ["merge-base", trunk, "HEAD"], timeout_s=10)
                mb_sha = mb_proc.stdout.strip() if mb_proc.returncode == 0 else ""
                if mb_sha and mb_sha != head_sha:
                    trunk_sha = _run_git(root, ["rev-parse", trunk], timeout_s=10).stdout.strip()
                    base_label = trunk if mb_sha == trunk_sha else mb_sha[:12]
                    return base_label, resolved_head
                break

    if dirty and has_head:
        return "HEAD", (head_arg or "WORKTREE")

    if has_head:
        parent_ok = _run_git(root, ["rev-parse", "--verify", "HEAD~1"], timeout_s=10).returncode == 0
        if parent_ok:
            return "HEAD~1", (head_arg or "HEAD")
        return "HEAD", (head_arg or "WORKTREE")

    return "HEAD", (head_arg or "WORKTREE")


def _list_untracked_non_ignored_files(root: Path) -> List[str]:
    if not is_git_repo(root):
        return []
    proc = _run_git(root, ["ls-files", "--others", "--exclude-standard"], timeout_s=15)
    if proc.returncode != 0:
        return []
    out: List[str] = []
    for raw in proc.stdout.splitlines():
        rel = raw.strip().strip('"').replace("\\", "/")
        if rel and not rel.startswith(".bughunt/") and "/.bughunt/" not in rel:
            out.append(rel)
    return sorted(set(out))


def _list_gitignored_relpaths(root: Path) -> Set[str]:
    if not is_git_repo(root):
        return set()
    proc = _run_git(root, ["status", "--porcelain", "--ignored", "--", "."], timeout_s=20)
    if proc.returncode != 0:
        return set()
    ignored: Set[str] = set()
    for ln in proc.stdout.splitlines():
        if ln.startswith("!! "):
            p = ln[3:].strip().strip('"').replace("\\", "/").rstrip("/")
            if p and not p.startswith(".bughunt"):
                ignored.add(p)
    return ignored


def _write_lf_file(path: Path, text: str) -> None:
    """Write UTF-8 text with guaranteed pure LF (\\n) line endings across Windows/POSIX."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(normalized.encode("utf-8"))


def _verify_patch_applies(base_dir: Path, patch_path: Path) -> Tuple[bool, str]:
    """Verify exported patch cleanly applies with whitespace tolerance."""
    if not patch_path.is_file() or not patch_path.read_text(encoding="utf-8", errors="replace").strip():
        return True, "empty"
    if not shutil.which("git"):
        return True, "git_unavailable"
    proc = subprocess.run(
        ["git", "apply", "--check", "--ignore-space-change", "--ignore-whitespace", str(patch_path.resolve())],
        cwd=str(base_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    if proc.returncode == 0:
        return True, "OK"
    return False, trunc(proc.stderr or proc.stdout or "git apply --check failed", 120)


def take_snapshot(root: Path, forced_mode: Optional[str] = None) -> Tuple[str, List[str]]:
    use_git = forced_mode == "git" or (forced_mode is None and is_git_repo(root))
    if use_git:
        try:
            proc = _run_git(root, ["status", "--porcelain", "--ignored", "--", "."], timeout_s=30)
            if proc.returncode == 0:
                lines = [ln.rstrip("\r\n") for ln in proc.stdout.splitlines() if ln.strip()]
                return "git", lines
        except (OSError, subprocess.SubprocessError):
            pass

    entries: List[str] = []
    root_res = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root_res):
        dirnames[:] = sorted(d for d in dirnames if d not in SNAPSHOT_FS_SKIP_DIRS)
        dp = Path(dirpath)
        for fname in sorted(filenames):
            fp = dp / fname
            try:
                st = fp.lstat()
            except OSError:
                continue
            rel = fp.relative_to(root_res).as_posix()
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
            entries.append(f"{rel}\t{mtime}")
    entries.sort()
    return "fs", entries


def diff_snapshots(before_lines: List[str], after_lines: List[str], mode: str) -> Dict[str, List[str]]:
    if mode == "git":
        b_filtered = [ln for ln in before_lines if not _is_bughunt_git_line(ln)]
        a_filtered = [ln for ln in after_lines if not _is_bughunt_git_line(ln)]
        b_set, a_set = set(b_filtered), set(a_filtered)
        return {
            "new_or_changed": [ln for ln in a_filtered if ln not in b_set],
            "resolved": [ln for ln in b_filtered if ln not in a_set],
        }

    def parse_fs(lines: Iterable[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for ln in lines:
            if "\t" in ln:
                p, ts = ln.split("\t", 1)
                if not p.startswith(".bughunt/"):
                    out[p] = ts
        return out

    b_map = parse_fs(before_lines)
    a_map = parse_fs(after_lines)
    return {
        "added": [p for p in sorted(a_map) if p not in b_map],
        "modified": [p for p in sorted(a_map) if p in b_map and a_map[p] != b_map[p]],
        "removed": [p for p in sorted(b_map) if p not in a_map],
    }


def _remove_readonly_and_rmtree(path: Path) -> None:
    def _onerror(func: Any, p: str, _exc: Any) -> None:
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except OSError:
            pass

    if not path.exists():
        return
    gc.collect()
    for attempt in range(3):
        try:
            shutil.rmtree(path, onerror=_onerror)
            if not path.exists():
                return
        except OSError:
            if attempt < 2:
                time.sleep(0.15)


def _clean_new_agent_caches(root: Path, diff: Dict[str, List[str]], snap_mode: str) -> List[str]:
    removed: List[str] = []
    if snap_mode == "git":
        candidates = []
        for ln in diff.get("new_or_changed", []):
            if len(ln) > 3 and ln[:2] in ("??", "!!"):
                candidates.append(ln[3:].strip().strip('"').replace("\\", "/"))
    else:
        candidates = diff.get("added", [])

    for rel_s in candidates:
        parts = [p for p in rel_s.split("/") if p]
        if ".bughunt" in parts:
            continue
        cache_idx = next((i for i, p in enumerate(parts) if p in ("__pycache__", ".pytest_cache")), None)
        if cache_idx is None:
            continue
        target = root / Path(*parts[: cache_idx + 1])
        if target.exists():
            if target.is_dir():
                _remove_readonly_and_rmtree(target)
            else:
                target.unlink(missing_ok=True)
            removed.append(rel_posix(target, root))
    return sorted(set(removed))


def find_prior_sessions(root: Path, current_sname: str) -> List[str]:
    bh_root = root / ".bughunt"
    if not bh_root.is_dir():
        return []
    out = [
        d.name for d in bh_root.iterdir()
        if d.is_dir() and d.name.startswith("pr-") and d.name != current_sname
    ]
    return sorted(out, reverse=True)


# ---------------------------------------------------------------------------
# Subcommand Execution & Environment Helpers
# ---------------------------------------------------------------------------


def build_isolated_env(cache_dir: Path, target_root: Path, phase: str) -> Dict[str, str]:
    env = os.environ.copy()
    pycache = cache_dir / "pycache"
    pytest_cache = cache_dir / "pytest"
    pycache.mkdir(parents=True, exist_ok=True)
    pytest_cache.mkdir(parents=True, exist_ok=True)

    env["VERIPR_TARGET_ROOT"] = str(target_root.resolve())
    env["VERIPR_PHASE"] = phase
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPYCACHEPREFIX"] = str(pycache)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTEST_ADDOPTS"] = f"{env.get('PYTEST_ADDOPTS', '')} -o cache_dir={pytest_cache}".strip()
    env["GOPROXY"] = "off"
    env["CARGO_NET_OFFLINE"] = "true"
    env["NO_COLOR"] = "1"
    env["CI"] = "1"

    # Prepend target_root to PYTHONPATH so imports inside target worktree always win over host root
    existing_pp = env.get("PYTHONPATH", "")
    parts = [str(target_root.resolve())]
    if existing_pp:
        parts.append(existing_pp)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _split_cli_args(cmd_str: str) -> List[str]:
    text = cmd_str.strip()
    if not text:
        return []
    if os.name == "nt":
        tokens: List[str] = []
        buf: List[str] = []
        quote: Optional[str] = None
        i = 0
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == "\\" and i + 1 < len(text) and text[i + 1] == quote:
                    buf.append(quote)
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                else:
                    buf.append(ch)
            else:
                if ch in ('"', "'"):
                    quote = ch
                elif ch.isspace():
                    if buf:
                        tokens.append("".join(buf))
                        buf = []
                else:
                    buf.append(ch)
            i += 1
        if buf:
            tokens.append("".join(buf))
        return tokens
    return shlex.split(text, posix=True)


def parse_cmd_tokens(raw_cmd: Sequence[str] | str) -> List[str]:
    if isinstance(raw_cmd, str):
        tokens = _split_cli_args(raw_cmd)
    else:
        tokens = list(raw_cmd)
        if tokens and tokens[0] == "--":
            tokens = tokens[1:]
        if len(tokens) == 1 and " " in tokens[0] and not Path(tokens[0]).exists():
            tokens = _split_cli_args(tokens[0])
    if not tokens:
        raise SystemExit("[vpr.py] ERROR: Empty command")
    return tokens


def _resolve_bughunt_cmd_paths(cmd_tokens: List[str], root: Path) -> List[str]:
    out: List[str] = []
    for tok in cmd_tokens:
        norm = tok.replace("\\", "/")
        if norm.startswith(".bughunt/") or norm.startswith("./.bughunt/"):
            rel = norm[2:] if norm.startswith("./") else norm
            out.append(str((root / rel).resolve()))
        else:
            out.append(tok)
    return out


def resolve_executable(cmd: List[str], cwd: Path, env: Dict[str, str]) -> List[str]:
    exe = cmd[0]
    if os.path.sep in exe or "/" in exe:
        return cmd
    if exe in ("python", "python3", "py"):
        if sys.executable and Path(sys.executable).is_file():
            return [sys.executable, *cmd[1:]]
    path_val = env.get("PATH", "")
    found = shutil.which(exe, path=path_val)
    if found:
        return [found, *cmd[1:]]
    if os.name == "nt":
        for ext in (".cmd", ".bat", ".exe"):
            found = shutil.which(exe + ext, path=path_val)
            if found:
                return [found, *cmd[1:]]
    return cmd


# ---------------------------------------------------------------------------
# Subcommand: init & log
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    base_ref, head_ref = _detect_base_head_refs(root, args.base, args.head)
    bh_dir = root / ".bughunt"
    cache_dir = bh_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    sname = f"pr-{ts}"
    s_dir = bh_dir / sname
    suffix = 1
    while s_dir.exists():
        sname = f"pr-{ts}-{suffix:02d}"
        s_dir = bh_dir / sname
        suffix += 1

    for sub in ("tests", "logs", "tmp", "patches"):
        (s_dir / sub).mkdir(parents=True, exist_ok=True)

    snap_mode, snap_lines = take_snapshot(root)
    (s_dir / "logs" / "fs-before.txt").write_text(
        ("\n".join(snap_lines) + "\n") if snap_lines else "",
        encoding="utf-8",
    )
    (s_dir / "logs" / ".snapshot-mode").write_text(snap_mode + "\n", encoding="utf-8")

    meta = {
        "SNAME": sname,
        "ROOT": str(root),
        "base_ref": base_ref,
        "head_ref": head_ref,
        "is_git": is_git_repo(root),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_session_meta(s_dir, meta)

    worklog = s_dir / "worklog.md"
    worklog.write_text(
        f"# VeriPR Worklog ({sname})\n"
        f"Status: Phase 0-1 - Init & Diff-Scope\n"
        f"BaseRef: `{base_ref}` | HeadRef: `{head_ref}`\n\n"
        f"## Entries\n"
        f"- [{datetime.now().strftime('%H:%M:%S')}] Session initialized under `.bughunt/{sname}/` "
        f"(base={base_ref}, head={head_ref}).\n",
        encoding="utf-8",
    )

    payload = {
        "S": f".bughunt/{sname}",
        "SNAME": sname,
        "CACHE": ".bughunt/.cache",
        "ROOT": str(root),
        "base_ref": base_ref,
        "head_ref": head_ref,
        "prior_sessions": find_prior_sessions(root, sname),
    }
    print(json.dumps(payload, separators=(",", ":")))
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    _, s_dir, _, _ = resolve_session(args.s, args.root)
    worklog = s_dir / "worklog.md"
    if not worklog.is_file():
        raise SystemExit(f"[vpr.py] ERROR: worklog.md not found in {s_dir}")

    text = worklog.read_text(encoding="utf-8")
    lines = text.splitlines()

    if args.status:
        replaced = False
        for i, ln in enumerate(lines):
            if ln.startswith("Status:"):
                lines[i] = f"Status: {args.status.strip()}"
                replaced = True
                break
        if not replaced:
            lines.insert(1 if lines else 0, f"Status: {args.status.strip()}")

    entries = args.entry or []
    ts = datetime.now().strftime("%H:%M:%S")
    for raw_entry in entries:
        clean_entry = " ".join(raw_entry.splitlines()).strip()
        if clean_entry:
            lines.append(f"- [{ts}] {clean_entry}")

    worklog.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    status_val = next((ln[7:].strip() for ln in lines if ln.startswith("Status:")), "")
    print(f"[vpr log] status={status_val!r} added={len(entries)} entries")
    return 0


# ---------------------------------------------------------------------------
# Symbol & Semantic Diff Analysis (diff-scope)
# ---------------------------------------------------------------------------


@dataclass
class SymbolInfo:
    qualname: str
    kind: str
    start_line: int
    end_line: int
    params: List[str] = field(default_factory=list)
    defaults_sig: str = ""
    return_kinds: Set[str] = field(default_factory=set)
    dict_keys: Set[str] = field(default_factory=set)
    guard_count: int = 0


def _infer_py_expr_kind(node: Optional[ast.AST]) -> str:
    if node is None:
        return "none"
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "none"
        if isinstance(node.value, bool):
            return "bool"
        if isinstance(node.value, (int, float)):
            return "number"
        if isinstance(node.value, str):
            return "string"
        return type(node.value).__name__
    if isinstance(node, (ast.Dict, ast.DictComp)):
        return "dict"
    if isinstance(node, (ast.List, ast.ListComp)):
        return "list"
    if isinstance(node, ast.Tuple):
        return "tuple"
    if isinstance(node, (ast.Set, ast.SetComp)):
        return "set"
    if isinstance(node, ast.BinOp):
        if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            return "string"
        if isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            return "string"
        return "number"
    if isinstance(node, ast.JoinedStr):
        return "string"
    if isinstance(node, ast.Compare):
        return "bool"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = node.func.id
        if fn in ("round", "int", "float", "abs", "sum", "len"):
            return "number"
        if fn in ("dict",):
            return "dict"
        if fn in ("list", "sorted"):
            return "list"
        if fn in ("str",):
            return "string"
        if fn in ("bool",):
            return "bool"
    return "expr"


def _extract_py_dict_keys(node: ast.AST) -> Set[str]:
    keys: Set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            for k in sub.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def _parse_python_symbols(src: str) -> Dict[str, SymbolInfo]:
    out: Dict[str, SymbolInfo] = {}
    if not src.strip():
        return out
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out

    def visit_body(stmts: Sequence[ast.stmt], class_prefix: str = "") -> None:
        for node in stmts:
            if isinstance(node, ast.ClassDef):
                cname = f"{class_prefix}.{node.name}" if class_prefix else node.name
                class_keys: Set[str] = set()
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        class_keys.add(item.target.id)
                out[cname] = SymbolInfo(
                    qualname=cname,
                    kind="class",
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    dict_keys=class_keys,
                )
                visit_body(node.body, cname)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{class_prefix}.{node.name}" if class_prefix else node.name
                args = node.args
                pos_names = [a.arg for a in getattr(args, "posonlyargs", [])] + [a.arg for a in args.args]
                kw_names = [a.arg for a in args.kwonlyargs]
                all_params = pos_names + ([f"*{args.vararg.arg}"] if args.vararg else []) + kw_names + ([f"**{args.kwarg.arg}"] if args.kwarg else [])
                n_def = len(args.defaults)
                req_count = max(0, len(pos_names) - n_def)
                kw_req = sum(1 for d in args.kw_defaults if d is None)
                def_sig = f"req={req_count}+{kw_req};defaults={len(args.defaults)}+{len([d for d in args.kw_defaults if d is not None])}"

                ret_kinds: Set[str] = set()
                dict_keys: Set[str] = set()
                guards = 0

                class _FnWalker(ast.NodeVisitor):
                    def visit_FunctionDef(self, _n: ast.FunctionDef) -> None:
                        return  # skip nested functions for parent summary

                    def visit_AsyncFunctionDef(self, _n: ast.AsyncFunctionDef) -> None:
                        return

                    def visit_ClassDef(self, _n: ast.ClassDef) -> None:
                        return

                    def visit_Return(self, rnode: ast.Return) -> None:
                        ret_kinds.add(_infer_py_expr_kind(rnode.value))
                        if rnode.value is not None:
                            dict_keys.update(_extract_py_dict_keys(rnode.value))

                    def visit_Dict(self, dnode: ast.Dict) -> None:
                        for k in dnode.keys:
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                dict_keys.add(k.value)
                        self.generic_visit(dnode)

                    def visit_Try(self, tnode: ast.Try) -> None:
                        nonlocal guards
                        guards += len(tnode.handlers) or 1
                        self.generic_visit(tnode)

                    def visit_Raise(self, rnode: ast.Raise) -> None:
                        nonlocal guards
                        guards += 1
                        self.generic_visit(rnode)

                walker = _FnWalker()
                for child in node.body:
                    walker.visit(child)

                out[qname] = SymbolInfo(
                    qualname=qname,
                    kind="function",
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    params=all_params,
                    defaults_sig=def_sig,
                    return_kinds=ret_kinds,
                    dict_keys=dict_keys,
                    guard_count=guards,
                )

    visit_body(tree.body)
    return out


_BRACE_FN_PATTERNS = (
    re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>"),
    re.compile(r"^\s*func\s+(?:\([^)]+\)\s*)?([A-Za-z_]\w*)\s*\(([^)]*)\)"),
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)\s*\(([^)]*)\)"),
    re.compile(r"^\s*(?:public|private|protected|internal|static|async|\s)+[\w<>\[\]?,\s]+\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?:\{|where|throws|$)"),
)


def _strip_brace_comments_preserve_lines(src: str) -> str:
    src = re.sub(r"/\*[\s\S]*?\*/", lambda m: "\n" * m.group(0).count("\n"), src)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def _parse_brace_symbols(src: str) -> Dict[str, SymbolInfo]:
    out: Dict[str, SymbolInfo] = {}
    if not src.strip():
        return out
    clean = _strip_brace_comments_preserve_lines(src)
    lines = clean.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        matched_name: Optional[str] = None
        matched_params: List[str] = []
        for pat in _BRACE_FN_PATTERNS:
            m = pat.match(ln)
            if m:
                name = m.group(1)
                if name in ("if", "for", "while", "switch", "catch", "return", "sizeof"):
                    continue
                matched_name = name
                raw_p = m.group(2).strip()
                matched_params = [p.strip() for p in raw_p.split(",") if p.strip()] if raw_p else []
                break
        if matched_name is None:
            i += 1
            continue

        start = i + 1
        depth = 0
        seen_brace = False
        end = start
        for j in range(i, len(lines)):
            opens = lines[j].count("{")
            closes = lines[j].count("}")
            if opens:
                seen_brace = True
            depth += opens - closes
            end = j + 1
            if seen_brace and depth <= 0:
                break

        body_text = "\n".join(lines[i:end])
        ret_kinds: Set[str] = set()
        if re.search(r"\breturn\s*(?:null|undefined|nil|None)\b|\breturn\s*;", body_text):
            ret_kinds.add("none")
        if re.search(r"\breturn\s+\{", body_text):
            ret_kinds.add("dict")
        if re.search(r"\breturn\s+[-+]?\d", body_text):
            ret_kinds.add("number")
        if re.search(r"\breturn\s+[\"']", body_text):
            ret_kinds.add("string")

        dict_keys = set(re.findall(r"""["']([A-Za-z_][A-Za-z0-9_-]{1,40})["']\s*:""", body_text))
        dict_keys.update(re.findall(r"""(?:^|[{,\s])([A-Za-z_][A-Za-z0-9_]{1,40})\s*:\s*[^:\n]""", body_text))
        guards = len(re.findall(r"\b(?:try\s*\{|catch\s*\(|if\s+err\s*!=\s*nil|throw\b)", body_text))

        out[matched_name] = SymbolInfo(
            qualname=matched_name,
            kind="function",
            start_line=start,
            end_line=end,
            params=matched_params,
            defaults_sig=f"argc={len(matched_params)}",
            return_kinds=ret_kinds,
            dict_keys=dict_keys,
            guard_count=guards,
        )
        i = max(i + 1, end)
    return out


def _parse_symbols_for_file(rel_path: str, src: str) -> Dict[str, SymbolInfo]:
    if Path(rel_path).suffix.lower() == ".py":
        return _parse_python_symbols(src)
    return _parse_brace_symbols(src)


def _read_git_file_text(root: Path, ref: str, rel_posix_path: str) -> str:
    if ref == "WORKTREE":
        fp = root / rel_posix_path
        if not fp.is_file():
            return ""
        return fp.read_text(encoding="utf-8", errors="replace")
    proc = _run_git(root, ["show", f"{ref}:{rel_posix_path}"], timeout_s=15)
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _parse_git_hunks(diff_text: str) -> Dict[str, List[Tuple[int, int, int, int]]]:
    """Return {rel_path: [(old_start, old_cnt, new_start, new_cnt), ...]}."""
    hunks: Dict[str, List[Tuple[int, int, int, int]]] = {}
    curr_file: Optional[str] = None
    for ln in diff_text.splitlines():
        if ln.startswith("+++ "):
            p = ln[4:].strip().strip('"')
            if p == "/dev/null":
                curr_file = None
            else:
                curr_file = p[2:] if p.startswith("b/") else p
                hunks.setdefault(curr_file, [])
        elif ln.startswith("--- ") and curr_file is None:
            p = ln[4:].strip().strip('"')
            if p != "/dev/null":
                curr_file = p[2:] if p.startswith("a/") else p
                hunks.setdefault(curr_file, [])
        elif ln.startswith("@@ ") and curr_file is not None:
            m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", ln)
            if m:
                os_ = int(m.group(1))
                oc = int(m.group(2)) if m.group(2) is not None else 1
                ns = int(m.group(3))
                nc = int(m.group(4)) if m.group(4) is not None else 1
                hunks[curr_file].append((os_, oc, ns, nc))
    return hunks


def _ranges_overlap(a1: int, a2: int, b1: int, b2: int) -> bool:
    if b2 < b1:
        b2 = b1
    return not (a2 < b1 or b2 < a1)


def _compute_symbol_risk_tags(
    base_sym: Optional[SymbolInfo], head_sym: Optional[SymbolInfo]
) -> Tuple[List[str], Dict[str, Any]]:
    tags: List[str] = []
    meta: Dict[str, Any] = {}
    if base_sym is None or head_sym is None:
        if base_sym is not None and head_sym is None:
            tags.append("SYMBOL_REMOVED")
        elif base_sym is None and head_sym is not None:
            tags.append("NEW_SYMBOL")
        return tags, meta

    b_ret = base_sym.return_kinds
    h_ret = head_sym.return_kinds
    if "none" in h_ret and "none" not in b_ret and bool(b_ret):
        tags.append("RETURN_SHAPE_CHANGED")
        meta["return_detail"] = f"{sorted(b_ret)} -> {sorted(h_ret)}"
    elif b_ret and h_ret and ("dict" in b_ret) != ("dict" in h_ret):
        tags.append("RETURN_SHAPE_CHANGED")
        meta["return_detail"] = f"{sorted(b_ret)} -> {sorted(h_ret)}"

    removed_keys = sorted(base_sym.dict_keys - head_sym.dict_keys)
    added_keys = sorted(head_sym.dict_keys - base_sym.dict_keys)
    if removed_keys:
        tags.append("CONTRACT_KEY_RENAMED_OR_DROPPED")
        meta["removed_keys"] = removed_keys
        meta["added_keys"] = added_keys

    if base_sym.params != head_sym.params or base_sym.defaults_sig != head_sym.defaults_sig:
        tags.append("SIGNATURE_CHANGED")
        meta["sig_before"] = base_sym.params
        meta["sig_after"] = head_sym.params

    if base_sym.guard_count > head_sym.guard_count:
        tags.append("ERROR_GUARD_REMOVED")
        meta["guards_delta"] = f"{base_sym.guard_count}->{head_sym.guard_count}"

    return tags, meta


def cmd_diff_scope(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    s_dir: Optional[Path] = None
    session_meta: Dict[str, Any] = {}
    if args.s:
        root, s_dir, _, _ = resolve_session(args.s, args.root)
        session_meta = _load_session_meta(s_dir)

    base_ref = args.base or session_meta.get("base_ref") or "HEAD"
    head_ref = args.head or session_meta.get("head_ref") or "WORKTREE"
    if not args.base and not args.head and not session_meta:
        base_ref, head_ref = _detect_base_head_refs(root, None, None)

    changed_files: List[Tuple[str, str]] = []
    hunk_map: Dict[str, List[Tuple[int, int, int, int]]] = {}

    if is_git_repo(root):
        diff_args = ["diff", "--name-status", base_ref]
        udiff_args = ["diff", "--unified=0", base_ref]
        if head_ref != "WORKTREE":
            diff_args.append(head_ref)
            udiff_args.append(head_ref)
        ns_proc = _run_git(root, diff_args, timeout_s=20)
        if ns_proc.returncode == 0:
            for ln in ns_proc.stdout.splitlines():
                parts = ln.strip().split("\t")
                if len(parts) >= 2:
                    st_code = parts[0][0]
                    rel_f = parts[-1].replace("\\", "/")
                    if not rel_f.startswith(".bughunt/") and "/.bughunt/" not in rel_f:
                        changed_files.append((st_code, rel_f))

        if head_ref in ("WORKTREE", "HEAD"):
            existing = {f for _, f in changed_files}
            for rel_f in _list_untracked_non_ignored_files(root):
                if rel_f not in existing:
                    changed_files.append(("A", rel_f))

        ud_proc = _run_git(root, udiff_args, timeout_s=20)
        if ud_proc.returncode == 0:
            hunk_map = _parse_git_hunks(ud_proc.stdout)

    symbol_records: List[Dict[str, Any]] = []
    for st_code, rel_f in sorted(changed_files, key=lambda x: x[1]):
        ext = Path(rel_f).suffix.lower()
        if ext not in EXT_LANG:
            continue
        base_txt = "" if st_code == "A" else _read_git_file_text(root, base_ref, rel_f)
        head_txt = "" if st_code == "D" else _read_git_file_text(root, head_ref, rel_f)
        base_syms = _parse_symbols_for_file(rel_f, base_txt)
        head_syms = _parse_symbols_for_file(rel_f, head_txt)
        file_hunks = hunk_map.get(rel_f, [])

        all_names = sorted(set(base_syms) | set(head_syms))
        for sym_name in all_names:
            bsym = base_syms.get(sym_name)
            hsym = head_syms.get(sym_name)
            if bsym and bsym.kind == "class" and any(n.startswith(f"{sym_name}.") for n in all_names):
                continue  # inspect methods individually unless class body directly altered
            touched = False
            if bsym is None or hsym is None:
                touched = True
            elif file_hunks:
                for os_, oc, ns, nc in file_hunks:
                    old_end = os_ + max(0, oc - 1)
                    new_end = ns + max(0, nc - 1)
                    if (hsym and _ranges_overlap(hsym.start_line, hsym.end_line, ns, new_end)) or (
                        bsym and _ranges_overlap(bsym.start_line, bsym.end_line, os_, old_end)
                    ):
                        touched = True
                        break
            elif base_txt != head_txt:
                touched = True

            if not touched:
                continue

            tags, meta = _compute_symbol_risk_tags(bsym, hsym)
            # Prioritize production semantic breaking tags over plain edits & test files
            if _is_test_relpath(rel_f):
                prio = 0
            elif any(t in ("CONTRACT_KEY_RENAMED_OR_DROPPED", "RETURN_SHAPE_CHANGED", "SIGNATURE_CHANGED") for t in tags):
                prio = 3
            elif "ERROR_GUARD_REMOVED" in tags:
                prio = 2
            else:
                prio = 1
            loc_line = hsym.start_line if hsym else (bsym.start_line if bsym else 1)
            symbol_records.append(
                {
                    "symbol": sym_name,
                    "short_name": sym_name.split(".")[-1],
                    "file": rel_f,
                    "line": loc_line,
                    "lang": EXT_LANG[ext],
                    "status": st_code,
                    "risk_tags": tags or ["LOGIC_CHANGED"],
                    "meta": meta,
                    "priority": prio,
                }
            )

    symbol_records.sort(key=lambda r: (-r["priority"], r["file"], r["line"]))
    payload = {
        "base_ref": base_ref,
        "head_ref": head_ref,
        "changed_files": [{"status": st, "file": f} for st, f in changed_files],
        "changed_symbols": symbol_records,
    }
    if s_dir is not None:
        (s_dir / "logs" / "diff_scope.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # Emit compact <= 25-line digest
    out_lines: List[str] = [
        f"[vpr diff-scope] base={base_ref} head={head_ref} files={len(changed_files)} symbols={len(symbol_records)}",
        "Symbol | File:Line | RiskTags | SemanticDelta",
    ]
    max_rows = MAX_CLI_LINES - 3  # room for header + table header + overflow line
    for rec in symbol_records[:max_rows]:
        tags_s = ",".join(rec["risk_tags"])
        meta = rec["meta"]
        delta_parts: List[str] = []
        if meta.get("return_detail"):
            delta_parts.append(f"ret:{meta['return_detail']}")
        if meta.get("removed_keys"):
            delta_parts.append(f"dropped_keys={meta['removed_keys']} added={meta.get('added_keys', [])}")
        if meta.get("guards_delta"):
            delta_parts.append(f"guards:{meta['guards_delta']}")
        if meta.get("sig_before") != meta.get("sig_after") and meta.get("sig_after") is not None:
            delta_parts.append(f"params:{len(meta.get('sig_before', []))}->{len(meta.get('sig_after', []))}")
        delta_s = trunc("; ".join(delta_parts) or "body updated", 70)
        out_lines.append(f"{rec['symbol']} | {rec['file']}:{rec['line']} | {tags_s} | {delta_s}")

    if len(symbol_records) > max_rows:
        out_lines.append(f"... (+{len(symbol_records) - max_rows} more symbols in logs/diff_scope.json)")
    elif not symbol_records:
        out_lines.append("(no code symbol changes detected between base and head)")

    print("\n".join(out_lines[:MAX_CLI_LINES]))
    return 0


# ---------------------------------------------------------------------------
# 1-Hop Caller Impact Mapper (impact-map)
# ---------------------------------------------------------------------------


def _is_test_relpath(rel_posix_path: str) -> bool:
    parts = [p.lower() for p in rel_posix_path.split("/")]
    if any(p in TEST_DIR_NAMES for p in parts[:-1]):
        return True
    name = parts[-1]
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_test.go")
        or ".test." in name
        or ".spec." in name
    )


def _strip_python_comments_and_docstrings(src: str) -> str:
    """Only used to confirm AST/text lookups ignore docstrings cleanly."""
    return src


def _extract_py_caller_usages(
    rel_file: str,
    src: str,
    target_map: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return hits

    # Track imported symbol aliases from changed modules
    imported_aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                orig = alias.name
                asname = alias.asname or orig
                if orig in target_map:
                    imported_aliases[asname] = orig

    lines = src.splitlines()

    def _iter_scope_nodes(root_node: ast.AST, skip_defs: bool) -> Iterable[ast.AST]:
        stack: List[ast.AST] = [root_node]
        while stack:
            curr = stack.pop()
            yield curr
            for child in ast.iter_child_nodes(curr):
                if skip_defs and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                stack.append(child)

    def scan_function(fn_node: Optional[ast.AST], caller_name: str, start_l: int) -> None:
        target_vars: Dict[str, str] = {}
        call_lines: List[Tuple[str, int, str]] = []
        search_root = fn_node if fn_node is not None else tree
        skip_defs = fn_node is None

        for sub in _iter_scope_nodes(search_root, skip_defs=skip_defs):
            if isinstance(sub, ast.Assign):
                if isinstance(sub.value, ast.Call):
                    cname = _py_called_name(sub.value.func)
                    canon = imported_aliases.get(cname, cname)
                    if canon in target_map:
                        for tgt in sub.targets:
                            if isinstance(tgt, ast.Name):
                                target_vars[tgt.id] = canon
            elif isinstance(sub, ast.AnnAssign) and sub.value is not None:
                if isinstance(sub.value, ast.Call) and isinstance(sub.target, ast.Name):
                    cname = _py_called_name(sub.value.func)
                    canon = imported_aliases.get(cname, cname)
                    if canon in target_map:
                        target_vars[sub.target.id] = canon
            elif isinstance(sub, ast.Call):
                cname = _py_called_name(sub.func)
                canon = imported_aliases.get(cname, cname)
                if canon in target_map:
                    # Skip calling self inside exact defining file & function
                    same_self = any(
                        t["file"] == rel_file and t["symbol"].split(".")[-1] == caller_name.split(".")[-1]
                        for t in target_map[canon]
                    )
                    if not same_self:
                        call_lines.append((canon, sub.lineno, lines[sub.lineno - 1].strip() if sub.lineno <= len(lines) else ""))

        if not call_lines:
            return

        # Inspect how target return variables are used in the caller
        usage_patterns: Dict[str, Set[str]] = {}
        for sub in _iter_scope_nodes(search_root, skip_defs=skip_defs):
            if isinstance(sub, ast.BinOp) and (
                isinstance(sub.left, ast.Name) and sub.left.id in target_vars
                or isinstance(sub.right, ast.Name) and sub.right.id in target_vars
            ):
                var = sub.left.id if isinstance(sub.left, ast.Name) and sub.left.id in target_vars else sub.right.id  # type: ignore[union-attr]
                usage_patterns.setdefault(target_vars[var], set()).add("arithmetic_op")
            elif isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) and sub.value.id in target_vars:
                usage_patterns.setdefault(target_vars[sub.value.id], set()).add("subscript_access")
            elif isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id in target_vars:
                usage_patterns.setdefault(target_vars[sub.value.id], set()).add(f"attr(.{sub.attr})")

        seen_pairs: Set[Tuple[str, str]] = set()
        for canon, clno, snippet in call_lines:
            pair = (canon, caller_name)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            tgt_entries = target_map[canon]
            tgt_tags = sorted({tag for t in tgt_entries for tag in t.get("risk_tags", [])})
            usages = sorted(usage_patterns.get(canon, set()))
            risk = "MED"
            if any(t in ("RETURN_SHAPE_CHANGED", "CONTRACT_KEY_RENAMED_OR_DROPPED", "SIGNATURE_CHANGED") for t in tgt_tags):
                risk = "HIGH"
            elif usages:
                risk = "HIGH"
            hits.append(
                {
                    "caller_symbol": caller_name,
                    "caller_file": rel_file,
                    "caller_line": clno,
                    "calls_target": canon,
                    "target_risk_tags": tgt_tags,
                    "usage": usages or ["direct_call"],
                    "snippet": trunc(snippet, 75),
                    "risk": risk,
                }
            )

    def walk_defs(stmts: Sequence[ast.stmt], prefix: str = "") -> None:
        for node in stmts:
            if isinstance(node, ast.ClassDef):
                cname = f"{prefix}.{node.name}" if prefix else node.name
                walk_defs(node.body, cname)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}.{node.name}" if prefix else node.name
                scan_function(node, qname, node.lineno)

    walk_defs(tree.body)
    scan_function(None, "<module>", 1)
    return hits


def _py_called_name(func_node: ast.AST) -> str:
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return ""


def _extract_brace_caller_usages(
    rel_file: str,
    src: str,
    target_map: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    clean = _strip_brace_comments_preserve_lines(src)
    syms = _parse_brace_symbols(clean)
    lines = clean.splitlines()
    raw_lines = src.splitlines()
    if not target_map:
        return hits
    pat = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(target_map, key=len, reverse=True)) + r")\s*\(")

    def find_enclosing(lineno: int) -> str:
        for sname, sinfo in syms.items():
            if sinfo.start_line <= lineno <= sinfo.end_line:
                return sname
        return "<module>"

    seen: Set[Tuple[str, str]] = set()
    for idx, ln in enumerate(lines, start=1):
        for m in pat.finditer(ln):
            target = m.group(1)
            caller = find_enclosing(idx)
            # Ignore declaration header line
            if caller == target and idx == syms.get(caller, SymbolInfo("", "", 0, 0)).start_line:
                continue
            if (target, caller) in seen:
                continue
            seen.add((target, caller))
            tgt_tags = sorted({tag for t in target_map[target] for tag in t.get("risk_tags", [])})
            risk = "HIGH" if any(t in ("RETURN_SHAPE_CHANGED", "CONTRACT_KEY_RENAMED_OR_DROPPED", "SIGNATURE_CHANGED") for t in tgt_tags) else "MED"
            hits.append(
                {
                    "caller_symbol": caller,
                    "caller_file": rel_file,
                    "caller_line": idx,
                    "calls_target": target,
                    "target_risk_tags": tgt_tags,
                    "usage": ["direct_call"],
                    "snippet": trunc(raw_lines[idx - 1].strip(), 75),
                    "risk": risk,
                }
            )
    return hits


def cmd_impact_map(args: argparse.Namespace) -> int:
    root = resolve_root(args.root)
    s_dir: Optional[Path] = None
    if args.s:
        root, s_dir, _, _ = resolve_session(args.s, args.root)

    target_entries: List[Dict[str, Any]] = []
    if args.symbols:
        for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
            target_entries.append({"symbol": sym, "short_name": sym.split(".")[-1], "file": "", "risk_tags": ["MANUAL_SCOPE"]})
    elif s_dir is not None and (s_dir / "logs" / "diff_scope.json").is_file():
        ds = json.loads((s_dir / "logs" / "diff_scope.json").read_text(encoding="utf-8"))
        target_entries = ds.get("changed_symbols", [])

    target_map: Dict[str, List[Dict[str, Any]]] = {}
    for entry in target_entries:
        if entry.get("file") and _is_test_relpath(entry["file"]):
            continue
        short = entry.get("short_name") or entry["symbol"].split(".")[-1]
        if short in ("__init__", "main"):
            continue
        target_map.setdefault(short, []).append(entry)

    # Walk repository files
    prod_files: List[Tuple[str, Path]] = []
    test_corpus: List[Tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_SCAN_DIRS)
        for fname in sorted(filenames):
            fp = dp / fname
            if fp.suffix.lower() not in EXT_LANG:
                continue
            rel = rel_posix(fp, root)
            if _is_test_relpath(rel):
                try:
                    test_corpus.append((rel, fp.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
            else:
                prod_files.append((rel, fp))

    all_hits: List[Dict[str, Any]] = []
    if target_map:
        for rel, fp in prod_files:
            try:
                src = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if fp.suffix.lower() == ".py":
                hits = _extract_py_caller_usages(rel, src, target_map)
            else:
                hits = _extract_brace_caller_usages(rel, src, target_map)
            all_hits.extend(hits)

    # Check if each 1-hop caller has direct test coverage
    for hit in all_hits:
        csym = hit["caller_symbol"].split(".")[-1]
        if csym == "<module>":
            mod_stem = Path(hit["caller_file"]).stem
            tested = any(re.search(rf"\b{re.escape(mod_stem)}\b", txt) for _, txt in test_corpus)
        else:
            tested = any(re.search(rf"\b{re.escape(csym)}\b", txt) for _, txt in test_corpus)
        hit["tested"] = "YES" if tested else "NO"
        # Untested HIGH risk callers rank first!
        hit["_sort_key"] = (
            0 if hit["risk"] == "HIGH" else 1,
            0 if hit["tested"] == "NO" else 1,
            hit["caller_file"],
            hit["caller_line"],
        )

    all_hits.sort(key=lambda h: h["_sort_key"])
    for h in all_hits:
        h.pop("_sort_key", None)

    if s_dir is not None:
        (s_dir / "logs" / "impact_map.json").write_text(
            json.dumps({"targets": sorted(target_map), "callers": all_hits}, indent=2) + "\n",
            encoding="utf-8",
        )

    max_lines = min(max(5, int(args.max_lines or MAX_CLI_LINES)), MAX_CLI_LINES)
    out_lines: List[str] = [
        f"[vpr impact-map] targets={len(target_map)} 1hop_callers={len(all_hits)} max_lines={max_lines}",
        "Risk | Tested | Caller (File:Line) | CallsTarget | Usage / RiskTags",
    ]
    max_rows = max_lines - 3
    for h in all_hits[:max_rows]:
        usage_s = ",".join(h["usage"])
        tags_s = ",".join(h["target_risk_tags"])
        out_lines.append(
            f"{h['risk']} | {h['tested']} | {h['caller_symbol']} ({h['caller_file']}:{h['caller_line']}) | "
            f"-> {h['calls_target']} | {usage_s} [{tags_s}]"
        )
    if len(all_hits) > max_rows:
        out_lines.append(f"... (+{len(all_hits) - max_rows} more callers in logs/impact_map.json)")
    elif not all_hits:
        out_lines.append("(no 1-hop external callers detected for modified symbols)")

    print("\n".join(out_lines[:max_lines]))
    return 0


# ---------------------------------------------------------------------------
# Recursive API Schema Signature & Drift Comparison
# ---------------------------------------------------------------------------


def _merge_elem_list(elems: List[Any]) -> List[Any]:
    dict_elems = [e for e in elems if isinstance(e, dict)]
    other_elems = [e for e in elems if not isinstance(e, dict)]
    merged_list: List[Any] = []
    if dict_elems:
        acc = dict_elems[0]
        for nxt in dict_elems[1:]:
            acc = merge_schema_signatures(acc, nxt)
        merged_list.append(acc)
    seen: Dict[str, Any] = {}
    for item in other_elems:
        key = json.dumps(item, sort_keys=True, separators=(",", ":"))
        seen[key] = item
    for k in sorted(seen):
        merged_list.append(seen[k])
    return merged_list


def merge_schema_signatures(sig_a: Any, sig_b: Any) -> Any:
    """Merge two schema signatures (e.g. empty list + populated list, or nullable + concrete type)."""
    if sig_a == sig_b:
        return sig_a
    if sig_a == "null" and sig_b is not None:
        return sig_b
    if sig_b == "null" and sig_a is not None:
        return sig_a
    if sig_a == ["empty"] and isinstance(sig_b, list) and len(sig_b) == 2 and sig_b[0] == "array":
        return sig_b
    if sig_b == ["empty"] and isinstance(sig_a, list) and len(sig_a) == 2 and sig_a[0] == "array":
        return sig_a
    if isinstance(sig_a, dict) and isinstance(sig_b, dict):
        merged: Dict[str, Any] = {}
        for k in sorted(set(sig_a) | set(sig_b)):
            if k in sig_a and k in sig_b:
                merged[k] = merge_schema_signatures(sig_a[k], sig_b[k])
            elif k in sig_a:
                merged[k] = sig_a[k]
            else:
                merged[k] = sig_b[k]
        return merged
    if (
        isinstance(sig_a, list) and len(sig_a) == 2 and sig_a[0] == "array"
        and isinstance(sig_b, list) and len(sig_b) == 2 and sig_b[0] == "array"
    ):
        return ["array", _merge_elem_list(list(sig_a[1]) + list(sig_b[1]))]
    if isinstance(sig_a, list) and len(sig_a) == 2 and sig_a[0] == "union":
        parts = list(sig_a[1])
    else:
        parts = [sig_a]
    if isinstance(sig_b, list) and len(sig_b) == 2 and sig_b[0] == "union":
        parts.extend(sig_b[1])
    else:
        parts.append(sig_b)
    uniq = {json.dumps(p, sort_keys=True, separators=(",", ":")): p for p in parts}
    if len(uniq) == 1:
        return next(iter(uniq.values()))
    return ["union", [uniq[k] for k in sorted(uniq)]]


def compute_schema_signature(obj: Any) -> Any:
    """Compute canonical structure/type tree while ignoring dynamic values of same type."""
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "bool"
    if isinstance(obj, (int, float)):
        return "number"
    if isinstance(obj, str):
        return "string"
    if isinstance(obj, dict):
        return {str(k): compute_schema_signature(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        if not obj:
            return ["empty"]
        elem_sigs = [compute_schema_signature(item) for item in obj]
        return ["array", _merge_elem_list(elem_sigs)]
    return type(obj).__name__


def schema_hash(sig: Any) -> str:
    canonical = json.dumps(sig, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compare_schemas(base_sig: Any, head_sig: Any, path: str = "$") -> Dict[str, Any]:
    """Compare Base vs Head schema signatures and classify breaking vs additive changes."""
    missing_keys: List[str] = []
    added_keys: List[str] = []
    type_changes: List[str] = []
    renamed_candidates: List[str] = []

    def _walk(b: Any, h: Any, p: str) -> None:
        if isinstance(b, dict) and isinstance(h, dict):
            b_keys = set(b.keys())
            h_keys = set(h.keys())
            miss = sorted(b_keys - h_keys)
            add = sorted(h_keys - b_keys)
            for k in miss:
                missing_keys.append(f"{p}.{k}")
            for k in add:
                added_keys.append(f"{p}.{k}")
            if miss and add:
                for mk, ak in zip(miss, add):
                    renamed_candidates.append(f"{p}: {mk} -> {ak}")
            for k in sorted(b_keys & h_keys):
                _walk(b[k], h[k], f"{p}.{k}")
            return

        if isinstance(b, list) and isinstance(h, list):
            if b == ["empty"] or h == ["empty"]:
                return
            if len(b) == 2 and len(h) == 2 and b[0] == "array" and h[0] == "array":
                b_elems = b[1]
                h_elems = h[1]
                if len(b_elems) == 1 and len(h_elems) == 1:
                    _walk(b_elems[0], h_elems[0], f"{p}[]")
                elif b_elems != h_elems:
                    type_changes.append(f"{p}[]: array item schema changed")
            elif b != h:
                type_changes.append(f"{p}: {b} -> {h}")
            return

        if b != h:
            type_changes.append(f"{p}: {b} -> {h}")

    _walk(base_sig, head_sig, path)
    breaking = bool(missing_keys or type_changes)
    return {
        "breaking": breaking,
        "missing_keys": missing_keys,
        "added_keys": added_keys,
        "type_changes": type_changes,
        "renamed_candidates": renamed_candidates,
    }


# ---------------------------------------------------------------------------
# Worktree & Sandbox Management
# ---------------------------------------------------------------------------


def _compute_sandbox_exclusions(root: Path, extra: Sequence[str]) -> Tuple[set[str], set[str]]:
    global_excl = set(SANDBOX_ALWAYS_EXCLUDE)
    root_excl: set[str] = set()
    if (root / "Cargo.toml").is_file():
        root_excl.add("target")
    if (root / "pom.xml").is_file() or any((root / f).is_file() for f in ("build.gradle", "build.gradle.kts")):
        root_excl.update({"target", "build", "out"})
    if any(root.glob("*.sln")) or any(root.glob("*.csproj")):
        global_excl.update({"bin", "obj"})
    for item in extra:
        c = item.strip().strip("/\\")
        if c and c != "vendor":
            global_excl.add(c)
    global_excl.discard("vendor")
    root_excl.discard("vendor")
    return global_excl, root_excl


def _copy_project_tree(
    root: Path,
    dest: Path,
    global_excl: set[str],
    root_excl: set[str],
    ignored_rels: Optional[Set[str]] = None,
) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    root_res = root.resolve()
    ign = ignored_rels or set()
    for dirpath, dirnames, filenames in os.walk(root_res):
        dp = Path(dirpath)
        rel_dir = dp.relative_to(root_res)
        at_root = rel_dir == Path(".")
        kept_dirs: List[str] = []
        for d in sorted(dirnames):
            if d in global_excl or (at_root and d in root_excl):
                continue
            if (dp / d).is_symlink() and not (dp / d).exists():
                continue
            rel_d = d if at_root else f"{rel_dir.as_posix()}/{d}"
            if rel_d in ign:
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs
        out_dir = dest if at_root else (dest / rel_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for fname in sorted(filenames):
            rel_f = fname if at_root else f"{rel_dir.as_posix()}/{fname}"
            if rel_f in ign:
                continue
            src_f = dp / fname
            dst_f = out_dir / fname
            try:
                if src_f.is_symlink() and not src_f.exists():
                    continue
                shutil.copy2(src_f, dst_f, follow_symlinks=True)
                copied += 1
            except OSError:
                continue
    return copied


def _copy_untracked_into_dest(root: Path, dest: Path) -> int:
    copied = 0
    for rel_f in _list_untracked_non_ignored_files(root):
        src_f = root / rel_f
        if not src_f.is_file():
            continue
        dst_f = dest / rel_f
        dst_f.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_f, dst_f, follow_symlinks=True)
        copied += 1
    return copied


def _create_git_worktree_or_copy(
    root: Path,
    ref: str,
    dest: Path,
    include_untracked: bool = False,
) -> str:
    if dest.exists():
        _remove_worktree_dir(root, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ref == "WORKTREE" or not is_git_repo(root):
        global_excl, root_excl = _compute_sandbox_exclusions(root, [])
        ignored = _list_gitignored_relpaths(root) if is_git_repo(root) else set()
        _copy_project_tree(root, dest, global_excl, root_excl, ignored_rels=ignored)
        if include_untracked and is_git_repo(root):
            _copy_untracked_into_dest(root, dest)
        return "copy"

    proc = _run_git(root, ["worktree", "add", "--detach", "--force", str(dest), ref], timeout_s=45)
    if proc.returncode == 0:
        if include_untracked:
            _copy_untracked_into_dest(root, dest)
        return "git_worktree"

    # Fallback: materialize tree via git archive / ls-tree
    dest.mkdir(parents=True, exist_ok=True)
    ls_proc = _run_git(root, ["ls-tree", "-r", "--name-only", ref], timeout_s=20)
    if ls_proc.returncode == 0:
        for rel_f in ls_proc.stdout.splitlines():
            rel_f = rel_f.strip()
            if not rel_f:
                continue
            out_f = dest / rel_f
            out_f.parent.mkdir(parents=True, exist_ok=True)
            b_proc = subprocess.run(
                ["git", "-C", str(root), "show", f"{ref}:{rel_f}"],
                capture_output=True,
                timeout=15,
            )
            if b_proc.returncode == 0:
                out_f.write_bytes(b_proc.stdout)
        if include_untracked:
            _copy_untracked_into_dest(root, dest)
        return "git_show_fallback"

    raise SystemExit(f"[vpr.py] ERROR: Unable to materialize worktree for ref {ref!r}: {proc.stderr.strip()}")


def _remove_worktree_dir(root: Path, wt_dir: Path) -> None:
    if is_git_repo(root) and wt_dir.exists():
        _run_git(root, ["worktree", "remove", "--force", str(wt_dir)], timeout_s=20)
    _remove_readonly_and_rmtree(wt_dir)
    if is_git_repo(root):
        _run_git(root, ["worktree", "prune"], timeout_s=15)


def _ensure_worktrees(root: Path, s_dir: Path, base_ref: str, head_ref: str, force: bool = False) -> Tuple[Path, Path]:
    wt_base = s_dir / "tmp" / "worktree-base"
    wt_head = s_dir / "tmp" / "worktree-head"
    if force or not wt_base.is_dir():
        _create_git_worktree_or_copy(root, base_ref, wt_base, include_untracked=False)
    if force or not wt_head.is_dir():
        _create_git_worktree_or_copy(root, head_ref, wt_head, include_untracked=True)
    return wt_base, wt_head


def _checkpoint_dir(s_dir: Path, name: str) -> Path:
    return s_dir / "tmp" / "checkpoints" / sanitize_id(name)


def _save_sandbox_checkpoint(root: Path, s_dir: Path, name: str) -> Tuple[Path, int]:
    fix_dir = s_dir / "sandbox-fix"
    if not fix_dir.is_dir():
        raise SystemExit(f"[vpr.py] ERROR: {fix_dir} not found; run sandbox --mode fix first")
    dest = _checkpoint_dir(s_dir, name)
    if dest.exists():
        _remove_readonly_and_rmtree(dest)
    global_excl, root_excl = _compute_sandbox_exclusions(root, [])
    copied = _copy_project_tree(fix_dir, dest, global_excl, root_excl)
    return dest, copied


def _restore_sandbox_from(root: Path, s_dir: Path, src_dir: Path) -> int:
    fix_dir = s_dir / "sandbox-fix"
    if fix_dir.exists():
        _remove_readonly_and_rmtree(fix_dir)
    global_excl, root_excl = _compute_sandbox_exclusions(root, [])
    return _copy_project_tree(src_dir, fix_dir, global_excl, root_excl)


def _collect_source_files_for_diff(base: Path, global_excl: set[str], root_excl: set[str]) -> Dict[str, Path]:
    files: Dict[str, Path] = {}
    if not base.is_dir():
        return files
    base_res = base.resolve()
    for dirpath, dirnames, filenames in os.walk(base_res):
        dp = Path(dirpath)
        rel_dir = dp.relative_to(base_res)
        at_root = rel_dir == Path(".")
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in global_excl and not (at_root and d in root_excl)
        )
        for fname in sorted(filenames):
            fp = dp / fname
            if fp.suffix.lower() in BINARY_EXTS or fname.lower().endswith((".pyc", ".pyo", ".class")):
                continue
            rel = fname if at_root else f"{rel_dir.as_posix()}/{fname}"
            files[rel] = fp
    return files


def _read_text_lines_for_diff(fp: Path) -> Optional[List[str]]:
    try:
        if fp.stat().st_size > 2_000_000:
            return None
        raw = fp.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    return raw.decode("utf-8", errors="replace").splitlines()


def _compute_unified_diff(base_dir: Path, target_dir: Path, root: Path) -> Tuple[List[str], str]:
    global_excl, root_excl = _compute_sandbox_exclusions(root, [])
    base_map = _collect_source_files_for_diff(base_dir, global_excl, root_excl)
    targ_map = _collect_source_files_for_diff(target_dir, global_excl, root_excl)
    changed_files: List[str] = []
    chunks: List[str] = []
    for rel in sorted(set(base_map) | set(targ_map)):
        b_fp, t_fp = base_map.get(rel), targ_map.get(rel)
        a_lines = _read_text_lines_for_diff(b_fp) if b_fp is not None else []
        b_lines = _read_text_lines_for_diff(t_fp) if t_fp is not None else []
        if a_lines is None or b_lines is None or a_lines == b_lines:
            continue
        fromfile = "/dev/null" if b_fp is None else f"a/{rel}"
        tofile = "/dev/null" if t_fp is None else f"b/{rel}"
        udiff = list(difflib.unified_diff(a_lines, b_lines, fromfile=fromfile, tofile=tofile, n=3, lineterm=""))
        if udiff:
            changed_files.append(rel)
            chunks.append("\n".join(udiff))
    full_patch = ("\n".join(chunks) + "\n") if chunks else ""
    return changed_files, full_patch


# ---------------------------------------------------------------------------
# Test Runner & Payload Parsing (ab-verify & verify-fix)
# ---------------------------------------------------------------------------


@dataclass
class RunEval:
    runner: str
    exit_code: int
    ok: bool
    ran: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    duration_s: float = 0.0
    failed_tests: List[str] = field(default_factory=list)
    failure_sigs: List[str] = field(default_factory=list)
    harness_signals: List[str] = field(default_factory=list)
    schemas: Dict[str, Any] = field(default_factory=dict)
    schema_hash: str = ""
    veripr_checks: List[Dict[str, Any]] = field(default_factory=list)
    snippet: str = ""


def _detect_runner(cmd: Optional[Sequence[str]], text: str) -> str:
    if f"\n{CHECK_PREFIX}" in f"\n{text}":
        return "veripr_check"
    if f"\n{PAYLOAD_PREFIX}" in f"\n{text}" and not re.search(r"^Ran \d+ tests? in ", text, re.M):
        return "veripr_payload"
    if cmd:
        joined = " ".join(cmd).lower()
        base0 = Path(cmd[0]).name.lower()
        if "pytest" in joined:
            return "pytest"
        if "unittest" in joined:
            return "unittest"
        if "vitest" in joined:
            return "vitest"
        if "jest" in joined:
            return "jest"
        if base0.startswith("node"):
            return "node"
        if base0 == "go" or base0.startswith("go."):
            return "go"
    if "test session starts" in text or re.search(r"^collected \d+ items", text, re.M):
        return "pytest"
    if re.search(r"^Ran \d+ tests? in [\d.]+s", text, re.M):
        return "unittest"
    if "TAP version" in text or re.search(r"^(?:#|[ℹi])\s+tests\s+\d+", text, re.M):
        return "node"
    if re.search(r"^=== RUN\s+", text, re.M) or re.search(r"^--- (PASS|FAIL):\s+", text, re.M):
        return "go"
    return "generic"


def _run_command_in_dir(
    root: Path,
    cache_dir: Path,
    target_dir: Path,
    phase: str,
    cmd_tokens: List[str],
    timeout_s: int,
) -> Tuple[int, float, str]:
    resolved_tokens = _resolve_bughunt_cmd_paths(cmd_tokens, root)
    env = build_isolated_env(cache_dir, target_root=target_dir, phase=phase)
    cmd_exec = resolve_executable(resolved_tokens, target_dir, env)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd_exec,
            cwd=str(target_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        dur = time.monotonic() - t0
        return proc.returncode, dur, strip_ansi(proc.stdout or "")
    except subprocess.TimeoutExpired as exc:
        dur = time.monotonic() - t0
        partial = exc.stdout if isinstance(exc.stdout, str) else ""
        return 124, dur, strip_ansi(partial) + f"\n[vpr.py] TIMEOUT after {timeout_s}s\n"
    except OSError as exc:
        dur = time.monotonic() - t0
        return 127, dur, f"[vpr.py] EXEC_ERROR: {exc}\n"


def _parse_run_output(raw: str, exit_code: int, duration_s: float, cmd: List[str]) -> RunEval:
    runner = _detect_runner(cmd, raw)
    ev = RunEval(runner=runner, exit_code=exit_code, ok=(exit_code == 0), duration_s=round(duration_s, 3))

    if "[vpr.py] TIMEOUT" in raw or exit_code == 124:
        ev.harness_signals.append("command timeout")
    if "[vpr.py] EXEC_ERROR" in raw or exit_code == 127:
        ev.harness_signals.append("command launch error")

    for pat, lbl in (
        (r"\bSyntaxError:", "SyntaxError"),
        (r"\bIndentationError:", "IndentationError"),
        (r"\bModuleNotFoundError:", "ModuleNotFoundError"),
        (r"\bImportError:", "ImportError"),
        (r"\bERR_MODULE_NOT_FOUND\b", "ERR_MODULE_NOT_FOUND"),
    ):
        if re.search(pat, raw):
            ev.harness_signals.append(lbl)

    # Extract VERIPR_PAYLOAD:{...} & VERIPR_CHECK:{...} (merging multi-sample payloads)
    payloads: Dict[str, Any] = {}
    p_idx = 0

    def _record_payload(key: str, raw_obj: Dict[str, Any]) -> None:
        sigs: List[Any] = []
        if isinstance(raw_obj.get("samples"), list) and raw_obj["samples"]:
            sigs = [compute_schema_signature(smp) for smp in raw_obj["samples"]]
        elif "payload" in raw_obj:
            sigs = [compute_schema_signature(raw_obj["payload"])]
        else:
            sigs = [compute_schema_signature(raw_obj)]
        combined = sigs[0]
        for nxt in sigs[1:]:
            combined = merge_schema_signatures(combined, nxt)
        if key in payloads:
            payloads[key] = merge_schema_signatures(payloads[key], combined)
        else:
            payloads[key] = combined

    for ln in raw.splitlines():
        s = ln.strip()
        if s.startswith(PAYLOAD_PREFIX):
            try:
                obj = json.loads(s[len(PAYLOAD_PREFIX) :].strip())
                if isinstance(obj, dict) and ("payload" in obj or "samples" in obj):
                    key = str(obj.get("endpoint") or obj.get("id") or f"payload_{p_idx}")
                    if "endpoint" not in obj and "id" not in obj:
                        p_idx += 1
                    _record_payload(key, obj)
                else:
                    payloads[f"payload_{p_idx}"] = compute_schema_signature(obj)
                    p_idx += 1
            except json.JSONDecodeError:
                ev.harness_signals.append("invalid VERIPR_PAYLOAD JSON")
        elif s.startswith(CHECK_PREFIX):
            try:
                chk = json.loads(s[len(CHECK_PREFIX) :].strip())
                if isinstance(chk, dict):
                    ev.veripr_checks.append(chk)
                    if "payload" in chk or "samples" in chk:
                        key = str(chk.get("endpoint") or chk.get("id") or f"check_{p_idx}")
                        if "endpoint" not in chk and "id" not in chk:
                            p_idx += 1
                        _record_payload(key, chk)
            except json.JSONDecodeError:
                ev.harness_signals.append("invalid VERIPR_CHECK JSON")

    if payloads:
        ev.schemas = payloads
        ev.schema_hash = schema_hash(payloads)

    if ev.veripr_checks and runner == "veripr_check":
        last = ev.veripr_checks[-1]
        ev.ran = int(last.get("ran", len(ev.veripr_checks)))
        st = str(last.get("status", "PASS" if exit_code == 0 else "FAIL")).upper()
        ev.ok = (exit_code == 0) and st in ("PASS", "OK", "TRUE")
        ev.passed = ev.ran if ev.ok else max(0, ev.ran - 1)
        ev.failed = 0 if ev.ok else 1
        if last.get("details"):
            ev.failure_sigs.append(trunc(str(last["details"]), 120))
        ev.snippet = trunc(str(last.get("details") or last), 140)
        return ev

    if runner == "veripr_payload" and payloads and exit_code == 0:
        ev.ran = len(payloads)
        ev.passed = ev.ran
        ev.ok = True
        ev.snippet = f"schema_hash={ev.schema_hash} endpoints={sorted(payloads)}"
        return ev

    # Parse standard test runners
    if runner == "unittest":
        for ln in raw.splitlines():
            m_hdr = re.match(r"^(FAIL|ERROR):\s+(\S+)\s+\(([^)]+)\)", ln)
            if m_hdr:
                ev.failed_tests.append(f"{m_hdr.group(3)}.{m_hdr.group(2)}")
        m_ran = re.search(r"Ran\s+(\d+)\s+tests?\s+in\s+[\d.]+s", raw)
        if m_ran:
            ev.ran = int(m_ran.group(1))
        m_fail = re.search(r"FAILED\s+\(([^)]+)\)", raw)
        if m_fail:
            parts = m_fail.group(1)
            mf = re.search(r"failures=(\d+)", parts)
            me = re.search(r"errors=(\d+)", parts)
            ms = re.search(r"skipped=(\d+)", parts)
            ev.failed = int(mf.group(1)) if mf else 0
            ev.errors = int(me.group(1)) if me else 0
            skipped = int(ms.group(1)) if ms else 0
            ev.passed = max(0, ev.ran - ev.failed - ev.errors - skipped)
        elif re.search(r"^OK(?:\s+\(.*\))?$", raw, re.M):
            ev.passed = ev.ran
        for blk in re.split(r"^={70}$", raw, flags=re.M):
            blk_s = blk.strip()
            if blk_s.startswith(("FAIL:", "ERROR:")):
                blk_lines = [l.strip() for l in blk_s.splitlines() if l.strip()]
                if blk_lines:
                    ev.failure_sigs.append(trunc(f"{blk_lines[0]} | {blk_lines[-1]}", 130))

    elif runner == "pytest":
        for ln in raw.splitlines():
            m_f = re.match(r"^FAILED\s+(\S+)", ln)
            if m_f:
                ev.failed_tests.append(m_f.group(1))
            m_e = re.match(r"^(?:E\s+|.*Error:\s+|.*AssertionError:)(.+)$", ln)
            if m_e and len(ev.failure_sigs) < 6:
                ev.failure_sigs.append(trunc(ln.strip(), 130))
        mp = re.search(r"(\d+)\s+passed", raw)
        mf = re.search(r"(\d+)\s+failed", raw)
        me = re.search(r"(\d+)\s+error", raw)
        ev.passed = int(mp.group(1)) if mp else 0
        ev.failed = int(mf.group(1)) if mf else 0
        ev.errors = int(me.group(1)) if me else 0
        ev.ran = ev.passed + ev.failed + ev.errors

    elif runner == "node":
        for ln in raw.splitlines():
            m_fail = re.match(r"^\s*(?:not ok \d+ -|✖)\s+(.+)$", ln)
            if m_fail:
                ev.failed_tests.append(m_fail.group(1).strip())
        mt = re.search(r"tests\s+(\d+)", raw)
        mp = re.search(r"pass\s+(\d+)", raw)
        mf = re.search(r"fail\s+(\d+)", raw)
        if mt:
            ev.ran = int(mt.group(1))
        if mp:
            ev.passed = int(mp.group(1))
        if mf:
            ev.failed = int(mf.group(1))

    elif runner == "go":
        for ln in raw.splitlines():
            m_p = re.match(r"^--- PASS:\s+(\S+)", ln)
            m_f = re.match(r"^--- FAIL:\s+(\S+)", ln)
            if m_p:
                ev.passed += 1
                ev.ran += 1
            elif m_f:
                ev.failed += 1
                ev.ran += 1
                ev.failed_tests.append(m_f.group(1))

    else:
        ev.ran = 1
        if exit_code == 0:
            ev.passed = 1
        else:
            ev.failed = 1

    if not ev.failure_sigs and not ev.ok:
        nonempty = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("Ran ")]
        if nonempty:
            ev.failure_sigs.append(trunc(" | ".join(nonempty[-2:]), 130))

    if ev.ran == 0 and ev.harness_signals:
        ev.ok = False
    elif ev.ran > 0 and (ev.failed > 0 or ev.errors > 0):
        ev.ok = False

    if ev.failure_sigs:
        ev.snippet = ev.failure_sigs[0]
    elif ev.schemas:
        ev.snippet = f"PASS schema_hash={ev.schema_hash}"
    else:
        ev.snippet = f"ran={ev.ran} passed={ev.passed} failed={ev.failed}"
    return ev


def _match_preexisting_name(failed_name: str, known_preexisting: Set[str]) -> bool:
    if failed_name in known_preexisting:
        return True

    def _sig_parts(name: str) -> Tuple[str, str]:
        norm = name.replace("::", ".")
        segs = [s for s in norm.split(".") if s]
        short = segs[-1] if segs else name
        pair = ".".join(segs[-2:]) if len(segs) >= 2 else short
        return pair, short

    f_pair, f_short = _sig_parts(failed_name)
    for known in known_preexisting:
        k_pair, k_short = _sig_parts(known)
        if failed_name.endswith(known) or known.endswith(failed_name):
            return True
        if "." in f_pair and "." in k_pair:
            if f_pair == k_pair:
                return True
        elif f_short == k_short:
            return True
    return False


# ---------------------------------------------------------------------------
# Subcommand: ab-verify (Gate 1 — Differential Gate)
# ---------------------------------------------------------------------------


def cmd_ab_verify(args: argparse.Namespace) -> int:
    root, s_dir, _, cache_dir = resolve_session(args.s, args.root)
    meta = _load_session_meta(s_dir)
    tid = sanitize_id(args.id)
    category = (args.category or "RIPPLE").upper()
    base_ref = args.base or meta.get("base_ref") or "HEAD"
    head_ref = args.head or meta.get("head_ref") or "WORKTREE"
    cmd_tokens = parse_cmd_tokens(args.cmd)

    wt_base, wt_head = _ensure_worktrees(root, s_dir, base_ref, head_ref, force=args.refresh_worktrees)

    b_code, b_dur, b_raw = _run_command_in_dir(root, cache_dir, wt_base, "base", cmd_tokens, args.timeout)
    h_code, h_dur, h_raw = _run_command_in_dir(root, cache_dir, wt_head, "head", cmd_tokens, args.timeout)

    (s_dir / "logs" / f"{tid}_base.log").write_text(b_raw.rstrip() + f"\nexit={b_code}\n", encoding="utf-8")
    (s_dir / "logs" / f"{tid}_head.log").write_text(h_raw.rstrip() + f"\nexit={h_code}\n", encoding="utf-8")

    b_ev = _parse_run_output(b_raw, b_code, b_dur, cmd_tokens)
    h_ev = _parse_run_output(h_raw, h_code, h_dur, cmd_tokens)

    schema_cmp: Dict[str, Any] = {"breaking": False, "missing_keys": [], "added_keys": [], "type_changes": [], "renamed_candidates": []}
    if b_ev.schemas and h_ev.schemas:
        schema_cmp = compare_schemas(b_ev.schemas, h_ev.schemas)

    explicit_drift = any(bool(chk.get("contract_drift")) for chk in h_ev.veripr_checks)

    # Gate 1 Differential Verdict
    verdict = "CLEAN_PASS"
    blocks_pr = False
    rationale = "Base and Head both pass with compatible contract schema."

    if h_ev.harness_signals or b_ev.harness_signals or (b_ev.ran == 0 and h_ev.ran == 0 and not (b_ev.schemas or h_ev.schemas)):
        verdict = "HARNESS_BROKEN"
        blocks_pr = True
        sigs = sorted(set(b_ev.harness_signals + h_ev.harness_signals))
        rationale = f"Harness/runtime error: {', '.join(sigs) or 'ran=0'}"
    elif not b_ev.ok and not h_ev.ok:
        b_set = set(b_ev.failed_tests)
        h_set = set(h_ev.failed_tests)
        new_failures = [t for t in h_ev.failed_tests if not _match_preexisting_name(t, b_set)] if (b_set and h_set) else []
        if new_failures:
            verdict = "GATE1_NEW_REGRESSION"
            blocks_pr = True
            rationale = f"Head introduced new failure(s) {new_failures} beyond Base failure(s) {b_ev.failed_tests}"
        else:
            verdict = "PRE_EXISTING_FAILURE"
            blocks_pr = False
            rationale = "Fails identically on Base and Head; filtered as pre-existing branch debt."
    elif schema_cmp["breaking"] or explicit_drift:
        verdict = "GATE1_CONTRACT_DRIFT"
        blocks_pr = True
        parts = []
        if schema_cmp["missing_keys"]:
            parts.append(f"missing={schema_cmp['missing_keys']}")
        if schema_cmp["type_changes"]:
            parts.append(f"type_changes={schema_cmp['type_changes']}")
        if schema_cmp["renamed_candidates"]:
            parts.append(f"renamed={schema_cmp['renamed_candidates']}")
        rationale = "; ".join(parts) or "API contract schema hash mismatch between Base and Head."
    elif b_ev.ok and not h_ev.ok:
        if category in ("RIPPLE", "CALLER", "RETURN_NULL", "RETURN_SHAPE_CHANGED", "SIGNATURE"):
            verdict = "GATE1_RIPPLE_CONFIRMED"
        else:
            verdict = "GATE1_NEW_REGRESSION"
        blocks_pr = True
        rationale = f"Base PASSED while Head FAILED: {h_ev.snippet}"
    elif not b_ev.ok and h_ev.ok:
        verdict = "PRE_EXISTING_FIXED"
        blocks_pr = False
        rationale = "Failed on Base and now passes on Head."

    record = {
        "id": tid,
        "category": category,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "cmd": cmd_tokens,
        "verdict": verdict,
        "blocks_pr": blocks_pr,
        "rationale": rationale,
        "base": {
            "ok": b_ev.ok,
            "exit_code": b_ev.exit_code,
            "ran": b_ev.ran,
            "passed": b_ev.passed,
            "failed": b_ev.failed,
            "errors": b_ev.errors,
            "failed_tests": b_ev.failed_tests,
            "failure_sigs": b_ev.failure_sigs,
            "schema_hash": b_ev.schema_hash,
            "schemas": b_ev.schemas,
            "snippet": b_ev.snippet,
        },
        "head": {
            "ok": h_ev.ok,
            "exit_code": h_ev.exit_code,
            "ran": h_ev.ran,
            "passed": h_ev.passed,
            "failed": h_ev.failed,
            "errors": h_ev.errors,
            "failed_tests": h_ev.failed_tests,
            "failure_sigs": h_ev.failure_sigs,
            "schema_hash": h_ev.schema_hash,
            "schemas": h_ev.schemas,
            "snippet": h_ev.snippet,
        },
        "schema_diff": schema_cmp,
    }
    (s_dir / "logs" / f"{tid}_ab.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    # Compact <= 10-line digest
    lines = [
        f"[vpr ab-verify] id={tid} cat={category} verdict={verdict} blocks_pr={blocks_pr}",
        f"Base ({base_ref}): {'PASS' if b_ev.ok else 'FAIL'} ran={b_ev.ran} fail={b_ev.failed + b_ev.errors}"
        + (f" schema={b_ev.schema_hash}" if b_ev.schema_hash else ""),
        f"Head ({head_ref}): {'PASS' if h_ev.ok else 'FAIL'} ran={h_ev.ran} fail={h_ev.failed + h_ev.errors}"
        + (f" schema={h_ev.schema_hash}" if h_ev.schema_hash else ""),
        f"Detail: {trunc(rationale, 130)}",
    ]
    if schema_cmp["added_keys"]:
        lines.append(f"SchemaAddedKeys: {schema_cmp['added_keys']}")
    print("\n".join(lines[:10]))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: verify-fix (Gate 2 — Zero-Ripple Fix Gate + Patch Export)
# ---------------------------------------------------------------------------


def _load_known_preexisting(s_dir: Path) -> Set[str]:
    known: Set[str] = set()
    logs_dir = s_dir / "logs"
    if not logs_dir.is_dir():
        return known
    for jf in sorted(logs_dir.glob("*_ab.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("verdict") == "PRE_EXISTING_FAILURE":
            for t in data.get("base", {}).get("failed_tests", []):
                known.add(str(t))
            for t in data.get("head", {}).get("failed_tests", []):
                known.add(str(t))
    return known


def cmd_verify_fix(args: argparse.Namespace) -> int:
    root, s_dir, _, cache_dir = resolve_session(args.s, args.root)
    meta = _load_session_meta(s_dir)
    tid = sanitize_id(args.id)

    fix_dir = s_dir / "sandbox-fix"
    if not fix_dir.is_dir():
        raise SystemExit(f"[vpr.py] ERROR: {fix_dir} not found; run sandbox --mode fix first")

    base_ref = meta.get("base_ref") or "HEAD"
    head_ref = meta.get("head_ref") or "WORKTREE"
    _, wt_head = _ensure_worktrees(root, s_dir, base_ref, head_ref, force=False)

    ab_file = s_dir / "logs" / f"{tid}_ab.json"
    ab_data: Dict[str, Any] = json.loads(ab_file.read_text(encoding="utf-8")) if ab_file.is_file() else {}
    ab_cmd = ab_data.get("cmd") or []

    # 1. Target check (either stored ab-verify check or explicit --test-cmd if no ab_cmd)
    target_cmd = ab_cmd if ab_cmd else (parse_cmd_tokens(args.test_cmd) if args.test_cmd else [])
    if not target_cmd:
        raise SystemExit(f"[vpr.py] ERROR: No stored ab-verify command for {tid}; provide --test-cmd")

    t_code, t_dur, t_raw = _run_command_in_dir(root, cache_dir, fix_dir, "fix", target_cmd, args.timeout)
    (s_dir / "logs" / f"{tid}_fix_target.log").write_text(t_raw.rstrip() + f"\nexit={t_code}\n", encoding="utf-8")
    t_ev = _parse_run_output(t_raw, t_code, t_dur, target_cmd)

    # Check schema compatibility vs Base if ab-verify recorded base schemas
    schema_ok = True
    schema_note = "N/A"
    base_schemas = ab_data.get("base", {}).get("schemas") or {}
    if base_schemas:
        if not t_ev.schemas:
            schema_ok = False
            schema_note = "fix output missing VERIPR_PAYLOAD schema"
        else:
            cmp_res = compare_schemas(base_schemas, t_ev.schemas)
            if cmp_res["breaking"]:
                schema_ok = False
                schema_note = f"missing={cmp_res['missing_keys']} type={cmp_res['type_changes']}"
            else:
                schema_note = f"RESTORED ({t_ev.schema_hash})"

    # 2. Caller check (--caller-cmd)
    caller_ok = True
    caller_note = "N/A"
    if args.caller_cmd:
        c_tokens = parse_cmd_tokens(args.caller_cmd)
        c_code, c_dur, c_raw = _run_command_in_dir(root, cache_dir, fix_dir, "fix", c_tokens, args.timeout)
        (s_dir / "logs" / f"{tid}_fix_caller.log").write_text(c_raw.rstrip() + f"\nexit={c_code}\n", encoding="utf-8")
        c_ev = _parse_run_output(c_raw, c_code, c_dur, c_tokens)
        caller_ok = c_ev.ok and not c_ev.harness_signals
        caller_note = f"{'PASS' if caller_ok else 'FAIL'} (ran={c_ev.ran}, fail={c_ev.failed + c_ev.errors})"

    # 3. Project test suite check (--test-cmd, filtering verified PRE_EXISTING_FAILURE)
    suite_ok = True
    suite_note = "N/A"
    filtered_preexisting: List[str] = []
    if args.test_cmd:
        s_tokens = parse_cmd_tokens(args.test_cmd)
        if s_tokens == target_cmd:
            suite_ok = t_ev.ok and not t_ev.harness_signals
            suite_note = f"{'PASS' if suite_ok else 'FAIL'} (ran={t_ev.ran})"
        else:
            s_code, s_dur, s_raw = _run_command_in_dir(root, cache_dir, fix_dir, "fix", s_tokens, args.timeout)
            (s_dir / "logs" / f"{tid}_fix_suite.log").write_text(s_raw.rstrip() + f"\nexit={s_code}\n", encoding="utf-8")
            s_ev = _parse_run_output(s_raw, s_code, s_dur, s_tokens)
            if s_ev.harness_signals:
                suite_ok = False
                suite_note = f"FAIL (harness={s_ev.harness_signals})"
            elif s_ev.ok:
                suite_ok = True
                suite_note = f"PASS (ran={s_ev.ran})"
            else:
                known_pre = _load_known_preexisting(s_dir)
                if s_ev.failed_tests and known_pre and all(_match_preexisting_name(ft, known_pre) for ft in s_ev.failed_tests):
                    suite_ok = True
                    filtered_preexisting = list(s_ev.failed_tests)
                    suite_note = f"PASS_FILTERED_PREEXISTING (ran={s_ev.ran}, ignored={len(filtered_preexisting)})"
                else:
                    suite_ok = False
                    suite_note = f"FAIL (ran={s_ev.ran}, failed={s_ev.failed_tests or [s_ev.snippet]})"

    # 4. New PR Feature Preservation (--feature-cmd)
    feature_ok = True
    feature_note = "N/A"
    if args.feature_cmd:
        f_tokens = parse_cmd_tokens(args.feature_cmd)
        f_code, f_dur, f_raw = _run_command_in_dir(root, cache_dir, fix_dir, "fix", f_tokens, args.timeout)
        (s_dir / "logs" / f"{tid}_fix_feature.log").write_text(f_raw.rstrip() + f"\nexit={f_code}\n", encoding="utf-8")
        f_ev = _parse_run_output(f_raw, f_code, f_dur, f_tokens)
        feature_ok = f_ev.ok and not f_ev.harness_signals
        feature_note = f"{'PASS' if feature_ok else 'FAIL'} ({f_ev.snippet})"

    # Evaluate Gate 2 outcome (priority: feature check / incomplete / caller regression)
    target_ok = t_ev.ok and not t_ev.harness_signals and schema_ok
    if not feature_ok:
        verdict = "REJECTED_FEATURE_BROKEN"
        reason = f"Fix broke or reverted new PR feature test: {feature_note}"
    elif not target_ok:
        verdict = "REJECTED_INCOMPLETE_FIX"
        reason = f"Target defect or schema parity not fixed (ok={t_ev.ok}, schema={schema_note})"
    elif not caller_ok or not suite_ok:
        verdict = "REJECTED_CALLER_REGRESSION"
        reason = f"Caller/suite regression detected (caller={caller_note}, suite={suite_note})"
    else:
        verdict = "CONFIRMED_ZERO_RIPPLE_FIX"
        reason = "Target fixed, API schema compatible, 1-hop callers & new feature preserved."

    rollback_note = ""
    patch_rel = ""
    combined_patch_rel = ""
    changed_files: List[str] = []

    patch_check_note = ""
    if verdict == "CONFIRMED_ZERO_RIPPLE_FIX":
        # Export per-ID incremental patch with guaranteed pure LF (\n) endings
        id_ckpt = _checkpoint_dir(s_dir, tid)
        last_ckpt = _checkpoint_dir(s_dir, "_last_accepted")
        incr_base = id_ckpt if id_ckpt.is_dir() else (last_ckpt if last_ckpt.is_dir() else wt_head)
        changed_files, id_patch = _compute_unified_diff(incr_base, fix_dir, root)
        patch_path = s_dir / "patches" / f"{tid}.patch"
        _write_lf_file(patch_path, id_patch)
        patch_rel = rel_posix(patch_path, root)

        # Save _last_accepted checkpoint & cumulative patch relative to worktree-head
        _save_sandbox_checkpoint(root, s_dir, "_last_accepted")
        _, comb_patch = _compute_unified_diff(wt_head, fix_dir, root)
        comb_path = s_dir / "patches" / "veripr-fixes.patch"
        _write_lf_file(comb_path, comb_patch)
        combined_patch_rel = rel_posix(comb_path, root)
        chk_ok, chk_msg = _verify_patch_applies(wt_head, comb_path)
        patch_check_note = f"git_apply={chk_msg}" if chk_ok else f"git_apply_warn={chk_msg}"
    elif args.auto_rollback:
        id_ckpt = _checkpoint_dir(s_dir, tid)
        last_ckpt = _checkpoint_dir(s_dir, "_last_accepted")
        init_ckpt = _checkpoint_dir(s_dir, "_initial")
        if id_ckpt.is_dir():
            src_dir, src_lbl = id_ckpt, tid
        elif last_ckpt.is_dir():
            src_dir, src_lbl = last_ckpt, "_last_accepted"
        elif init_ckpt.is_dir():
            src_dir, src_lbl = init_ckpt, "_initial"
        else:
            src_dir, src_lbl = wt_head, "worktree-head"
        _restore_sandbox_from(root, s_dir, src_dir)
        rollback_note = f"AUTO_RESTORED (checkpoint:{src_lbl})"

    fix_record = {
        "id": tid,
        "verdict": verdict,
        "reason": reason,
        "target_ok": target_ok,
        "schema_note": schema_note,
        "caller_note": caller_note,
        "suite_note": suite_note,
        "filtered_preexisting": filtered_preexisting,
        "feature_note": feature_note,
        "rollback": rollback_note,
        "changed_files": changed_files,
        "patch_file": patch_rel,
        "combined_patch": combined_patch_rel,
        "patch_check": patch_check_note,
    }
    (s_dir / "logs" / f"{tid}_fix.json").write_text(json.dumps(fix_record, indent=2) + "\n", encoding="utf-8")

    hist_path = s_dir / "logs" / f"{tid}_fix_history.json"
    hist: List[Dict[str, Any]] = []
    if hist_path.is_file():
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            hist = []
    hist.append(fix_record)
    hist_path.write_text(json.dumps(hist, indent=2) + "\n", encoding="utf-8")

    out_lines = [
        f"[vpr verify-fix] id={tid} verdict={verdict}" + (f" ({rollback_note})" if rollback_note else ""),
        f"Target={ 'PASS' if target_ok else 'FAIL' } | Schema={schema_note} | Callers={caller_note}",
        f"Suite={suite_note} | FeaturePreserved={feature_note}",
    ]
    if patch_rel:
        out_lines.append(
            f"PatchExport: {patch_rel} (combined: {combined_patch_rel}; {patch_check_note}; files={changed_files})"
        )
    else:
        out_lines.append(f"Reason: {trunc(reason, 130)}")
    print("\n".join(out_lines[:10]))
    return 0 if verdict == "CONFIRMED_ZERO_RIPPLE_FIX" else 1


# ---------------------------------------------------------------------------
# Subcommand: sandbox
# ---------------------------------------------------------------------------


def cmd_sandbox(args: argparse.Namespace) -> int:
    root, s_dir, _, cache_dir = resolve_session(args.s, args.root)
    meta = _load_session_meta(s_dir)
    base_ref = meta.get("base_ref") or "HEAD"
    head_ref = meta.get("head_ref") or "WORKTREE"
    global_excl, root_excl = _compute_sandbox_exclusions(root, args.exclude or [])

    if args.mode == "init-worktrees":
        wt_base, wt_head = _ensure_worktrees(root, s_dir, base_ref, head_ref, force=args.force)
        print(
            f"[vpr sandbox init-worktrees] base={rel_posix(wt_base, root)} ({base_ref}) "
            f"head={rel_posix(wt_head, root)} ({head_ref})"
        )
        return 0

    if args.mode == "fix":
        target = s_dir / "sandbox-fix"
        if target.exists():
            if not args.force:
                raise SystemExit(f"[vpr.py] ERROR: {target} already exists (pass --force to overwrite)")
            _remove_readonly_and_rmtree(target)
        _, wt_head = _ensure_worktrees(root, s_dir, base_ref, head_ref, force=False)
        t0 = time.monotonic()
        copied = _copy_project_tree(wt_head, target, global_excl, root_excl)
        _save_sandbox_checkpoint(root, s_dir, "_initial")
        dur = time.monotonic() - t0
        print(f"[vpr sandbox fix] path={rel_posix(target, root)} files={copied} duration={dur:.2f}s")
        return 0

    if args.mode == "save":
        if not args.id:
            raise SystemExit("[vpr.py] ERROR: --id is required for sandbox --mode save")
        dest, copied = _save_sandbox_checkpoint(root, s_dir, args.id)
        print(f"[vpr sandbox save] id={sanitize_id(args.id)} path={rel_posix(dest, root)} files={copied}")
        return 0

    if args.mode == "rollback":
        if not args.id:
            raise SystemExit("[vpr.py] ERROR: --id is required for sandbox --mode rollback")
        ckpt = _checkpoint_dir(s_dir, args.id)
        wt_head = s_dir / "tmp" / "worktree-head"
        if ckpt.is_dir():
            src_dir, src_lbl = ckpt, rel_posix(ckpt, root)
        elif wt_head.is_dir():
            src_dir, src_lbl = wt_head, rel_posix(wt_head, root)
        else:
            src_dir, src_lbl = root, "ROOT"
        copied = _restore_sandbox_from(root, s_dir, src_dir)
        print(f"[vpr sandbox rollback] id={sanitize_id(args.id)} restored_from={src_lbl} files={copied}")
        return 0

    if args.mode == "diff":
        fix_dir = s_dir / "sandbox-fix"
        if not fix_dir.is_dir():
            raise SystemExit(f"[vpr.py] ERROR: {fix_dir} not found; run --mode fix first")
        wt_head = s_dir / "tmp" / "worktree-head"
        base_dir = wt_head if wt_head.is_dir() else root
        changed_files, full_patch = _compute_unified_diff(base_dir, fix_dir, root)
        patch_path = s_dir / "patches" / "veripr-fixes.patch"
        _write_lf_file(patch_path, full_patch)
        diff_log = s_dir / "logs" / "sandbox-fix.diff"
        _write_lf_file(diff_log, full_patch)

        header = f"[vpr sandbox diff] files_changed={len(changed_files)} saved={rel_posix(patch_path, root)}"
        if not changed_files:
            print(f"{header} (no changes)")
            return 0
        max_out = 7800
        out = header + "\n" + full_patch.rstrip()
        if len(out) > max_out:
            note = f"\n... (truncated to <=8000 chars; full patch in {rel_posix(patch_path, root)})"
            out = out[: max_out - len(note)] + note
        print(out)
        return 0

    # mode == "clean"
    cleaned: List[str] = []
    for label, wt in (("worktree-base", s_dir / "tmp" / "worktree-base"), ("worktree-head", s_dir / "tmp" / "worktree-head")):
        if wt.exists():
            _remove_worktree_dir(root, wt)
            cleaned.append(label)

    for label, target in (
        ("sandbox-fix", s_dir / "sandbox-fix"),
        ("tmp", s_dir / "tmp"),
        (".cache", cache_dir),
    ):
        if target.exists():
            _remove_readonly_and_rmtree(target)
            cleaned.append(label)

    if is_git_repo(root):
        _run_git(root, ["worktree", "prune"], timeout_s=15)

    before_file = s_dir / "logs" / "fs-before.txt"
    mode_file = s_dir / "logs" / ".snapshot-mode"
    snap_mode = mode_file.read_text(encoding="utf-8").strip() if mode_file.is_file() else None
    before_lines = before_file.read_text(encoding="utf-8", errors="replace").splitlines() if before_file.is_file() else []

    actual_mode, after_lines = take_snapshot(root, forced_mode=snap_mode)
    diff = diff_snapshots(before_lines, after_lines, actual_mode)
    removed_caches = _clean_new_agent_caches(root, diff, actual_mode)
    if removed_caches:
        actual_mode, after_lines = take_snapshot(root, forced_mode=actual_mode)
        diff = diff_snapshots(before_lines, after_lines, actual_mode)

    after_file = s_dir / "logs" / "fs-after.txt"
    after_file.write_text(("\n".join(after_lines) + "\n") if after_lines else "", encoding="utf-8")

    dirty_items = [
        f"{k}: {it}"
        for k, items in diff.items()
        if k not in ("resolved", "removed")
        for it in items
    ]
    lines = [
        f"[vpr sandbox clean] removed_dirs=[{', '.join(cleaned) or 'none'}] "
        f"auto_removed_caches=[{', '.join(removed_caches) or 'none'}]",
    ]
    if not dirty_items:
        lines.append("cleanliness: CLEAN (0 new/modified files outside .bughunt/)")
    else:
        lines.append(f"cleanliness: DIRTY ({len(dirty_items)} unexpected files outside .bughunt/)")
        lines.extend(f"  - {it}" for it in dirty_items[:8])
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: summary
# ---------------------------------------------------------------------------


def _extract_snippet_lines(log_path: Path, max_lines: int = 6) -> List[str]:
    if not log_path.is_file():
        return []
    lines = [ln.rstrip() for ln in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    important = [
        ln for ln in lines
        if any(k in ln for k in ("FAIL:", "ERROR:", "TypeError:", "AssertionError:", "KeyError:", PAYLOAD_PREFIX, CHECK_PREFIX, "Ran ", "FAILED"))
    ]
    chosen = important if important else lines[-max_lines:]
    return [trunc(ln, 140) for ln in chosen[:max_lines]]


def cmd_summary(args: argparse.Namespace) -> int:
    root, s_dir, _, _ = resolve_session(args.s, args.root)
    logs_dir = s_dir / "logs"

    ab_items: List[Dict[str, Any]] = []
    for jf in sorted(logs_dir.glob("*_ab.json")):
        try:
            ab_items.append(json.loads(jf.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue

    fix_map: Dict[str, Dict[str, Any]] = {}
    fix_history: List[Dict[str, Any]] = []
    for jf in sorted(logs_dir.glob("*_fix.json")):
        try:
            rec = json.loads(jf.read_text(encoding="utf-8"))
            fix_map[rec["id"]] = rec
        except (OSError, json.JSONDecodeError):
            continue
    for hf in sorted(logs_dir.glob("*_fix_history.json")):
        try:
            items = json.loads(hf.read_text(encoding="utf-8"))
            if isinstance(items, list):
                fix_history.extend(items)
        except (OSError, json.JSONDecodeError):
            continue

    ds_path = logs_dir / "diff_scope.json"
    im_path = logs_dir / "impact_map.json"
    ds_data = json.loads(ds_path.read_text(encoding="utf-8")) if ds_path.is_file() else {}
    im_data = json.loads(im_path.read_text(encoding="utf-8")) if im_path.is_file() else {}

    out: List[str] = [
        f"# VeriPR Summary (`{s_dir.name}`)",
        f"- Changed Symbols: {len(ds_data.get('changed_symbols', []))} | 1-Hop Callers: {len(im_data.get('callers', []))}",
        "",
        "## Differential & Zero-Ripple Gate Results",
        "| ID | Category | Gate 1 (Base vs Head) | Gate 2 (Zero-Ripple Fix) | Patch |",
        "|---|---|---|---|---|",
    ]
    for ab in ab_items:
        tid = ab["id"]
        fx = fix_map.get(tid)
        g2 = fx["verdict"] if fx else ("FILTERED_NON_BLOCKING" if not ab.get("blocks_pr") else "PENDING")
        patch_s = fx.get("patch_file") or "-" if fx else "-"
        out.append(f"| `{tid}` | {ab.get('category', '-')} | `{ab['verdict']}` | `{g2}` | `{patch_s}` |")

    rejected = [h for h in fix_history if h.get("verdict") != "CONFIRMED_ZERO_RIPPLE_FIX"]
    if rejected:
        out.extend(["", "## Rejected / Auto-Rolled-Back Fix Attempts", "| ID | Verdict | Rollback | Reason |", "|---|---|---|---|"])
        for r in rejected:
            out.append(f"| `{r['id']}` | `{r['verdict']}` | `{r.get('rollback') or '-'}` | {trunc(r.get('reason', ''), 90)} |")

    if args.snippets and ab_items:
        out.extend(["", "## Compact Evidence Snippets (<=6 lines per phase)"])
        for ab in ab_items:
            tid = ab["id"]
            out.append(f"### `{tid}` ({ab['verdict']})")
            for phase in ("base", "head"):
                snips = _extract_snippet_lines(logs_dir / f"{tid}_{phase}.log", max_lines=6)
                if snips:
                    out.append(f"- **{phase}:** " + " ; ".join(snips))
            if tid in fix_map:
                fsnips = _extract_snippet_lines(logs_dir / f"{tid}_fix_target.log", max_lines=4)
                if fsnips:
                    out.append("- **fix:** " + " ; ".join(fsnips))

    print("\n".join(out))
    return 0


# ---------------------------------------------------------------------------
# Argument Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vpr.py", description="VeriPR Deterministic Differential & 1-Hop Ripple Guard CLI")
    sub = p.add_subparsers(dest="subcommand", required=True)

    p_init = sub.add_parser("init", help="Initialize .bughunt/pr-<ts>/ session")
    p_init.add_argument("--root", default=".", help="Project root directory")
    p_init.add_argument("--base", default=None, help="Base Git ref (default: auto-detect)")
    p_init.add_argument("--head", default=None, help="Head Git ref or WORKTREE (default: auto-detect)")
    p_init.set_defaults(func=cmd_init)

    p_ds = sub.add_parser("diff-scope", help="Analyze Git diff + AST/brace symbols and semantic risks")
    p_ds.add_argument("--root", default=".", help="Project root directory")
    p_ds.add_argument("--s", default=None, help="Session directory (.bughunt/pr-...)")
    p_ds.add_argument("--base", default=None, help="Base Git ref")
    p_ds.add_argument("--head", default=None, help="Head Git ref or WORKTREE")
    p_ds.set_defaults(func=cmd_diff_scope)

    p_im = sub.add_parser("impact-map", help="Find 1-hop callers of changed symbols (<=25 lines)")
    p_im.add_argument("--root", default=".", help="Project root directory")
    p_im.add_argument("--s", default=None, help="Session directory (.bughunt/pr-...)")
    p_im.add_argument("--symbols", default=None, help="Comma-separated symbols (optional if diff_scope.json exists)")
    p_im.add_argument("--max-lines", type=int, default=25, help="Max output lines (hard-capped at 25)")
    p_im.set_defaults(func=cmd_impact_map)

    p_ab = sub.add_parser("ab-verify", help="Run check in parallel Base vs Head worktrees + schema hash check")
    p_ab.add_argument("--root", default=None, help="Project root directory")
    p_ab.add_argument("--s", required=True, help="Session directory (.bughunt/pr-...)")
    p_ab.add_argument("--id", required=True, help="Finding/check ID (e.g. pr1)")
    p_ab.add_argument("--category", default="RIPPLE", help="Category tag (RIPPLE, CONTRACT, REGRESSION, LEGACY)")
    p_ab.add_argument("--base", default=None, help="Override Base Git ref")
    p_ab.add_argument("--head", default=None, help="Override Head Git ref or WORKTREE")
    p_ab.add_argument("--refresh-worktrees", action="store_true", help="Rebuild worktree-base & worktree-head")
    p_ab.add_argument("--timeout", type=int, default=90, help="Command timeout seconds")
    p_ab.add_argument("cmd", nargs=argparse.REMAINDER, help="Command after --")
    p_ab.set_defaults(func=cmd_ab_verify)

    p_vf = sub.add_parser("verify-fix", help="Gate 2 verification in sandbox-fix + incremental .patch export")
    p_vf.add_argument("--root", default=None, help="Project root directory")
    p_vf.add_argument("--s", required=True, help="Session directory (.bughunt/pr-...)")
    p_vf.add_argument("--id", required=True, help="Finding ID (e.g. pr1)")
    p_vf.add_argument("--auto-rollback", action="store_true", help="Auto-restore checkpoint if fix rejected")
    p_vf.add_argument("--test-cmd", default=None, help="Full project suite command (auto-filters pre-existing failures)")
    p_vf.add_argument("--caller-cmd", default=None, help="1-hop caller verification command")
    p_vf.add_argument("--feature-cmd", default=None, help="New PR feature preservation test command")
    p_vf.add_argument("--timeout", type=int, default=90, help="Command timeout seconds")
    p_vf.set_defaults(func=cmd_verify_fix)

    p_sb = sub.add_parser("sandbox", help="Manage worktrees, sandbox-fix, checkpoints, diff, and clean")
    p_sb.add_argument("--root", default=None, help="Project root directory")
    p_sb.add_argument("--s", required=True, help="Session directory (.bughunt/pr-...)")
    p_sb.add_argument("--mode", required=True, choices=("init-worktrees", "fix", "save", "rollback", "diff", "clean"))
    p_sb.add_argument("--id", default=None, help="Checkpoint ID for save/rollback")
    p_sb.add_argument("--force", action="store_true", help="Overwrite existing sandbox/worktrees")
    p_sb.add_argument("--exclude", action="append", default=[], help="Extra dir names to exclude")
    p_sb.set_defaults(func=cmd_sandbox)

    p_log = sub.add_parser("log", help="Update worklog.md Status and append entries")
    p_log.add_argument("--root", default=None, help="Project root directory")
    p_log.add_argument("--s", required=True, help="Session directory")
    p_log.add_argument("--status", default=None, help="New Status line")
    p_log.add_argument("--entry", action="append", default=[], help="1-line entry to append")
    p_log.set_defaults(func=cmd_log)

    p_sum = sub.add_parser("summary", help="Print Markdown summary tables and compact log snippets")
    p_sum.add_argument("--root", default=None, help="Project root directory")
    p_sum.add_argument("--s", required=True, help="Session directory")
    p_sum.add_argument("--snippets", action="store_true", help="Include <=6-line Base/Head/Fix snippets")
    p_sum.set_defaults(func=cmd_summary)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
