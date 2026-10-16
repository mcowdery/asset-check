# asset-check

Review sheets for 3D models and animations, rendered in headless Blender, so that a person, or an AI agent that can see
still images but not video, can judge a figure or a motion without opening Blender.

An agent building models and animations by script works half blind: it writes code, runs it, and sees the result only if
something renders it. This tool renders it the same way every time, from the angles where faults show, and lays the pictures
out as a few sheets that can be read at a glance.

## What it makes

`asset-check sheet <file>` writes, into `./asset-check/<name>/` (or `--out`):

| Sheet | What's in it |
| --- | --- |
| `model_lit.png` | Six sides (front, front-right, right, back-right, back, left), lit by key, fill and rim lights, on a checked floor |
| `model_clay.png` | The same six sides as plain shape with no textures, which shows lumps, holes and bad proportions that textures hide |
| `model_close.png` | The head from the front and the side, each hand, the feet, and the figure small, as seen from across a street |
| `anim_<clip>_side.png`, `anim_<clip>_front.png` | Frames of the clip in order, each stamped with its time and frame number, from an orthographic camera |
| `frames/` | Every picture on its own |
| `report.json` | What was found: size, triangles, rig, facing, each clip's length, travel and lowest point, and warnings |
| `index.html` | All of the above on one page |

The strips are orthographic and the floor's checks stay put, so a foot that slides, sinks or floats shows against them. A clip
that travels more than the figure's height is followed by the camera.

## Usage

```sh
node bin/asset-check.mjs sheet character.glb                       # the model sheets, and each clip if there are 6 or fewer
node bin/asset-check.mjs sheet library.glb --list                  # the clips in a file
node bin/asset-check.mjs sheet library.glb --clip Walk_Loop,Kick   # those clips (by name or part of one); --clip all for every one
node bin/asset-check.mjs sheet character.glb --anim walk.fbx       # a clip from another file, fitted to the model's skeleton
node bin/asset-check.mjs sheet character.glb --anim library.glb --clip Walk_Loop --no-model   # just the strips
node bin/asset-check.mjs blender                                   # which Blender it uses
```

Options: `--out <dir>`, `--frames <n>` (12 a strip), `--size <px>` (512), `--engine eevee|cycles`, `--front auto|-y|+y|-x|+x`,
`--model` / `--no-model` / `--no-anims`, `--verbose` (Blender's own output).

Reads `.glb`, `.gltf`, `.fbx`, `.bvh`, `.obj` and `.blend`. Needs Blender 4.4 or later (tested on 5.2) and Node 18 or later.
Blender is found from `BLENDER`, then the `PATH`, then the usual install folders.

## How it decides things

- **Scale.** Everything is measured from the figure itself: the cameras fit it, the floor's checks are a quarter of its
  height. A file in centimetres reads the same as one in metres.
- **Facing.** From the line through the hips (facing is perpendicular to it), averaged over a clip; failing that, from the
  feet toward the toes; failing that, -Y (glTF's front). Set it with `--front` when the guess is wrong.
- **Joints by name.** Head, hands, feet and hips are found from bone or node names in the common conventions
  (`mixamorig:LeftHand`, `hand.L`, `hand_l`, `CC_Base_L_Hand`, ...). The close-ups take the vertices weighted to that joint
  and the joints under it.
- **A clip from another file** (`--anim`). It's fitted to the model's skeleton the way its bones turn in the world: each of
  the model's bones turns from its rest as far as the clip's bone of the same name turns from the clip's rest. The model's
  rest is first swung into the clip's rest pose limb by limb (arms, legs, hands and fingers aimed the way the clip's point;
  feet turned only about the vertical; the trunk, neck, head and collar taken as matching), and the hips' travel is scaled by
  leg length. So rest poses (T-pose against A-pose), bone rolls and proportions may differ; only the names must match
  (prefixes like `mixamorig:` aside). The clip can be an armature (FBX, BVH, a skinned glTF) or a glTF of animated nodes
  with no skin; for the latter the rest pose is read from the file itself. Feet aren't planted, so on a body of other
  proportions they slide a little. `report.json`'s `fit` says how many bones matched.
- **Skeletons with no mesh.** A clip file with only bones, or with only animated nodes (how a glTF clip library with no skin
  imports), is drawn as rods and knots that move with it. Its floor is the clip's own lowest point.

## Not yet

- Planting the feet (IK) when a fitted clip is played on a body of other proportions.
- Numeric checks: foot slide, joint limits, limb stretch, self-intersection, proportions against a reference.
- Comparing a model or a clip with a reference side by side.
