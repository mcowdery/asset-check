"""Review sheets for a model or an animation: what it looks like, lit and as plain shape, from every side, close up and at a
distance, and how it moves, as frames in order stamped with their time. Run by `asset-check sheet` (bin/asset-check.mjs);
directly:

  blender -b --factory-startup --python-exit-code 1 --python blender/sheet.py -- <file> --out <dir> [options]

  <file>          .glb .gltf .fbx .bvh .obj, or .blend (opened as it is)
  --anim <file>   a clip file to play on the model, fitted to its skeleton (bones matched by name; see Fit): an armature
                  (.fbx, .bvh, a skinned .glb) or a .glb of animated nodes
  --clip <name>   which clips: all, none, or a name or a part of one; several with commas. Left out: all of them if there
                  are six or fewer, else none (the clips are listed, to pick from)
  --frames <n>    frames in each strip (12)
  --size <px>     the side of each picture (512 for the model, two thirds of that for the strips)
  --engine <e>    eevee (the default) or cycles, for the lit pictures; the plain-shape pictures are always Workbench
  --front <axis>  the way the figure faces: auto (the default: from its feet, else -y), -y, +y, -x, +x
  --model         the model sheets even for a skeleton drawn as sticks; --no-model never; --no-anims no strips
  --list          print the clips and stop
  --no-plant      fit an --anim clip without planting the feet (see Fit.pose)
  --proportions none   leave out the human proportions check (a stylised figure)
  --checks-only   measure each clip, draw nothing (no model sheets, no strips); --no-checks draw only

Writes into the out folder: model_lit.png and model_clay.png (six sides each), model_close.png (head, hands, feet, and the
figure small, as from across a street), anim_<clip>_side.png and anim_<clip>_front.png (the strips), frames/ (every picture on
its own), and report.json (what was found and drawn). A clip whose figure travels more than its own height is followed by the
camera; the ground's checks (a quarter of the figure's height across) stay put, so a foot that slides shows against them.

A skeleton with no mesh (an armature, or a tree of animated nodes, which is how a glTF clip library with no skin imports) is
drawn as rods and knots that move with it. Everything is measured from the figure itself, so units and scale don't matter.
Needs Blender 4.4 or later (slotted actions).
"""
import json
import math
import os
import re
import sys
import time

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

T0 = time.time()
Z = Vector((0, 0, 1))


# ------------------------------------------------------------------------------------------------------------------ arguments

def parse_args():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    a = {'file': None, 'out': None, 'anim': None, 'clip': None, 'frames': 12, 'size': 512, 'engine': 'eevee', 'front': 'auto',
         'model': None, 'anims': True, 'strips': True, 'checks': True, 'list': False, 'samples': 24}
    flags = {'--model': ('model', True), '--no-model': ('model', False), '--no-anims': ('anims', False), '--list': ('list', True),
             '--no-checks': ('checks', False), '--no-plant': ('no-plant', True)}
    i = 0
    while i < len(argv):
        k = argv[i]
        if k == '--checks-only':
            a['model'], a['strips'] = False, False
        elif k in flags:
            a[flags[k][0]] = flags[k][1]
        elif k.startswith('--'):
            a[k[2:]] = argv[i + 1]
            i += 1
        elif a['file'] is None:
            a['file'] = k
        i += 1
    if not a['file']:
        raise SystemExit('sheet.py: no file given')
    a['file'] = os.path.abspath(a['file'])
    a['out'] = os.path.abspath(a['out'] or os.path.splitext(a['file'])[0] + '.sheets')
    a['frames'], a['size'], a['samples'] = int(a['frames']), int(a['size']), int(a['samples'])
    return a


ARGS = parse_args()
OUT = ARGS['out']
FRAMES_DIR = os.path.join(OUT, 'frames')
os.makedirs(FRAMES_DIR, exist_ok=True)
REPORT = {'file': ARGS['file'], 'blender': bpy.app.version_string, 'warnings': [], 'sheets': [], 'clips': []}


def warn(msg):
    REPORT['warnings'].append(msg)
    print(f'SHEET warning: {msg}')


# --------------------------------------------------------------------------------------------------------------------- import

def import_file(path):
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if ext in ('.glb', '.gltf'):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == '.fbx':
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == '.bvh':
        bpy.ops.import_anim.bvh(filepath=path, update_scene_fps=True, update_scene_duration=True)
    elif ext == '.obj':
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise SystemExit(f'sheet.py: can\'t read {ext} files')
    return [o for o in bpy.data.objects if o not in before]


if ARGS['file'].lower().endswith('.blend'):
    bpy.ops.wm.open_mainfile(filepath=ARGS['file'])
    imported = list(bpy.data.objects)
else:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    imported = import_file(ARGS['file'])

SC = bpy.context.scene
FPS = SC.render.fps / SC.render.fps_base


# ------------------------------------------------------------------------------------------------------------------ the rig

def is_animated(o):
    ad = o.animation_data
    return bool(ad and (ad.action or len(ad.nla_tracks)))


meshes = [o for o in imported if o.type == 'MESH' and o.visible_get()]
arms = sorted((o for o in imported if o.type == 'ARMATURE'), key=lambda o: -len(o.data.bones))
ARM = arms[0] if arms else None
if len(arms) > 1:
    warn(f'{len(arms)} armatures; using {ARM.name} ({len(ARM.data.bones)} bones)')

# Joints: the armature's bones, or else the animated empties and the empties above them (a node-animated skeleton).
# name -> {'parent': name or None, 'children': [names], 'obj': the empty, if it's one}
JOINTS = {}
NODE_RIG = []
if ARM:
    for b in ARM.data.bones:
        JOINTS[b.name] = {'parent': b.parent.name if b.parent else None, 'children': [c.name for c in b.children], 'obj': None}
else:
    empties = [o for o in imported if o.type == 'EMPTY']
    moving = {o for o in empties if is_animated(o)}
    NODE_RIG = [o for o in empties if o in moving or any(c in moving for c in o.children_recursive)]
    keep = set(NODE_RIG)
    for o in NODE_RIG:
        JOINTS[o.name] = {'parent': o.parent.name if o.parent in keep else None,
                          'children': [c.name for c in o.children if c in keep], 'obj': o, 'moving': o in moving}
if not meshes and not JOINTS:
    raise SystemExit('sheet.py: nothing to draw (no meshes, no armature, no animated nodes)')


def joint_pos(name):
    """Where the joint is now, in the world."""
    j = JOINTS[name]
    if j['obj']:
        return j['obj'].matrix_world.translation.copy()
    pb = ARM.pose.bones[name]
    return ARM.matrix_world @ pb.head


def depth(name):
    n, p = 0, JOINTS[name]['parent']
    while p:
        n, p = n + 1, JOINTS[p]['parent']
    return n


def below(name):
    """The joint and every joint under it."""
    out, todo = [], [name]
    while todo:
        n = todo.pop()
        out.append(n)
        todo += JOINTS[n]['children']
    return out


def tokens(name):
    """'mixamorig:LeftHand' -> ['mixamorig', 'left', 'hand']; 'hand.L' -> ['hand', 'l']; 'CC_Base_L_Foot' -> [..., 'l', 'foot']."""
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    name = re.sub(r'([A-Za-z])(\d)', r'\1 \2', name)  # 'upperarm01' -> 'upperarm 01'
    return [t.lower() for t in re.split(r'[^A-Za-z0-9]+', name) if t]


def side_of(toks):
    if 'left' in toks or 'l' in toks:
        return 'l'
    if 'right' in toks or 'r' in toks:
        return 'r'
    return None


def find_joint(part, side=None):
    """The highest joint named for the part (hand, not its fingers; head, not its end): the match with the fewest ancestors."""
    best = None
    for name in JOINTS:
        t = tokens(name)
        if part not in t or 'end' in t or 'top' in t or (side and side_of(t) != side):
            continue
        if best is None or depth(name) < best[0]:
            best = (depth(name), name)
    return best[1] if best else None


# ---------------------------------------------------------------------------------------- a body for a skeleton with no mesh

class Pieces:
    """Rods and knots, each vertex owned by one joint, built in world space."""

    def __init__(self, r):
        self.r, self.verts, self.faces, self.owner = r, [], [], []

    def rod(self, p, q, joint, rad=None):
        rad = rad or self.r
        d = q - p
        if d.length < 1e-6:
            return
        u = d.orthogonal().normalized() * rad
        v = d.normalized().cross(u)
        base = len(self.verts)
        for end in (p, q):
            for k in range(6):
                a = k * math.pi / 3
                self.verts.append(end + u * math.cos(a) + v * math.sin(a))
                self.owner.append(joint)
        for k in range(6):
            n = (k + 1) % 6
            self.faces.append((base + k, base + n, base + 6 + n, base + 6 + k))
        self.faces.append(tuple(base + k for k in range(6))[::-1])
        self.faces.append(tuple(base + 6 + k for k in range(6)))

    def knot(self, c, joint, rad):
        base = len(self.verts)
        for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            self.verts.append(c + Vector(d) * rad)
            self.owner.append(joint)
        for a, b, c2 in ((0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)):
            self.faces.append((base + a, base + b, base + c2))

    def object(self, name, local=None):
        """A mesh object of these pieces, with a vertex group per owning joint; `local` maps world into its own space."""
        me = bpy.data.meshes.new(name)
        me.from_pydata([tuple(local @ p if local else p) for p in self.verts], [], self.faces)
        me.update()
        ob = bpy.data.objects.new(name, me)
        SC.collection.objects.link(ob)
        groups = {}
        for i, j in enumerate(self.owner):
            g = groups.get(j) or ob.vertex_groups.new(name=j)
            groups[j] = g
            g.add([i], 1.0, 'REPLACE')
        mat = bpy.data.materials.get('stick') or bpy.data.materials.new('stick')
        mat.diffuse_color = (0.62, 0.6, 0.57, 1)
        if mat.node_tree is None:
            mat.use_nodes = True
        mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (0.62, 0.6, 0.57, 1)
        me.materials.append(mat)
        return ob


