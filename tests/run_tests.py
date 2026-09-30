import importlib
import os
import sys
import tempfile
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testutil  # noqa: E402 - path must be set up first

addon = testutil.import_addon()
addon.register()
# the operators import these lazily, in execute()
dotxsi = importlib.import_module(testutil.PACKAGE + ".dotxsi")
importer = importlib.import_module(testutil.PACKAGE + ".xsi3_blender_importer")
exporter = importlib.import_module(testutil.PACKAGE + ".xsi3_blender_exporter")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTDATA = os.path.join(REPO_ROOT, "tests", "testdata")
FIXTURE = os.path.join(TESTDATA, "simpleskel.xsi")
FIXTURE_FRAMES = 12
FIXTURE_MODELS = {"model_root", "skeleton_root", "pelvis", "lfemurYZ", "ltibia", "lleg_eff", "lower_lumbar",
                  "cranium", "face_always_", "leye", "mesh_root"}

IMPORT_DEFAULTS: Dict[str, Any] = dict(
    bones="ALL", scale=0.64, origin_offset=(0.0, 0.0, -24.0), y_up_to_z_up=True, nla_strip=True,
    strip_start=-1, sequence_name="", loop_frame=-1, start_at_zero=True, set_scene_range=True)
EXPORT_DEFAULTS: Dict[str, Any] = dict(scale=0.64, origin_offset=(0.0, 0.0, -24.0), y_up_to_z_up=True)


class FakeOperator:
    """Stands in for the operator, so a case can check what would have been reported to the user."""

    def __init__(self) -> None:
        self.reports: List[Tuple[str, str]] = []

    def report(self, types, message) -> None:
        for t in types:
            self.reports.append((t, message))


def _import(**opt) -> FakeOperator:
    import bpy
    op = FakeOperator()
    result = importer.load(op, bpy.context, filepath=opt.pop("filepath", FIXTURE), **{**IMPORT_DEFAULTS, **opt})
    if result != {"FINISHED"}:
        raise AssertionError(f"import failed: {op.reports}")
    return op


def _export(filepath: str) -> FakeOperator:
    import bpy
    op = FakeOperator()
    result = exporter.save(op, bpy.context, filepath=filepath, **EXPORT_DEFAULTS)
    if result != {"FINISHED"}:
        raise AssertionError(f"export failed: {op.reports}")
    return op


def _skeleton():
    import bpy
    obj = bpy.data.objects.get("skeleton_root")
    if obj is None or obj.type != "ARMATURE":
        raise AssertionError("no skeleton_root armature")
    return obj


def _strips(obj) -> Dict[str, Any]:
    return {s.name: (t, s) for t in testutil.anim(obj).nla_tracks for s in t.strips}


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="ja-xsi-test-"), name)


def _expected_pose(scene, xsi_frame: float, xsi_name: str, opt: Dict[str, Any]):
    """Armature-space pose a new armature built from `scene` must have: the XSI world matrix,
    converted to Z-up / scaled / offset, in Blender bone axes."""
    from mathutils import Matrix, Vector
    conv = importer.Importer(FakeOperator(), None, FIXTURE, opt)
    world = testutil.world_matrices(scene, xsi_frame)[xsi_name]
    return importer.gla_to_blender_bone(Matrix.Translation(Vector(opt["origin_offset"])) @ conv.convert(world))


# ---------------------------------------------------------------------------

def case_smoke() -> None:
    import bpy
    print(f"[test] Imported {addon.bl_info['name']} OK")
    mismatches = []
    for op in ("import_anim.dotxsi3", "export_anim.dotxsi3"):
        category, name = op.split(".")
        if not hasattr(getattr(bpy.ops, category), name):
            mismatches.append(f"operator {op} not registered")
    labels = {"IMPORT_ANIM_OT_dotxsi3": "Import dotXSI as NLA Strip", "EXPORT_ANIM_OT_dotxsi3": "Export NLA Strip as dotXSI"}
    for cls, label in labels.items():
        actual = getattr(bpy.types, cls).bl_label
        if actual != label:
            mismatches.append(f"{cls}.bl_label is {actual!r}, expected {label!r}")
    for menu in ("TOPBAR_MT_file_import", "TOPBAR_MT_file_export"):
        funcs = getattr(getattr(bpy.types, menu).draw, "_draw_funcs", [])
        if not any(f.__module__ == testutil.PACKAGE for f in funcs):
            mismatches.append(f"no menu entry in {menu}")
    testutil.check(mismatches)


