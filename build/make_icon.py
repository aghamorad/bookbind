#!/usr/bin/env python3
"""Build Bookbind's app icon from a flat source image.

The source art is a rounded square sitting on a solid cream page. macOS icons need
the rounded square itself on transparency, sized to Apple's grid (the shape is 824
of a 1024 canvas, centred), so this keys out the page, then writes both the .icns
for the app bundle and the small png the web UI shows in its header.

    python3 build/make_icon.py <source.png>
"""
import os
import subprocess
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CANVAS, TILE = 1024, 824          # Apple's app-icon grid
SPEC = [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
        (128, "128x128"), (256, "128x128@2x"), (256, "256x256"),
        (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x")]

FLOOR = 150        # luminance: the square is far below this, the page and its
                   # drop shadow far above, so the gap between them is the knife
STEPS = 200        # growth is capped by FLOOR anyway, so overshooting is free


def grow(mask):
    """One dilation step."""
    return mask.filter(ImageFilter.MaxFilter(3))


def keyed(src):
    """Drop the page and its drop shadow, leaving only the rounded square."""
    im = Image.open(src).convert("RGB")
    w, h = im.size

    # flood the page inwards from every corner; anything untouched is art
    flood = im.copy()
    for xy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        ImageDraw.floodfill(flood, xy, (255, 0, 255), thresh=60)

    r, g, b = flood.split()
    page = ImageChops.darker(ImageChops.darker(r.point([0] * 255 + [255]),
                                               g.point([255] + [0] * 255)),
                             b.point([0] * 255 + [255]))

    # the flood alone stops dead at the drop shadow, which then reads as a pale
    # halo on a dark desktop.  Grow it outwards through everything brighter than
    # the square: the luminance floor is what stops the growth from ever leaking
    # into the book's own cream pages.
    bright = im.convert("L").point([0 if v < FLOOR else 255 for v in range(256)])
    for _ in range(STEPS):
        wider = ImageChops.darker(grow(page), bright)
        if ImageChops.difference(wider, page).getbbox() is None:
            break
        page = wider

    art = ImageChops.invert(page).filter(ImageFilter.GaussianBlur(0.8))
    out = im.convert("RGBA")
    out.putalpha(art)
    return out.crop(out.getchannel("A").getbbox())


def square(art):
    """Scale to Apple's tile size and centre it on the icon canvas."""
    w, h = art.size
    s = TILE / max(w, h)
    art = art.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.paste(art, ((CANVAS - art.width) // 2, (CANVAS - art.height) // 2), art)
    return canvas


def main(src):
    icon = square(keyed(src))

    iset = os.path.join(HERE, "AppIcon.iconset")
    os.makedirs(iset, exist_ok=True)
    for px, name in SPEC:
        icon.resize((px, px), Image.LANCZOS).save(os.path.join(iset, f"icon_{name}.png"))

    icns = os.path.join(HERE, "AppIcon.icns")
    subprocess.run(["iconutil", "-c", "icns", iset, "-o", icns], check=True)

    res = os.path.join(ROOT, "Bookbind.app", "Contents", "Resources")
    if os.path.isdir(res):
        subprocess.run(["cp", icns, os.path.join(res, "AppIcon.icns")], check=True)
    icon.resize((256, 256), Image.LANCZOS).save(os.path.join(ROOT, "static", "icon.png"))

    # proof sheet: how it reads on both a light and a dark desktop
    proof = Image.new("RGB", (1120, 560), "#f2f2f4")
    proof.paste(Image.new("RGB", (560, 560), "#1b1c1f"), (560, 0))
    for i, bg in enumerate(("#f2f2f4", "#1b1c1f")):
        tile = Image.new("RGB", (560, 560), bg)
        big = icon.resize((420, 420), Image.LANCZOS)
        tile.paste(big, (70, 70), big)
        proof.paste(tile, (i * 560, 0))
    proof.save("/tmp/bookbind-icon-proof.png")
    print("wrote", icns, "and static/icon.png; proof at /tmp/bookbind-icon-proof.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else
         "/Users/Morad/Desktop/2eccd4fc-5409-4806-af2c-2efc0a129259.png")
