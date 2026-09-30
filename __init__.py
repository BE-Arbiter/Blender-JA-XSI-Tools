import importlib
import sys
from typing import Any, Dict, Set, cast

import bpy
from bpy.props import StringProperty, BoolProperty, FloatProperty, EnumProperty, IntProperty, FloatVectorProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper

from .xsi3_blender_importer import OperatorReturnItems

bl_info = {
    "name": "JA dotXSI NLA Import/Export (.xsi)",
    "author": "BE-Arbiter",
    "version": (1, 0, 0),
    "blender": (4, 1, 0),
    "location": "File > Import / Export > JA dotXSI NLA Import / Export (.xsi)",
    "description": "Imports and exports skeletal animations as NLA strips from / to Softimage dotXSI 3.0 text files (3ds Max exports for Jedi Academy / Carcass)",
    "category": "Import-Export"
}

# F8 "Reload Scripts": the operators import the submodules lazily, so reload the ones already loaded
for _name in ("dotxsi", "xsi3_blender_importer", "xsi3_blender_exporter"):
    _module = sys.modules.get("%s.%s" % (__name__, _name))
    if _module is not None:
        importlib.reload(_module)


class ImportDotXSI3(bpy.types.Operator, ImportHelper):  # pyright: ignore[reportIncompatibleMethodOverride]  # invoke() stubs of the two bases differ
    """Import a dotXSI 3.0 skeletal animation"""
    bl_idname = "import_anim.dotxsi3"
    bl_label = "Import dotXSI as NLA Strip"
    bl_options = {"UNDO", "PRESET"}

    filename_ext = ".xsi"
    filter_glob: StringProperty(default="*.xsi", options={"HIDDEN"})  # pyright: ignore[reportInvalidTypeForm]

    bones: EnumProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Bones",
        description="Which bones the armature gets, when there is no skeleton_root yet and one has to be created",
        items=(
            ("HUMANOID", "JKA _humanoid", "Only the 53 bones of Jedi Academy's _humanoid.gla, with its hierarchy and bone order"),
            ("NO_HELPERS", "All but helpers", "Every model except effectors, skeleton_root and mesh_root"),
            ("ALL", "All", "Every model in the file"),
        ),
        default="HUMANOID"
    )

    scale: FloatProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Scale",
        description="Scale applied to the file (0.64 for Jedi Academy's _humanoid)",
        default=0.64, min=0.0001, max=1000.0
    )

    origin_offset: FloatVectorProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Origin Offset",
        description="Translation added to the whole skeleton in every frame, in Ghoul2 units. Carcass applies the model origin at compile time: every stock _humanoid animation has model_root at (0, 0, -24)",
        default=(0.0, 0.0, -24.0),
        subtype="TRANSLATION", size=3
    )

    y_up_to_z_up: BoolProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Y Up to Z Up",
        description="Convert from the file's Y-up space to Blender / Ghoul2 Z-up",
        default=True
    )

    nla_strip: BoolProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Add as NLA Strip",
        description="Put the animation in a new NLA strip (one action per sequence, like the jediacademy addon's .gla import with animation.cfg) instead of making it the active action",
        default=True
    )

    strip_start: IntProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Strip Start",
        description="Frame where the strip starts, -1 = right after the last existing strip",
        default=-1, min=-1
    )

    sequence_name: StringProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Sequence Name",
        description="Name of the action / animation.cfg sequence (e.g. BOTH_STAND2). Several names separated by dots (BOTH_SABERFAST_STANCE.BOTH_STAND2.BOTH_SABERSLOW_STANCE) create one action per name, with their strips stacked at the same frames. Empty = file name",
        default=""
    )

    loop_frame: IntProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Loop Frame",
        description="animation.cfg loop frame: -1 = no loop, 0 = loop from the start",
        default=-1, min=-1
    )

    start_at_zero: BoolProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Start at Frame 0",
        description="Shift the animation so its first frame is frame 0 (Ghoul2 animations start at 0)",
        default=True
    )

    set_scene_range: BoolProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Set Scene Range && FPS",
        description="Set the scene frame range and frame rate to the animation's",
        default=True
    )

    def draw(self, context):
        from .xsi3_blender_importer import find_skeleton
        layout = self.layout
        assert layout is not None

        skeleton = find_skeleton(context)
        if skeleton is not None:
            layout.label(text="Armature: %s" % skeleton.name, icon="ARMATURE_DATA")
        else:
            layout.label(text="No skeleton_root, one will be created", icon="ERROR")
            layout.prop(self, "bones")
        layout.prop(self, "scale")
        layout.prop(self, "origin_offset")
        layout.prop(self, "y_up_to_z_up")

        box = layout.box()
        box.prop(self, "nla_strip")
        sub = box.column()
        sub.enabled = self.nla_strip
        sub.prop(self, "strip_start")
        sub.label(text="Sequence Name:")
        sub.prop(self, "sequence_name", text="")
        sub.prop(self, "loop_frame")
        sub = layout.column()
        sub.enabled = not self.nla_strip
        sub.prop(self, "start_at_zero")
        layout.prop(self, "set_scene_range")

    def execute(self, context: bpy.types.Context) -> Set[OperatorReturnItems]:
        from . import xsi3_blender_importer
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        keywords = cast(Dict[str, Any], self.as_keywords(ignore=("filter_glob", "filepath")))
        return xsi3_blender_importer.load(self, context, filepath=self.filepath, **keywords)  # pyright: ignore[reportAttributeAccessIssue]  # from ImportHelper