def case_parse() -> None:
    scene = dotxsi.load_scene(FIXTURE)
    mismatches = []
    names = {m.name for m in scene.all_models()}
    if names != FIXTURE_MODELS:
        mismatches.append(f"models differ: missing={FIXTURE_MODELS - names} extra={names - FIXTURE_MODELS}")
    if scene.frame_range() != (1.0, float(FIXTURE_FRAMES)):
        mismatches.append(f"frame range {scene.frame_range()}, expected (1, {FIXTURE_FRAMES})")
    if scene.fps != 30.0:
        mismatches.append(f"fps {scene.fps}, expected 30")
    for model in scene.all_models():
        if len(model.fcurves) != 9:
            mismatches.append(f"{model.name}: {len(model.fcurves)} fcurves, expected 9")

    # SRT (local) composed down the hierarchy must give BASEPOSE (world): the fixture's rest pose
    # is frame 1, as in the Max exports
    world = testutil.world_matrices(scene, 1)
    for name, base in testutil.base_matrices(scene).items():
        mismatches += testutil.compare_matrices(f"frame 1 vs BASEPOSE '{name}'", world[name], base)

    # Unsupported / broken files are rejected with a DotXSIError, not a crash
    bad = {
        "1.x header": "xsi 0101txt 0032\n\nFrame x {\n}\n",
        "binary": "xsi 0300bin 0032\n",
        "not xsi": "hello\n",
        "unclosed": "xsi 0300txt 0032\n\nSI_Model MDL-a {\n",
        "stray brace": "xsi 0300txt 0032\n\n}\n",
    }
    for label, text in bad.items():
        try:
            dotxsi.parse(text)
        except dotxsi.DotXSIError:
            continue
        mismatches.append(f"{label}: no DotXSIError raised")
    testutil.check(mismatches)


def case_import_new_armature() -> None:
    import bpy
    op = _import(sequence_name="SEQ_A")
    obj = _skeleton()
    mismatches = []
    if not any(t == "WARNING" and "created one" in m for t, m in op.reports):
        mismatches.append(f"no warning about the created skeleton: {op.reports}")
    if {b.name for b in testutil.bones(obj)} != FIXTURE_MODELS:
        mismatches.append(f"bones differ from the file's models: {sorted(b.name for b in testutil.bones(obj))}")

    strips = _strips(obj)
    if set(strips) != {"SEQ_A"}:
        mismatches.append(f"strips {sorted(strips)}, expected ['SEQ_A']")
    else:
        track, strip = strips["SEQ_A"]
        if (strip.frame_start, strip.frame_end) != (0.0, FIXTURE_FRAMES - 1.0):
            mismatches.append(f"strip frames {strip.frame_start}-{strip.frame_end}")
        if (strip.extrapolation, strip.blend_type, track.name) != ("NOTHING", "REPLACE", "Sequences Layer 1"):
            mismatches.append(f"strip settings {strip.extrapolation}/{strip.blend_type} on {track.name}")
    if testutil.anim(obj).action is not None:
        mismatches.append("an action was left active instead of only living in the NLA")
    scene = testutil.scene()
    if (scene.frame_start, scene.frame_end, scene.render.fps) != (0, FIXTURE_FRAMES - 1, 30):
        mismatches.append(f"scene range/fps {scene.frame_start}-{scene.frame_end} @ {scene.render.fps}")

    # Evaluated pose (through the NLA) against the file
    xsi = dotxsi.load_scene(FIXTURE)
    for frame in (0, 5, FIXTURE_FRAMES - 1):
        scene.frame_set(frame)
        for pose_bone in testutil.pose_bones(obj):
            expected = _expected_pose(xsi, frame + 1, pose_bone.name, IMPORT_DEFAULTS)
            mismatches += testutil.compare_matrices(f"frame {frame} '{pose_bone.name}'", pose_bone.matrix, expected)

    # Carcass' origin: every stock _humanoid frame has model_root at (0, 0, -24)
    root = testutil.pose_bones(obj)["model_root"].matrix.translation
    if (root - importer.Vector((0, 0, -24))).length > 1e-4:
        mismatches.append(f"model_root at {root.to_tuple()}, expected the (0, 0, -24) origin offset")
    testutil.check(mismatches)


