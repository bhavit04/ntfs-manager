"""Generate AppIcon.icns for NTFS Manager (run inside build_venv)."""
import os
import subprocess
from PIL import Image, ImageDraw, ImageFont

BG1 = (124, 106, 247)   # accent purple
BG2 = (90, 79, 207)     # deeper purple
FG  = (255, 255, 255)


def _font(size):
    for path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
    ]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def render(px: int) -> Image.Image:
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Vertical gradient rounded-rect background (macOS-style squircle-ish)
    margin = int(px * 0.06)
    radius = int(px * 0.225)
    grad = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(px):
        t = y / px
        r = int(BG1[0] * (1 - t) + BG2[0] * t)
        g = int(BG1[1] * (1 - t) + BG2[1] * t)
        b = int(BG1[2] * (1 - t) + BG2[2] * t)
        gd.line([(0, y), (px, y)], fill=(r, g, b, 255))
    mask = Image.new("L", (px, px), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([margin, margin, px - margin, px - margin],
                         radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # Drive body
    dw, dh = int(px * 0.50), int(px * 0.34)
    dx, dy = (px - dw) // 2, int(px * 0.30)
    d.rounded_rectangle([dx, dy, dx + dw, dy + dh],
                        radius=int(px * 0.045),
                        fill=(255, 255, 255, 40),
                        outline=(255, 255, 255, 220),
                        width=max(2, int(px * 0.012)))
    # Activity light
    lr = int(px * 0.018)
    lcx, lcy = dx + dw - int(px * 0.06), dy + dh - int(px * 0.07)
    d.ellipse([lcx - lr, lcy - lr, lcx + lr, lcy + lr], fill=(74, 222, 128, 255))

    # "NTFS" label (skip on tiny sizes where it would be illegible / break metrics)
    fs = int(px * 0.135)
    if fs >= 10:
        f = _font(fs)
        text = "NTFS"
        try:
            tb = d.textbbox((0, 0), text, font=f)
            tw = tb[2] - tb[0]
            d.text(((px - tw) / 2 - tb[0], dy + dh + int(px * 0.05) - tb[1]),
                   text, font=f, fill=FG)
        except OSError:
            pass
    return img


def main():
    iconset = "AppIcon.iconset"
    os.makedirs(iconset, exist_ok=True)
    specs = [
        (16, "16x16"), (32, "16x16@2x"),
        (32, "32x32"), (64, "32x32@2x"),
        (128, "128x128"), (256, "128x128@2x"),
        (256, "256x256"), (512, "256x256@2x"),
        (512, "512x512"), (1024, "512x512@2x"),
    ]
    for size, name in specs:
        render(size).save(os.path.join(iconset, f"icon_{name}.png"))
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", "AppIcon.icns"],
                   check=True)
    print("wrote AppIcon.icns")


if __name__ == "__main__":
    main()
