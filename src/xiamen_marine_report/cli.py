"""Cross-platform CLI with machine-readable JSON and fail-closed exit codes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .models import parse_date
from .provenance import write_json


def _date(value: str) -> str:
    try:
        return parse_date(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Date must be a valid YYYY-MM-DD") from exc


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="marine-report", description="厦门三地海况证据与未批准参考报告；不提供下水许可。")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="Fetch available public evidence; missing required data blocks the run")
    fetch.add_argument("--date", required=True, type=_date)
    fetch.add_argument("--sites", nargs="+", choices=("haicang", "wuyuan", "huandong"), default=["haicang", "wuyuan", "huandong"])
    fetch.add_argument("--output", required=True, type=Path)
    fetch.add_argument("--timeout", type=float, default=20)
    fetch.add_argument("--evidence-dir", type=Path, help="Optional independently verified local tide/official evidence bundle")
    fetch.add_argument("--commercial", action="store_true", help="Apply commercial-use source restrictions")
    validate = commands.add_parser("validate", help="Audit frozen hashes, units, grids, official bodies and all hourly coverage")
    validate.add_argument("--run", required=True, type=Path)
    render = commands.add_parser("render", help="Render a new frozen UNAPPROVED_REFERENCE candidate")
    render.add_argument("--run", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)
    render.add_argument("--allow-partial", action="store_true", help="Explicitly produce a marked incomplete reference view")
    review = commands.add_parser("review", help="Record an independent decision bound to final candidate hashes")
    review.add_argument("--run", required=True, type=Path)
    review.add_argument("--decision", required=True, choices=("approve", "reject"))
    review.add_argument("--review-file", required=True, type=Path)
    review.add_argument("--candidate", type=Path, help="Optional contained candidate path; otherwise latest candidate")
    demo = commands.add_parser("demo", help="Entirely offline synthetic demonstration; never auto-approved")
    demo.add_argument("--fixture", type=Path)
    demo.add_argument("--date", type=_date, default="2026-10-10")
    demo.add_argument("--output", required=True, type=Path)
    demo.add_argument("--allow-partial", action="store_true")
    app = commands.add_parser("app", help="Open the local HTML app (binds to 127.0.0.1)")
    app.add_argument("--port", type=int, default=8765)
    app.add_argument("--output", type=Path, default=Path("runs/app"))
    return root


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.command == "fetch":
            if args.timeout <= 0 or args.timeout > 120:
                raise ValueError("Timeout must be between 0 and 120 seconds")
            from .fetch import fetch_run
            from .validate import validate_run
            run = fetch_run(args.date, list(dict.fromkeys(args.sites)), args.output, timeout=args.timeout,
                            evidence_dir=args.evidence_dir, commercial=args.commercial)
            validation = validate_run(run)
            write_json(run / "validation.json", validation)
            _emit({"run": str(run), "state": validation["state"], "validation": validation})
            return 0 if validation["state"] == "VALIDATED" else 2
        if args.command == "validate":
            from .validate import validate_run
            result = validate_run(args.run)
            if args.run.is_dir():
                write_json(args.run / "validation.json", result)
            _emit(result)
            return 0 if result["state"] == "VALIDATED" else 2
        if args.command == "render":
            from .provenance import read_json
            from .render import render_run
            candidate = render_run(args.run, args.output, allow_partial=args.allow_partial)
            result = read_json(candidate)
            _emit({"candidate": str(candidate), "state": result["state"],
                   "validation_state": result["validation_state"], "delivery_label": "UNAPPROVED_REFERENCE"})
            return 0 if result["validation_state"] == "VALIDATED" else 2
        if args.command == "review":
            from .review import review_candidate
            result = review_candidate(args.run, args.decision, args.review_file, args.candidate)
            _emit(result)
            return 0
        if args.command == "demo":
            from .demo import demo_run
            result = demo_run(args.output, args.fixture, args.date, allow_partial=args.allow_partial)
            _emit(result)
            return 0 if result.get("validation_state") == "VALIDATED" else 2
        if args.command == "app":
            if not 1 <= args.port <= 65535:
                raise ValueError("Port must be between 1 and 65535")
            from .web import serve
            serve(port=args.port, output_root=args.output)
            return 0
    except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
        # Do not print third-party exception strings that may contain credentialed URLs.
        _emit({"state": "BLOCKED", "error_type": type(exc).__name__,
               "message": "Command failed. Check frozen evidence, paths and command arguments; no approval was issued."})
        return 3
    return 3


if __name__ == "__main__":
    sys.exit(main())