def case_import_humanoid_bones() -> None:
    _import(bones="HUMANOID")
    obj = _skeleton()
    expected = {name for name, _ in importer.HUMANOID_BONES if name in FIXTURE_MODELS or name == "face"}
    actual = {b.name for b in testutil.bones(obj)}
    mismatches = []
    if actual != expected:
        mismatches.append(f"bones differ: missing={expected - actual} extra={actual - expected}")
    face = testutil.bones(obj).get("face")
    if face is None or face.parent is None or face.parent.name != "cranium":
        mismatches.append("'face' (from face_always_) is missing or not parented to cranium")
    leye = testutil.bones(obj).get("leye")
    if leye is None or leye.parent is None or leye.parent.name != "face":
        mismatches.append("'leye' is not parented to face, as in _humanoid.gla")
    # cervical/thoracic/upper_lumbar are not in the file: nearest present ancestor instead
    cranium = testutil.bones(obj).get("cranium")
    if cranium is None or cranium.parent is None or cranium.parent.name != "lower_lumbar":
        mismatches.append("'cranium' is not parented to its nearest present ancestor lower_lumbar")
    testutil.check(mismatches)


def case_import_existing_stacked() -> None:
    import bpy
    _import(sequence_name="SEQ_A")
    for o in testutil.view_layer().objects:
        o.select_set(False)
    testutil.view_layer().objects.active = None
    op = _import(sequence_name=" SEQ_B . SEQ_C..", loop_frame=0)
    obj = _skeleton()
    mismatches = []
    armatures = [o.name for o in bpy.data.objects if o.type == "ARMATURE"]
    if armatures != ["skeleton_root"]:
        mismatches.append(f"existing skeleton_root not reused: {armatures}")
    if any(t == "WARNING" for t, _ in op.reports):
        mismatches.append(f"unexpected warning with an existing skeleton: {op.reports}")

    strips = _strips(obj)
    expected = {
        "SEQ_A": ("Sequences Layer 1", 0.0),
        "SEQ_B": ("Sequences Layer 1", float(FIXTURE_FRAMES)),
        "SEQ_C": ("Sequences Layer 2", float(FIXTURE_FRAMES)),
    }
    actual = {name: (t.name, s.frame_start) for name, (t, s) in strips.items()}
    if actual != expected:
        mismatches.append(f"strips {actual}, expected {expected}")
    selected = sorted(name for name, (_, s) in strips.items() if s.select)
    if selected != ["SEQ_B", "SEQ_C"]:
        mismatches.append(f"selected strips {selected}, expected only the new ones")
    if "SEQ_B" in strips and "SEQ_C" in strips:
        b, c = strips["SEQ_B"][1].action, strips["SEQ_C"][1].action
        if b == c:
            mismatches.append("stacked strips share one action instead of one action per name")
        if not (b.use_fake_user and c.use_fake_user):
            mismatches.append("actions have no fake user")
    testutil.check(mismatches)


def case_export_roundtrip() -> None:
    import bpy
    _import(sequence_name="SEQ_A")
    for o in testutil.view_layer().objects:
        o.select_set(False)
    testutil.view_layer().objects.active = None  # the exporter has to find skeleton_root itself
    path = _tmp("roundtrip.xsi")
    op = _export(path)
    mismatches = []
    if [t for t, _ in op.reports] != ["INFO"]:
        mismatches.append(f"unexpected reports for a single strip: {op.reports}")
    with open(path, "rb") as f:
        head = f.read(18)
    if head != b"xsi 0300txt 0032\r\n":
        mismatches.append(f"header/line endings {head!r}")

    original = dotxsi.load_scene(FIXTURE)
    exported = dotxsi.load_scene(path)
    names = {m.name for m in exported.all_models()}
    if names != FIXTURE_MODELS:
        mismatches.append(f"exported models differ: missing={FIXTURE_MODELS - names} extra={names - FIXTURE_MODELS}")
    if exported.frame_range() != original.frame_range():
        mismatches.append(f"frame range {exported.frame_range()} vs {original.frame_range()}")

    common = names & FIXTURE_MODELS
    exp_base, org_base = testutil.base_matrices(exported), testutil.base_matrices(original)
    for name in sorted(common):
        mismatches += testutil.compare_matrices(f"BASEPOSE '{name}'", exp_base[name], org_base[name])
    for frame in range(1, FIXTURE_FRAMES + 1):
        exp_world, org_world = testutil.world_matrices(exported, frame), testutil.world_matrices(original, frame)
        for name in sorted(common):
            mismatches += testutil.compare_matrices(f"frame {frame} '{name}'", exp_world[name], org_world[name])

    # ... and back in: same pose as the first import
    scene = testutil.scene()
    first = {}
    for frame in (0, 7):
        scene.frame_set(frame)
        first[frame] = {pb.name: pb.matrix.copy() for pb in testutil.pose_bones(_skeleton())}
    testutil.reset_scene()
    _import(filepath=path, sequence_name="BACK")
    scene = testutil.scene()
    for frame, poses in first.items():
        scene.frame_set(frame)
        for pb in testutil.pose_bones(_skeleton()):
            mismatches += testutil.compare_matrices(f"reimport frame {frame} '{pb.name}'", pb.matrix, poses[pb.name])
    testutil.check(mismatches)


