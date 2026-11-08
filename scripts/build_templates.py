"""Build anchor templates for M1.0 template matching.

Captures the game in the states the anchors belong to (in-game / phone / map),
crops every anchor region at native resolution and writes:

    assets/templates/<game>/<state>/<anchor_id>.png
    assets/templates/<game>/index.json

With ``--write-config`` it also rewrites ``configs/<game>.yaml`` so that each
anchor gets ``template: assets/templates/<game>/<state>/<id>.png``.  Anchors
whose state was not captured (e.g. ``title_enter`` when the game is already
running) keep ``template: null`` and keep working as coordinate anchors.

Pillow is only needed by this developer tool: ``pip install -e .[tools]``.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from kiseki_runtime.tools import Runtime

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "templates"


def state_of(anchor_id: str) -> str:
    if anchor_id == "title_enter":
        return "title"
    if anchor_id.startswith("phone_"):
        return "phone"
    if anchor_id.startswith("map_"):
        return "map"
    return "ingame"


def grab_states(runtime: Runtime, game: str) -> dict[str, Path]:
    screens: dict[str, Path] = {}

    def grab(state: str) -> None:
        shot = runtime.game_capture(game, label=f"templates-{state}", scale=1.0)
        screens[state] = ROOT / shot["relative_path"]
        print(f"  captured {state}: {shot['relative_path']} {shot['width']}x{shot['height']}")

    # The game must start from the normal in-game HUD (no panel open): a single
    # Esc toggles the phone, so we capture ingame first and only toggle panel
    # states afterwards.
    grab("ingame")

    runtime.game_skill(game, "phone_open", dry_run=False)
    time.sleep(2.0)
    grab("phone")
    runtime.game_skill(game, "phone_close", dry_run=False)
    time.sleep(1.5)

    runtime.game_act(game, [{"type": "key", "keys": ["m"], "action": "tap"}], dry_run=False)
    time.sleep(2.5)
    grab("map")
    runtime.game_skill(game, "close_panel", dry_run=False)
    time.sleep(1.0)
    return screens


def crop_templates(
    runtime: Runtime, game: str, screens: dict[str, Path]
) -> list[dict[str, object]]:
    from PIL import Image

    profile = runtime.profile(game)
    anchors = profile.get("ui", {}).get("anchors", [])
    rows: list[dict[str, object]] = []
    for state, path in screens.items():
        image = Image.open(path)
        width, height = image.size
        folder = OUT / game / state
        folder.mkdir(parents=True, exist_ok=True)
        for anchor in anchors:
            anchor_id = str(anchor.get("id", ""))
            if state_of(anchor_id) != state:
                continue
            x0, y0, x1, y1 = anchor["region"]
            box = (int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height))
            crop = image.crop(box)
            target = folder / f"{anchor_id}.png"
            crop.save(target)
            rows.append(
                {
                    "game": game,
                    "state": state,
                    "id": anchor_id,
                    "path": str(target.relative_to(ROOT)).replace("\\", "/"),
                    "size": [crop.width, crop.height],
                    "region": anchor["region"],
                }
            )
        print(f"  {state}: {sum(1 for row in rows if row['state'] == state)} templates")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / game / "index.json").write_bytes(
        (json.dumps(rows, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return rows


def write_config(game: str, rows: list[dict[str, object]]) -> int:
    """Point every captured anchor at its template; leave the rest untouched."""

    config = ROOT / "configs" / f"{game}.yaml"
    lines = config.read_text(encoding="utf-8").splitlines()
    by_id = {str(row["id"]): str(row["path"]) for row in rows}
    current: str | None = None
    changed = 0
    for index, line in enumerate(lines):
        match = re.match(r"^\s*- id:\s*(\S+)\s*$", line)
        if match:
            current = match.group(1)
            continue
        if current and re.match(r"^\s*template:\s*", line):
            wanted = by_id.get(current)
            if wanted:
                indent = line[: len(line) - len(line.lstrip())]
                replacement = f"{indent}template: {wanted}"
                if line != replacement:
                    lines[index] = replacement
                    changed += 1
            current = None
    config.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Build template images for a Game Profile")
    parser.add_argument("game", nargs="?", default="hsr", help="profile id, default hsr")
    parser.add_argument(
        "--write-config", action="store_true", help="also rewrite template: fields in configs/<game>.yaml"
    )
    args = parser.parse_args()

    runtime = Runtime()
    runtime.game_focus(args.game)
    screens = grab_states(runtime, args.game)
    rows = crop_templates(runtime, args.game, screens)
    print(f"{len(rows)} templates under {OUT / args.game}")
    if args.write_config:
        changed = write_config(args.game, rows)
        print(f"configs/{args.game}.yaml: {changed} template lines updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
