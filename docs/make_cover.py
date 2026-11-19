#!/usr/bin/env python3
"""Generates docs/cover.png (1200x675). Requires Pillow."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

S = 2                                   # draw at 2x, then downsample
W, H = 1200 * S, 675 * S
BG, PANEL, LINE = (13, 20, 38), (24, 34, 58), (52, 68, 102)
TEXT, MUTED = (241, 245, 249), (148, 163, 184)
AMBER, TEAL, RED = (251, 191, 36), (45, 212, 191), (248, 113, 113)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf"


def font(size, bold=False):
    return ImageFont.truetype(FONT.format("-Bold" if bold else ""), size * S)


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

# faint grid
for x in range(0, W, 48 * S):
    d.line([(x, 0), (x, H)], fill=(18, 27, 48), width=S)
for y in range(0, H, 48 * S):
    d.line([(0, y), (W, y)], fill=(18, 27, 48), width=S)

# title block
d.rounded_rectangle([72 * S, 70 * S, 84 * S, 190 * S], radius=6 * S, fill=AMBER)
d.text((108 * S, 62 * S), "InvoiceWarden", font=font(76, True), fill=TEXT)
d.text((110 * S, 156 * S), "The agent that loses privileges when it reads untrusted content",
       font=font(26), fill=MUTED)


def box(x, y, w, h, title, sub, accent):
    d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + h) * S], radius=18 * S,
                        fill=PANEL, outline=LINE, width=2 * S)
    d.rounded_rectangle([x * S, y * S, (x + w) * S, (y + 8) * S], radius=4 * S, fill=accent)
    tw = d.textlength(title, font=font(26, True))
    d.text(((x + w / 2) * S - tw / 2, (y + h - 74) * S), title, font=font(26, True), fill=TEXT)
    sw = d.textlength(sub, font=font(17))
    d.text(((x + w / 2) * S - sw / 2, (y + h - 38) * S), sub, font=font(17), fill=MUTED)
    return (x + w / 2) * S, (y + 78) * S


def arrow(x1, x2, y, color, dashed=False):
    y *= S
    if dashed:
        x = x1 * S
        while x < (x2 - 14) * S:
            d.line([(x, y), (min(x + 12 * S, (x2 - 14) * S), y)], fill=color, width=4 * S)
            x += 22 * S
    else:
        d.line([(x1 * S, y), ((x2 - 12) * S, y)], fill=color, width=4 * S)
    d.polygon([((x2 - 16) * S, y - 10 * S), (x2 * S, y), ((x2 - 16) * S, y + 10 * S)], fill=color)


Y, BW, BH = 300, 222, 200
xs = [72, 350, 628, 906]

# 1. invoice (untrusted)
cx, cy = box(xs[0], Y, BW, BH, "Invoice", "untrusted text", RED)
d.rounded_rectangle([cx - 34 * S, cy - 46 * S, cx + 34 * S, cy + 40 * S], radius=6 * S, outline=TEXT, width=3 * S)
for i, wdt in enumerate((44, 44, 28)):
    yy = cy - 24 * S + i * 18 * S
    d.line([(cx - 22 * S, yy), (cx - 22 * S + wdt * S, yy)], fill=MUTED, width=3 * S)
d.line([(cx - 22 * S, cy + 30 * S - 4 * S), (cx + 22 * S, cy + 30 * S - 4 * S)], fill=RED, width=4 * S)

# 2. agent
cx, cy = box(xs[1], Y, BW, BH, "Agent", "proposes actions", TEAL)
d.rounded_rectangle([cx - 44 * S, cy - 36 * S, cx + 44 * S, cy + 34 * S], radius=16 * S, outline=TEXT, width=3 * S)
for dx in (-18, 18):
    d.ellipse([cx + (dx - 7) * S, cy - 10 * S, cx + (dx + 7) * S, cy + 4 * S], fill=TEAL)
d.line([(cx, cy - 36 * S), (cx, cy - 54 * S)], fill=TEXT, width=3 * S)
d.ellipse([cx - 6 * S, cy - 64 * S, cx + 6 * S, cy - 52 * S], fill=TEXT)

# 3. policy gate (shield)
cx, cy = box(xs[2], Y, BW, BH, "Policy gate", "code, outside the model", AMBER)
shield = [(cx, cy - 52 * S), (cx + 44 * S, cy - 34 * S), (cx + 40 * S, cy + 14 * S),
          (cx, cy + 46 * S), (cx - 40 * S, cy + 14 * S), (cx - 44 * S, cy - 34 * S)]
d.polygon(shield, outline=AMBER, width=4 * S)
d.line([(cx - 18 * S, cy - 4 * S), (cx - 4 * S, cy + 12 * S), (cx + 22 * S, cy - 20 * S)],
       fill=AMBER, width=6 * S, joint="curve")

# 4. payment
cx, cy = box(xs[3], Y, BW, BH, "Payment", "account on file only", TEAL)
d.rounded_rectangle([cx - 52 * S, cy - 34 * S, cx + 52 * S, cy + 34 * S], radius=10 * S, outline=TEXT, width=3 * S)
d.ellipse([cx - 20 * S, cy - 20 * S, cx + 20 * S, cy + 20 * S], outline=TEAL, width=3 * S)
dw = d.textlength("$", font=font(26, True))
d.text((cx - dw / 2, cy - 17 * S), "$", font=font(26, True), fill=TEAL)

# connectors
mid = Y + BH / 2 - 22
arrow(xs[0] + BW + 8, xs[1] - 8, mid, RED, dashed=True)
arrow(xs[1] + BW + 8, xs[2] - 8, mid, MUTED)
arrow(xs[2] + BW + 8, xs[3] - 8, mid, TEAL)

# taint tag over the first connector
tag = "TAINTED"
tw = d.textlength(tag, font=font(13, True))
tx = (xs[0] + BW + (xs[1] - xs[0] - BW) / 2) * S
d.rounded_rectangle([tx - tw / 2 - 8 * S, (mid - 40) * S, tx + tw / 2 + 8 * S, (mid - 14) * S],
                    radius=6 * S, fill=RED)
d.text((tx - tw / 2, (mid - 36) * S), tag, font=font(13, True), fill=BG)

# footer
d.line([(72 * S, 560 * S), (1128 * S, 560 * S)], fill=LINE, width=S)
d.ellipse([72 * S, 590 * S, 88 * S, 606 * S], fill=AMBER)
d.text((100 * S, 584 * S), "Tainted session  →  no payment without a human", font=font(22, True), fill=TEXT)
right = "Amazon Bedrock AgentCore harness"
rw = d.textlength(right, font=font(19))
d.text((1128 * S - rw, 587 * S), right, font=font(19), fill=MUTED)

out = Path(__file__).resolve().parent / "cover.png"
img.resize((1200, 675), Image.LANCZOS).save(out, optimize=True)
print(out, out.stat().st_size // 1024, "KB")
