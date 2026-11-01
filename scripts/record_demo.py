r"""Record the demo video of the new UI with on-screen captions (no audio), then convert it.

.venv\Scripts\python.exe scripts\record_demo.py
-> results/submission/demo.mp4 (under 3 min, under 50 MB) and results/submission/demo.gif (short, for the README)

Walkthrough: Home -> Story -> 3D -> Why this design -> Print (PreForm) -> Downloads -> Charts.
Every number in the captions comes from the run log / result files (see DEMO.md)."""
import shutil
import subprocess
import sys
import time
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "submission"
TMP = ROOT / "results" / "tmp" / "video"
PORT = 8598
W, H = 1440, 900

CAPTION_JS = """(t) => {
  let d = document.getElementById('sc-cap');
  if (!d) { d = document.createElement('div'); d.id = 'sc-cap'; document.body.appendChild(d);
    Object.assign(d.style, {position: 'fixed', left: '50%', bottom: '28px', transform: 'translateX(-50%)',
      maxWidth: '1100px', padding: '14px 22px', background: 'rgba(16,20,22,0.92)', color: '#edf4f1',
      border: '1px solid #4f6d61', borderRadius: '12px', font: '600 21px/1.4 system-ui, Segoe UI, sans-serif',
      zIndex: 999999, textAlign: 'center', boxShadow: '0 8px 30px rgba(0,0,0,.5)'}); }
  d.innerHTML = t;
}"""


def cap(page, text, secs):
    page.evaluate(CAPTION_JS, text)
    page.wait_for_timeout(int(secs * 1000))


MARKS = {}
URL = {"Story": "story", "3D": "viewer", "Why": "why", "Print": "print", "Downloads": "downloads", "Charts": "charts"}


def go(page, path):
    MARKS[path] = round(time.time() - MARKS["t0"], 1)
    try:
        page.locator('header a, [data-testid="stTopNavLink"]').filter(has_text=path).first.click(timeout=4000)
    except Exception:  # noqa: BLE001
        page.goto(f"http://localhost:{PORT}/{URL[path]}")
    page.wait_for_timeout(600)
    page.wait_for_selector("h1", timeout=60000)
    for _ in range(60):
        if not page.locator('[data-testid="stStatusWidget"]').count():
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(700)


def scroll(page, dy, steps=10, pause=120):
    page.mouse.move(W * 0.6, H * 0.5)
    for _ in range(steps):
        page.mouse.wheel(0, dy / steps)
        page.wait_for_timeout(pause)


def to_step(page, text):
    """Smooth-scroll the Story page so the step whose title starts with `text` is near the top."""
    page.evaluate("""(t) => { const h = [...document.querySelectorAll('h3')].find(e => e.textContent.trim().startsWith(t));
      if (h) h.scrollIntoView({behavior: 'smooth', block: 'center'}); }""", text)
    page.wait_for_timeout(1200)


def drag(page, box, dx, steps=40):
    x, y = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.55
    page.mouse.move(x, y)
    page.mouse.down()
    for i in range(steps):
        page.mouse.move(x + dx * (i + 1) / steps, y + 6 * (i + 1) / steps)
        page.wait_for_timeout(30)
    page.mouse.up()


