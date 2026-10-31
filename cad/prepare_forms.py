r"""Run the real PreForm pipeline (orient, layout, supports, checks) on every STL in a folder and save a
.form file next to each, using the one `print:` setting from config.yaml.

Run:  .venv\Scripts\python.exe -m cad.prepare_forms results\test_print results\fit_check results\enclosure\v0_generic
Writes <folder>/<part>.form and <folder>/preform_report.json. Falls back to a clearly labelled MOCK if
PreFormServer is down (then no .form is written).
"""
import json
import sys
from pathlib import Path

from cad.common import load_config, print_settings
from checks.preform_check import check


def prepare(folder, cfg):
    folder = Path(folder).resolve()
    report = {"print_settings": print_settings(cfg), "parts": {}}
    for stl in sorted(folder.glob("*.stl")):
        r = check(stl, cfg, form_path=str(stl.with_suffix(".form")))
        report["parts"][stl.name] = {k: r.get(k) for k in ("mocked", "mock_reason", "print_time_s", "volume_ml",
                                                           "warnings", "passed", "form_path")}
        tag = "MOCK" if r["mocked"] else "PreForm"
        print(f"[{tag}] {stl.name}: {r['print_time_s']} s, {r['volume_ml']} ml, passed={r['passed']}, "
              f"warnings={[w['type'] for w in r['warnings']]}, form={r.get('form_path')}")
    (folder / "preform_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    c = load_config()
    for f in sys.argv[1:]:
        prepare(f, c)
