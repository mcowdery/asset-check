---
name: asset-check
description: Look at and measure 3D models and animations before calling them done. Renders review sheets in headless Blender (lit and plain-shape turnarounds, close-ups of head, hands and feet, time-stamped frame strips of each clip) and checks every frame of a clip for faults (sinking, foot slide, knees and elbows bending wrong, pops, loop gaps, stretch, clothes through the body). Use after building or changing a model, rig, outfit or animation, after retargeting a clip, when choosing clips from a library, and when the user reports something wrong with how a figure looks or moves.
---

# asset-check

A model or an animation made by script is otherwise judged blind: this renders it the same way every time, from the angles
where faults show, and measures what stills can't show. The tool is in this skill's repository: the command is
`node <this skill's folder>/../bin/asset-check.mjs` (below, `asset-check`). It needs Blender 4.4+ (found from `BLENDER`, the
PATH, or the usual install folders) and Node 18+. The repository's README has every option.

## When

- **Before saying a model, outfit, rig or clip is done.** Run it, read the sheets, and say what they show, faults included.
  Don't describe a figure from the code that built it.
- **After fitting a clip to a character**, and when proposing clips from a library: the checks say which clips have faults
  of their own and which the fitting adds.
- **When the user reports a fault in how something looks or moves**: reproduce it here first, then fix the named fault, and
  `compare` the file from before the fix with the one after: the named fault should be in "found in a only" and nothing
  should be in "found in b only". Report both lists.

## Commands

```sh
asset-check sheet model.glb --out <dir>                    # model sheets (and each clip if the file has six or fewer)
asset-check sheet clips.glb --list                         # the clips in a file
asset-check sheet model.glb --anim clips.glb --clip Walk_Loop,Kick --no-model --out <dir>   # clips fitted to the model
asset-check check model.glb --anim clips.glb --clip Walk_Loop --out <dir>                   # the checks alone, seconds a clip
asset-check compare before.glb after.glb --out <dir>       # side by side, what differs, faults new in b (exit 2)
```

Give every run an output folder of its own (`--out`), not the default, when other agents may be running it too. A run of a
few clips takes seconds to a minute; `--clip all` on a big library is heavy: in a project with rules about heavy local runs
(a GPU pod, warning the user first), follow them.

## Reading what comes back

- `model_lit.png`, `model_clay.png`: six sides each. The clay sheet has no textures, so read shape there: lumps, holes,
  floating bits, proportions, a hand too big. The lit one for colour, seams, texture stretching.
- `model_close.png`: head front and side, each hand, the feet, and the figure small as from across a street. Hands and
  faces go wrong most; look at them here, not in the turnaround.
- `anim_<clip>_side.png`, `anim_<clip>_front.png`: frames in order, each stamped with time and frame. Orthographic, on a
  checked floor that stays put, so a foot that slides, sinks or floats shows against it. Look at contact frames, the
  fastest moment, and the first and last frame together (a loop's join).
- `report.json` and `index.html`: sizes, rig, facing, and per clip the faults with their times, plus charts of foot height,
  foot slide, knee and elbow bend. Go from a fault's time to that frame in the strip and look before reporting it.

## What the faults mean

The model at rest (any file with a mesh; `check` runs these too):

- **pieces floating off the figure**: a small separate bit with nothing near it: a stray clump at a hairline, a hand below
  its cuff with a gap at the wrist. The fault gives the mesh and the height; find it in `model_close.png`.
- **edges on more than two faces, flipped normals, faces with no area, vertices no bone moves, more than 4 bones**: mesh and
  skinning faults that show as holes, black patches, or parts left behind when the figure moves.
- **a proportion outside a person's range**: hands first (swollen hands show as palm thickness and width), then feet, arms,
  legs, shoulders, head. For a stylised figure pass `--proportions none`.

A clip:

Thresholds are fractions of the figure's height, in `LIMITS` in `blender/sheet.py`. A fault is something to look at, not a
verdict: confirm it in the pictures, and say which it is.

- **sinks / never touches the ground**: the fault names the lowest mesh; a coat's hem through the floor in a crouch is the
  coat, not the feet.
- **foot slides while down**: against the walking pace for a clip that walks on the spot (`pace` in the report).
- **bends backwards / folds / bends sideways off its hinge**: a knee or elbow past what a joint does, or bending in a plane
  its rest pose doesn't, which twists the joint on a skinned body.
- **jumps**: one frame off the path and back; a keyframe pop.
- **loop gap**, **stretches/shrinks**, **faces through the other**: as they say; the last is one mesh through another
  (clothes through the body), against how much they cross at rest.

To tell a fault in a clip from one the fitting adds, check the clip on its own skeleton (`asset-check check clips.glb
--clip <name>`) and fitted (`--anim`), and compare. `--no-plant` shows a fitted clip without the feet planted.

## Limits to say out loud

Stills and numbers don't show feel: whether a walk suits a character, whether a move reads as intended. Say so, and leave
that to the user moving it in the game or a showroom. Sideways foot slide on a body of other proportions isn't corrected
(no IK), and a mesh through itself (a hand through the chest) isn't checked.
