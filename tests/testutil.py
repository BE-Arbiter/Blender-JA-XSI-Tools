import importlib.util
import os
import sys
from types import ModuleType
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    import mathutils

PACKAGE = "io_scene_dotXsi3"


def import_addon() -> ModuleType:
    """Import the repo as the add-on package regardless of its on-disk directory name."""
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        PACKAGE, os.path.join(repo_root, "__init__.py"),
        submodule_search_locations=[repo_root],
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


def reset_scene() -> None:
    """Clear all Blender state between test cases so one case can't leak objects/data into the next."""
    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)


class TestRunner:
    """Runs each case, logs pass/fail immediately, defers raising until every case has run."""

    def __init__(self) -> None:
        self.results: List[Tuple[str, Optional[Exception]]] = []

    def run(self, name: str, fn: Callable[[], None]) -> None:
        print(f"[test] Running {name}...")
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[test] {name}: FAIL - {e}")
            self.results.append((name, e))
        else:
            print(f"[test] {name}: PASS")
            self.results.append((name, None))

    def report(self) -> None:
        print("[test] === Summary ===")
        failed = []
        for name, err in self.results:
            print(f"[test]   {name}: {'FAIL' if err else 'PASS'}")
            if err is not None:
                failed.append(name)
        if failed:
            raise RuntimeError(f"{len(failed)}/{len(self.results)} test case(s) failed: {', '.join(failed)}")


def check(mismatches: List[str]) -> None:
    """A case's assertion helper: turn a list of mismatch strings into an exception if non-empty."""
    if mismatches:
        shown = mismatches[:30]
        more = f"\n  ... and {len(mismatches) - len(shown)} more" if len(mismatches) > len(shown) else ""
        raise AssertionError(f"{len(mismatches)} mismatch(es):\n" + "\n".join(f"  - {m}" for m in shown) + more)


def world_matrices(scene, frame: float) -> Dict[str, "mathutils.Matrix"]:
    """World matrix of every model of a parsed dotxsi.Scene at `frame`, from the local SRT curves."""
    addon = import_addon()
    world: Dict[str, "mathutils.Matrix"] = {}
    for model in scene.all_models():
        local = addon.xsi3_blender_importer.srt_matrix(model.srt_at(frame))
        world[model.name] = world[model.parent.name] @ local if model.parent else local
    return world


def base_matrices(scene) -> Dict[str, "mathutils.Matrix"]:
    addon = import_addon()
    return {m.name: addon.xsi3_blender_importer.srt_matrix(m.basepose) for m in scene.all_models()}


def compare_matrices(label: str, actual: "mathutils.Matrix", expected: "mathutils.Matrix",
                     rotation_atol: float = 1e-4, translation_atol: float = 1e-3) -> List[str]:
    """Rotation part compared column by column, ignoring scale (the add-on drops bone scale)."""
    mismatches = []
    a3 = actual.to_3x3().normalized()
    e3 = expected.to_3x3().normalized()
    rot = max((a3.col[i] - e3.col[i]).length for i in range(3))
    if rot > rotation_atol:
        mismatches.append(f"{label}: rotation differs by {rot:.6f} (atol={rotation_atol})")
    trans = (actual.translation - expected.translation).length
    if trans > translation_atol:
        mismatches.append(f"{label}: translation differs by {trans:.6f} (atol={translation_atol})")
    return mismatches


# bpy's Optional-typed accessors, asserted once here rather than at every call site in the cases

def scene():
    import bpy
    scene = bpy.context.scene
    assert scene is not None
    return scene


def view_layer():
    import bpy
    view_layer = bpy.context.view_layer
    assert view_layer is not None
    return view_layer


def bones(obj):
    import bpy
    assert isinstance(obj.data, bpy.types.Armature)
    return obj.data.bones


def pose_bones(obj):
    assert obj.pose is not None
    return obj.pose.bones


def anim(obj):
    assert obj.animation_data is not None
    return obj.animation_data