def case_export_skeleton_only() -> None:
    _import(sequence_name="SEQ_A")
    for _, (_, strip) in _strips(_skeleton()).items():
        strip.select = False
    path = _tmp("skeleton.xsi")
    op = _export(path)
    mismatches = []
    if not any(t == "WARNING" and "skeleton" in m for t, m in op.reports):
        mismatches.append(f"no 'skeleton only' warning: {op.reports}")
    exported = dotxsi.load_scene(path)
    curves = sum(len(m.fcurves) for m in exported.all_models())
    if curves:
        mismatches.append(f"{curves} fcurves written without any selected strip")
    exp_base = testutil.base_matrices(exported)
    org_base = testutil.base_matrices(dotxsi.load_scene(FIXTURE))
    for name in sorted(exp_base):
        mismatches += testutil.compare_matrices(f"BASEPOSE '{name}'", exp_base[name], org_base[name])
    testutil.check(mismatches)


def case_export_concatenated() -> None:
    _import(sequence_name="SEQ_A.SEQ_B")  # stacked copies at the same frames
    obj = _skeleton()
    for _, (_, strip) in _strips(obj).items():
        strip.select = True
    path = _tmp("concat.xsi")
    op = _export(path)
    mismatches = []
    if not any(t == "WARNING" and "concatenated" in m for t, m in op.reports):
        mismatches.append(f"no 'concatenated' warning: {op.reports}")
    exported = dotxsi.load_scene(path)
    if exported.frame_range() != (1.0, 2.0 * FIXTURE_FRAMES):
        mismatches.append(f"frame range {exported.frame_range()}, expected 1-{2 * FIXTURE_FRAMES}")
    original = dotxsi.load_scene(FIXTURE)
    for frame in (1, 6, FIXTURE_FRAMES):
        org = testutil.world_matrices(original, frame)
        for offset in (0, FIXTURE_FRAMES):
            exp = testutil.world_matrices(exported, frame + offset)
            for name in ("pelvis", "ltibia", "cranium"):
                mismatches += testutil.compare_matrices(f"frame {frame + offset} '{name}'", exp[name], org[name])
    testutil.check(mismatches)


def case_export_no_armature() -> None:
    import bpy
    op = FakeOperator()
    result = exporter.save(op, bpy.context, filepath=_tmp("none.xsi"), **EXPORT_DEFAULTS)
    if result != {"CANCELLED"} or not any(t == "ERROR" for t, _ in op.reports):
        raise AssertionError(f"export without armature: {result} {op.reports}")


runner = testutil.TestRunner()
cases = [
    ("smoke", case_smoke),
    ("parse", case_parse),
    ("import_new_armature", case_import_new_armature),
    ("import_humanoid_bones", case_import_humanoid_bones),
    ("import_existing_stacked", case_import_existing_stacked),
    ("export_roundtrip", case_export_roundtrip),
    ("export_skeleton_only", case_export_skeleton_only),
    ("export_concatenated", case_export_concatenated),
    ("export_no_armature", case_export_no_armature),
]
for name, fn in cases:
    testutil.reset_scene()
    runner.run(name, fn)
runner.report()
