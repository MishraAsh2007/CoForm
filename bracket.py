# Create a bracket using FreeCAD. Geometry is built in FreeCAD's own Python by
# freecad_bracket.py; this module validates parameters and runs it as a subprocess.
import json
import subprocess
from pathlib import Path

import config
from schemas import GeometryParams

GENERATOR = Path(__file__).resolve().parent / "freecad_bracket.py"


def build_bracket(plate_thickness, rib_height, rib_thickness, stl_path, step_path, timeout=120):
    """Build an L-shaped cantilever bracket with a triangular gusset rib.

    Coordinates (mm): the vertical wall plate lies in the XZ plane at Y=0 and is
    the fixed mounting face; the horizontal arm extends along +Y from Z=0 and
    carries the load. The plates meet at an inner fillet (config.INNER_FILLET_RADIUS).
    The rib sits centered in X in the inner corner, with legs of length
    rib_height measured from the inner plate faces. The envelope comes from config.

    Parameters are validated against config.PARAMETER_BOUNDS (raises
    pydantic.ValidationError). Writes STEP (for analysis and Fusion) and STL
    (for PreForm) and returns {"volume_mm3", "freecad_version", "stl_path", "step_path"}.
    """
    params = GeometryParams(plate_thickness=plate_thickness, rib_height=rib_height,
                            rib_thickness=rib_thickness)
    request = {
        "parameters": {
            **params.model_dump(),
            "width": config.BRACKET_WIDTH, "arm_length": config.ARM_LENGTH,
            "wall_height": config.WALL_HEIGHT, "fillet_radius": config.INNER_FILLET_RADIUS,
            "rib_fillet_radius": config.RIB_FILLET_RADIUS,
        },
        "stl_path": str(stl_path),
        "step_path": str(step_path),
    }
    try:
        proc = subprocess.run([config.FREECAD_PYTHON, str(GENERATOR)],
                              input=json.dumps(request), capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError(f"FreeCAD Python not found at {config.FREECAD_PYTHON!r}; "
                           "set FREECAD_PYTHON in .env") from None
    if proc.returncode != 0:
        raise RuntimeError(f"FreeCAD bracket generation failed:\n{proc.stderr.strip()}")

    # FreeCAD may log to stdout; the result is the last line.
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    result.update(stl_path=str(stl_path), step_path=str(step_path))
    return result


if __name__ == "__main__":
    # Smoke test: python bracket.py [plate_thickness rib_height rib_thickness]
    import sys

    import store

    params = [float(v) for v in sys.argv[1:4]] or [4.0, 30.0, 5.0]
    print(build_bracket(*params, *store.geometry_paths("smoke", "c0")))
