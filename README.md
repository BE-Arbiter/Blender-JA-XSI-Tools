# Blender JA dotXSI Tools

Blender add-on importing and exporting Softimage **dotXSI 3.0** skeletal animations (`.xsi`) as NLA
strips, for Jedi Academy animation work. It complements the
[Blender Jedi Academy Plugin Suite](https://github.com/BE-Arbiter/Blender-Jedi-Academy-Tools), which
reads and writes the game's `.gla` and `animation.cfg`.

dotXSI 3.0 is the text format 3ds Max's exporter writes and Raven's Carcass compiles into `.gla`
animations; many community animations (e.g. made with Ashuradx's CAT rig of `_humanoid`) are
distributed in it.

## Installation

Download `io_scene_dotXsi3.zip` from the [releases](https://github.com/BE-Arbiter/Blender-JA-XSI-Tools/releases),
install it with "Install from Disk" in Blender's add-on preferences and enable
"JA dotXSI NLA Import/Export (.xsi)". Blender 4.1 or newer.

## Usage

- **File > Import > JA dotXSI NLA Import (.xsi)** adds the animation as a new NLA strip on
  `skeleton_root` (or the active armature). Without one, an armature is created from the file's base
  pose. Several sequence names separated by dots (`BOTH_SABERFAST_STANCE.BOTH_STAND2`) create stacked
  strips, one action per name.
- **File > Export > JA dotXSI NLA Export (.xsi)** writes the selected NLA strips of the armature,
  concatenated end to end. With no strip selected, only the skeleton is written.

Typical workflow: import `_humanoid.gla` with the Jedi Academy plugin suite, import the `.xsi`, then
export the `.gla` with `models/players/_humanoid/_humanoid` as "gla reference", and the
`animation.cfg` from the NLA strips.

See [ja_xsi_tools_doc.tex](ja_xsi_tools_doc.tex) (the PDF is attached to each release) for all
settings and the file format conventions.

## Development

- `make` builds `build/io_scene_dotXsi3.zip` and the manual (needs `zip` and `pdflatex`).
- `make pep8` / `make format` check / fix the code style.
- Tests run headless in Blender:
  `blender --background --factory-startup --python-exit-code 1 --python tests/run_tests.py`
  (or `.claude/skills/blender-tests/run_blender_tests.sh 4.1 5.2` with podman).
