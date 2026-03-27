from __future__ import annotations

import argparse
import json
import sys

import launcher_core


def main() -> int:
    parser = argparse.ArgumentParser(description="Private Note launcher control CLI")
    parser.add_argument("command", choices=["status", "shutdown-all"])
    args = parser.parse_args()

    if args.command == "status":
        payload = launcher_core.get_status()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "shutdown-all":
        launcher_core.force_shutdown_all()
        return 0

    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
