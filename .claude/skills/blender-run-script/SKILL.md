---
name: blender-run-script
description: Run an arbitrary one-off Python script headlessly inside a pinned Blender version via podman. Use for anything the fixed test suite (blender-tests) doesn't cover -- regenerating the .xsi test fixture, inspecting data that needs bpy/mathutils, or any other ad-hoc headless Blender task.
---

# Run an arbitrary script in headless Blender

A generic counterpart to `.claude/skills/blender-tests`, which only runs the fixed
`tests/run_tests.py` suite. This one takes any script path instead, for the recurring need to run
one-off headless Blender work (e.g. regenerating `tests/testdata/simpleskel.xsi`) without having to craft a
fresh `podman run ... -v "$(pwd)":/repo:Z ...` invocation each time -- the `$(pwd)`-based mount is
what makes the ad-hoc version unsafe to whitelist as a fixed pattern.

```
.claude/skills/blender-run-script/run_blender_script.sh <version> <script-path-relative-to-repo> [-- <args> ...]
# e.g.
.claude/skills/blender-run-script/run_blender_script.sh 4.1 tests/tools/generate_test_xsi.py
```

Pulls `docker.io/blenderkit/headless-blender:blender-<version>-stable` if not already present,
mounts the repo read-write at `/repo` inside the container, and runs
`blender --background --python-exit-code 1 --python /repo/<script-path> -- <args>`. Doesn't rely on
the caller's `$(pwd)` or take a repo path -- it derives the repo root from its own on-disk
location, so call it directly from any cwd.

Pin the version deliberately: the `.xsi` fixture is plain text, but generate it with the oldest
supported Blender (`bl_info["blender"]` in `__init__.py`) so it only relies on what that version's
`mathutils` offers.
