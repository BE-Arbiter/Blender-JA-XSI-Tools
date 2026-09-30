"""Writes an armature's selected NLA strips to a dotXSI 3.0 text file.

Exact inverse of xsi3_blender_importer (same axis, scale and origin conventions), written in the
layout of the 3ds Max exporter the Jedi Academy tools use: one SI_Model per bone with nine LINEAR
SI_FCurves (local SRT), a local SI_Transform SRT- and a world-space SI_Transform BASEPOSE-.
Only the armature is exported, no meshes.
"""

import os
import time
from math import degrees
from typing import Set

import bpy
from mathutils import Matrix, Vector, Euler, Quaternion

from .xsi3_blender_importer import find_skeleton, gla_to_blender_bone, rigid, NAME_ALIASES, OperatorReturnItems

# Blender bone axes (Y along the bone) -> Ghoul2 / XSI bone axes (X along the bone).
# gla_to_blender_bone only permutes columns, i.e. it is a right multiplication by a constant.
BLENDER_TO_GLA_AXES = gla_to_blender_bone(Matrix.Identity(4)).inverted()

# Blender bone name -> name to write, the inverse of the importer's aliases. Carcass reads the
# "_always_" suffix as a flag and drops it, which is why _humanoid.gla calls this bone "face".
EXPORT_NAMES = {gla: xsi for gla, xsi in NAME_ALIASES.items()}


class ExportError(Exception):
    pass


def pick_skeleton(context):
    """Active armature, else skeleton_root / an armature found like the importer does, else the
    first armature of the scene."""
    obj = context.active_object
    if obj is not None and obj.type == "ARMATURE" and obj.name in context.scene.objects:
        return obj
    obj = find_skeleton(context)
    if obj is not None:
        return obj
    return next((o for o in context.scene.objects if o.type == "ARMATURE"), None)


def selected_strips(obj):
    """Selected NLA strips of the armature, in timeline order."""
    anim = obj.animation_data if obj else None
    if anim is None:
        return []
    strips = [s for t in anim.nla_tracks for s in t.strips if s.select and s.action is not None]
    return sorted(strips, key=lambda s: s.frame_start)


def strip_length(strip):
    """Number of frames of a strip's sequence. jediacademy records it on the action, because a
    1-frame sequence still needs a 2-frame strip."""
    recorded = getattr(getattr(strip.action, "g2_sequence_prop", None), "num_frames", 0)
    if recorded:
        return int(recorded)
    return int(round(strip.action_frame_end - strip.action_frame_start)) + 1


def strip_fcurves(strip):
    action = strip.action
    if hasattr(action, "layers"):
        from bpy_extras import anim_utils
        slot = getattr(strip, "action_slot", None)
        if slot is None and len(action.slots):
            slot = action.slots[0]
        channelbag = anim_utils.action_get_channelbag_for_slot(action, slot) if slot else None
        fcurves = channelbag.fcurves if channelbag else []
    else:
        fcurves = action.fcurves
    return {(fc.data_path, fc.array_index): fc for fc in fcurves}


