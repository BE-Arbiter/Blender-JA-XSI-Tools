"""Parser for Softimage dotXSI 3.0 text files (xsi 0300txt 0032).

Only depends on the standard library, so it can be used and tested outside Blender.
"""

import re

_TOKEN = re.compile(r'"[^"]*"|[{}]|,|[^\s{},"]+')
_NUMBER = re.compile(r'^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$')
_HEADER = re.compile(r'^xsi\s+(\d{4})(txt|bin)\s*(\d{4})')


class DotXSIError(Exception):
    pass


class Block:
    __slots__ = ("type", "name", "values", "children", "line")

    def __init__(self, type, name, line):
        self.type = type
        self.name = name
        self.values = []    # numbers (float) and strings, in file order
        self.children = []  # nested Blocks
        self.line = line

    def find(self, type, prefix=None):
        for child in self.children:
            if child.type == type and (prefix is None or child.name.startswith(prefix)):
                return child
        return None

    def find_all(self, type):
        return [child for child in self.children if child.type == type]

    def __repr__(self):
        return "<%s %s: %d values, %d children>" % (self.type, self.name, len(self.values), len(self.children))


def parse(text):
    """Returns (version, root Block) where root.children are the file's top level blocks."""
    match = _HEADER.match(text)
    if not match:
        raise DotXSIError("Not a dotXSI file (header %r)" % text[:20])
    if match.group(2) != "txt":
        raise DotXSIError("Binary dotXSI files are not supported")
    version = int(match.group(1))
    if version < 300:
        raise DotXSIError("dotXSI version %s is not 3.x (older 1.x files use a different, DirectX-like syntax)" % match.group(1))

    body_start = text.index("\n")
    root = Block("ROOT", "", 1)
    stack = [root]
    pending = None  # (type, name) of a block header waiting for its "{"
    # line numbers are counted incrementally, only up to the tokens that need one
    counted = [0, 1]  # position, line number at that position

    def line_at(position):
        counted[1] += text.count("\n", counted[0], position)
        counted[0] = position
        return counted[1]

    for m in _TOKEN.finditer(text, body_start):
        tok = m.group(0)
        if tok == ",":
            continue

        if tok == "{":
            if pending is None:
                raise DotXSIError("Line %d: unexpected '{'" % line_at(m.start()))
            block = Block(pending[0], pending[1], line_at(m.start()))
            stack[-1].children.append(block)
            stack.append(block)
            pending = None

        elif tok == "}":
            if pending is not None or len(stack) == 1:
                raise DotXSIError("Line %d: unexpected '}'" % line_at(m.start()))
            stack.pop()

        elif tok[0] == '"':
            stack[-1].values.append(tok[1:-1])

        elif _NUMBER.match(tok):
            stack[-1].values.append(float(tok))

        else:
            # Unquoted word: a block type, or the name following a block type
            if pending is None:
                pending = (tok, "")
            elif pending[1] == "":
                pending = (pending[0], tok)
            else:
                raise DotXSIError("Line %d: unexpected word %r" % (line_at(m.start()), tok))

    if len(stack) != 1:
        raise DotXSIError("Unexpected end of file (%d unclosed blocks)" % (len(stack) - 1))

    return version, root


def read(filepath):
    with open(filepath, "r", encoding="latin-1") as f:
        return parse(f.read())


# ---------------------------------------------------------------------------
# Higher level view: models (transform nodes) with their animation curves

