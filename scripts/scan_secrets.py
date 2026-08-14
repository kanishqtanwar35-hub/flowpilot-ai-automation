"""Refuse to let a secret reach git.

    python scripts/scan_secrets.py                 # scan the working tree
    python scripts/scan_secrets.py --staged        # scan what is about to be committed
    python scripts/scan_secrets.py --install-hook  # wire it into .git/hooks/pre-commit

Exit code 1 means something was found. The hook makes that block the commit.

Checks, in order of how people actually leak keys:
  1. `.env` (or any secret file) staged for commit — the #1 cause
  2. `.env` already tracked by git from an earlier commit
  3. token-shaped strings anywhere in tracked/staged content
  4. `.gitignore` missing its `.env` rule
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.security import PATTERNS  # noqa: E402

SECRET_FILENAMES = {".env", ".env.local", ".env.production", "credentials.json", "id_rsa"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".db", ".pyc"}
# Only the files that necessarily contain the detection patterns themselves.
# tests/test_security.py is deliberately NOT here: it assembles its fake tokens at
# runtime, so it holds no token-shaped literal and is scanned like any other file.
SELF = {
    "scripts/scan_secrets.py",
    "app/security.py",
    "docs/SECURITY.md",
}
# Escape hatch for a deliberate false positive: put this on the line.
ALLOW_MARKER = "secret-scan: allow"

RED, GREEN, YELLOW, OFF = "\033[31m", "\033[32m", "\033[33m", "\033[0m"


def git(*args: str) -> list[str]:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def scan_text(rel: str, text: str) -> list[str]:
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for pattern in PATTERNS:
            match = pattern.search(line)
            if match:
                shown = match.group(0)
                shown = shown[:6] + "…" + shown[-2:] if len(shown) > 12 else shown[:4] + "…"
                findings.append(f"{rel}:{lineno}  matches {pattern.pattern[:34]}…  ({shown})")
                break
    return findings


def read(path: Path) -> str | None:
    if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
        return None
    if path.stat().st_size > 2_000_000:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def run(staged: bool) -> int:
    problems: list[str] = []

    in_repo = bool(git("rev-parse", "--is-inside-work-tree"))
    tracked = set(git("ls-files"))
    staged_files = set(git("diff", "--cached", "--name-only"))

    if staged:
        files = sorted(staged_files)
    elif in_repo:
        files = sorted(tracked)
    else:
        files = sorted(
            str(p.relative_to(ROOT)).replace("\\", "/")
            for p in ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
        )

    ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").exists() else ""

    # 1 + 2 — a secret file git can actually see.
    # A local `.env` that is ignored is exactly what we WANT, so it is not a finding.
    for rel in sorted(set(files) | tracked | staged_files):
        if Path(rel).name not in SECRET_FILENAMES:
            continue
        if rel in staged_files:
            problems.append(f"{rel}  ← secret file STAGED FOR COMMIT. Run: git restore --staged {rel}")
        elif rel in tracked:
            problems.append(f"{rel}  ← secret file tracked by git. Run: git rm --cached {rel}")
        elif not in_repo and ".env" not in ignore_text:
            problems.append(f"{rel}  ← secret file present and .gitignore does not cover it")

    # 3 — token-shaped content
    for rel in files:
        if rel in SELF or Path(rel).name in SECRET_FILENAMES:
            continue
        text = read(ROOT / rel)
        if text:
            problems.extend(scan_text(rel, text))

    # 4 — the gitignore rule itself
    if ".env" not in ignore_text:
        problems.append(".gitignore has no `.env` rule — add it before committing anything")

    scope = "staged changes" if staged else "working tree"
    if problems:
        print(f"{RED}✗ secret scan failed{OFF}  ({len(problems)} issue(s) in {scope})\n")
        for p in problems:
            print("   " + p)
        print(f"\n{YELLOW}Nothing was committed. Remove the secret, then re-run.{OFF}")
        print("If a key was ever committed, treat it as burned: revoke and reissue it.")
        return 1

    print(f"{GREEN}✓ secret scan clean{OFF}  ({len(files)} files in {scope})")
    return 0


HOOK = """#!/bin/sh
# installed by scripts/scan_secrets.py --install-hook
# The interpreter is baked in at install time: git hooks run under a minimal shell
# whose PATH often does not include the python you installed with (very common on
# Windows, where bare `python` hits the Microsoft Store shim).
for PY in "{interpreter}" python3 python py; do
    if command -v "$PY" >/dev/null 2>&1; then
        exec "$PY" scripts/scan_secrets.py --staged
    fi
done
echo "pre-commit: no python interpreter found - cannot run the secret scan." >&2
echo "  Fix the hook, or bypass this one commit with: git commit --no-verify" >&2
exit 1
"""


def install_hook() -> int:
    hooks = ROOT / ".git" / "hooks"
    if not hooks.parent.exists():
        print(f"{RED}No .git directory here — run `git init` first.{OFF}")
        return 1
    hooks.mkdir(exist_ok=True)
    path = hooks / "pre-commit"
    interpreter = sys.executable.replace("\\", "/") if sys.executable else "python3"
    path.write_text(HOOK.format(interpreter=interpreter), encoding="utf-8", newline="\n")
    try:
        path.chmod(0o755)
    except OSError:
        pass
    print(f"{GREEN}✓ installed{OFF} {path}")
    print("  Every commit now fails if a secret is staged.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true", help="scan only what is staged for commit")
    ap.add_argument("--install-hook", action="store_true", help="install the git pre-commit hook")
    args = ap.parse_args()
    sys.exit(install_hook() if args.install_hook else run(args.staged))
