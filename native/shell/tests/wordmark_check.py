#!/usr/bin/env python3
"""Check that the greeter leaves the wallpaper's wordmark untouched.

    wordmark_check.py <screenshot.png> <wallpaper.jpg> [max-mean-diff]

The wallpaper is scaled to the screenshot like the greeter does (aspect fill,
centre crop) and compared inside the wordmark's glyph box (fractions of the
screen, measured per aspect ratio, see LayoutMath::wordmarkRect). Exits 1 when
the mean absolute difference per channel exceeds the limit (default 6 of 255),
which would mean something is painted over the wordmark. Needs Pillow.
"""
import sys
from PIL import Image, ImageChops, ImageStat

shot = Image.open(sys.argv[1]).convert("RGB")
wall = Image.open(sys.argv[2]).convert("RGB")
limit = float(sys.argv[3]) if len(sys.argv) > 3 else 6.0
W, H = shot.size
ar = W / H
if ar >= 2.3:
    box = (0.445, 0.44, 0.555, 0.55)
elif ar >= 1.2:
    box = (0.40, 0.445, 0.595, 0.575)
else:
    box = (0.27, 0.41, 0.73, 0.47)
# Aspect fill.
k = max(W / wall.width, H / wall.height)
scaled = wall.resize((round(wall.width * k), round(wall.height * k)), Image.LANCZOS)
ox, oy = (scaled.width - W) // 2, (scaled.height - H) // 2
scaled = scaled.crop((ox, oy, ox + W, oy + H))
r = (round(box[0] * W), round(box[1] * H), round(box[2] * W), round(box[3] * H))
diff = ImageChops.difference(shot.crop(r), scaled.crop(r))
mean = sum(ImageStat.Stat(diff).mean) / 3
print(f"{sys.argv[1]}: wordmark box {r} mean abs diff {mean:.2f} (limit {limit})")
sys.exit(0 if mean <= limit else 1)
