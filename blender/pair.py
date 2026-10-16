"""Two pictures side by side, the first on the left: the sheets of two runs, for `asset-check compare`.

  blender -b --factory-startup --python-exit-code 1 --python blender/pair.py -- <out.png> <left.png> <right.png> [...]

Takes pairs after the output: `out1 left1 right1 out2 left2 right2 ...`. Pictures of different sizes are put top-aligned on
a dark ground.
"""
import sys

import bpy
import numpy as np

args = sys.argv[sys.argv.index('--') + 1:]
GAP = 12


def load(path):
    im = bpy.data.images.load(path)
    w, h = im.size
    a = np.empty(w * h * 4, dtype=np.float32)
    im.pixels.foreach_get(a)
    bpy.data.images.remove(im)
    return a.reshape(h, w, 4)


for i in range(0, len(args) - 2, 3):
    out, left, right = args[i:i + 3]
    a, b = load(left), load(right)
    H = max(a.shape[0], b.shape[0])
    W = a.shape[1] + GAP + b.shape[1]
    sheet = np.full((H, W, 4), 0.02, dtype=np.float32)
    sheet[..., 3] = 1
    sheet[H - a.shape[0]:, :a.shape[1]] = a  # (rows are stored bottom first: top-aligned is the top rows)
    sheet[H - b.shape[0]:, a.shape[1] + GAP:] = b
    sheet[:, a.shape[1]:a.shape[1] + GAP, :3] = 0.35  # a light rule between them
    im = bpy.data.images.new('pair', W, H, alpha=False)
    im.pixels.foreach_set(sheet.ravel())
    im.filepath_raw = out
    im.file_format = 'PNG'
    im.save()
    bpy.data.images.remove(im)
    print(f'PAIR wrote {out}')
