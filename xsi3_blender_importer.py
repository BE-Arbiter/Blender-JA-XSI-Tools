"""Builds Blender armatures / actions from a parsed dotXSI 3.0 scene.

Conventions verified against Raven's _humanoid.gla (compiled by Carcass from the same kind of file):
- SI_Transform "SRT-" values are local to the parent model, "BASEPOSE-" values are in world space.
- Rotations are Euler XYZ in degrees, local matrix = T @ R @ S, world = parent_world @ local.
- The files are Y-up. Ghoul2 (and Blender) are Z-up: (x, y, z) -> (x, -z, y), and _humanoid is
  scaled by 0.64. With that conversion the XSI base pose matches the GLA base pose, including the
  bone axes (Ghoul2 bones point along X).

Animation is transferred as world-space deltas from the XSI base pose, applied on top of each
bone's own rest pose, so it also works on an armature whose rest orientations differ
(e.g. one imported by the jediacademy addon with its skeleton fixes).
"""

import os
from math import radians

import bpy
from typing import Literal, Set, cast

from mathutils import Matrix, Vector, Euler

from . import dotxsi

# Blender operator return values (the stub's own alias isn't importable at runtime)
OperatorReturnItems = Literal["RUNNING_MODAL", "CANCELLED", "FINISHED", "PASS_THROUGH", "INTERFACE"]

# Bones of Jedi Academy's models/players/_humanoid/_humanoid.gla, in file order, with their parent.
# Carcass drops the CAT/Max helpers (effectors, skeleton_root, extra face bones...) and reparents
# some bones, so this is the hierarchy the game actually uses.
HUMANOID_BONES = (
    ("model_root", None), ("pelvis", "model_root"), ("Motion", "pelvis"),
    ("lfemurYZ", "pelvis"), ("lfemurX", "lfemurYZ"), ("ltibia", "lfemurYZ"), ("ltalus", "ltibia"),
    ("rfemurYZ", "pelvis"), ("rfemurX", "rfemurYZ"), ("rtibia", "rfemurYZ"), ("rtalus", "rtibia"),
    ("lower_lumbar", "pelvis"), ("upper_lumbar", "lower_lumbar"), ("thoracic", "upper_lumbar"),
    ("cervical", "thoracic"), ("cranium", "cervical"),
    ("ceyebrow", "face"), ("jaw", "face"), ("lblip2", "face"), ("leye", "face"), ("rblip2", "face"),
    ("ltlip2", "face"), ("rtlip2", "face"), ("reye", "face"),
    ("rclavical", "thoracic"), ("rhumerus", "thoracic"), ("rhumerusX", "rhumerus"),
    ("rradius", "rhumerus"), ("rradiusX", "rradius"), ("rhand", "rradius"),
    ("r_d1_j1", "rradius"), ("r_d1_j2", "rradius"), ("r_d2_j1", "rradius"), ("r_d2_j2", "rradius"),
    ("r_d4_j1", "rradius"), ("r_d4_j2", "rradius"), ("rhang_tag_bone", "rradius"),
    ("lclavical", "thoracic"), ("lhumerus", "thoracic"), ("lhumerusX", "lhumerus"),
    ("lradius", "lhumerus"), ("lradiusX", "lradius"), ("lhand", "lradius"),
    ("l_d4_j1", "lradius"), ("l_d4_j2", "lradius"), ("l_d2_j1", "lradius"), ("l_d2_j2", "lradius"),
    ("l_d1_j1", "lradius"), ("l_d1_j2", "lradius"),
    ("ltail", "lfemurYZ"), ("rtail", "rfemurYZ"), ("lhang_tag_bone", "lradius"), ("face", "cranium"),
)

# Ghoul2 bone name -> XSI model name, where Carcass renamed it
NAME_ALIASES = {"face": "face_always_"}

BONE_LENGTH = 4.0  # same as the jediacademy addon, before scaling


def find_skeleton(context):
    """The armature to animate: skeleton_root (as named by the jediacademy addon), else the
    active armature. None if the scene has neither."""
    scene_objects = context.scene.objects
    obj = scene_objects.get("skeleton_root")
    if obj is not None and obj.type == "ARMATURE":
        return obj
    obj = context.active_object
    if obj is not None and obj.type == "ARMATURE" and obj.name in scene_objects:
        return obj
    return None