def stick_figure():
    """Rods from each joint to its children and a knot at each joint (a big one at the head), moving with the skeleton, so a
    skeleton-only clip has something to light, frame and cast a shadow."""
    bpy.context.view_layer.update()
    pos = {n: joint_pos(n) for n in JOINTS}
    zs = [p.z for p in pos.values()]
    r = max(max(zs) - min(zs), 1e-3) * 0.012

    def build(n, pc):
        pc.knot(pos[n], n, r * (5 if 'head' in tokens(n) else 1.6))
        kids = JOINTS[n]['children']
        for c in kids:
            pc.rod(pos[n], pos[c], n)
        if not kids and ARM:
            pc.rod(pos[n], ARM.matrix_world @ ARM.pose.bones[n].tail, n, r * 0.8)

    if ARM:
        pc = Pieces(r)
        for n in JOINTS:
            build(n, pc)
        ob = pc.object('stick')
        ob.modifiers.new('arm', 'ARMATURE').object = ARM
        return [ob]
    out = []
    for n, j in JOINTS.items():
        if not j['moving']:
            continue  # (the scene's root, standing at the origin, would draw a rod to the hips)
        pc = Pieces(r)
        build(n, pc)
        o = j['obj']
        ob = pc.object(f'stick_{n}', o.matrix_world.inverted())
        ob.parent = o
        out.append(ob)
    return out


# ------------------------------------------------------------------------------------------------------------------- clips

def action_fcurves(act):
    if getattr(act, 'layers', None):
        return [fc for layer in act.layers for strip in layer.strips for bag in strip.channelbags for fc in bag.fcurves]
    return list(getattr(act, 'fcurves', []))


def bone_names_in(act):
    names = set()
    for fc in action_fcurves(act):
        m = re.match(r'pose\.bones\["(.+?)"\]', fc.data_path)
        if m:
            names.add(m.group(1))
    return names


def armature_clips(arm, actions):
    own = set(arm.data.bones.keys())
    out = []
    for act in actions:
        names = bone_names_in(act)
        if names and len(names & own) >= max(1, 0.6 * len(names)):
            out.append({'name': act.name, 'bind': [(arm, act, None)], 'range': tuple(act.frame_range)})
    return out


def node_clips(nodes):
    """A clip of a node-animated skeleton is one action with a slot for each node it moves."""
    out = []
    for act in bpy.data.actions:
        ids = {s.identifier: s for s in getattr(act, 'slots', [])}
        bind = [(o, act, ids['OB' + o.name]) for o in nodes if 'OB' + o.name in ids]
        if bind:
            out.append({'name': act.name, 'bind': bind, 'range': tuple(act.frame_range)})
    return out


def node_rig(objs):
    """The animated empties of a set of objects and the empties above them."""
    empties = [o for o in objs if o.type == 'EMPTY']
    moving = {o for o in empties if is_animated(o)}
    return [o for o in empties if o in moving or any(c in moving for c in o.children_recursive)]


RIG_OBJECTS = [ARM] if ARM else NODE_RIG


# ------------------------------------------------------------------------------------------ fitting another file's clip

def plain(name):
    """A joint's name without a rig's prefix or Blender's .001: 'mixamorig:LeftArm' and 'LeftArm.001' are 'leftarm'."""
    return re.sub(r'\.\d{3}$', '', name.split(':')[-1]).lower()


def gltf_rest(path):
    """Each node's world matrix at rest, read from the file (Blender's import leaves the nodes posed in a clip), in Blender's
    axes: the importer turns glTF's +Y up to +Z by conjugating every node's transform with a quarter turn about X."""
    with open(path, 'rb') as fh:
        data = fh.read()
    doc = json.loads(data[20:20 + int.from_bytes(data[12:16], 'little')] if data[:4] == b'glTF' else data)
    nodes = doc.get('nodes', [])
    parent = {c: i for i, nd in enumerate(nodes) for c in nd.get('children', [])}

    def local(nd):
        if 'matrix' in nd:
            m = nd['matrix']
            return Matrix([m[0:4], m[4:8], m[8:12], m[12:16]]).transposed()
        x, y, z, w = nd.get('rotation', (0, 0, 0, 1))
        sx, sy, sz = nd.get('scale', (1, 1, 1))
        return (Matrix.Translation(Vector(nd.get('translation', (0, 0, 0)))) @ Quaternion((w, x, y, z)).to_matrix().to_4x4()
                @ Matrix.Diagonal((sx, sy, sz, 1)))

    def world(i):
        return world(parent[i]) @ local(nodes[i]) if i in parent else local(nodes[i])

    C = Matrix.Rotation(math.radians(90), 4, 'X')
    return {plain(nd.get('name', f'node{i}')): C @ world(i) @ C.inverted() for i, nd in enumerate(nodes)}


class Source:
    """A clip's skeleton: its joints by plain name, each one's parent, where it is at rest and where it is now (world)."""

    def __init__(self, objs, path):
        arms = sorted((o for o in objs if o.type == 'ARMATURE'), key=lambda o: -len(o.data.bones))
        if arms:
            self.arm = arms[0]
            self.objects = [self.arm]
            bones = self.arm.data.bones
            self.parent = {plain(b.name): plain(b.parent.name) if b.parent else None for b in bones}
            self.rest = {plain(b.name): self.arm.matrix_world @ b.matrix_local for b in bones}
            self.real = {plain(b.name): b.name for b in bones}
        else:
            self.arm = None
            self.objects = node_rig(objs)
            if not self.objects:
                raise SystemExit('sheet.py: the clip file has no armature and no animated nodes')
            if not path.lower().endswith(('.glb', '.gltf')):
                raise SystemExit('sheet.py: a clip of animated nodes is read from .glb or .gltf only')
            keep = set(self.objects)
            self.parent = {plain(o.name): plain(o.parent.name) if o.parent in keep else None for o in self.objects}
            rest = gltf_rest(path)
            self.rest = {plain(o.name): rest[plain(o.name)] for o in self.objects if plain(o.name) in rest}
            self.real = {plain(o.name): o for o in self.objects}
        self.children = {}
        for n, p in self.parent.items():
            if p:
                self.children.setdefault(p, []).append(n)

    def now(self, n):
        if self.arm:
            return self.arm.matrix_world @ self.arm.pose.bones[self.real[n]].matrix
        return self.real[n].matrix_world

    def clips(self):
        if self.arm:
            return armature_clips(self.arm, list(bpy.data.actions))
        return node_clips(self.objects)


LIMB = {'arm', 'upperarm', 'lowerarm', 'forearm', 'thigh', 'calf', 'shin', 'leg', 'upleg', 'upperleg', 'lowerleg', 'knee',
        'elbow', 'thumb', 'index', 'middle', 'ring', 'pinky', 'finger'}


def leg_length(names, children, pos):
    """Hip to knee to ankle, down the left leg, found by name ('thigh', 'UpLeg', 'leg': the one with the most below it, so
    Mixamo's LeftUpLeg and not its LeftLeg); None if there's no such chain."""
    def size(n):
        return 1 + sum(size(c) for c in children.get(n, []))

    def main(n):
        return max(children[n], key=size) if children.get(n) else None

    legs = [n for n in names if side_of(tokens(n)) == 'l' and set(tokens(n)) & {'thigh', 'upleg', 'upperleg', 'leg'}]
    hip = max(legs, key=size) if legs else None
    knee = main(hip) if hip else None
    ankle = main(knee) if knee else None
    if not ankle:
        return None
    return (pos[knee] - pos[hip]).length + (pos[ankle] - pos[knee]).length


