"""Manual-only live smoke; BLOCKED is a failing job with preserved evidence."""
import argparse
import sys
from pathlib import Path

from xiamen_marine_report.fetch import fetch_run
from xiamen_marine_report.models import parse_date
from xiamen_marine_report.provenance import write_json
from xiamen_marine_report.validate import validate_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    date = parse_date(args.date).isoformat()
    run = fetch_run(date, ["haicang", "wuyuan", "huandong"], Path("runs/live"))
    validation = validate_run(run)
    write_json(run / "validation.json", validation)
    print(f"{date}: {validation['state']}; see evidence artifact for gaps")
    return 0 if validation["state"] == "VALIDATED" else 2


if __name__ == "__main__":
    sys.exit(main())