def srt_matrix(srt):
    return Matrix.LocRotScale(
        Vector(srt.translation),
        Euler([radians(a) for a in srt.rotation], "XYZ"),
        Vector(srt.scale))


def gla_to_blender_bone(m):
    """Ghoul2 bone axes (X along the bone) -> Blender bone axes (Y along the bone).
    Same as JAG2Math.GLABoneRotToBlender in the jediacademy addon, so both addons agree."""
    m = m.copy()
    new_x = -m.col[1].copy()
    new_y = m.col[0].copy()
    m.col[0] = new_x
    m.col[1] = new_y
    m[0][0], m[1][0], m[2][0], m[0][2], m[1][2], m[2][2] = \
        -m[0][2], -m[1][2], -m[2][2], m[0][0], m[1][0], m[2][0]
    return m


def rigid(m):
    """Drop scale, keep rotation and translation."""
    return Matrix.LocRotScale(m.to_translation(), m.to_quaternion(), None)


def get_fcurves(obj, action):
    """FCurve collection of `action` for `obj`, on Blender 4.4+ (slotted actions) and older."""
    anim = obj.animation_data_create()
    anim.action = action
    if not hasattr(action, "slots"):
        return action.fcurves

    slot = anim.action_slot
    if slot is None:
        slot = action.slots.new(id_type="OBJECT", name=obj.name)
        anim.action_slot = slot

    from bpy_extras import anim_utils
    ensure = getattr(anim_utils, "action_ensure_channelbag_for_slot")  # Blender 4.4+
    return ensure(action, slot).fcurves


def new_fcurve(fcurves, data_path, index, group):
    # the group keyword was renamed with slotted actions (channelbags)
    for keyword in ("group_name", "action_group"):
        try:
            return fcurves.new(data_path, index=index, **{keyword: group})
        except TypeError:
            continue
    return fcurves.new(data_path, index=index)