class Fit:
    """Plays a clip made on another skeleton on the model's, the way the bones turn in the world: each of the model's bones
    turns from its rest as far as the clip's bone of the same name turns from the clip's rest. The model's rest is first swung
    into the clip's rest pose limb by limb (arms, legs, hands, fingers aimed the way the clip's point; feet turned only about
    the vertical, so a foot stays as flat as its own shape has it; trunk, neck, head and collar taken as matching: their
    difference is build, not pose). The hips' travel is scaled by leg length. Rest poses, bone rolls and proportions may all
    differ; names must match (prefixes like 'mixamorig:' aside). Feet aren't planted: on a body of other proportions they
    slide a little."""

    def __init__(self, src):
        self.src = src
        bones = sorted(ARM.data.bones, key=lambda b: len(b.parent_recursive))
        self.order = [b.name for b in bones]
        self.match = {b.name: plain(b.name) for b in bones if plain(b.name) in src.rest}
        if len(self.match) < 0.3 * len(bones):
            raise SystemExit(f'sheet.py: only {len(self.match)} of the model\'s {len(bones)} bones are in the clip by name '
                             f'(clip e.g. {sorted(src.rest)[:5]}, model e.g. {self.order[:5]})')
        mw = ARM.matrix_world
        self.arm_inv = mw.inverted()
        self.arm_rot = mw.to_quaternion()
        t_rest = {b.name: (mw @ b.matrix_local).decompose() for b in bones}
        self.tQ0 = {n: d[1] for n, d in t_rest.items()}
        self.tP0 = {n: d[0] for n, d in t_rest.items()}
        s_rest = {n: m.decompose() for n, m in src.rest.items()}
        self.sQ0 = {n: d[1] for n, d in s_rest.items()}
        self.sP0 = {n: d[0] for n, d in s_rest.items()}

        align = {}
        for b in bones:
            n, s = b.name, self.match.get(b.name)
            if not s:
                continue
            t = set(tokens(n))
            kids = [c.name for c in b.children if c.name in self.match]
            up = align.get(b.parent.name, Quaternion()) if b.parent else Quaternion()

            def swing(kid, flat=False):
                a = self.tP0[kid] - self.tP0[n]
                c = self.sP0[self.match[kid]] - self.sP0[s]
                if flat:
                    a.z = c.z = 0
                return a.rotation_difference(c) if a.length > 1e-6 and c.length > 1e-6 else Quaternion()

            if 'foot' in t:
                align[n] = swing(kids[0], flat=True) if kids else Quaternion()
            elif t & {'ball', 'toe', 'toes', 'toebase'}:
                align[n] = up
            elif 'hand' in t:
                mid = next((k for k in kids if 'middle' in tokens(k)), kids[0] if kids else None)
                align[n] = swing(mid) if mid else up  # (a clip with no fingers: the hand goes with the forearm)
            elif t & LIMB:
                align[n] = swing(kids[0]) if len(kids) == 1 else up
            else:
                align[n] = Quaternion()
        self.fit = {n: self.sQ0[s].inverted() @ align[n] @ self.tQ0[n] for n, s in self.match.items()}

        tj = {b.name: [c.name for c in b.children] for b in bones}
        tl = leg_length(self.order, tj, self.tP0)
        sl = leg_length(list(src.rest), src.children, self.sP0)
        self.scale = tl / sl if tl and sl else 1.0
        hips = [n for n in self.order if n in self.match and set(tokens(n)) & {'pelvis', 'hips', 'hip'} and not side_of(tokens(n))]
        self.hips = hips[0] if hips else next(n for n in self.order if n in self.match)

        # The model's legs swung into the clip's rest pose reach lower or higher than they do at the model's own rest (an
        # A-pose's legs slant out; a T-pose's hang straight down, and a straight leg reaches further), so the hips placed from
        # the model's rest would sink the feet into the floor or lift them off it. Measured once, with the clip at its rest,
        # and taken off the hips' height from then on.
        self.lift = Vector()
        self.plant = False
        feet = [f for f in (find_joint('foot', 'l'), find_joint('foot', 'r')) if f and f in self.match]
        self.feet = feet
        self.reach = 0.05 * (sl or 1.0)  # how far up a foot is still mostly trusted as the one on the ground
        if feet:
            self.pose(now=lambda s: src.rest[s])
            posed = sum((ARM.matrix_world @ ARM.pose.bones[f].head).z for f in feet) / len(feet)
            self.lift = Vector((0, 0, sum(self.tP0[f].z for f in feet) / len(feet) - posed))
            for pb in ARM.pose.bones:
                pb.matrix_basis = Matrix()
        self.plant = bool(feet) and not ARGS.get('no-plant')

    def pose(self, now=None):
        now = now or self.src.now
        bones = ARM.data.bones
        W, P = {}, {}
        for n in self.order:
            b = bones[n]
            p = b.parent.name if b.parent else None
            s = self.match.get(n)
            if s:
                W[n] = now(s).decompose()[1] @ self.fit[n]
            elif p:
                W[n] = W[p] @ (self.tQ0[p].inverted() @ self.tQ0[n])
            else:
                W[n] = self.tQ0[n]
            base = P[p] @ (b.parent.matrix_local.inverted() @ b.matrix_local) if p else b.matrix_local.copy()
            head = base.to_translation()
            if n == self.hips:
                hs = self.match[n]
                moved = now(hs).to_translation() - self.sP0[hs]
                head = self.arm_inv @ (self.tP0[n] + moved * self.scale + self.lift)
            M = Matrix.Translation(head) @ (self.arm_rot.inverted() @ W[n]).to_matrix().to_4x4()
            P[n] = M
            ARM.pose.bones[n].matrix_basis = base.inverted() @ M
            if n == self.hips:
                hips_base = base
        if self.plant:
            # Planting: the same knee angles on legs of other proportions reach higher or lower, so the hips are moved up or
            # down (and the whole body with them, nothing else changed) to put the feet at the clip's height, scaled; the foot
            # that's lower in the clip counts most, so the one on the ground holds. Only up and down: moving the body
            # sideways to hold one foot drags the other, and lurches as the weight passes between them (that needs IK).
            mw = ARM.matrix_world
            num, den = Vector(), 0.0
            for f in self.feet:
                s = self.match[f]
                sp = now(s).to_translation()
                w = math.exp(-max(sp.z - self.sP0[s].z, 0.0) / self.reach)
                want = self.tP0[f] + (sp - self.sP0[s]) * self.scale
                num += (want - mw @ P[f].to_translation()) * w
                den += w
            shift = self.arm_inv.to_3x3() @ Vector((0, 0, (num / den).z))
            ARM.pose.bones[self.hips].matrix_basis = hips_base.inverted() @ Matrix.Translation(shift) @ P[self.hips]
        bpy.context.view_layer.update()


FIT = None


def play(clip):
    for o in RIG_OBJECTS:
        ad = o.animation_data or o.animation_data_create()
        for t in ad.nla_tracks:
            t.mute = True
        ad.action = None
    for o, act, slot in clip['bind'] if clip else []:
        ad = o.animation_data
        ad.action = act
        if slot is not None:
            ad.action_slot = slot
        elif hasattr(ad, 'action_slot') and ad.action_slot is None:
            slots = list(getattr(ad, 'action_suitable_slots', [])) or list(act.slots)
            if slots:
                ad.action_slot = slots[0]
    if ARM:
        ARM.data.pose_position = 'POSE' if clip else 'REST'
    bpy.context.view_layer.update()


if ARGS['anim']:
    if not ARM:
        raise SystemExit('sheet.py: --anim needs a model with an armature')
    anim_path = os.path.abspath(ARGS['anim'])
    model_actions = set(bpy.data.actions)
    extra = import_file(anim_path)
    for o in extra:
        if o.type == 'MESH':
            o.hide_render = True  # (a clip file that carries its own figure: only its skeleton is wanted)
    SRC = Source(extra, anim_path)
    CLIPS = [c for c in SRC.clips() if c['bind'][0][1] not in model_actions]
    RIG_OBJECTS = SRC.objects + [ARM]
    FIT = Fit(SRC)
    REPORT['anim'] = anim_path
    REPORT['fit'] = {'bones_matched': len(FIT.match), 'bones': len(ARM.data.bones), 'leg_scale': round(FIT.scale, 4),
                     'hips': FIT.hips, 'feet_planted': FIT.plant, 'hips_raised': round(FIT.lift.z, 4)}
elif ARM:
    CLIPS = armature_clips(ARM, list(bpy.data.actions))
else:
    CLIPS = node_clips(NODE_RIG)

ALL_CLIPS = CLIPS
want = (ARGS['clip'] or '').strip()
if ARGS['list'] or (not want and len(CLIPS) > 6):
    for c in CLIPS:
        s, e = c['range']
        print(f'SHEET clip {c["name"]!r}: frames {s:.0f}-{e:.0f}, {(e - s) / FPS:.2f} s at {FPS:g} fps')
    if ARGS['list']:
        sys.exit(0)
    print(f'SHEET {len(CLIPS)} clips: none drawn; pick with --clip <name>[,<name>...] or --clip all')
    CLIPS = []
elif want == 'none':
    CLIPS = []
elif want and want != 'all':
    parts = [p.strip().lower() for p in want.split(',') if p.strip()]
    CLIPS = [c for c in CLIPS if any(p in c['name'].lower() for p in parts)]
    if not CLIPS:
        raise SystemExit(f'sheet.py: no clip matches {want!r} (there are {len(ALL_CLIPS)}; --list shows them)')

play(None)
STICK = False
if not meshes:
    if not CLIPS and ALL_CLIPS:
        play(ALL_CLIPS[0])  # a node skeleton has no rest pose of its own: stand it in a clip's first frame
    meshes = stick_figure()
    STICK = True
    warn('no mesh: drawn as a stick figure from the skeleton')


# ----------------------------------------------------------------------------------------------------------------- geometry