class Exporter:
    def __init__(self, operator, context, filepath, opt):
        self.operator = operator
        self.context = context
        self.filepath = filepath
        self.opt = opt
        self.warnings = []

        scale = opt["scale"]
        conv = Matrix.Identity(4)
        if opt["y_up_to_z_up"]:
            conv = Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
        self.conv = Matrix.Diagonal((scale, scale, scale, 1.0)) @ conv
        self.conv_inv = self.conv.inverted()
        self.origin_inv = Matrix.Translation(-Vector(opt["origin_offset"]))

    def to_xsi(self, m):
        """Blender/Ghoul2 space (bone X axis along the bone) -> XSI world space."""
        return rigid(self.conv_inv @ m @ self.conv)

    def run(self):
        obj = pick_skeleton(self.context)
        if obj is None:
            raise ExportError("No armature in the scene")
        self.obj = obj
        arm = obj.data

        self.bones = []

        def walk(bone):
            self.bones.append(bone)
            for child in bone.children:
                walk(child)
        for bone in arm.bones:
            if bone.parent is None:
                walk(bone)

        # Space the jediacademy addon exports from (skeleton_root.matrix_local), as in the importer
        L = obj.matrix_local.copy()
        self.L = L
        self.rest = {b.name: b.matrix_local.copy() for b in self.bones}
        self.rest_inv = {n: m.inverted() for n, m in self.rest.items()}

        # Base pose: the armature's rest pose, in XSI world space
        rest_world = {b.name: self.to_xsi(L @ self.rest[b.name] @ BLENDER_TO_GLA_AXES) for b in self.bones}

        strips = selected_strips(obj)
        frames = []  # per output frame: {bone: xsi world matrix}
        for strip in strips:
            frames.extend(self.sample_strip(strip))

        fps = self.context.scene.render.fps / self.context.scene.render.fps_base
        if strips:
            strip_fps = getattr(getattr(strips[0].action, "g2_sequence_prop", None), "fps", 0)
            if strip_fps:
                fps = strip_fps

        self.write(rest_world, frames, fps)
        return obj, strips, len(frames)

    def sample_strip(self, strip):
        fcurves = strip_fcurves(strip)
        pose_bones = self.obj.pose.bones
        L = self.L
        out = []
        for i in range(strip_length(strip)):
            action_frame = strip.action_frame_start + i
            posed = {}
            world = {}
            for bone in self.bones:
                basis = self.bone_basis(pose_bones[bone.name], bone, fcurves, action_frame)
                if bone.parent:
                    p = bone.parent.name
                    posed[bone.name] = posed[p] @ self.rest_inv[p] @ self.rest[bone.name] @ basis
                else:
                    posed[bone.name] = self.rest[bone.name] @ basis
                world[bone.name] = self.to_xsi(self.origin_inv @ L @ posed[bone.name] @ BLENDER_TO_GLA_AXES)
            out.append(world)
        return out

    @staticmethod
    def bone_basis(pose_bone, bone, fcurves, frame):
        path = 'pose.bones["%s"].' % bpy.utils.escape_identifier(bone.name)

        def value(prop, size, default):
            vals = list(default)
            for i in range(size):
                fc = fcurves.get((path + prop, i))
                if fc is not None:
                    vals[i] = fc.evaluate(frame)
            return vals

        loc = Vector(value("location", 3, (0.0, 0.0, 0.0)))
        if bone.use_connect:
            loc = Vector()  # Blender ignores the location of connected bones
        mode = pose_bone.rotation_mode
        if mode == "QUATERNION":
            rot = Quaternion(value("rotation_quaternion", 4, (1.0, 0.0, 0.0, 0.0))).normalized()
        elif mode == "AXIS_ANGLE":
            a = value("rotation_axis_angle", 4, (0.0, 0.0, 1.0, 0.0))
            rot = Quaternion(Vector(a[1:4]).normalized() if Vector(a[1:4]).length else Vector((0, 0, 1)), a[0])
        else:
            rot = Euler(value("rotation_euler", 3, (0.0, 0.0, 0.0)), mode).to_quaternion()
        scale = Vector(value("scale", 3, (1.0, 1.0, 1.0)))
        return Matrix.LocRotScale(loc, rot, scale)

    # -----------------------------------------------------------------------
    def write(self, rest_world, frames, fps):
        name = os.path.splitext(os.path.basename(self.filepath))[0].replace(" ", "_") or "scene"
        num = len(frames)
        out = []
        w = out.append

        w("xsi 0300txt 0032\n\n")
        w("SI_FileInfo  { \n\t\"%s\", \n\t\"%s\", \n\t\"%s\", \n\t\"Blender %s\", \n}\n\n" % (
            name, os.environ.get("USERNAME", "Blender"), time.strftime("%a %b %d %H:%M:%S %Y"), bpy.app.version_string))
        w("SI_Scene %s { \n\t\"FRAMES\", \n\t%f, \n\t%f, \n\t%f, \n}\n\n" % (name, 1.0, float(max(num, 1)), fps))
        w("SI_CoordinateSystem coord { \n\t1, \n\t0, \n\t1, \n\t0, \n\t2, \n\t5, \n}\n\n")
        w("SI_Angle  { \n\t0, \n}\n\n")
        w("SI_Ambience  { \n\t0.000000, \n\t0.000000, \n\t0.000000, \n}\n\n")

        # Local matrices per frame and at rest
        def local(worlds, bone):
            m = worlds[bone.name]
            return worlds[bone.parent.name].inverted() @ m if bone.parent else m

        eulers = {}  # continuous euler angles per bone, frame to frame
        anim = {b.name: [] for b in self.bones}
        for world in frames:
            for bone in self.bones:
                loc, quat, _ = local(world, bone).decompose()
                prev = eulers.get(bone.name)
                e = quat.to_euler("XYZ", prev) if prev is not None else quat.to_euler("XYZ")
                eulers[bone.name] = e
                anim[bone.name].append((loc, e))

        def write_bone(bone, depth):
            t = "\t" * depth
            xname = EXPORT_NAMES.get(bone.name, bone.name)
            w("%sSI_Model MDL-%s { \n" % (t, xname))
            t1, t2 = t + "\t", t + "\t\t"

            if num:
                keys = anim[bone.name]
                channels = (
                    ("SCALING", lambda k, i: 1.0),
                    ("ROTATION", lambda k, i: degrees(k[1][i])),
                    ("TRANSLATION", lambda k, i: k[0][i]),
                )
                for kind, get in channels:
                    for i, axis in enumerate("XYZ"):
                        param = "%s-%s" % (kind, axis)
                        w("%sSI_FCurve %s-%s { \n" % (t1, xname, param))
                        w("%s\"%s\", \n%s\"%s\", \n%s\"LINEAR\", \n%s1, \n%s1, \n%s%d, \n" % (t2, xname, t2, param, t2, t2, t2, t2, num))
                        for f, k in enumerate(keys):
                            w("%s%f,%f,\n" % (t2, float(f + 1), get(k, i)))
                        w("%s}\n\n" % t1)

            rest_local = local(rest_world, bone)
            first_local = local(frames[0], bone) if num else rest_local
            for label, m in (("SRT", first_local), ("BASEPOSE", rest_world[bone.name])):
                loc, quat, _ = m.decompose()
                rot = quat.to_euler("XYZ")
                values = (1.0, 1.0, 1.0, degrees(rot.x), degrees(rot.y), degrees(rot.z), loc.x, loc.y, loc.z)
                w("%sSI_Transform %s-%s { \n" % (t1, label, xname))
                for v in values:
                    w("%s%f, \n" % (t2, v))
                w("%s}\n\n" % t1)

            w("%sSI_Visibility  { \n%s1, \n%s}\n\n" % (t1, t2, t1))
            w("%sSI_Null %s { \n%s}\n\n" % (t1, xname, t1))
            for child in bone.children:
                write_bone(child, depth + 1)
            w("%s}\n\n" % t)

        for bone in self.bones:
            if bone.parent is None:
                write_bone(bone, 0)

        with open(self.filepath, "w", encoding="latin-1", newline="\r\n") as f:
            f.write("".join(out))


def save(operator, context, filepath="", **opt) -> Set[OperatorReturnItems]:
    exporter = Exporter(operator, context, filepath, opt)
    try:
        obj, strips, num = exporter.run()
    except (ExportError, OSError) as e:
        operator.report({"ERROR"}, str(e))
        return {"CANCELLED"}

    if not strips:
        operator.report({"WARNING"}, "No NLA strip selected: exported the skeleton of %r only (base pose, no animation)" % obj.name)
    elif len(strips) > 1:
        operator.report({"WARNING"}, "%d NLA strips concatenated end to end (%s): %d frames from %r" % (
            len(strips), ", ".join(s.name for s in strips), num, obj.name))
    else:
        operator.report({"INFO"}, "Exported NLA strip %r of %r: %d frames" % (strips[0].name, obj.name, num))
    return {"FINISHED"}