def walkthrough(page):
    page.goto(f"http://localhost:{PORT}/", wait_until="domcontentloaded")
    page.wait_for_selector("h1", timeout=90000)
    page.wait_for_timeout(2500)
    cap(page, "ScienceClaw: four specialist AI agents design a print-ready Raspberry Pi 5 case, with no boss.", 6)
    cap(page, "Each agent owns one real tool and its own part of the design. Fixes in someone else's part become requests.", 6)
    scroll(page, 450)
    cap(page, "Best run (Sonnet, 4 agents): local check failures 20 → 0. Print job in real PreForm: 0 warnings, 23.0 min, 61.9 ml.", 7)

    go(page, "Story")
    cap(page, "The Story: every step from the run log. Problem found → who fixed it → result.", 5)
    to_step(page, "v2 ·")
    cap(page, "v2 · Assembly raises the lid to 18.4 mm and opens the port tops: 20 → 13 problems. It posts needs to Fit and Manufacturing.", 7)
    to_step(page, "v3 ·")
    cap(page, "v3 · Fit, asked by Assembly, raises the standoffs 1.5 → 2.9 mm so the board screws grip: 13 → 5.", 7)
    to_step(page, "v4 ·")
    cap(page, "v4 · Manufacturing adds a vent over the fan (5 → 4). v5 · Assembly opens the USB-A tops: 4 → 0.", 7)
    to_step(page, "v10 ·")
    cap(page, "Edits that make any check worse are rejected by the protocol, e.g. a thinner floor that broke 4 screw checks.", 6)

    go(page, "3D")
    cap(page, "3D: first (naive) vs final design, rotating together. Final: tabs, vent, open port tops, taller posts.", 4)
    fl = page.frame_locator("iframe").nth(0)
    box = fl.locator("canvas").first.bounding_box()
    if box:
        drag(page, box, 260)
        page.wait_for_timeout(400)
        drag(page, box, -420)
    fl.locator("#lift").evaluate("e => { e.value = 0; e.dispatchEvent(new Event('input')); }")
    cap(page, "Lid down: the vent sits over the Active Cooler's fan.", 4)
    for v in range(0, 46, 3):
        fl.locator("#lift").evaluate(f"e => {{ e.value = {v}; e.dispatchEvent(new Event('input')); }}")
        page.wait_for_timeout(70)
    fl.locator('input[data-g="keepout"]').check()
    cap(page, "Board keep-out on: every connector zone and the cooler (approximate) the case must clear.", 6)
    scroll(page, 760)
    page.wait_for_timeout(800)
    cap(page, "Final design: base with the Raspberry Pi 5, lid, and assembled (lid see-through).", 6)

    go(page, "Why")
    cap(page, "Why this design: built only from the log. Assembly's need to Fit, Fit's fix and the numbers.", 7)
    page.get_by_role("button", name="Write AI summary").click()
    page.mouse.move(W - 20, H * 0.4)
    page.wait_for_timeout(2500)
    scroll(page, 260)
    cap(page, "The AI summary may not write numbers: they are filled in from the log (MOCK template here, labelled).", 7)

    go(page, "Print")
    cap(page, "Real PreForm, Form Studio 1, Alpha Fast V1, 0.15 mm. Auto-orient found 1 resin-trapping cup (and 1 collision).", 7)
    scroll(page, 420)
    cap(page, "Manufacturing's orientation tool compared 6 orientations in PreForm and picked flat for both parts.", 6)
    cap(page, "Result: 0 warnings, both parts on one platform, 23.0 min, 61.9 ml of resin.", 6)

    go(page, "Downloads")
    cap(page, "Downloads: .form for Formlabs, STL for any resin printer, FDM STL for a Creality (not PreForm-checked), STEP for Onshape.", 8)

    go(page, "Charts")
    cap(page, "Benchmark (Haiku, brief a, 3 seeds per mode): mean failures left: team 4, generalist 6, one shot 20.", 7)
    scroll(page, 700)
    cap(page, "Honest: no run passed every check (0/3 per mode), and the team costs more per run. Sonnet benchmark incomplete.", 8)
    scroll(page, 700)
    cap(page, "Every chart is plain code over the logs. Repo: github.com/MishraAsh2007/ScienceClaw_Project (branch Mo_ver).", 6)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True)
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true",
                             "--server.port", str(PORT)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(7)
        with sync_playwright() as p:
            br = p.chromium.launch()
            # warm-up (cache meshes) without recording
            pg = br.new_page(viewport={"width": W, "height": H})
            for path in ("", "story", "viewer", "why", "print", "charts"):
                pg.goto(f"http://localhost:{PORT}/{path}")
                pg.wait_for_selector("h1", timeout=90000)
                pg.wait_for_timeout(1500)
            pg.close()
            ctx = br.new_context(viewport={"width": W, "height": H}, record_video_dir=str(TMP),
                                 record_video_size={"width": W, "height": H})
            page = ctx.new_page()
            t0 = time.time()
            MARKS["t0"] = t0
            walkthrough(page)
            print("section starts (s):", {k: v for k, v in MARKS.items() if k != "t0"})
            print("walkthrough seconds", round(time.time() - t0, 1))
            ctx.close()
            br.close()
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    webm = next(TMP.glob("*.webm"))
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    mp4 = OUT / "demo.mp4"
    subprocess.run([ff, "-y", "-i", str(webm), "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-pix_fmt",
                    "yuv420p", "-movflags", "+faststart", "-an", str(mp4)], check=True, capture_output=True)
    gif = OUT / "demo.gif"
    # 14 s from the 3D part, 760 px wide, 8 fps
    start = MARKS.get("3D", DEMO_GIF_START) + 1
    subprocess.run([ff, "-y", "-ss", str(start), "-t", "14", "-i", str(mp4), "-vf",
                    "fps=8,scale=760:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse",
                    str(gif)], check=True, capture_output=True)
    print("mp4 MB", round(mp4.stat().st_size / 1e6, 1), "gif MB", round(gif.stat().st_size / 1e6, 1))


DEMO_GIF_START = 60

if __name__ == "__main__":
    main()
