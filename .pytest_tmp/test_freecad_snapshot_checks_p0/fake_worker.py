
import json, os, sys
sys.path.insert(0, 'C:\\Users\\Jash_\\ScienceClaw')
from scienceclaw.domain import Geometry
from scienceclaw.mechanics import generate_stl, section_properties
job = json.load(open(os.environ["SCIENCECLAW_FREECAD_JOB"], encoding="utf-8"))
result = {"ok": True, "freecad_version": "fake-1.1"}
if job["action"] == "build":
    if os.getenv("FAKE_FREECAD_FAIL"):
        result = {"ok": False, "freecad_version": "fake-1.1", "error": "FreeCAD model failed to rebuild: WallB"}
    else:
        v = job["values_mm"]
        g = Geometry(section_family=job["family"], length_m=v["sc_length"] / 1000, base_width_m=v["sc_base_width"] / 1000,
                     wall_height_m=v["sc_wall_height"] / 1000, thickness_m=v["sc_thickness"] / 1000)
        props = section_properties(g)
        error = float(os.getenv("FAKE_FREECAD_INERTIA_ERROR", "0"))
        open(job["stl"], "w").write(generate_stl(g))
        open(job["fcstd"], "wb").write(b"fcstd-" + json.dumps(v).encode())
        open(job["step"], "wb").write(b"step-" + json.dumps(v).encode())
        if not os.path.exists(job["master"]):
            open(job["master"], "wb").write(b"master")
        result.update(volume_mm3=props["area_m2"] * g.length_m * 1e9, area_mm2=props["area_m2"] * 1e6,
                      second_moment_mm4=props["second_moment_m4"] * 1e12 * (1 + error), extents_mm=[1, 2, 3],
                      solids=1, is_valid=True)
json.dump(result, open(job["result"], "w"))
