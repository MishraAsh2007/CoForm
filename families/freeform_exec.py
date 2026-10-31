r"""Run model-written CadQuery code for the EXPERIMENTAL free-form family, in its own process.

  python -m families.freeform_exec <code.py> <out_dir>

The code must define `parts = {"name": cq.Workplane or cq.Shape, ...}` in the ASSEMBLED position.
Safety: this is model-written code, so before running it an AST check rejects every import except cadquery and
math, and any use of open/exec/eval/compile/__import__/getattr/os/sys/subprocess/dunder names. It also runs in a
separate process with a timeout (families/freeform.py). This lowers the risk; it is not a sandbox.
Writes <part>.stl and <part>.step per part and manifest.json.
"""
import ast
import json
import math
import sys
from pathlib import Path

ALLOWED_IMPORTS = {"cadquery", "math"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "getattr", "setattr", "delattr", "globals",
                "locals", "vars", "input", "breakpoint", "os", "sys", "subprocess", "shutil", "pathlib", "socket"}


def check_code(src):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else \
                [(node.module or "").split(".")[0]]
            bad = [n for n in names if n not in ALLOWED_IMPORTS]
            if bad:
                raise ValueError(f"import not allowed: {bad}")
        if isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            raise ValueError(f"name not allowed: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError(f"dunder attribute not allowed: {node.attr}")
    return tree


def main(code_path, out_dir):
    import cadquery as cq
    src = Path(code_path).read_text(encoding="utf-8")
    check_code(src)
    env = {"cq": cq, "math": math, "__builtins__": {"range": range, "len": len, "min": min, "max": max,
                                                    "abs": abs, "round": round, "float": float, "int": int,
                                                    "list": list, "dict": dict, "tuple": tuple, "enumerate": enumerate,
                                                    "zip": zip, "sum": sum, "__import__": __import__}}
    exec(compile(src, "freeform_code", "exec"), env)
    parts = env.get("parts")
    if not isinstance(parts, dict) or not parts:
        raise ValueError("code must define parts = {name: cq.Workplane, ...}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, obj in parts.items():
        shape = obj.val() if isinstance(obj, cq.Workplane) else obj
        stl, step = out / f"{name}.stl", out / f"{name}.step"
        cq.exporters.export(shape, str(stl), tolerance=0.01, angularTolerance=0.1)
        cq.exporters.export(shape, str(step))
        manifest[name] = {"stl": str(stl), "step": str(step), "volume_mm3": round(shape.Volume(), 2)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