class Importer:
    def __init__(self, operator, context, filepath, opt):
        self.operator = operator
        self.context = context
        self.filepath = filepath
        self.opt = opt

        scale = opt["scale"]
        conv = Matrix.Identity(4)
        if opt["y_up_to_z_up"]:
            conv = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
        self.conv = Matrix.Diagonal((scale, scale, scale, 1.0)) @ conv
        self.conv_inv = self.conv.inverted()

    def convert(self, m):
        return rigid(self.conv @ m @ self.conv_inv)

    def run(self):
        scene = dotxsi.load_scene(self.filepath)
        self.models = {m.name: m for m in scene.all_models()}
        if not self.models:
            raise dotxsi.DotXSIError("The file contains no SI_Model")

        self.fps = scene.fps
        self.message = None
        self.warnings = []
        first, last = scene.frame_range()
        first, last = int(round(first)), int(round(last))
        self.frames = list(range(first, last + 1))

        # World-space base pose and per-frame world matrices, converted to Blender space
        self.rest = {name: self.convert(srt_matrix(m.basepose)) for name, m in self.models.items()}
        self.anim = {name: [] for name in self.models}
        for frame in self.frames:
            world = {}
            for m in scene.all_models():
                local = srt_matrix(m.srt_at(frame))
                world[m.name] = world[m.parent.name] @ local if m.parent else local
            for name, w in world.items():
                self.anim[name].append(self.convert(w))

        obj = find_skeleton(self.context)
        if obj is None:
            obj = self.create_armature()
            self.warnings.append("No skeleton_root in the scene, created one from the file's base pose")

        if self.opt["nla_strip"]:
            action = self.import_as_strip(obj)
            return obj, action

        action = self.bake(obj, [float(self.frame_offset(f)) for f in self.frames])

        render = self.context.scene.render
        if self.opt["set_scene_range"]:
            self.context.scene.frame_start = self.frame_offset(self.frames[0])
            self.context.scene.frame_end = self.frame_offset(self.frames[-1])
            self.context.scene.frame_current = self.context.scene.frame_start
            if scene.fps:
                render.fps = int(round(scene.fps))
                render.fps_base = 1.0

        return obj, action

    def sequence_names(self):
        """Sequence names from the operator: "A.B.C" gives three sequences. Default: file name."""
        names = [n.strip() for n in self.opt.get("sequence_name", "").split(".")]
        names = [n for n in names if n]
        return names or [os.path.splitext(os.path.basename(self.filepath))[0].replace(" ", "_")]

    def import_as_strip(self, obj):
        """Bakes the animation into its own action (keys 0..N-1) and adds it as a new NLA strip,
        the way the jediacademy addon lays out the sequences of a .gla imported with its
        animation.cfg: one action per sequence, strips on "Sequences Layer n" tracks, extrapolation
        NOTHING, REPLACE blending, sequence metadata in action.g2_sequence_prop.

        Several names ("A.B.C") give one copy of the action per name, with their strips stacked at
        the same frames on successive tracks - e.g. to use one stance for several saber styles."""
        anim = obj.animation_data_create()
        prev_action = anim.action
        prev_slot = getattr(anim, "action_slot", None)

        num = len(self.frames)
        start = self.opt["strip_start"]
        if start < 0:  # after the last existing strip
            ends = [s.frame_end for t in anim.nla_tracks for s in t.strips]
            start = int(max(ends)) + 1 if ends else 0

        names = self.sequence_names()
        action = self.bake(obj, [float(f) for f in range(num)], names[0])
        slot = getattr(anim, "action_slot", None)

        props = getattr(action, "g2_sequence_prop", None)  # jediacademy addon: animation.cfg metadata
        if props is not None:
            props.loop_start_frame = self.opt["loop_frame"]
            props.num_frames = num
            if self.fps:
                props.fps = int(round(self.fps))

        # Blender would clip the strip to the action range of the previous assignment
        if hasattr(anim, "action_slot"):
            anim.action_slot = None
        anim.action = None

        actions = [action]
        for name in names[1:]:
            copy = action.copy()  # keeps keys, slots and g2_sequence_prop
            copy.name = name
            copy.use_fake_user = True
            actions.append(copy)

        # Only the new strips end up selected, ready for "Export NLA Strip as dotXSI"
        for track in anim.nla_tracks:
            for s in track.strips:
                s.select = False

        placed = []
        for act in actions:
            strip, track = self.add_strip(anim, act, slot, start, num)
            placed.append((strip, track))
        anim.use_nla = True

        # Restore what was being previewed before
        anim.action = prev_action
        if prev_action is not None and hasattr(anim, "action_slot") and prev_slot is not None:
            try:
                anim.action_slot = prev_slot
            except (RuntimeError, TypeError):
                pass

        end = start + max(0, num - 1)
        scene = self.context.scene
        if self.opt["set_scene_range"]:
            # The .gla export samples the scene range, so it has to include the new sequence
            only_ours = sum(len(t.strips) for t in anim.nla_tracks) == len(placed)
            if only_ours:
                scene.frame_start, scene.frame_end = start, end
                if self.fps:
                    scene.render.fps = int(round(self.fps))
                    scene.render.fps_base = 1.0
            else:
                scene.frame_start = min(scene.frame_start, start)
                scene.frame_end = max(scene.frame_end, end)
            scene.frame_current = start

        our_tracks = {track for _, track in placed}
        solo = [t.name for t in anim.nla_tracks if t.is_solo and t not in our_tracks]
        msg = "Added %s, frames %d-%d" % (", ".join("%r on %r" % (s.name, t.name) for s, t in placed), start, end)
        if solo:
            msg += " (track %r is soloed, un-solo it to see the new strips)" % solo[0]
        self.message = msg
        print("dotXSI:", msg)
        return action

    def add_strip(self, anim, action, slot, start, num):
        """Adds a strip for `action` on the first "Sequences Layer n" track that is free there."""
        if slot is not None and hasattr(action, "slots"):
            # a copied action has its own slots, with the same identifiers
            slot = next((s for s in action.slots if s.identifier == slot.identifier), None)

        strip = None
        track = None
        layer = 1
        while strip is None:
            if layer > 64:
                raise dotxsi.DotXSIError("Could not find a free NLA track at frame %d" % start)
            track_name = "Sequences Layer %d" % layer
            track = anim.nla_tracks.get(track_name) or anim.nla_tracks.new()
            track.name = track_name
            free = all(s.frame_end < start or s.frame_start > start + num - 1 for s in track.strips)
            if free:
                try:
                    strip = track.strips.new(action.name, start, action)
                except RuntimeError:
                    strip = None
            layer += 1

        if hasattr(strip, "action_slot") and slot is not None:
            strip.action_slot = slot
        strip.action_frame_start = 0
        strip.action_frame_end = max(0, num - 1)
        strip.frame_start = start
        strip.frame_end = start + max(0, num - 1)
        strip.scale = 1.0
        strip.repeat = 1.0
        strip.extrapolation = "NOTHING"
        strip.blend_type = "REPLACE"
        strip.use_auto_blend = False
        strip.blend_in = strip.blend_out = 0.0
        strip.select = True
        return strip, track

    def frame_offset(self, frame):
        return frame - self.frames[0] if self.opt["start_at_zero"] else frame

    def xsi_name(self, bone_name):
        if bone_name in self.models:
            return bone_name
        alias = NAME_ALIASES.get(bone_name)
        return alias if alias in self.models else None

    # -----------------------------------------------------------------------
    def create_armature(self):
        context = self.context
        name = "skeleton_root"
        arm = bpy.data.armatures.new(name)
        obj = bpy.data.objects.new(name, arm)
        context.collection.objects.link(obj)

        if self.opt["bones"] == "HUMANOID":
            missing = [b for b, _ in HUMANOID_BONES if not self.xsi_name(b)]
            if missing:
                self.warnings.append("Missing _humanoid bones in file: %s" % ", ".join(missing))
            bones = [(b, p) for b, p in HUMANOID_BONES if self.xsi_name(b)]
        else:
            bones = list(self.model_bones())

        if self.opt["y_up_to_z_up"] and "scene_root" in bpy.data.objects:
            obj.parent = bpy.data.objects["scene_root"]

        g2_prop = getattr(obj, "g2_prop", None)  # jediacademy addon: header scale of an exported .gla
        if g2_prop is not None:
            try:
                g2_prop.scale = self.opt["scale"] * 100
            except Exception:
                pass

        for o in context.view_layer.objects:
            o.select_set(False)
        context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")

        length = BONE_LENGTH * self.opt["scale"]
        for bone_name, _ in bones:
            eb = arm.edit_bones.new(bone_name)
            eb.head = (0, 0, 0)
            eb.tail = (0, length, 0)
            eb.matrix = gla_to_blender_bone(self.rest[self.xsi_name(bone_name)])

        # Parents in a second pass: _humanoid.gla lists "face" after its children. A parent missing
        # from the file is replaced by its nearest ancestor that is present.
        parent_of = dict(bones)
        if self.opt["bones"] == "HUMANOID":
            parent_of.update({b: p for b, p in HUMANOID_BONES if b not in parent_of})
        for bone_name, parent in bones:
            while parent is not None and parent not in arm.edit_bones:
                parent = parent_of.get(parent)
            if parent is not None:
                arm.edit_bones[bone_name].parent = arm.edit_bones[parent]

        bpy.ops.object.mode_set(mode="OBJECT")
        arm.display_type = "STICK"
        obj.show_in_front = True
        return obj

    def model_bones(self):
        """(name, parent) for every model of the file, optionally without the Max/CAT helpers."""
        def is_helper(name):
            return name.endswith("eff") or name.startswith("eff") or name in ("skeleton_root", "mesh_root")

        skip_helpers = self.opt["bones"] == "NO_HELPERS"
        for name, m in self.models.items():
            if skip_helpers and is_helper(name):
                continue
            parent = m.parent
            while skip_helpers and parent and is_helper(parent.name):
                parent = parent.parent
            yield name, parent.name if parent else None

    # -----------------------------------------------------------------------
    def bake(self, obj, frames, action_name=None):
        """Writes the animation into a new action, one key per XSI frame at the given frame numbers."""
        arm = obj.data
        # The jediacademy exporter reads poses as skeleton_root.matrix_local @ pose_bone.matrix, so
        # the XSI (Ghoul2) space is the armature's parent space.
        L = obj.matrix_local.copy()
        L_inv = L.inverted()

        # Bones in hierarchy order
        ordered = []

        def walk(bone):
            ordered.append(bone)
            for child in bone.children:
                walk(child)
        for bone in arm.bones:
            if bone.parent is None:
                walk(bone)

        mapped = {b.name: self.xsi_name(b.name) for b in ordered}
        matched = [n for n, x in mapped.items() if x]
        if not matched:
            raise dotxsi.DotXSIError("No bone of armature %r matches a model in the file" % obj.name)
        unmatched = [n for n, x in mapped.items() if not x]
        if unmatched:
            print("dotXSI: %d bones without animation data: %s" % (len(unmatched), ", ".join(unmatched)))

        rest = {b.name: b.matrix_local.copy() for b in ordered}
        rest_inv = {n: m.inverted() for n, m in rest.items()}
        base_inv = {n: self.rest[x].inverted() for n, x in mapped.items() if x}

        # Carcass moves the whole skeleton by the model's origin when it compiles a .gla: every
        # frame of Raven's _humanoid.gla has model_root translated by (0, 0, -24) - the player
        # origin sits 24 units above the feet - while the base pose stays at 0. The XSI does not
        # contain that offset, so without it the animation floats 24 units above the stock ones.
        origin = Matrix.Translation(Vector(self.opt.get("origin_offset", (0.0, 0.0, 0.0))))

        action = bpy.data.actions.new(action_name or self.sequence_names()[0])
        action.use_fake_user = True
        obj.animation_data_create()
        fcurves = get_fcurves(obj, action)

        num = len(self.frames)
        locs = {b.name: [] for b in ordered}
        quats = {b.name: [] for b in ordered}

        for fi in range(num):
            posed = {}
            for bone in ordered:
                name = bone.name
                x = mapped[name]
                parent = bone.parent
                if x:
                    delta = origin @ self.anim[x][fi] @ base_inv[name]
                    posed[name] = L_inv @ delta @ L @ rest[name]
                elif parent:
                    posed[name] = posed[parent.name] @ rest_inv[parent.name] @ rest[name]
                else:
                    posed[name] = rest[name]

                if parent:
                    basis = rest_inv[name] @ rest[parent.name] @ posed[parent.name].inverted() @ posed[name]
                else:
                    basis = rest_inv[name] @ posed[name]

                loc, quat, _ = basis.decompose()
                prev = quats[name][-1] if quats[name] else None
                if prev is not None:
                    quat.make_compatible(prev)
                locs[name].append(loc)
                quats[name].append(quat)

        for bone in ordered:
            name = bone.name
            obj.pose.bones[name].rotation_mode = "QUATERNION"
            escaped = bpy.utils.escape_identifier(name)
            for path, values, size in (
                    ('pose.bones["%s"].location' % escaped, locs[name], 3),
                    ('pose.bones["%s"].rotation_quaternion' % escaped, quats[name], 4)):
                for component in range(size):
                    fc = new_fcurve(fcurves, path, component, name)
                    fc.keyframe_points.add(num)
                    co = [0.0] * (2 * num)
                    co[0::2] = frames
                    co[1::2] = [v[component] for v in values]
                    fc.keyframe_points.foreach_set("co", co)
                    fc.keyframe_points.foreach_set("interpolation", [LINEAR] * num)
                    fc.update()

        return action


LINEAR = getattr(bpy.types.Keyframe.bl_rna.properties["interpolation"], "enum_items")["LINEAR"].value


def load(operator, context, filepath="", **opt) -> Set[OperatorReturnItems]:
    importer = Importer(operator, context, filepath, opt)
    try:
        obj, action = importer.run()
    except (dotxsi.DotXSIError, OSError) as e:
        operator.report({"ERROR"}, str(e))
        return {"CANCELLED"}

    message = importer.message or "Imported %r onto %r (%d bones)" % (action.name, obj.name, len(cast(bpy.types.Armature, obj.data).bones))
    if importer.warnings:
        operator.report({"WARNING"}, " - ".join(importer.warnings + [message]))
    else:
        operator.report({"INFO"}, message)
    return {"FINISHED"}
