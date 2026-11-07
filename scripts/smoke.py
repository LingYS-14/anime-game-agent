"""Read-only smoke test for a running game.

Usage:
    python scripts/smoke.py hsr
    python scripts/smoke.py endfield-map --capture

It never sends input: it only focuses the window, captures a PNG and prints the
runtime state. Exit code 0 means the runtime can see the game.
"""

from __future__ import annotations

import argparse
import json
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiseki_runtime.tools import Runtime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="KISEKI read-only smoke test")
    parser.add_argument("game_id", help="profile id, e.g. hsr or endfield-map")
    parser.add_argument("--capture", action="store_true", help="also save a PNG capture")
    parser.add_argument("--label", default="smoke", help="capture label")
    args = parser.parse_args()

    runtime = Runtime()
    summary: dict = {"game_id": args.game_id}
    try:
        summary["status"] = runtime.game_status()
        summary["focus"] = runtime.game_focus(args.game_id)
        if args.capture:
            summary["capture"] = runtime.game_capture(args.game_id, label=args.label)
    except Exception as error:  # noqa: BLE001 - report, do not crash
        summary["ok"] = False
        summary["error"] = getattr(error, "to_dict", lambda: str(error))()
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 1

    summary["ok"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
