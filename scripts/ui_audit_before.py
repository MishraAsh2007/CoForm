r"""UI audit of the classic app: open every page and tab, screenshot, time each load.
.venv\Scripts\python.exe scripts\ui_audit_before.py [app_file] [out_dir]
Starts Streamlit headless on port 8599, stops it at the end."""
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APP = sys.argv[1] if len(sys.argv) > 1 else "app.py"
OUT = ROOT / (sys.argv[2] if len(sys.argv) > 2 else "results/ui_audit/before")
RUN = "a_pi5_standard__collective__generic__s0__claude-sonnet-5-5"
PORT = 8599


def wait_idle(page, timeout=90):
    """Wait until Streamlit stops running (no 'Running...' status widget)."""
    t0 = time.time()
    page.wait_for_timeout(600)
    while time.time() - t0 < timeout:
        if page.locator('[data-testid="stStatusWidget"]').count() == 0:
            page.wait_for_timeout(400)
            if page.locator('[data-testid="stStatusWidget"]').count() == 0:
                return time.time() - t0
        page.wait_for_timeout(300)
    return time.time() - t0


def pick_select(page, label_text, value):
    box = page.locator('[data-testid="stSelectbox"]').filter(has_text=label_text).first
    box.click()
    page.keyboard.type(value)
    page.keyboard.press("Enter")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", APP, "--server.headless", "true",
                             "--server.port", str(PORT), "--browser.gatherUsageStats", "false"], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    timings = {}
    try:
        time.sleep(6)
        with sync_playwright() as p:
            br = p.chromium.launch()
            page = br.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            t0 = time.time()
            page.goto(f"http://localhost:{PORT}", wait_until="domcontentloaded")
            page.wait_for_selector('[data-testid="stSidebar"]', timeout=60000)
            wait_idle(page)

            def shot(name, t):
                timings[name] = round(t, 2)
                page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
                print(name, round(t, 2), flush=True)

            shot("01_run", time.time() - t0)
            for i, name in enumerate(["Versions", "Downloads", "Charts", "Tools", "Fit test"], start=2):
                t0 = time.time()
                page.locator('[data-testid="stSidebar"] label').filter(has_text=name).first.click()
                t = wait_idle(page)
                if name == "Versions":
                    t0b = time.time()
                    pick_select(page, "Run", RUN)
                    t = wait_idle(page) + (t0b - t0)
                    shot("02_versions_3d", time.time() - t0)
                    for j, tab in enumerate(["Spec diff", "Checks", "PreForm", "Why this design"]):
                        t1 = time.time()
                        page.get_by_role("tab", name=tab).click()
                        wait_idle(page)
                        shot(f"02{chr(98 + j)}_versions_{tab.lower().replace(' ', '_')}", time.time() - t1)
                    continue
                if name == "Charts":
                    shot("04_charts_default", time.time() - t0)
                    t1 = time.time()
                    pick_select(page, "Run set", "runs_haiku_v2")
                    wait_idle(page, 120)
                    shot("04b_charts_haiku_v2", time.time() - t1)
                    continue
                shot(f"{i:02d}_{name.lower().replace(' ', '_')}", time.time() - t0)
            (OUT / "timings.json").write_text(json.dumps({"seconds": timings, "console_errors": errors[:30]}, indent=1))
            br.close()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