class FCurve:
    """One animated parameter of a model, e.g. ROTATION-X."""
    __slots__ = ("parameter", "interpolation", "frames", "values")

    def __init__(self, block):
        v = block.values
        # "object", "PARAMETER", "INTERPOLATION", numDimensions, numValuesPerKey?, numKeys, keys...
        self.parameter = v[1]
        self.interpolation = v[2]
        num_keys = int(v[5])
        keys = v[6:]
        if num_keys <= 0:
            self.frames, self.values = [], []
            return
        stride = len(keys) // num_keys
        if stride < 2 or stride * num_keys != len(keys):
            raise DotXSIError("Line %d: fcurve %s has %d values for %d keys" % (block.line, block.name, len(keys), num_keys))
        # frame, value, [tangents...] - tangents are ignored, keys are sampled linearly
        self.frames = keys[0::stride]
        self.values = keys[1::stride]

    def evaluate(self, frame):
        frames, values = self.frames, self.values
        if not frames:
            return None
        if frame <= frames[0]:
            return values[0]
        if frame >= frames[-1]:
            return values[-1]
        # binary search for the segment
        lo, hi = 0, len(frames) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if frames[mid] <= frame:
                lo = mid
            else:
                hi = mid
        if self.interpolation == "CONSTANT":
            return values[lo]
        t = (frame - frames[lo]) / (frames[hi] - frames[lo])
        return values[lo] + (values[hi] - values[lo]) * t


class SRT:
    """Scale (xyz), rotation in degrees (xyz), translation (xyz)."""
    __slots__ = ("scale", "rotation", "translation")

    def __init__(self, values=None):
        values = values if values and len(values) >= 9 else [1, 1, 1, 0, 0, 0, 0, 0, 0]
        self.scale = list(values[0:3])
        self.rotation = list(values[3:6])
        self.translation = list(values[6:9])


_PARAMS = {"SCALING": "scale", "ROTATION": "rotation", "TRANSLATION": "translation"}
_AXES = {"X": 0, "Y": 1, "Z": 2}


class Model:
    def __init__(self, block, parent):
        self.name = block.name[4:] if block.name.startswith("MDL-") else block.name
        self.parent = parent
        self.children = []
        self.kind = None  # SI_Null, SI_Mesh, ...
        self.visible = True

        srt = block.find("SI_Transform", "SRT-")
        base = block.find("SI_Transform", "BASEPOSE-")
        self.srt = SRT(srt.values if srt else None)
        self.basepose = SRT(base.values) if base else SRT(srt.values if srt else None)

        self.fcurves = {}  # ("rotation", 0) -> FCurve
        for child in block.children:
            if child.type == "SI_FCurve":
                curve = FCurve(child)
                kind, _, axis = curve.parameter.partition("-")
                if kind in _PARAMS and axis in _AXES and curve.frames:
                    self.fcurves[(_PARAMS[kind], _AXES[axis])] = curve
            elif child.type == "SI_Visibility" and child.values:
                self.visible = bool(child.values[0])
            elif child.type in ("SI_Null", "SI_Mesh", "SI_Camera", "SI_Light", "SI_IK_Effector", "SI_IK_Joint", "SI_IK_Root"):
                self.kind = child.type

        for child in block.find_all("SI_Model"):
            self.children.append(Model(child, self))

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def srt_at(self, frame):
        """SRT at a frame, animated values falling back to the static transform."""
        out = SRT()
        for attr in ("scale", "rotation", "translation"):
            static = getattr(self.srt, attr)
            vec = getattr(out, attr)
            for axis in range(3):
                curve = self.fcurves.get((attr, axis))
                value = curve.evaluate(frame) if curve else None
                vec[axis] = static[axis] if value is None else value
        return out

    def frame_range(self):
        frames = [f for c in self.fcurves.values() for f in (c.frames[0], c.frames[-1])]
        return (min(frames), max(frames)) if frames else None


class Scene:
    def __init__(self, root):
        self.root = root
        info = root.find("SI_Scene")
        # "FRAMES", start, end, fps
        self.start = self.end = self.fps = None
        if info and len(info.values) >= 4:
            self.start, self.end, self.fps = info.values[1], info.values[2], info.values[3]
        self.models = [Model(b, None) for b in root.find_all("SI_Model")]

    def all_models(self):
        for model in self.models:
            yield from model.walk()

    def frame_range(self):
        ranges = [r for r in (m.frame_range() for m in self.all_models()) if r]
        if ranges:
            return min(r[0] for r in ranges), max(r[1] for r in ranges)
        if self.start is not None and self.end is not None:
            return self.start, self.end
        return 0, 0


def load_scene(filepath):
    version, root = read(filepath)
    return Scene(root)
