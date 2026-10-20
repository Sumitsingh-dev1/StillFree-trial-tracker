import argparse
import json
import logging
from dataclasses import asdict

from app.services.reminders import run_expiry_check


def main():
    parser = argparse.ArgumentParser(description="Send due StillFree trial reminders.")
    parser.add_argument("--dry-run", action="store_true", help="Count due trials without sending or marking them.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    result = run_expiry_check(dry_run=args.dry_run)
    print(json.dumps(asdict(result)))
    raise SystemExit(1 if result.failed else 0)


if __name__ == "__main__":
    main()
