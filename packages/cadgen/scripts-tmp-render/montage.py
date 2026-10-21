"""Side-by-side sheet of one shot across candidates, each cell labelled with engine and time.
usage: python montage.py OUT.png "LABEL=PATH" ...   (2 columns)"""
import sys

from PIL import Image, ImageDraw, ImageFont

out, cells = sys.argv[1], [a.split("=", 1) for a in sys.argv[2:]]
CW, CH = 960, 540
cols = 2
rows = (len(cells) + cols - 1) // cols
sheet = Image.new("RGB", (CW * cols, CH * rows), (0, 0, 0))
try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 26)
except OSError:
    font = ImageFont.load_default()
for i, (label, path) in enumerate(cells):
    im = Image.open(path).convert("RGB").resize((CW, CH), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, CW, 40], fill=(0, 0, 0))
    d.text((14, 6), label, fill=(235, 235, 235), font=font)
    sheet.paste(im, ((i % cols) * CW, (i // cols) * CH))
sheet.save(out)
print(out)
