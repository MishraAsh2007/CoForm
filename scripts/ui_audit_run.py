r"""Click 'Start the run' (Mock, budget 4) in the new UI and screenshot the live progress twice.
.venv\Scripts\python.exe scripts\ui_audit_run.py [out_dir]"""
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "results/ui_audit/after")
PORT = 8599


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true",
                             "--server.port", str(PORT)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(6)
        with sync_playwright() as p:
            br = p.chromium.launch()
            page = br.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://localhost:{PORT}/run")
            page.wait_for_selector("h1", timeout=60000)
            page.get_by_role("button", name="Start the run").click()
            t0 = time.time()
            for i, wait in enumerate([30, 120, 420]):
                while time.time() - t0 < wait:
                    page.wait_for_timeout(2000)
                    if page.get_by_text("Done. Open the Story").count():
                        break
                page.screenshot(path=str(OUT / f"11_run_progress_{i + 1}.png"), full_page=True)
                print("shot", i + 1, round(time.time() - t0), flush=True)
                if page.get_by_text("Done. Open the Story").count():
                    break
            br.close()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
