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
| `index.html` | All of the above on one page, with each clip's faults and charts of its feet, knees and elbows |

The strips are orthographic and the floor's checks stay put, so a foot that slides, sinks or floats shows against them. A clip
that travels more than the figure's height is followed by the camera.

## The checks

**The model at rest** (any file with a real mesh):

| Check | Fault when |
| --- | --- |
| Mesh soundness | edges shared by more than two faces; neighbouring faces facing opposite ways (flipped normals); faces with no area |
| Floating pieces | a small separate piece of a mesh with no other surface within 0.4% of H of it: a stray clump at a hairline, a hand that hangs below its cuff with a gap at the wrist |
| Skinning | vertices no bone moves; vertices on more than four bones |
| Proportions | a human figure's hand (length, palm width and thickness, the thumb left out), foot, arm, leg, shoulders or head outside an adult's range, or left and right differing by more than 8%. Set on MakeHuman figures, hand-built and generated ones; it caught the swollen hands of a generated figure (palms 3.9% of the height thick against 1.8% for hands that pass). `--proportions none` for a stylised figure |

**Every clip** drawn is also measured at every frame (`asset-check check` does only this, in a few seconds a clip, and exits 2
if it finds a fault). Thresholds are fractions of the figure's height H, so they hold at any scale; they're in `LIMITS` at the
top of the checks in `blender/sheet.py`. A fault is something to look at in the strips, not a verdict.

| Check | Fault when |
| --- | --- |
| Sinking, floating | the body goes below the ground by more than 1.5% of H (the fault names the mesh that's lowest: a coat's hem in a crouch isn't the feet); the feet never come within 2% of H of it |
| Foot slide | a planted foot moves more than 3% of H in one step against the ground. For a clip that walks on the spot, the ground is taken to move back under the feet at the walking pace (the median motion of every planted foot), which is reported as `pace` |
| Knees, elbows | a knee bends backwards more than 8° or folds past 165°; an elbow backwards more than 12° or past 160°; either bends more than 45° off the hinge it has at rest (the plane its rest pose bends in, carried by the upper bone), which on a skinned mesh twists the joint |
| Pops | a joint jumps more than 5% of H off its path for one frame and comes back (a foot strike or a fist stopping changes speed once and isn't one); fingers carried by a popping hand aren't listed again |
| Loop gap | a clip named `_Loop` whose last pose is more than 3% of H from its first |
| Stretch | a bone longer or shorter than at rest by more than 3% |
| Meshes crossing | one mesh's faces through another's (a coat through trousers, a sleeve through a hip) on more than 5% more of the smaller mesh than at rest |

Comparing a clip on its own skeleton with the same clip fitted to a model (`--anim`) tells faults that are in the clip from
faults the fitting adds.

## Usage

```sh
node bin/asset-check.mjs sheet character.glb                       # the model sheets, and each clip if there are 6 or fewer
node bin/asset-check.mjs sheet library.glb --list                  # the clips in a file
node bin/asset-check.mjs sheet library.glb --clip Walk_Loop,Kick   # those clips (by name or part of one); --clip all for every one
node bin/asset-check.mjs sheet character.glb --anim walk.fbx       # a clip from another file, fitted to the model's skeleton
node bin/asset-check.mjs sheet character.glb --anim library.glb --clip Walk_Loop --no-model   # just the strips
node bin/asset-check.mjs check character.glb --anim library.glb --clip Walk_Loop     # the checks alone; exit 2 on a fault
node bin/asset-check.mjs compare before.glb after.glb              # both, side by side, and what differs
node bin/asset-check.mjs compare model.glb model.glb --anim old_clips.glb --anim-b new_clips.glb --clip Walk_Loop
node bin/asset-check.mjs blender                                   # which Blender it uses
```

Options: `--out <dir>`, `--frames <n>` (12 a strip), `--size <px>` (512), `--engine eevee|cycles`, `--front auto|-y|+y|-x|+x`,
`--model` / `--no-model` / `--no-anims`, `--verbose` (Blender's own output).

Reads `.glb`, `.gltf`, `.fbx`, `.bvh`, `.obj` and `.blend`. Needs Blender 4.4 or later (tested on 5.2) and Node 18 or later.
Blender is found from `BLENDER`, then the `PATH`, then the usual install folders.

## Comparing

`compare <a> <b>` runs both (into `a/` and `b/`), puts each sheet they share side by side (`pair_<sheet>.png`, a on the left,
every picture stamped A or B), and lists what differs: height, triangles, each proportion and each clip's length, pace and
loop gap where they change by more than 1%, and the faults found in only one of them, matched by their words and counts so a
measurement shifting a little doesn't count as a new fault. It exits 2 when b has a fault a hasn't.

Its main use is before and after a fix: the fault that was named should be gone and nothing else should have changed. On a
generated figure whose swollen hands were replaced, it showed the four hand faults gone, the palms 38 to 55% slimmer, and one
fault new: the new hand hanging 1.6 cm below its cuff, a gap at the wrist. It also compares a new figure with an approved one,
and two versions of a clip on one model (`--anim-b`).

## The Claude Code skill

`skill/SKILL.md` tells Claude Code sessions when to run this (before calling a model or clip done, after fitting a clip, when
a fault is reported), how to read the sheets and what each fault means. Link it into the user's skills so every project gets
it and it stays current with the repository:

```powershell
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\asset-check" -Target "<this repo>\skill"   # Windows
```
```sh
ln -s "<this repo>/skill" ~/.claude/skills/asset-check                                                         # macOS, Linux
```

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
  with no skin; for the latter the rest pose is read from the file itself. `report.json`'s `fit` says how many bones matched.
- **Planting the feet** (on by default; `--no-plant` leaves it out, to see a clip as a plain world-rotation fit plays it).
  The same knee angles on legs of other proportions reach higher or lower: on Mack, the game's hero, the library's fighting
  stance put his feet 3.5 cm into the floor. Each frame the hips, and the whole body with them, are moved up or down to put
  the feet at the clip's height, scaled, the foot that's lower in the clip counting most. Only up and down: moving the body
  sideways to hold one foot drags the other (that needs IK on each leg), so on a body of other proportions a planted foot
  still slides a little.
- **Skeletons with no mesh.** A clip file with only bones, or with only animated nodes (how a glTF clip library with no skin
  imports), is drawn as rods and knots that move with it. Its floor is the clip's own lowest point.

## Not yet

- Planting the feet sideways too (IK on each leg) when a fitted clip is played on a body of other proportions.
- A mesh through itself (a hand through the chest); only one mesh through another is checked.
- Comparing at one scale: each side of a comparison is framed to its own height, so a height difference is in the numbers,
  not the pictures.