def world_points(objs, masks=None):
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        oe = o.evaluated_get(dg)
        me = oe.to_mesh()
        a = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get('co', a)
        a = a.reshape(-1, 3)
        M = np.array(oe.matrix_world)
        a = a @ M[:3, :3].T + M[:3, 3]
        oe.to_mesh_clear()
        if masks is not None:
            m = masks.get(o.name)
            if m is None or len(m) != len(a):
                continue
            a = a[m]
        pts.append(a)
    return np.concatenate(pts) if pts else np.zeros((0, 3))


def region_masks(joint):
    """Per mesh, which vertices the joint or the joints under it move (weight over 0.3): a hand with its fingers."""
    names = set(below(joint))
    masks = {}
    for o in meshes:
        idx = {g.index for g in o.vertex_groups if g.name in names}
        if not idx:
            continue
        m = np.zeros(len(o.data.vertices), dtype=bool)
        for v in o.data.vertices:
            for g in v.groups:
                if g.group in idx and g.weight > 0.3:
                    m[v.index] = True
                    break
        if m.any():
            masks[o.name] = m
    return masks


REST = world_points(meshes)
LO, HI = REST.min(axis=0), REST.max(axis=0)
H = float(HI[2] - LO[2]) or float(np.ptp(REST, axis=0).max()) or 1.0
CENTRE = Vector(((LO + HI) / 2).tolist())
GROUND_Z = float(LO[2])


AXES = {'-y': (0, -1, 0), '+y': (0, 1, 0), '-x': (-1, 0, 0), '+x': (1, 0, 0)}
HIPS = [find_joint(p, s) for p in ('thigh', 'upleg', 'upperleg', 'leg', 'hip') for s in ('l', 'r')]
HIPS = next(((HIPS[i], HIPS[i + 1]) for i in range(0, len(HIPS), 2) if HIPS[i] and HIPS[i + 1]), None)


def hip_front():
    """The way the hips face now: square to the line from the right hip to the left (None without both hips)."""
    if not HIPS:
        return None
    across = joint_pos(HIPS[1]) - joint_pos(HIPS[0])  # right hip minus left: toward the figure's right
    across.z = 0
    return Z.cross(across).normalized() if across.length > 1e-6 else None


def snapped(f):
    snap = max((Vector(v) for v in AXES.values()), key=lambda v: v.dot(f))
    return snap if snap.dot(f) > math.cos(math.radians(25)) else f


def guess_front():
    if ARGS['front'] in AXES:
        return Vector(AXES[ARGS['front']]), 'given'
    f = hip_front()
    if f:
        return snapped(f), 'from the hips'
    dirs = []
    for side in ('l', 'r'):
        f = find_joint('foot', side)
        if not f:
            continue
        kids = JOINTS[f]['children']
        if kids:
            toe = joint_pos(kids[0])
        elif ARM:
            toe = ARM.matrix_world @ ARM.pose.bones[f].tail
        else:
            continue
        d = toe - joint_pos(f)
        d.z = 0
        if d.length > 1e-6:
            dirs.append(d.normalized())
    if dirs:
        return snapped(sum(dirs, Vector()).normalized()), 'from the feet'
    return Vector((0, -1, 0)), 'default'


FRONT, FRONT_HOW = guess_front()
RIGHT = FRONT.cross(Z).normalized()  # the figure's own right


def view_dir(azimuth, elevation=0.0, front=None):
    """From the figure toward the camera: azimuth 0 in front of it, 90 at its right, 180 behind; elevation up from level."""
    front = front or FRONT
    right = front.cross(Z).normalized()
    a, e = math.radians(azimuth), math.radians(elevation)
    h = front * math.cos(a) + right * math.sin(a)
    return (h * math.cos(e) + Z * math.sin(e)).normalized()


tris = 0
for o in meshes:
    o.data.calc_loop_triangles()
    tris += len(o.data.loop_triangles)
REPORT['model'] = {
    'meshes': len(meshes), 'stick_figure': STICK, 'triangles': None if STICK else tris,
    'rig': 'armature' if ARM else ('nodes' if JOINTS else None), 'joints': len(JOINTS),
    'height': round(H, 4), 'size': [round(float(x), 4) for x in (HI - LO)], 'ground_z': round(GROUND_Z, 4),
    'front': [round(c, 3) for c in FRONT], 'front_from': FRONT_HOW, 'fps': FPS,
    'clips_in_file': [c['name'] for c in ALL_CLIPS],
}


# -------------------------------------------------------------------------------------------------------------------- stage

def stage():
    world = bpy.data.worlds.new('stage')
    SC.world = world
    if world.node_tree is None:
        world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    bg.inputs['Color'].default_value = (0.045, 0.05, 0.058, 1)
    bg.inputs['Strength'].default_value = 1.0

    def sun(name, energy, d, angle=4):
        ld = bpy.data.lights.new(name, 'SUN')
        ld.energy = energy
        ld.angle = math.radians(angle)
        ob = bpy.data.objects.new(name, ld)
        SC.collection.objects.link(ob)
        ob.rotation_euler = (-d.normalized()).to_track_quat('-Z', 'Y').to_euler()

    sun('key', 3.2, FRONT - RIGHT * 0.7 + Z * 1.1)
    sun('fill', 0.9, FRONT + RIGHT * 0.9 + Z * 0.25, 12)
    sun('rim', 2.6, -FRONT + RIGHT * 0.3 + Z * 0.8)

    size = H * 40
    me = bpy.data.meshes.new('ground')
    c = CENTRE
    me.from_pydata([(c.x - size, c.y - size, GROUND_Z), (c.x + size, c.y - size, GROUND_Z),
                    (c.x + size, c.y + size, GROUND_Z), (c.x - size, c.y + size, GROUND_Z)], [], [(0, 1, 2, 3)])
    ground = bpy.data.objects.new('ground', me)
    SC.collection.objects.link(ground)
    mat = bpy.data.materials.new('ground')
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes['Principled BSDF']
    bsdf.inputs['Roughness'].default_value = 0.85
    chk = nt.nodes.new('ShaderNodeTexChecker')
    chk.inputs['Color1'].default_value = (0.2, 0.2, 0.21, 1)
    chk.inputs['Color2'].default_value = (0.12, 0.12, 0.13, 1)
    chk.inputs['Scale'].default_value = 1.0 / (H / 4)
    tc = nt.nodes.new('ShaderNodeTexCoord')
    nt.links.new(tc.outputs['Object'], chk.inputs['Vector'])
    nt.links.new(chk.outputs['Color'], bsdf.inputs['Base Color'])
    mat.diffuse_color = (0.16, 0.16, 0.17, 1)
    me.materials.append(mat)

    cd = bpy.data.cameras.new('cam')
    cam = bpy.data.objects.new('cam', cd)
    SC.collection.objects.link(cam)
    SC.camera = cam
    cd.clip_start = H * 0.005
    cd.clip_end = H * 200
    transforms = [i.identifier for i in SC.view_settings.bl_rna.properties['view_transform'].enum_items]
    SC.view_settings.view_transform = 'AgX' if 'AgX' in transforms else 'Filmic'
    r = SC.render
    r.image_settings.file_format = 'PNG'
    r.image_settings.color_mode = 'RGB'
    r.film_transparent = False
    if hasattr(r, 'use_stamp'):
        r.use_stamp = True
        for k in ('date', 'time', 'render_time', 'frame', 'frame_range', 'memory', 'hostname', 'camera', 'lens', 'scene',
                  'marker', 'filename', 'sequencer_strip', 'peak_memory'):
            if hasattr(r, 'use_stamp_' + k):
                setattr(r, 'use_stamp_' + k, False)
        r.use_stamp_note = True
        r.stamp_foreground = (1, 1, 1, 1)
        r.stamp_background = (0, 0, 0, 0.7)
    return cam


CAM = stage()
GROUND = bpy.data.objects['ground']


def engine(kind):
    r = SC.render
    if kind == 'clay':
        r.engine = 'BLENDER_WORKBENCH'
        sh = SC.display.shading
        sh.light = 'STUDIO'
        sh.color_type = 'SINGLE'
        sh.single_color = (0.72, 0.7, 0.67)
        sh.show_cavity = True
        sh.show_shadows = True
        SC.display.shadow_focus = 0.2
        return
    if ARGS['engine'] == 'cycles':
        r.engine = 'CYCLES'
        SC.cycles.samples = ARGS['samples'] * 2
        SC.cycles.use_denoising = True
        try:
            prefs = bpy.context.preferences.addons['cycles'].preferences
            prefs.refresh_devices()
            for t in ('OPTIX', 'CUDA', 'HIP', 'METAL', 'ONEAPI'):
                if any(d.type == t for d in prefs.devices):
                    prefs.compute_device_type = t
                    for d in prefs.devices:
                        d.use = True
                    SC.cycles.device = 'GPU'
                    break
        except Exception:
            pass
        return
    for name in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'):
        try:
            r.engine = name
            break
        except TypeError:
            continue
    SC.eevee.taa_render_samples = ARGS['samples']


# ------------------------------------------------------------------------------------------------------------------- camera

