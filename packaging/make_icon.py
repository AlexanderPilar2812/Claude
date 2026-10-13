"""Genera packaging/epacal.ico (gota de agua con curva de calibración)."""
import os
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=48, fill=(23, 92, 170, 255))
# gota
d.polygon([(128, 34), (70, 132), (186, 132)], fill=(255, 255, 255, 255))
d.ellipse((66, 92, 190, 216), fill=(255, 255, 255, 255))
# curva calibrada
pts = [(84, 168), (104, 150), (124, 172), (144, 140), (164, 158), (176, 146)]
d.line(pts, fill=(235, 104, 52, 255), width=10, joint="curve")
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "epacal.ico")
img.save(out, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
print("icono:", out)
