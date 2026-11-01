r"""Copy one finished run into results/demo_run/<run>/ so the app works on a fresh clone (results/runs/ and
results/cache/ are not in git). Copies the board log, summary, final files, and per version: base.stl, lid.stl
and a small snapshot PNG. Nothing is recomputed.

.venv\Scripts\python.exe scripts\make_demo_bundle.py [run_folder]
"""
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.board import Board  # noqa: E402

DEFAULT = ROOT / "results" / "runs" / "a_pi5_standard__collective__generic__s0__claude-sonnet-5-5"


def main(src=DEFAULT):
    src = Path(src)
    dst = ROOT / "results" / "demo_run" / src.name
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("board.sqlite", "summary.json", "log.md", "final_spec.json", "final_checks.json", "PRINT_SETTINGS.json",
              "base.stl", "lid.stl"):
        if (src / f).exists():
            shutil.copy2(src / f, dst / f)
    b = Board(src / "board.sqlite")
    for a in b.artifacts(1):
        p = Path(a["path"]) if a["kind"] in ("base", "lid", "assembled") else None
        if p is None or not p.exists() or a["version_id"] is None:
            continue
        vd = dst / "versions" / f"v{a['version_id']}"
        vd.mkdir(parents=True, exist_ok=True)
        if a["kind"] == "assembled":
            im = Image.open(p).convert("RGB")
            w, h = im.size
            im = im.crop((int(w * 0.12), int(h * 0.12), int(w * 0.9), int(h * 0.92)))  # drop the corner label
            im.thumbnail((640, 480))
            im.save(vd / "snapshot.png", optimize=True)
        else:
            shutil.copy2(p, vd / f"{a['kind']}.stl")
    print("bundle:", dst, sum(f.stat().st_size for f in dst.rglob("*") if f.is_file()) / 1e6, "MB")


if __name__ == "__main__":
    main(*sys.argv[1:])
