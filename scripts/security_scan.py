"""Scan release files for credential shapes and private machine paths."""
from __future__ import annotations

import gzip
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{25,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"[A-Za-z]:[/\\]Users[/\\][A-Za-z0-9_.-]+[/\\]"),
    re.compile(r"(?:/home|/Users)/[A-Za-z0-9_.-]+/"),
    re.compile(r"https?://[^\s/'\"]+\.(?:internal|local)(?:/|\b)"),
]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                            cwd=root, capture_output=True, text=True)
    if result.returncode == 0:
        paths = [root / name for name in result.stdout.splitlines()]
    else:
        paths = [path for path in root.rglob("*") if path.is_file() and not any(
            part in {".venv", "runs", "dist", "build", "__pycache__", ".git", ".pytest_cache", ".ruff_cache"}
            for part in path.relative_to(root).parts)]
    errors = []
    for path in paths:
        if not path.is_file() or path.suffix.lower() in {".ttf", ".png", ".pdf", ".zip", ".pyc"}:
            continue
        try:
            content = (gzip.decompress(path.read_bytes()).decode("utf-8")
                       if path.suffix.lower() == ".gz" else path.read_text(encoding="utf-8"))
        except UnicodeError:
            continue
        for pattern in PATTERNS:
            if pattern.search(content):
                # Show only the filename, never the secret matched.
                errors.append(path.relative_to(root).as_posix())
                break
    if errors:
        print("Sensitive content detected in: " + ", ".join(sorted(set(errors))))
        return 1
    print(f"Release scan passed: {len(paths)} files; no credential shapes or private machine paths found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

