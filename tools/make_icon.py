"""Draws the app icon (icon.ico + icon-256.png) - an indigo camera aperture on a near-black rounded tile."""
import math
import os

from PIL import Image, ImageDraw

S = 1024  # drawn at 4x and downsampled for smooth edges
INDIGO = (70, 80, 240)
INDIGO_LIGHT = (139, 147, 255)
INDIGO_MID = (110, 120, 250)
TILE = (10, 10, 14)
GAP = (10, 10, 16)


def point(r, deg):
    a = math.radians(deg)
    return (S / 2 + r * math.cos(a), S / 2 + r * math.sin(a))


def draw_icon():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile = Image.new("L", (S, S), 0)
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, S - 1, S - 1), int(S * 0.22), fill=255)
    img.paste(Image.new("RGBA", (S, S), TILE + (255,)), (0, 0), tile)

    blades = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(blades)
    r_in, r_out, twist, sweep = S * 0.16, S * 0.37, 24, 62
    colors = [INDIGO_LIGHT, INDIGO, INDIGO_MID]
    for i in range(6):
        th = i * 60
        poly = [point(r_in, th), point(r_out, th + twist), point(r_out, th + twist + sweep), point(r_in, th + 60)]
        d.polygon(poly, fill=colors[i % 3] + (255,))
        d.line(poly + [poly[0]], fill=GAP + (255,), width=int(S * 0.012))
    c = S / 2
    d.ellipse((c - r_out, c - r_out, c + r_out, c + r_out), outline=INDIGO_LIGHT + (255,), width=int(S * 0.014))
    img = Image.alpha_composite(img, blades)
    img.putalpha(tile)
    return img


root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
big = draw_icon().resize((256, 256), Image.LANCZOS)
big.save(os.path.join(root, "icon-256.png"))
out = os.path.join(root, "icon.ico")
big.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon:", out)
