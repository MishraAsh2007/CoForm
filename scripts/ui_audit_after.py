r"""UI audit of the new app: open every page by URL, screenshot, time each load.
.venv\Scripts\python.exe scripts\ui_audit_after.py [out_dir] [page ...]
Starts Streamlit headless on port 8599 and stops it at the end."""
import json
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "results/ui_audit/after")
ONLY = sys.argv[2:]
PORT = 8599
PAGES = ["", "run", "agents", "story", "viewer", "why", "print", "downloads", "charts", "workshop"]


def wait_idle(page, timeout=120):
    t0 = time.time()
    page.wait_for_timeout(700)
    quiet = 0
    while time.time() - t0 < timeout:
        busy = page.locator('[data-testid="stStatusWidget"]').count() or page.locator('[data-testid="stSkeleton"]').count()
        quiet = 0 if busy else quiet + 1
        if quiet >= 3:
            break
        page.wait_for_timeout(250)
    try:
        page.wait_for_selector("h1", timeout=60000)
    except Exception:  # noqa: BLE001
        pass
    # 3D viewers live in component iframes: wait until each has drawn its canvas
    n = page.locator("iframe").count()
    for i in range(n):
        try:
            page.locator("iframe").nth(i).scroll_into_view_if_needed(timeout=5000)
            fr = page.frame_locator("iframe").nth(i)
            fr.locator("canvas").first.wait_for(timeout=30000)
        except Exception:  # noqa: BLE001
            pass
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)
    return time.time() - t0


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true",
                             "--server.port", str(PORT), "--browser.gatherUsageStats", "false"], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    timings, errors = {}, []
    try:
        time.sleep(6)
        with sync_playwright() as p:
            br = p.chromium.launch()
            page = br.new_page(viewport={"width": 1440, "height": 900})
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            for i, name in enumerate(PAGES, 1):
                if ONLY and name not in ONLY:
                    continue
                t0 = time.time()
                page.goto(f"http://localhost:{PORT}/{name}", wait_until="domcontentloaded")
                page.wait_for_selector('[data-testid="stMain"]', timeout=60000)
                wait_idle(page)
                page.wait_for_timeout(800)
                t = time.time() - t0
                timings[name or "home"] = round(t, 2)
                page.screenshot(path=str(OUT / f"{i:02d}_{name or "home"}.png"), full_page=True)
                print(name, round(t, 2), flush=True)
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
