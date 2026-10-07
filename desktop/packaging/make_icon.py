"""Generate the NetWatch application icon.

A beacon pulsing inside a HUD reticle: concentric signal arcs in neon cyan
radiating from a magenta core, inside angular corner brackets. Drawn at 8x and
downsampled so the thin strokes stay smooth at 16px.

    python packaging/make_icon.py            -> packaging/netwatch.ico (+ .png)
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent
S = 1024                      # working canvas; downsampled at the end
SS = 4                        # extra supersample for the glow pass

BG_TOP = (10, 14, 20)
BG_BOT = (16, 10, 26)
CYAN = (45, 236, 220)
CYAN_DIM = (26, 150, 150)
MAGENTA = (255, 60, 160)
VIOLET = (150, 90, 255)


def rounded_bg(size: int) -> Image.Image:
    """Dark tile with a vertical gradient and a soft neon rim."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel((0, y), (
            int(BG_TOP[0] + (BG_BOT[0] - BG_TOP[0]) * t),
            int(BG_TOP[1] + (BG_BOT[1] - BG_TOP[1]) * t),
            int(BG_TOP[2] + (BG_BOT[2] - BG_TOP[2]) * t),
            255,
        ))
    grad = grad.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    # inner rim light
    rim = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle(
        [int(size * .02)] * 2 + [size - int(size * .02)] * 2,
        radius=int(size * .20), outline=CYAN + (70,), width=max(2, size // 180))
    img.alpha_composite(rim)
    return img


def draw_beacon(size: int) -> Image.Image:
    """The glowing part: arcs + core + reticle, on transparency."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2
    unit = size / 100.0

    # Signal arcs: three expanding pulses, fading outward. Drawn as open arcs
    # on the upper-right so it reads as emission rather than a target.
    for i, (r, w, col, a) in enumerate([
        (20, 4.6, CYAN, 255),
        (31, 4.0, CYAN, 190),
        (42, 3.4, CYAN_DIM, 140),
    ]):
        rr = r * unit
        d.arc([cx - rr, cy - rr, cx + rr, cy + rr],
              start=-78, end=18, fill=col + (a,), width=int(w * unit))
        d.arc([cx - rr, cy - rr, cx + rr, cy + rr],
              start=102, end=198, fill=col + (int(a * .55),), width=int(w * unit))

    # Core: magenta node with a violet halo.
    for rad, col in ((11, VIOLET + (90,)), (8, MAGENTA + (255,))):
        rr = rad * unit
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=col)
    rr = 3.2 * unit
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(255, 235, 250, 255))

    # HUD corner brackets.
    m, ln, w = 13 * unit, 17 * unit, 3.6 * unit
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x = cx + sx * (size / 2 - m)
        y = cy + sy * (size / 2 - m)
        d.line([x, y, x - sx * ln, y], fill=CYAN + (225,), width=int(w))
        d.line([x, y, x, y - sy * ln], fill=CYAN + (225,), width=int(w))

    # Scanlines across the core for a CRT feel.
    for y in range(int(cy - 14 * unit), int(cy + 14 * unit), max(2, int(2.6 * unit))):
        d.line([cx - 15 * unit, y, cx + 15 * unit, y], fill=(0, 0, 0, 46), width=1)
    return img


def build(size: int = S) -> Image.Image:
    base = rounded_bg(size)

    big = size * 2
    beacon = draw_beacon(big)
    glow = beacon.filter(ImageFilter.GaussianBlur(radius=big * 0.022))
    glow = Image.alpha_composite(Image.new("RGBA", (big, big), (0, 0, 0, 0)), glow)

    out = base.copy()
    out.alpha_composite(glow.resize((size, size), Image.LANCZOS))
    out.alpha_composite(glow.resize((size, size), Image.LANCZOS))   # bloom
    out.alpha_composite(beacon.resize((size, size), Image.LANCZOS))
    return out


def main() -> None:
    icon = build()
    png = OUT / "netwatch.png"
    icon.resize((512, 512), Image.LANCZOS).save(png)

    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    ico = OUT / "netwatch.ico"
    icon.save(ico, format="ICO", sizes=sizes)
    print(f"wrote {ico}  ({ico.stat().st_size:,} bytes, {len(sizes)} sizes)")
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
