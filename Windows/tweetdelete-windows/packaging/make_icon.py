"""One-off script: generates icon.ico used for the tray icon, the exe icon,
and the Start Menu / desktop shortcuts. Run once with Pillow installed:

    python make_icon.py

Not needed at build time by end users - the resulting icon.ico is checked
into packaging/ and just referenced by build.spec and installer.iss.
"""
from PIL import Image, ImageDraw

# Nexus palette teal, matching the web app's own accent color.
BG = (1, 105, 111, 255)  # #01696F
FG = (247, 246, 242, 255)  # #F7F6F2 (off-white, matches app background)

SIZE = 256


def build_base_image():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded square background.
    pad = 8
    d.rounded_rectangle([pad, pad, SIZE - pad, SIZE - pad], radius=56, fill=BG)

    # A simple trash-can glyph (delete metaphor), drawn geometrically so it
    # doesn't depend on any external font or icon asset.
    cx = SIZE // 2
    # Lid
    lid_y = 88
    d.rounded_rectangle([cx - 62, lid_y, cx + 62, lid_y + 16], radius=6, fill=FG)
    # Handle
    d.rounded_rectangle([cx - 22, lid_y - 18, cx + 22, lid_y + 2], radius=6, fill=FG)
    # Body (trapezoid-ish via polygon)
    body_top = lid_y + 20
    body_bottom = 200
    d.polygon(
        [
            (cx - 54, body_top),
            (cx + 54, body_top),
            (cx + 44, body_bottom),
            (cx - 44, body_bottom),
        ],
        fill=FG,
    )
    # Cut the body's bottom corners to round them slightly by drawing bg dots
    # (skipped for simplicity at icon size - looks fine without).

    # Three vertical ribs on the body, in the accent color, for a bit of detail.
    rib_top = body_top + 20
    rib_bottom = body_bottom - 16
    for dx in (-20, 0, 20):
        d.rounded_rectangle([cx + dx - 5, rib_top, cx + dx + 5, rib_bottom], radius=4, fill=BG)

    return img


def main():
    img = build_base_image()
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save("icon.ico", format="ICO", sizes=sizes)
    print("Wrote packaging/icon.ico")


if __name__ == "__main__":
    main()