class ExportDotXSI3(bpy.types.Operator, ExportHelper):  # pyright: ignore[reportIncompatibleMethodOverride]  # invoke() stubs of the two bases differ
    """Export the selected NLA strips of an armature as a dotXSI 3.0 skeletal animation"""
    bl_idname = "export_anim.dotxsi3"
    bl_label = "Export NLA Strip as dotXSI"
    bl_options = {"PRESET"}

    filename_ext = ".xsi"
    filter_glob: StringProperty(default="*.xsi", options={"HIDDEN"})  # pyright: ignore[reportInvalidTypeForm]

    scale: FloatProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Scale",
        description="Scale of the armature relative to the file (0.64 for Jedi Academy's _humanoid): the file is written 1 / scale bigger",
        default=0.64, min=0.0001, max=1000.0
    )

    origin_offset: FloatVectorProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Origin Offset",
        description="Translation removed from the whole skeleton in every frame, in Ghoul2 units (the inverse of the import). Carcass adds the model origin back at compile time: (0, 0, -24) for _humanoid",
        default=(0.0, 0.0, -24.0),
        subtype="TRANSLATION", size=3
    )

    y_up_to_z_up: BoolProperty(  # pyright: ignore[reportInvalidTypeForm]
        name="Z Up to Y Up",
        description="Convert from Blender / Ghoul2 Z-up to the file's Y-up space",
        default=True
    )

    def draw(self, context):
        from .xsi3_blender_exporter import pick_skeleton, selected_strips
        layout = self.layout
        assert layout is not None

        skeleton = pick_skeleton(context)
        if skeleton is None:
            layout.label(text="No armature in the scene", icon="ERROR")
        else:
            layout.label(text="Armature: %s" % skeleton.name, icon="ARMATURE_DATA")
            strips = selected_strips(skeleton)
            box = layout.box()
            if not strips:
                box.label(text="No NLA strip selected:", icon="ERROR")
                box.label(text="only the skeleton will be exported")
            else:
                if len(strips) > 1:
                    box.label(text="%d strips, concatenated end to end:" % len(strips), icon="ERROR")
                for strip in strips[:12]:
                    box.label(text=strip.name, icon="NLA")
                if len(strips) > 12:
                    box.label(text="... and %d more" % (len(strips) - 12))

        layout.prop(self, "scale")
        layout.prop(self, "origin_offset")
        layout.prop(self, "y_up_to_z_up")

    def execute(self, context: bpy.types.Context) -> Set[OperatorReturnItems]:
        from . import xsi3_blender_exporter
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        keywords = cast(Dict[str, Any], self.as_keywords(ignore=("filter_glob", "filepath", "check_existing")))
        return xsi3_blender_exporter.save(self, context, filepath=self.filepath, **keywords)  # pyright: ignore[reportAttributeAccessIssue]  # from ExportHelper


def menu_func_import(self, context):
    self.layout.operator(ImportDotXSI3.bl_idname, text="JA dotXSI NLA Import (.xsi)")


def menu_func_export(self, context):
    self.layout.operator(ExportDotXSI3.bl_idname, text="JA dotXSI NLA Export (.xsi)")


classes = (ImportDotXSI3, ExportDotXSI3)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