def fit_perspective(points, direction, target, fov=30.0, margin=1.08, size=512):
    """Puts the camera along `direction` from `target`, just far enough that every point is in frame."""
    cd = CAM.data
    cd.type = 'PERSP'
    cd.sensor_fit = 'AUTO'
    cd.angle = math.radians(fov)
    w = -direction
    u = w.cross(Z)
    if u.length < 1e-6:
        u = RIGHT.copy()
    u.normalize()
    v = u.cross(w).normalized()
    rel = points - np.array(target)
    lat, vert, dep = rel @ np.array(u), rel @ np.array(v), rel @ np.array(direction)
    t = math.tan(cd.angle / 2) / margin
    dist = float(np.max(dep + np.maximum(np.abs(lat), np.abs(vert)) / t))
    CAM.location = target + direction * dist
    CAM.rotation_euler = w.to_track_quat('-Z', 'Y').to_euler()
    SC.render.resolution_x = SC.render.resolution_y = size


def fit_ortho(points, direction, margin=1.1):
    """An orthographic view along `direction`: the screen's axes, the points' centre on them, and the span that holds them."""
    w = -direction
    u = w.cross(Z).normalized()
    v = u.cross(w).normalized()
    pu, pv = points @ np.array(u), points @ np.array(v)
    span = max(float(np.ptp(pu)), float(np.ptp(pv))) * margin
    return u, v, (float(pu.min() + pu.max()) / 2, float(pv.min() + pv.max()) / 2), span


def place_ortho(direction, u, v, cu, cv, span, along, size):
    cd = CAM.data
    cd.type = 'ORTHO'
    cd.ortho_scale = span
    centre = u * cu + v * cv + direction * along
    CAM.location = centre + direction * H * 20
    CAM.rotation_euler = (-direction).to_track_quat('-Z', 'Y').to_euler()
    SC.render.resolution_x = SC.render.resolution_y = size


