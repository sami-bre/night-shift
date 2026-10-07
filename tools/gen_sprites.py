#!/usr/bin/env python3
"""Generate the pixel-art sprite sheet for The Night Shift.

Everything the city is made of is drawn here in code — the PNG in web/assets/
has full provenance (this file). Run: make sprites (or python3 tools/gen_sprites.py)

Output: web/assets/sprites.png + web/assets/sprites.json (frame coords +
window grids so the JS lights exactly the windows the art defines).
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "web" / "assets"

# 16-bit-ish palette
BODY = "#39466b"
BODY_DARK = "#2b3552"
BODY_LIGHT = "#4d5f8c"
ROOF = "#232b45"
DOOR = "#1d2438"
BRICK = "#5a4a52"
BRICK_DARK = "#493b42"
CONCRETE = "#7d8aa5"
CONCRETE_DARK = "#5f6b83"
GLASS = "#10141f"
YELLOW = "#ffd75e"
ORANGE = "#ff9d3b"
GREEN = "#63c74d"
BLUE = "#4d9be6"
RED = "#e64539"
WHITE = "#e8e8ef"
DARK = "#151a28"
SKIN = "#eab188"
STEEL = "#8a97b5"
PIPE = "#c46a4a"

WINDOW = (GLASS, "#f5ee8c", "#ffd75e")  # dark, warm lit, bright lit

MANIFEST: dict = {"tile": 1, "frames": {}, "buildings": {}}


def px(d: ImageDraw.ImageDraw, x: int, y: int, color: str) -> None:
    d.point((x, y), fill=color)


def rect(d: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, color: str) -> None:
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=color)


# ---------------------------------------------------------------- buildings

def draw_bank(d: ImageDraw.ImageDraw, ox: int, oy: int, w: int = 96, h: int = 112) -> None:
    """The bank (payments-api): stone building, columns, 'BANK' sign band."""
    rect(d, ox, oy + 8, w, h - 8, CONCRETE)
    rect(d, ox, oy + 8, w, 4, CONCRETE_DARK)
    # roof cornice
    rect(d, ox - 2, oy + 4, w + 4, 5, CONCRETE_DARK)
    # pediment triangle
    for i in range(20):
        rect(d, ox + w // 2 - i, oy + 4 - 0, 1, 1, CONCRETE_DARK)
    # sign band
    rect(d, ox + 8, oy + 18, w - 16, 10, DARK)
    # columns
    for cx in (10, 30, w - 16, w - 30):
        rect(d, ox + cx, oy + 32, 6, h - 46, CONCRETE_DARK)
    # entrance
    rect(d, ox + w // 2 - 10, oy + h - 26, 20, 26, DOOR)
    rect(d, ox + w // 2 - 7, oy + h - 22, 14, 10, "#2c3fbf")
    # steps
    rect(d, ox + w // 2 - 14, oy + h - 2, 28, 2, CONCRETE)
    # coin emblem
    rect(d, ox + w // 2 - 4, oy + h - 42, 8, 8, YELLOW)
    px(d, ox + w // 2 - 2, oy + h - 40, "#a67c1b")
    px(d, ox + w // 2 + 1, oy + h - 37, "#a67c1b")


def draw_town_hall(d: ImageDraw.ImageDraw, ox: int, oy: int, w: int = 80, h: int = 96) -> None:
    """Town hall (control plane) with a clock and flag pole."""
    rect(d, ox, oy + 8, w, h - 8, BRICK)
    rect(d, ox, oy + 8, w, 3, BRICK_DARK)
    rect(d, ox - 2, oy + 4, w + 4, 5, BRICK_DARK)
    # tower
    rect(d, ox + w // 2 - 10, oy - 12, 20, 16, BRICK)
    rect(d, ox + w // 2 - 12, oy - 16, 24, 5, BRICK_DARK)
    # clock face
    rect(d, ox + w // 2 - 5, oy - 9, 10, 10, WHITE)
    px(d, ox + w // 2, oy - 6, DARK)
    px(d, ox + w // 2 + 2, oy - 4, DARK)
    # door + arch
    rect(d, ox + w // 2 - 8, oy + h - 24, 16, 24, DOOR)
    rect(d, ox + w // 2 - 10, oy + h - 26, 20, 2, BRICK_DARK)
    # windows are placed by manifest; add sills
    for yy in (28, 52):
        rect(d, ox + 6, oy + yy + 12, w - 12, 1, BRICK_DARK)
    # flag
    rect(d, ox + w - 12, oy - 16, 1, 12, STEEL)
    rect(d, ox + w - 11, oy - 16, 6, 4, RED)


def draw_hospital(d: ImageDraw.ImageDraw, ox: int, oy: int, w: int = 80, h: int = 88) -> None:
    rect(d, ox, oy + 6, w, h - 6, WHITE)
    rect(d, ox, oy + 6, w, 3, CONCRETE)
    rect(d, ox - 2, oy + 3, w + 4, 4, CONCRETE_DARK)
    # red cross
    rect(d, ox + w // 2 - 3, oy - 8, 6, 14, RED)
    rect(d, ox + w // 2 - 7, oy - 4, 14, 6, RED)
    rect(d, ox + w // 2 - 9, oy + 2, 18, 4, RED)
    # entrance canopy
    rect(d, ox + w // 2 - 14, oy + h - 30, 28, 4, BLUE)
    rect(d, ox + w // 2 - 10, oy + h - 26, 20, 26, DOOR)
    rect(d, ox + 8, oy + h - 2, w - 16, 2, CONCRETE)


def draw_power_plant(d: ImageDraw.ImageDraw, ox: int, oy: int, w: int = 72, h: int = 104) -> None:
    """Power plant (the agent): cooling stack + fuse box, glows when awake."""
    rect(d, ox, oy + 16, w, h - 16, BODY_DARK)
    rect(d, ox, oy + 16, w, 3, ROOF)
    rect(d, ox - 2, oy + 12, w + 4, 5, ROOF)
    # stack
    rect(d, ox + w - 22, oy - 24, 14, h - 8, BODY)
    rect(d, ox + w - 24, oy - 27, 18, 4, BODY_DARK)
    for i, col in enumerate((RED, WHITE, RED)):
        rect(d, ox + w - 22, oy - 20 + i * 5, 14, 3, col)
    # hazard stripes near door
    for i in range(6):
        rect(d, ox + 6 + i * 10, oy + h - 30, 5, 4, YELLOW if i % 2 == 0 else DARK)
    rect(d, ox + w // 2 - 8, oy + h - 24, 16, 24, DOOR)
    # fuse panel
    rect(d, ox + 6, oy + 26, 12, 16, STEEL)
    px(d, ox + 9, oy + 30, GREEN)
    px(d, ox + 13, oy + 30, RED)
    px(d, ox + 9, oy + 36, ORANGE)


def draw_house(d: ImageDraw.ImageDraw, ox: int, oy: int, w: int = 56, h: int = 64,
               body: str = BODY, roof: str = ROOF) -> None:
    rect(d, ox, oy + 14, w, h - 14, body)
    # pitched roof
    for i in range(h // 5):
        row = w - i * 4
        if row <= 0:
            break
        rect(d, ox + (w - row) // 2, oy + 14 - i * 5 - 4, row, 5, roof)
    rect(d, ox + w // 2 - 6, oy + h - 20, 12, 20, DOOR)
    rect(d, ox + w // 2 - 8, oy + h - 22, 16, 2, roof)


def windows_grid(x: int, y: int, cols: int, rows: int, cw: int = 8, ch: int = 10,
                 gap_x: int = 8, gap_y: int = 12) -> list[dict]:
    out = []
    for r in range(rows):
        for c in range(cols):
            out.append({"x": x + c * (cw + gap_x), "y": y + r * (ch + gap_y),
                        "w": cw, "h": ch})
    return out


# ------------------------------------------------------------- small sprites

def draw_repair_sprite(d: ImageDraw.ImageDraw, ox: int, oy: int, frame: str) -> None:
    """16x16 repair tech with hard hat. Frames: walk0..3, hammer0, hammer1."""
    # legs
    if frame == "walk0":
        rect(d, ox + 4, oy + 12, 3, 4, "#2c3fbf")
        rect(d, ox + 9, oy + 12, 3, 4, "#2c3fbf")
    elif frame == "walk1":
        rect(d, ox + 3, oy + 12, 3, 4, "#2c3fbf")
        rect(d, ox + 10, oy + 12, 3, 3, "#2c3fbf")
    elif frame == "walk2":
        rect(d, ox + 4, oy + 12, 3, 4, "#2c3fbf")
        rect(d, ox + 9, oy + 12, 3, 4, "#2c3fbf")
    elif frame == "walk3":
        rect(d, ox + 5, oy + 12, 3, 3, "#2c3fbf")
        rect(d, ox + 8, oy + 12, 3, 4, "#2c3fbf")
    elif frame.startswith("hammer"):
        rect(d, ox + 4, oy + 12, 3, 4, "#2c3fbf")
        rect(d, ox + 9, oy + 12, 3, 4, "#2c3fbf")
    # body (hi-vis vest)
    rect(d, ox + 3, oy + 6, 10, 6, ORANGE)
    rect(d, ox + 5, oy + 7, 2, 4, YELLOW)
    rect(d, ox + 9, oy + 7, 2, 4, YELLOW)
    # head
    rect(d, ox + 4, oy + 2, 8, 4, SKIN)
    px(d, ox + 6, oy + 4, DARK)   # eye
    px(d, ox + 9, oy + 4, DARK)
    # hard hat
    rect(d, ox + 3, oy, 10, 2, YELLOW)
    rect(d, ox + 5, oy - 2, 6, 2, YELLOW)
    # arms + hammer
    if frame.startswith("hammer"):
        up = frame == "hammer0"
        rect(d, ox + 12, oy + (2 if up else 6), 2, 4, SKIN)
        hx, hy = (ox + 13, oy - 2) if up else (ox + 13, oy + 9)
        rect(d, hx, hy, 2, 4, "#7a5a3a")
        rect(d, hx, hy - 1, 4, 2, STEEL)
    else:
        rect(d, ox + 2, oy + 7, 2, 4, SKIN)
        rect(d, ox + 12, oy + 7, 2, 4, SKIN)


def draw_smoke_puff(d: ImageDraw.ImageDraw, ox: int, oy: int, frame: int) -> None:
    shades = ["#565f6e", "#6e7887", "#8a93a3"]
    c = shades[frame]
    sizes = [(2, 6, 2, 5), (1, 4, 3, 7), (0, 3, 3, 4)]
    sx, sy, w, h = sizes[frame]
    rect(d, ox + sx, oy + sy, w, h, c)
    rect(d, ox + sx - 1, oy + sy + 2, 1, 2, c)


def draw_magnifier(d: ImageDraw.ImageDraw, ox: int, oy: int) -> None:
    rect(d, ox + 3, oy + 2, 8, 8, "#9fd3ff")
    rect(d, ox + 4, oy + 3, 6, 6, "#d7ecff")
    rect(d, ox + 10, oy + 9, 2, 1, STEEL)
    rect(d, ox + 11, oy + 10, 2, 2, STEEL)
    rect(d, ox + 3, oy + 2, 8, 1, WHITE)
    rect(d, ox + 3, oy + 2, 1, 8, WHITE)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGBA", (512, 320), (0, 0, 0, 0))
    d = ImageDraw.Draw(sheet)

    # buildings anchored at their bottom-left; ground line at canvas bottom
    ground = 320
    specs = {
        "bank": dict(w=96, h=112, draw=draw_bank),
        "town_hall": dict(w=80, h=96, draw=draw_town_hall),
        "hospital": dict(w=80, h=88, draw=draw_hospital),
        "power_plant": dict(w=72, h=104, draw=draw_power_plant),
        "house_a": dict(w=56, h=64, draw=lambda dd, x, y: draw_house(dd, x, y, body=BODY)),
        "house_b": dict(w=56, h=64, draw=lambda dd, x, y: draw_house(dd, x, y, body="#43507a")),
    }
    x = 0
    for name, spec in specs.items():
        w, h = spec["w"], spec["h"]
        spec["draw"](d, x, ground - h)
        MANIFEST["buildings"][name] = {
            "frame": {"x": x, "y": ground - h, "w": w, "h": h}}
        x += w + 16

    # window grids per building (relative to frame origin)
    b = MANIFEST["buildings"]
    b["bank"]["windows"] = windows_grid(30, 44, 3, 2, cw=6, ch=8, gap_x=14, gap_y=18)
    b["town_hall"]["windows"] = windows_grid(8, 28, 4, 2, cw=7, ch=9, gap_x=10, gap_y=15)
    b["hospital"]["windows"] = windows_grid(8, 22, 4, 2, cw=7, ch=8, gap_x=10, gap_y=14)
    b["power_plant"]["windows"] = windows_grid(6, 40, 2, 2, cw=7, ch=8, gap_x=10, gap_y=14)
    b["house_a"]["windows"] = windows_grid(8, 26, 2, 1, cw=8, ch=9, gap_x=14)
    b["house_b"]["windows"] = windows_grid(8, 26, 2, 1, cw=8, ch=9, gap_x=14)

    # repair sprite frames: 6 frames of 16x18 (top band, clear of buildings)
    frames = ["walk0", "walk1", "walk2", "walk3", "hammer0", "hammer1"]
    fx = 0
    for fr in frames:
        draw_repair_sprite(d, fx, 24, fr)
        MANIFEST["frames"][f"repair_{fr}"] = {"x": fx, "y": 20, "w": 16, "h": 20}
        fx += 18

    # smoke puffs
    for i in range(3):
        draw_smoke_puff(d, 130 + i * 12, 24, i)
        MANIFEST["frames"][f"smoke{i}"] = {"x": 130 + i * 12, "y": 24, "w": 8, "h": 10}

    draw_magnifier(d, 176, 22)
    MANIFEST["frames"]["magnifier"] = {"x": 176, "y": 22, "w": 14, "h": 14}

    sheet.save(OUT_DIR / "sprites.png")
    (OUT_DIR / "sprites.json").write_text(json.dumps(MANIFEST, indent=1))
    print(f"wrote {OUT_DIR / 'sprites.png'} ({sheet.size[0]}x{sheet.size[1]}) + sprites.json")


if __name__ == "__main__":
    main()
