#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def _ensure_pillow():
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is required: pip install pillow", file=sys.stderr)
        raise SystemExit(1)


def write_ico(src: Path, dest: Path) -> None:
    from PIL import Image

    img = Image.open(src).convert("RGBA")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icons = [img.resize(size, Image.Resampling.LANCZOS) for size in sizes]
    dest.parent.mkdir(parents=True, exist_ok=True)
    icons[-1].save(dest, format="ICO", sizes=sizes)
    print(f"Wrote {dest}")


def write_png_sizes(src: Path, out_dir: Path, sizes: list[int]) -> None:
    from PIL import Image

    img = Image.open(src).convert("RGBA")
    out_dir.mkdir(parents=True, exist_ok=True)
    for size in sizes:
        dest = out_dir / f"vklauncher-{size}.png"
        img.resize((size, size), Image.Resampling.LANCZOS).save(dest, format="PNG")
        print(f"Wrote {dest}")


def main() -> int:
    _ensure_pillow()
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "icons")
    args = parser.parse_args()

    universal = ASSETS / "icon_universal.png"
    mac = ASSETS / "icon_mac.png"
    if not universal.exists():
        print(f"Missing {universal}", file=sys.stderr)
        return 1

    write_ico(universal, args.out / "vklauncher.ico")
    write_png_sizes(universal, args.out, [16, 32, 48, 64, 128, 256, 512])
    (args.out / "icon_universal.png").write_bytes(universal.read_bytes())
    if mac.exists():
        (args.out / "icon_mac.png").write_bytes(mac.read_bytes())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