def render(name, label, size):
    r = SC.render
    if hasattr(r, 'stamp_note_text'):
        r.stamp_note_text = label
        r.stamp_font_size = max(11, size // 26)
    path = os.path.join(FRAMES_DIR, name + '.png')
    r.filepath = path
    bpy.ops.render.render(write_still=True)
    return path


def tile(paths, cols, name, gap=4):
    imgs = []
    for p in paths:
        im = bpy.data.images.load(p)
        w, h = im.size
        a = np.empty(w * h * 4, dtype=np.float32)
        im.pixels.foreach_get(a)
        imgs.append(a.reshape(h, w, 4))
        bpy.data.images.remove(im)
    h, w = imgs[0].shape[:2]
    rows = math.ceil(len(imgs) / cols)
    W, Ht = cols * w + (cols + 1) * gap, rows * h + (rows + 1) * gap
    sheet = np.full((Ht, W, 4), 0.02, dtype=np.float32)
    sheet[..., 3] = 1
    for i, a in enumerate(imgs):
        rr, cc = divmod(i, cols)
        y0 = Ht - gap - (rr + 1) * h - rr * gap  # images are stored bottom row first
        x0 = gap + cc * (w + gap)
        sheet[y0:y0 + h, x0:x0 + w] = a
    im = bpy.data.images.new(name, W, Ht, alpha=False)
    im.pixels.foreach_set(sheet.ravel())
    path = os.path.join(OUT, name + '.png')
    im.filepath_raw = path
    im.file_format = 'PNG'
    im.save()
    bpy.data.images.remove(im)
    REPORT['sheets'].append(os.path.basename(path))
    print(f'SHEET wrote {path}')
    return path


# ------------------------------------------------------------------------------------------------------------- model sheets

SIDES = [(0, 'front'), (45, 'front-right'), (90, 'right'), (135, 'back-right'), (180, 'back'), (270, 'left')]


def centre_of(pts):
    return Vector(((pts.min(0) + pts.max(0)) / 2).tolist())


def model_sheets():
    play(None) if ARM else None
    size = ARGS['size']
    for kind in ('lit', 'clay'):
        engine(kind)
        paths = []
        for az, label in SIDES:
            fit_perspective(REST, view_dir(az, 6), CENTRE, fov=28, size=size)
            paths.append(render(f'model_{kind}_{az:03d}', f'{label} · {kind} · h={H:.3g}', size))
        tile(paths, 3, f'model_{kind}')

    engine('lit')
    shots = []
    head = find_joint('head')
    pts = world_points(meshes, region_masks(head)) if head else np.zeros((0, 3))
    word = 'head'
    if not len(pts):
        pts, word = REST[REST[:, 2] > HI[2] - H * 0.14], 'top'
    c = centre_of(pts)
    shots += [('head_front', f'{word} · front', pts, view_dir(0, 4), c), ('head_side', f'{word} · right', pts, view_dir(90, 4), c)]
    hands = 0
    for side, word, az in (('l', 'left', 300), ('r', 'right', 60)):  # from in front and to that side, outside the hand
        j = find_joint('hand', side)
        pts = world_points(meshes, region_masks(j)) if j else np.zeros((0, 3))
        if len(pts):
            hands += 1
            shots.append((f'hand_{side}', f'{word} hand', pts, view_dir(az, 20), centre_of(pts)))
    if not hands:
        warn('no hands found by joint name and weights: no hand close-ups')
    feet = REST[REST[:, 2] < LO[2] + H * 0.09]
    if len(feet):
        shots.append(('feet', 'feet · front-right', feet, view_dir(30, 25), centre_of(feet)))

    paths = []
    for name, label, pts, d, c in shots:
        fit_perspective(pts, d, c, fov=30, margin=1.15, size=size)
        paths.append(render(f'close_{name}', label, size))
    # as from across a street: the camera at eye height, the figure about a sixth of the picture's height
    d = view_dir(25, 0)
    flat = Vector((d.x, d.y, 0)).normalized()
    eye = Vector((CENTRE.x, CENTRE.y, GROUND_Z + H * 0.9))
    cd = CAM.data
    cd.type = 'PERSP'
    cd.angle = math.radians(50)
    CAM.location = eye + flat * (H * 3 / math.tan(cd.angle / 2))
    CAM.rotation_euler = (-flat).to_track_quat('-Z', 'Y').to_euler()
    SC.render.resolution_x = SC.render.resolution_y = size
    paths.append(render('close_far', 'far · as in the street', size))
    tile(paths, 3, 'model_close')


# ------------------------------------------------------------------------------------------------------------- anim strips

def safe(name):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', name).strip('_')[:48] or 'clip'


def go_to(f):
    SC.frame_set(int(math.floor(f)), subframe=f - math.floor(f))
    if FIT:
        FIT.pose()


def anim_strips(clip, draw=True):
    play(clip)
    s, e = (float(x) for x in clip['range'])
    n = max(2, ARGS['frames']) if e > s else 1
    frames = [s + (e - s) * i / (n - 1) for i in range(n)] if n > 1 else [s]

    poses, centres, fronts = [], [], []
    for f in frames:
        go_to(f)
        p = world_points(meshes)
        poses.append(p)
        centres.append((p.min(0) + p.max(0)) / 2)
        fronts.append(hip_front())
    centres = np.array(centres)
    allp = np.concatenate(poses)
    path_span = float(np.ptp(centres[:, :2], axis=0).max())
    travel = float(np.linalg.norm(centres[-1, :2] - centres[0, :2]))
    follow = path_span > H
    low = float(allp[:, 2].min())
    # which way it faces over the clip: the hips' mean, unless told (a clip that turns is drawn from its mean)
    front, how = FRONT, FRONT_HOW
    if ARGS['front'] not in AXES and all(fronts):
        m = sum(fronts, Vector())
        if m.length > 0.3 * len(fronts):
            front, how = snapped(m.normalized()), 'from the hips over the clip'
    # a skeleton with no rest pose of its own stands on its own lowest point; a model on its rest pose's
    ground = low if STICK and not ARM else GROUND_Z
    GROUND.location.z = ground - GROUND_Z
    info = {'name': clip['name'], 'frames': [round(s, 2), round(e, 2)], 'duration': round((e - s) / FPS, 3),
            'sampled': len(frames), 'travel': round(travel, 4), 'path_span': round(path_span, 4),
            'camera': 'follows' if follow else 'fixed', 'front': [round(c, 3) for c in front], 'front_from': how,
            'lowest_point_above_ground': round(low - ground, 4), 'sheets': []}
    if low < ground - H * 0.02:
        warn(f'{clip["name"]}: the figure goes {ground - low:.3g} below the ground')

    engine('lit')
    size = max(160, ARGS['size'] * 2 // 3)
    cols = 4 if len(frames) > 6 else len(frames)
    stem = safe(clip['name'])
    for view, az in (('side', 90), ('front', 0)) if draw else ():
        d = view_dir(az, 10, front)
        if follow:
            rel = np.concatenate([p - [c[0], c[1], 0] for p, c in zip(poses, centres)])
            u, v, (cu, cv), span = fit_ortho(rel, d, 1.25)
        else:
            u, v, (cu, cv), span = fit_ortho(allp, d)
        paths = []
        for i, f in enumerate(frames):
            go_to(f)
            off = Vector((centres[i][0], centres[i][1], 0)) if follow else Vector()
            place_ortho(d, u, v, cu + off.dot(u), cv + off.dot(v), span, off.dot(d), size)
            t = (f - s) / FPS
            paths.append(render(f'anim_{stem}_{view}_{i:02d}', f'{view} · t={t:.2f}s · f{f:.1f}', size))
        info['sheets'].append(os.path.basename(tile(paths, cols, f'anim_{stem}_{view}')))
    REPORT['clips'].append(info)
    return info


# ------------------------------------------------------------------------------------------------------------------- checks
#
# Measured at every frame of a clip, from the joints and the mesh. Lengths are in the file's units and thresholds are fractions
# of the figure's height H, so they hold at any scale. A fault is a finding to look at in the strips, not a verdict.

LIMITS = {
    'contact': 0.02,      # a foot is down when its lowest point is within this of the floor (x H)
    'slide': 0.03,        # a planted foot sliding further than this in one step (x H)
    'sink': 0.015,        # the body below the ground by more than this (x H)
    'pop': 0.05,          # a joint jumping this far off its path in one frame (x H)
    'loop_gap': 0.03,     # a _Loop clip's last pose this far from its first (x H)
    'stretch': 0.03,      # a bone longer or shorter than at rest by this fraction
    'knee': (-8, 165),    # the knee's bend, degrees: below the first it bends backwards, past the second it folds too far
    'elbow': (-12, 160),
    'sideways': 45,       # a knee or elbow bent more than 25 degrees, its hinge this far off the way it bends at rest
    'crossing': 0.05,     # this much more of a mesh's faces through another's than at rest
}


def main_child(n):
    kids = JOINTS[n]['children']
    return max(kids, key=lambda c: len(below(c))) if kids else None


def limb(side, tops):
    """A limb's three joints by name: (shoulder or hip, elbow or knee, wrist or ankle), or None."""
    for p in tops:
        top = find_joint(p, side)
        mid = main_child(top) if top else None
        end = main_child(mid) if mid else None
        if end:
            return top, mid, end
    return None


def rest_matrices():
    """Each joint's world matrix at rest: the armature's bones, or a glTF's nodes as the file has them."""
    if ARM:
        return {n: ARM.matrix_world @ ARM.data.bones[n].matrix_local for n in JOINTS}
    if ARGS['file'].lower().endswith(('.glb', '.gltf')):
        r = gltf_rest(ARGS['file'])
        return {n: r[plain(n)] for n in JOINTS if plain(n) in r}
    return {}


def joint_matrix(n):
    return ARM.matrix_world @ ARM.pose.bones[n].matrix if ARM else JOINTS[n]['obj'].matrix_world


def hinges(rest):
    """Knees and elbows, each with the axis it bends about at rest, kept in the upper bone's own frame so it turns with
    the limb: a knee bends so the shin goes back, an elbow so the forearm comes forward."""
    out = []
    if not rest:
        return out
    pos = {n: m.to_translation() for n, m in rest.items()}
    f = FRONT
    if HIPS and all(h in pos for h in HIPS):
        across = pos[HIPS[1]] - pos[HIPS[0]]
        across.z = 0
        if across.length > 1e-6:
            f = Z.cross(across).normalized()
    for kind, tops, toward in (('knee', ('thigh', 'upleg', 'upperleg', 'leg'), -1), ('elbow', ('upperarm', 'arm'), 1)):
        for side in 'lr':
            ch = limb(side, tops)
            if not ch or not all(c in pos for c in ch):
                continue
            a = (pos[ch[1]] - pos[ch[0]]).normalized().cross(f * toward)
            if a.length < 1e-3:
                continue
            out.append({'name': f'{kind}_{side}', 'kind': kind, 'joints': ch,
                        'axis': rest[ch[0]].decompose()[1].inverted() @ a.normalized()})
    return out


def mesh_arrays():
    dg = bpy.context.evaluated_depsgraph_get()
    out = {}
    for o in meshes:
        oe = o.evaluated_get(dg)
        me = oe.to_mesh()
        a = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get('co', a)
        M = np.array(oe.matrix_world)
        out[o.name] = a.reshape(-1, 3) @ M[:3, :3].T + M[:3, 3]
        oe.to_mesh_clear()
    return out


def overlaps(arrs, polys):
    """For each pair of meshes, the share of the smaller one's faces crossing the other's: cloth through a leg, a sleeve
    through a hip."""
    from mathutils.bvhtree import BVHTree
    trees = {n: BVHTree.FromPolygons(arrs[n].tolist(), polys[n]) for n in polys}
    names = sorted(trees)
    out = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            pairs = trees[a].overlap(trees[b])
            fa, fb = len({p[0] for p in pairs}), len({p[1] for p in pairs})
            small = min(len(polys[a]), len(polys[b]))
            out[f'{a} × {b}'] = round((fa if len(polys[a]) <= len(polys[b]) else fb) / max(small, 1), 4)
    return out


def stances(mask):
    """Runs of True in a list, as (first, last) index pairs."""
    runs, start = [], None
    for i, m in enumerate(list(mask) + [False]):
        if m and start is None:
            start = i
        elif not m and start is not None:
            runs.append((start, i - 1))
            start = None
    return runs


REST_M = None
HINGES = []
FOOT_MASKS = {}
POLYS = {}
REST_OVERLAPS = None


def check_clip(clip):
    global REST_M, HINGES, FOOT_MASKS, POLYS, REST_OVERLAPS
    if REST_M is None:
        play(None)
        REST_M = rest_matrices()
        HINGES = hinges(REST_M)
        for side in 'lr':
            f = find_joint('foot', side)
            m = region_masks(f) if f else {}
            if m:
                FOOT_MASKS[side] = m
        if not STICK and len(meshes) > 1:
            POLYS = {o.name: [tuple(p.vertices) for p in o.data.polygons] for o in meshes}
            REST_OVERLAPS = overlaps(mesh_arrays(), POLYS)

    play(clip)
    s, e = (float(x) for x in clip['range'])
    n = max(2, int(round(e - s)) + 1)
    frames = np.linspace(s, e, n)
    dt = max((frames[1] - frames[0]) / FPS, 1e-6)
    times = (frames - s) / FPS
    names = list(JOINTS)
    every = max(1, n // 40)  # meshes crossing: at most about forty frames
    J = np.zeros((n, len(names), 3))
    low = np.zeros(n)
    feet = {side: [] for side in FOOT_MASKS}
    bend = {h['name']: np.zeros(n) for h in HINGES}
    sideways = {h['name']: np.zeros(n) for h in HINGES}
    crossing = {}
    lowest_mesh = []
    for i, f in enumerate(frames):
        go_to(f)
        arrs = mesh_arrays()
        lows = {o: a[:, 2].min() for o, a in arrs.items()}
        lowest_mesh.append(min(lows, key=lows.get))
        low[i] = lows[lowest_mesh[-1]]
        for side, masks in FOOT_MASKS.items():
            feet[side].append(np.concatenate([arrs[o][m] for o, m in masks.items() if o in arrs]))
        mats = {nm: joint_matrix(nm) for nm in names}
        J[i] = [tuple(mats[nm].to_translation()) for nm in names]
        for h in HINGES:
            a0, b0, c0 = (mats[j].to_translation() for j in h['joints'])
            u, l = (b0 - a0).normalized(), (c0 - b0).normalized()
            ax = mats[h['joints'][0]].decompose()[1] @ h['axis']
            c = u.cross(l)
            bend[h['name']][i] = math.degrees(math.atan2(c.dot(ax), u.dot(l)))
            sideways[h['name']][i] = math.degrees(math.acos(min(1.0, abs(c.dot(ax)) / c.length))) if c.length > 0.42 else 0.0
        if POLYS and i % every == 0:
            for pair, k in overlaps(arrs, POLYS).items():
                crossing.setdefault(pair, []).append((round(float(times[i]), 3), k))

    faults = []
    t = lambda i: f'{times[i]:.2f}s'  # noqa: E731
    res = {'frames_checked': n, 'fps': FPS}

    # the ground: a model stands on its rest pose's floor; a skeleton with no rest of its own on the clip's lowest point
    ground = float(low.min()) if STICK and not ARM else GROUND_Z
    sink = ground - low
    res['lowest'] = round(float(low.min() - ground), 4)
    deep = int(np.argmax(sink))
    if sink[deep] > LIMITS['sink'] * H:
        what = '' if STICK or len(meshes) == 1 else f' (lowest: {lowest_mesh[deep]})'
        faults.append(f'sinks {sink[deep]:.3g} below the ground at {t(deep)}{what} '
                      f'({int((sink > LIMITS["sink"] * H).sum())} of {n} frames)')

    # the feet: down when near the floor; a foot that's down should hold still against the ground, which for a clip made to
    # walk on the spot moves back under it at the walking pace (the common motion of every planted foot)
    if feet:
        floor = min(float(min(p[:, 2].min() for p in pts)) for pts in feet.values())
        res['feet_floor'] = round(floor - ground, 4)
        if floor - ground > LIMITS['contact'] * H and not STICK:
            faults.append(f'never touches the ground: the feet stay {floor - ground:.3g} above it')
        tol = LIMITS['contact'] * H
        moves = {side: [None] * n for side in feet}
        down = {side: [bool(p[:, 2].min() < floor + tol) for p in pts] for side, pts in feet.items()}
        for side, pts in feet.items():
            for i in range(n - 1):
                both = (pts[i][:, 2] < floor + tol) & (pts[i + 1][:, 2] < floor + tol)
                if both.sum() >= 1:
                    moves[side][i] = np.median(pts[i + 1][both, :2] - pts[i][both, :2], axis=0) / dt
        allv = [v for side in moves for v in moves[side] if v is not None]
        pace = np.median(np.array(allv), axis=0) if len(allv) >= 4 else np.zeros(2)
        if np.linalg.norm(pace) < 0.03 * H:  # (about 5 cm/s on a person: standing still)
            pace = np.zeros(2)
        res['pace'] = round(float(np.linalg.norm(pace)), 4)
        res['feet'] = {}
        for side in feet:
            slide = [float(np.linalg.norm(v - pace)) if v is not None else None for v in moves[side]]
            steps = []
            for a, b in stances(down[side]):
                d = sum(x for x in slide[a:b + 1] if x is not None) * dt
                steps.append({'from': round(float(times[a]), 3), 'to': round(float(times[b]), 3), 'slide': round(d, 4)})
                if d > LIMITS['slide'] * H and b > a:
                    faults.append(f'{side} foot slides {d:.3g} while down, {t(a)} to {t(b)}')
            res['feet'][side] = {'down_frames': int(sum(down[side])), 'steps': steps,
                                 'height': [round(float(p[:, 2].min() - floor), 4) for p in feet[side]],
                                 'slide_speed': [None if x is None else round(x, 4) for x in slide]}

    # knees and elbows
    res['hinges'] = {}
    for h in HINGES:
        lo, hi = LIMITS[h['kind']]
        b, sw = bend[h['name']], sideways[h['name']]
        res['hinges'][h['name']] = {'min': round(float(b.min()), 1), 'max': round(float(b.max()), 1),
                                    'sideways_max': round(float(sw.max()), 1), 'bend': [round(float(x), 1) for x in b],
                                    'sideways': [round(float(x), 1) for x in sw]}
        i = int(np.argmin(b))
        if b[i] < lo:
            faults.append(f'{h["name"]} bends backwards {-b[i]:.0f}° at {t(i)}')
        i = int(np.argmax(b))
        if b[i] > hi:
            faults.append(f'{h["name"]} folds to {b[i]:.0f}° at {t(i)}')
        bad = [k for k in range(n) if b[k] > 25 and sw[k] > LIMITS['sideways']]
        if bad:
            k = max(bad, key=lambda k: sw[k])
            faults.append(f'{h["name"]} bends sideways ({sw[k]:.0f}° off its hinge) at {t(k)}, {len(bad)} frames')

    # joints jumping off their path for a frame
    # (a pop is a frame off the path and back: the change of speed one way, then the other, either side of it; a foot
    # striking the ground or a fist stopping changes speed once, and isn't one)
    if n >= 5:
        D = J[2:] - 2 * J[1:-1] + J[:-2]  # frame 1..n-2
        d2 = np.linalg.norm(D, axis=2)
        pops = []
        for j, nm in enumerate(names):
            for k in range(1, len(d2) - 1):
                if (d2[k, j] > LIMITS['pop'] * H and np.dot(D[k - 1, j], D[k, j]) < 0 and np.dot(D[k + 1, j], D[k, j]) < 0
                        and min(d2[k - 1, j], d2[k + 1, j]) > 0.3 * d2[k, j]):
                    pops.append((nm, k + 1))
        # (a hand that pops carries its fingers: keep the highest joint of a chain popping at about the same time)
        at = {}
        for nm, k in pops:
            at.setdefault(nm, set()).add(k)

        def carried(nm, k):
            p = JOINTS[nm]['parent']
            while p:
                if at.get(p, set()) & {k - 1, k, k + 1}:
                    return True
                p = JOINTS[p]['parent']
            return False
        pops = [(nm, k) for nm, k in pops if not carried(nm, k)]
        res['pops'] = [{'joint': nm, 'time': round(float(times[k]), 3)} for nm, k in pops]
        for nm, k in pops[:5]:
            faults.append(f'{nm} jumps at {t(k)}')

    # a loop's ends
    hips = FIT.hips if FIT else (next((nm for nm in names if set(tokens(nm)) & {'pelvis', 'hips'}), None) or names[0])
    hj = names.index(hips)
    rel = J - J[:, hj:hj + 1]
    gap = float(max(np.linalg.norm(rel[-1] - rel[0], axis=1).max(), abs(J[-1, hj, 2] - J[0, hj, 2])))
    res['loop_gap'] = round(gap, 4)
    if 'loop' in clip['name'].lower() and gap > LIMITS['loop_gap'] * H:
        faults.append(f'a loop whose last pose is {gap:.3g} from its first')

    # bones stretching
    if REST_M:
        worst = (0.0, None, 0)
        rp = {nm: REST_M[nm].to_translation() for nm in names if nm in REST_M}
        for j, nm in enumerate(names):
            p = JOINTS[nm]['parent']
            if not p or nm not in rp or p not in rp or depth(nm) <= depth(hips):
                continue  # (the hips and above move by translation: their distance from the root isn't a bone)
            L0 = (rp[nm] - rp[p]).length
            if L0 < 0.01 * H:
                continue
            ratio = np.linalg.norm(J[:, j] - J[:, names.index(p)], axis=1) / L0 - 1
            k = int(np.argmax(np.abs(ratio)))
            if abs(ratio[k]) > abs(worst[0]):
                worst = (float(ratio[k]), nm, k)
        res['stretch'] = {'worst': round(worst[0], 4), 'bone': worst[1]}
        if abs(worst[0]) > LIMITS['stretch']:
            faults.append(f'{worst[1]} {"stretches" if worst[0] > 0 else "shrinks"} {abs(worst[0]) * 100:.0f}% at {t(worst[2])}')

    # meshes through each other, against how much they already cross at rest
    if crossing:
        res['crossing'] = {}
        for pair, seq in crossing.items():
            base = REST_OVERLAPS.get(pair, 0)
            tm, peak = max(seq, key=lambda x: x[1])
            res['crossing'][pair] = {'rest': base, 'max': peak, 'at': tm}
            if peak - base > LIMITS['crossing']:
                faults.append(f'{pair}: {peak * 100:.1f}% of the smaller one\'s faces through the other at {tm:.2f}s '
                              f'(at rest {base * 100:.1f}%)')

    res['times'] = [round(float(x), 3) for x in times]
    res['faults'] = faults
    for f in faults:
        print(f'SHEET fault {clip["name"]}: {f}')
    if not faults:
        print(f'SHEET clip {clip["name"]}: no faults found')
    return res


# ------------------------------------------------------------------------------------------------------------- model checks
#
# The model at rest: its meshes' soundness, bits floating off it, its skinning, and a human figure's proportions. The ranges
# are an adult's, wide enough for realistic builds and outfits (they were set on MakeHuman figures, hand-built and generated
# ones); a stylised figure will go outside them on purpose (--proportions none leaves them out).

HUMAN = {  # fraction of the figure's height: (low, high)
    'hand_length': (0.085, 0.135),    # wrist to the middle fingertip
    'palm_width': (0.038, 0.065),     # across the knuckles
    'palm_thickness': (0.009, 0.028), # back of the hand to the palm, mid-palm: swollen hands show here
    'foot_length': (0.12, 0.19),      # heel to toe, shoes included
    'arm_length': (0.34, 0.50),       # shoulder joint to the middle fingertip, along the bones
    'leg_length': (0.42, 0.57),       # hip joint to the floor
    'shoulder_width': (0.15, 0.30),   # between the shoulder joints
    'head_height': (0.09, 0.19),      # what the head bone moves, bottom to top
}


def islands(bm):
    """The vertex indices of each connected piece of a mesh."""
    seen = [False] * len(bm.verts)
    out = []
    for v in bm.verts:
        if seen[v.index]:
            continue
        piece, todo = [], [v]
        seen[v.index] = True
        while todo:
            w = todo.pop()
            piece.append(w.index)
            for e in w.link_edges:
                o = e.other_vert(w)
                if not seen[o.index]:
                    seen[o.index] = True
                    todo.append(o)
        out.append(piece)
    return out


def extent(values, lo=2, hi=98):
    return float(np.percentile(values, hi) - np.percentile(values, lo)) if len(values) else 0.0


def proportions(arrs):
    """Measurements of a human figure from its joints and the vertices they move, as fractions of its height."""
    out = {}
    pos = {n: joint_pos(n) for n in JOINTS}
    allp = np.concatenate(list(arrs.values()))

    def region(joint, without=()):
        masks = region_masks(joint)
        if without:
            for o, m in list(masks.items()):
                drop = np.zeros_like(m)
                for j in without:
                    for o2, m2 in region_masks(j).items():
                        if o2 == o:
                            drop |= m2
                masks[o] = m & ~drop
        pts = [arrs[o][m] for o, m in masks.items() if o in arrs]
        return np.concatenate(pts) if pts else np.zeros((0, 3))

    for side in 'lr':
        h = find_joint('hand', side)
        pts = region(h) if h else np.zeros((0, 3))
        if len(pts) < 20:
            continue
        wrist = np.array(pos[h])
        kids = JOINTS[h]['children']
        mid = next((k for k in kids if 'middle' in tokens(k)), None)
        index = next((k for k in kids if 'index' in tokens(k)), None)
        pinky = next((k for k in kids if set(tokens(k)) & {'pinky', 'little'}), None)
        rel = pts - wrist
        if mid:
            along = np.array(pos[mid]) - wrist
        else:  # (no fingers: the hand's longest direction)
            along = np.linalg.svd(rel - rel.mean(0), full_matrices=False)[2][0]
            if np.dot(along, rel.mean(0)) < 0:
                along = -along
        along /= np.linalg.norm(along)
        if index and pinky:
            across = np.array(pos[index]) - np.array(pos[pinky])
        else:
            across = np.linalg.svd(rel - rel.mean(0), full_matrices=False)[2][1]
        across -= along * np.dot(across, along)
        across /= np.linalg.norm(across)
        normal = np.cross(along, across)
        a = rel @ along
        length = float(np.percentile(a, 99.5))
        out[f'hand_length_{side}'] = length / H
        # the palm without the thumb, which at rest lies beside or under it
        thumb = [k for k in kids if 'thumb' in tokens(k)]
        rel = (region(h, thumb) if thumb else pts) - wrist
        a = rel @ along
        knuckles = (a > 0.40 * length) & (a < 0.50 * length)
        out[f'palm_width_{side}'] = extent(rel[knuckles] @ across) / H
        palm = (a > 0.20 * length) & (a < 0.40 * length)
        out[f'palm_thickness_{side}'] = extent(rel[palm] @ normal) / H
        out[f'_hand_{side}'] = length

    for side in 'lr':
        f = find_joint('foot', side)
        pts = region(f) if f else np.zeros((0, 3))
        if len(pts) >= 20:
            fwd = np.array(FRONT)
            out[f'foot_length_{side}'] = extent(pts @ fwd, 0.5, 99.5) / H

    for side in 'lr':
        arm = limb(side, ('upperarm', 'arm'))
        if arm and f'_hand_{side}' in out:  # (along the bones: a rest pose's elbow is often bent)
            reach = (pos[arm[1]] - pos[arm[0]]).length + (pos[arm[2]] - pos[arm[1]]).length + out[f'_hand_{side}']
            out[f'arm_length_{side}'] = reach / H
        leg = limb(side, ('thigh', 'upleg', 'upperleg', 'leg'))
        if leg:
            out[f'leg_length_{side}'] = float(pos[leg[0]].z - GROUND_Z) / H
    al, ar = limb('l', ('upperarm', 'arm')), limb('r', ('upperarm', 'arm'))
    if al and ar:
        out['shoulder_width'] = float((pos[al[0]] - pos[ar[0]]).length) / H
    head = find_joint('head')
    if head:
        pts = region(head)
        if len(pts):
            out['head_height'] = extent(pts[:, 2], 0.5, 100) / H
    del allp
    return {k: round(v, 4) for k, v in out.items() if not k.startswith('_')}


def model_checks():
    import bmesh
    play(None)
    arrs = mesh_arrays()
    faults, res = [], {'meshes': {}}
    gap_limit = 0.004 * H
    pieces_of = {}
    for o in meshes:
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bm.verts.ensure_lookup_table()
        many = sum(1 for e in bm.edges if len(e.link_faces) > 2)
        wire = sum(1 for e in bm.edges if not e.link_faces)
        flipped = sum(1 for e in bm.edges if len(e.link_faces) == 2 and not e.is_contiguous)
        tiny = sum(1 for f in bm.faces if f.calc_area() < (H * 1e-5) ** 2)
        loose = sum(1 for v in bm.verts if not v.link_faces)
        pieces = islands(bm)
        bm.free()
        pieces_of[o.name] = pieces
        info = {'vertices': len(o.data.vertices), 'faces': len(o.data.polygons), 'pieces': len(pieces),
                'edges_on_3_faces': many, 'wire_edges': wire, 'flipped_neighbours': flipped, 'degenerate_faces': tiny,
                'loose_vertices': loose}
        # skinning: what no bone moves, and more than four bones on one vertex (what glTF and most engines take)
        if ARM and any(m.type == 'ARMATURE' for m in o.modifiers):
            bones = {i for i, g in enumerate(o.vertex_groups) if g.name in ARM.data.bones}
            unweighted = over4 = 0
            for v in o.data.vertices:
                ws = [g.weight for g in v.groups if g.group in bones and g.weight > 1e-4]
                if not ws:
                    unweighted += 1
                if len(ws) > 4:
                    over4 += 1
            info['unweighted_vertices'] = unweighted
            info['over_4_bones'] = over4
            if unweighted:
                faults.append(f'{o.name}: {unweighted} vertices no bone moves (they stay behind when the figure moves)')
            if over4:
                faults.append(f'{o.name}: {over4} vertices on more than 4 bones (an engine drops the rest)')
        if many:
            faults.append(f'{o.name}: {many} edges shared by more than two faces')
        if flipped > 0.001 * len(o.data.polygons) + 2:
            faults.append(f'{o.name}: {flipped} edges between faces facing opposite ways (flipped normals)')
        if tiny > 0.001 * len(o.data.polygons) + 2:
            faults.append(f'{o.name}: {tiny} faces with no area')
        res['meshes'][o.name] = info

    # bits floating off the figure: a small piece with no other surface (of any mesh) within a little way of it
    from mathutils.bvhtree import BVHTree
    trees, face_piece = {}, {}
    for o in meshes:
        polys = [tuple(p.vertices) for p in o.data.polygons]
        if not polys:
            continue
        trees[o.name] = BVHTree.FromPolygons(arrs[o.name].tolist(), polys)
        piece_of = np.empty(len(o.data.vertices), dtype=np.int64)
        for k, piece in enumerate(pieces_of[o.name]):
            piece_of[piece] = k
        face_piece[o.name] = [int(piece_of[p[0]]) for p in polys]

    def gap_from(o_name, k, piece):
        """How far the piece is from any other surface, up to 16 times the limit (None: further)."""
        sample = piece[:: max(1, len(piece) // 24)]
        r = gap_limit
        while r <= 16 * gap_limit:
            best = None
            for i in sample:
                co = Vector(arrs[o_name][i])
                for name, tree in trees.items():
                    for hit in tree.find_nearest_range(co, r):
                        if name == o_name and face_piece[name][hit[2]] == k:
                            continue
                        best = hit[3] if best is None else min(best, hit[3])
            if best is not None:
                return best
            r *= 2
        return None

    floating = []
    for o in meshes:
        total = len(o.data.vertices)
        if o.name not in trees:
            continue
        for k, piece in enumerate(pieces_of[o.name]):
            if len(piece) > max(0.02 * total, 30) or len(piece) > 2000:
                continue
            gap = gap_from(o.name, k, piece)
            gap = 16 * gap_limit if gap is None else gap
            if gap > gap_limit:
                c = arrs[o.name][piece].mean(0)
                floating.append({'mesh': o.name, 'vertices': len(piece), 'gap': round(gap, 4),
                                 'at_height': round(float((c[2] - GROUND_Z) / H), 3)})
    floating.sort(key=lambda f: -f['gap'])
    res['floating'] = floating[:40]
    if floating:
        where = ', '.join(f'{f["mesh"]} ({f["vertices"]} vertices, {f["gap"]:.3g} off, at {f["at_height"] * 100:.0f}% of the height)'
                          for f in floating[:3])
        faults.append(f'{len(floating)} pieces floating off the figure: {where}{" ..." if len(floating) > 3 else ""}')

    if ARGS.get('proportions', 'human') != 'none' and JOINTS:
        props = proportions(arrs)
        res['proportions'] = props
        for k, v in props.items():
            key = re.sub(r'_[lr]$', '', k)
            lo, hi = HUMAN.get(key, (None, None))
            if lo is not None and not lo <= v <= hi:
                faults.append(f'{k.replace("_", " ")} is {v * 100:.1f}% of the height (a person: {lo * 100:.1f}–{hi * 100:.1f}%)')
        for a in ('hand_length', 'palm_width', 'palm_thickness', 'foot_length', 'arm_length'):
            l, r = props.get(f'{a}_l'), props.get(f'{a}_r')
            if l and r and abs(l - r) / max(l, r) > 0.08:
                faults.append(f'{a.replace("_", " ")} differs left to right: {l * 100:.1f}% against {r * 100:.1f}%')

    res['faults'] = faults
    for f in faults:
        print(f'SHEET fault model: {f}')
    if not faults:
        print('SHEET model: no faults found')
    return res


# --------------------------------------------------------------------------------------------------------------------- run

if ARGS['checks'] and not STICK:
    REPORT['model']['checks'] = model_checks()
if ARGS['model'] if ARGS['model'] is not None else not STICK:
    model_sheets()
if ARGS['anims']:
    for clip in CLIPS:
        print(f'SHEET clip {clip["name"]}')
        info = anim_strips(clip, draw=ARGS['strips'])
        if ARGS['checks']:
            info['checks'] = check_clip(clip)

REPORT['seconds'] = round(time.time() - T0, 1)
with open(os.path.join(OUT, 'report.json'), 'w', encoding='utf-8') as fh:
    json.dump(REPORT, fh, indent=2, ensure_ascii=False)
print(f'SHEET done in {REPORT["seconds"]} s: {len(REPORT["sheets"])} sheets in {OUT}')
