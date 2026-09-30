"""Generates tests/testdata/simpleskel.xsi, a small dotXSI 3.0 skeleton animation.

Written the way 3ds Max's dotXSI exporter (the one used for Jedi Academy / Carcass) writes files:
Y-up, SI_Transform "SRT-" local to the parent, "BASEPOSE-" in world space, Euler XYZ in degrees,
nine LINEAR SI_FCurves per model holding the local SRT per frame, CRLF line endings.

Bone names are a subset of Jedi Academy's _humanoid, plus the Max/CAT extras the importer has to
cope with: skeleton_root and mesh_root wrappers, an "_eff" effector and "face_always_" (which
Carcass turns into "face").

Needs mathutils, so run it in Blender:
    .claude/skills/blender-run-script/run_blender_script.sh 4.1 tests/tools/generate_test_xsi.py
"""

import math
import os

from mathutils import Euler, Matrix, Vector

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "testdata", "simpleskel.xsi")
NUM_FRAMES = 12

# name, parent, local rest rotation (deg, XYZ), local rest translation. Y is up.
BONES = (
    ("model_root", None, (0, 0, 0), (0, 0, 0)),
    ("skeleton_root", "model_root", (0, 0, 0), (0, 0, 0)),
    ("pelvis", "skeleton_root", (-90, 0, 90), (0, 55.6, -0.6)),
    ("lfemurYZ", "pelvis", (180, 4, 15), (0, -5.6, 0.9)),
    ("ltibia", "lfemurYZ", (0, 0, -2), (22.0, 0, 0)),
    ("lleg_eff", "ltibia", (0, 0, 0), (21.0, 0, 0)),
    ("lower_lumbar", "pelvis", (0, 0, 0), (9.0, 0, 0)),
    ("cranium", "lower_lumbar", (0, 0, -1), (28.5, 0, 0)),
    ("face_always_", "cranium", (0, 0, 0), (4.0, 0, 0)),
    ("leye", "face_always_", (84, -90, 0), (0.5, 1.8, 4.4)),
    ("mesh_root", "model_root", (0, 0, 0), (0, 0, 0)),
)


def local_at(name, rot, trans, frame):
    """Rest local transform plus a per-bone animation, frame 1 being the rest pose."""
    t = (frame - 1) / (NUM_FRAMES - 1)
    rot = list(rot)
    trans = list(trans)
    if name == "pelvis":
        trans[1] -= 12.0 * t        # crouch
        trans[0] += 3.0 * t
        rot[1] += 20.0 * t
    elif name == "lfemurYZ":
        rot[2] += 35.0 * math.sin(t * math.pi)
    elif name == "ltibia":
        rot[2] -= 50.0 * t
    elif name == "lower_lumbar":
        rot[0] += 15.0 * t
    elif name == "cranium":
        rot[1] += 25.0 * math.sin(2 * t * math.pi)
    return rot, trans


def matrix(rot, trans):
    return Matrix.LocRotScale(Vector(trans), Euler([math.radians(a) for a in rot], "XYZ"), None)


def main():
    world_rest = {}
    for name, parent, rot, trans in BONES:
        m = matrix(rot, trans)
        world_rest[name] = world_rest[parent] @ m if parent else m

    children = {}
    for name, parent, _, _ in BONES:
        children.setdefault(parent, []).append(name)
    by_name = {b[0]: b for b in BONES}

    out = []
    w = out.append
    w("xsi 0300txt 0032\n\n")
    w('SI_FileInfo  { \n\t"simpleskel", \n\t"test", \n\t"Mon Jan 01 00:00:00 2024", \n\t"3ds Max 2016", \n}\n\n')
    w('SI_Scene simpleskel { \n\t"FRAMES", \n\t1.000000, \n\t%f, \n\t30.000000, \n}\n\n' % NUM_FRAMES)
    w("SI_CoordinateSystem coord { \n\t1, \n\t0, \n\t1, \n\t0, \n\t2, \n\t5, \n}\n\n")
    w("SI_Angle  { \n\t0, \n}\n\n")
    w("SI_Ambience  { \n\t0.000000, \n\t0.000000, \n\t0.000000, \n}\n\n")

    def write(name, depth):
        _, _, rot, trans = by_name[name]
        t, t1, t2 = "\t" * depth, "\t" * (depth + 1), "\t" * (depth + 2)
        w("%sSI_Model MDL-%s { \n" % (t, name))
        keys = [local_at(name, rot, trans, f) for f in range(1, NUM_FRAMES + 1)]
        for kind, index in (("SCALING", None), ("ROTATION", 0), ("TRANSLATION", 1)):
            for axis, i in (("X", 0), ("Y", 1), ("Z", 2)):
                w('%sSI_FCurve %s-%s-%s { \n' % (t1, name, kind, axis))
                w('%s"%s", \n%s"%s-%s", \n%s"LINEAR", \n%s1, \n%s1, \n%s%d, \n' % (t2, name, t2, kind, axis, t2, t2, t2, t2, NUM_FRAMES))
                for f, key in enumerate(keys, start=1):
                    value = 1.0 if index is None else key[index][i]
                    w("%s%f,%f,\n" % (t2, float(f), value))
                w("%s}\n\n" % t1)
        rest_world = world_rest[name]
        loc, quat, _ = rest_world.decompose()
        e = quat.to_euler("XYZ")
        for label, values in (
                ("SRT", (1, 1, 1) + tuple(rot) + tuple(trans)),
                ("BASEPOSE", (1, 1, 1, math.degrees(e.x), math.degrees(e.y), math.degrees(e.z)) + tuple(loc))):
            w("%sSI_Transform %s-%s { \n" % (t1, label, name))
            for v in values:
                w("%s%f, \n" % (t2, v))
            w("%s}\n\n" % t1)
        w("%sSI_Visibility  { \n%s1, \n%s}\n\n" % (t1, t2, t1))
        w("%sSI_Null %s { \n%s}\n\n" % (t1, name, t1))
        for child in children.get(name, ()):
            write(child, depth + 1)
        w("%s}\n\n" % t)

    for root in children[None]:
        write(root, 0)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="latin-1", newline="\r\n") as f:
        f.write("".join(out))
    print("wrote", OUT)


main()
