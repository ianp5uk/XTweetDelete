#!/usr/bin/env python3
"""Converts the shared icon.ico (built for Windows) to a 256x256 PNG for
Linux's hicolor icon theme convention. Called by build_deb.sh; not meant to
be run manually.
"""
import sys
from PIL import Image

src, dst = sys.argv[1], sys.argv[2]
img = Image.open(src)
img.load()
img = img.convert("RGBA")
if img.size != (256, 256):
    img = img.resize((256, 256))
img.save(dst, format="PNG")
