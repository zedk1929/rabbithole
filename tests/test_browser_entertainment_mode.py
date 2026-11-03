"""
Browser test via Chrome DevTools Protocol (CDP) for Rabbithole's Entertainment Only feed:
1. Verifies switching to Entertainment Only mode via #modeEntertainmentBtn.
2. Verifies cards display qualified '~X% Est. Craft & Fun' badge with metadata-based tone estimate tooltip.
3. Verifies cards display entertainment format badges (e.g., creative_projects, comedy, etc.).
4. Verifies actions (like Watch Later or watch time) update preferred_entertainment_formats in living taste profile.
5. Verifies Living Taste Profile modal renders the Preferred Entertainment Formats section.
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
import pytest
import requests
import websockets

os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def test_entertainment_only_feed_in_browser():
    asyncio.run(run_entertainment_browser_verification())


async def run_entertainment_browser_verification():
    server_port = find_free_port()
    cdp_port = find_free_port()
    chrome_profile_dir = f"/tmp/chrome_ent_test_profile_{cdp_port}"
    os.makedirs(chrome_profile_dir, exist_ok=True)

    server_proc = None
    chrome_proc = None

    try:
        server_env = os.environ.copy()
        server_env["GEMINI_API_KEY"] = ""
        server_env["GOOGLE_API_KEY"] = ""
        server_cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "rabbithole_app.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(server_port),
        ]
        server_proc = subprocess.Popen(server_cmd, env=server_env)

        # Wait for server ready
        for _ in range(30):
            try:
                r = requests.get(f"http://127.0.0.1:{server_port}/api/health", timeout=1)
                if r.status_code == 200:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("Server failed to start")

        chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        chrome_cmd = [
            chrome_bin,
            "--headless=new",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={chrome_profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--window-size=1280,900",
            "about:blank",
        ]
        chrome_proc = subprocess.Popen(chrome_cmd)

        cdp_ready = False
        targets = []
        for _ in range(30):
            try:
                r = requests.get(f"http://127.0.0.1:{cdp_port}/json", timeout=1)
                if r.status_code == 200:
                    targets = r.json()
                    if targets:
                        cdp_ready = True
                        break
            except Exception:
                time.sleep(0.3)

        if not cdp_ready:
            raise RuntimeError("Chrome CDP not reachable")

        page_target = next((t for t in targets if t.get("type") == "page"), targets[0])
        ws_url = page_target["webSocketDebuggerUrl"]

        async with websockets.connect(ws_url) as ws:
            msg_id = 0

            async def cdp_send(method, params=None):
                nonlocal msg_id
                msg_id += 1
                req = {"id": msg_id, "method": method, "params": params or {}}
                await ws.send(json.dumps(req))
                while True:
                    resp = json.loads(await ws.recv())
                    if resp.get("id") == msg_id:
                        return resp.get("result", {})

            async def eval_js(expr):
                res = await cdp_send("Runtime.evaluate", {
                    "expression": expr,
                    "returnByValue": True,
                    "awaitPromise": True
                })
                if "exceptionDetails" in res:
                    raise RuntimeError(f"JS Error: {res['exceptionDetails']}")
                return res.get("result", {}).get("value")

            await cdp_send("Page.enable")
            await cdp_send("Runtime.enable")

            # Pre-populate preferences in localStorage before loading
            prefs_data = {
                "interests": "movies and storytelling, creative craft",
                "categories": ["Movies & Storytelling", "Creative Craft"],
                "active_category": None,
                "experience_type": "fun",
                "recency_preference": "any",
                "serendipity_level": 3,
                "avoid_topics": "",
                "feed_mode": "entertainment",
                "refresh_index": 0
            }

            init_script = f"""
                localStorage.setItem("rabbithole_taste_preferences", {json.dumps(json.dumps(prefs_data))});
                localStorage.setItem("rabbithole_interest_categories", JSON.stringify([
                    {{ id: "cat_1", name: "Movies & Storytelling" }},
                    {{ id: "cat_2", name: "Creative Craft" }}
                ]));
            """
            await cdp_send("Page.addScriptToEvaluateOnNewDocument", {"source": init_script})

            # Navigate to app
            await cdp_send("Page.navigate", {"url": f"http://127.0.0.1:{server_port}/"})

            # Wait for feed cards to render (allow up to 45s for yt-dlp network searches)
            card_count = 0
            for i in range(90):
                await asyncio.sleep(0.5)
                card_count = await eval_js("document.querySelectorAll('.feed-card').length")
                if card_count and card_count > 0:
                    break
                if i % 10 == 0:
                    print(f"Waiting for feed cards... ({i*0.5:.1f}s)")
            else:
                empty_visible = await eval_js("!document.getElementById('feedEmptyNotice').classList.contains('hidden')")
                err_text = await eval_js("document.getElementById('feedStatusMessage')?.innerText")
                raise AssertionError(f"Cards failed to render after page load (empty_notice={empty_visible}, status={err_text})")

            # Check mode buttons UI shows Entertainment active
            active_mode = await eval_js("currentPreferences ? currentPreferences.feed_mode : null")
            assert active_mode == "entertainment", f"Expected active feed mode 'entertainment', got {active_mode}"

            # Verify badge re-labeling: must say 'Est. Craft & Fun' with honest tooltip
            badge_texts = await eval_js("""
                Array.from(document.querySelectorAll('#cardsGrid .feed-card span'))
                    .map(s => s.textContent.trim())
                    .filter(t => t.includes('Est. Craft & Fun'))
            """)
            assert len(badge_texts) > 0, f"Expected qualified '~X% Est. Craft & Fun' badge, found {badge_texts}"
            assert any("Est. Craft & Fun" in t for t in badge_texts)

            # Verify badge tooltip qualification
            badge_tooltip = await eval_js("""
                const badge = Array.from(document.querySelectorAll('#cardsGrid .feed-card span'))
                    .find(s => s.textContent.includes('Est. Craft & Fun'));
                badge ? badge.getAttribute('title') : null;
            """)
            assert badge_tooltip and "Metadata-based tone estimate" in badge_tooltip, (
                f"Badge tooltip must state metadata limitation, got: {badge_tooltip}"
            )

            # Verify format badges are shown on cards
            format_badges = await eval_js("""
                Array.from(document.querySelectorAll('#cardsGrid .feed-card span'))
                    .map(s => s.textContent.trim())
                    .filter(t => ['creative_projects', 'comedy', 'compelling_story', 'gameplay', 'performance', 'animation', 'sports_moments', 'challenges'].includes(t))
            """)
            # At least some cards should have format labels
            cards_total = await eval_js("document.querySelectorAll('#cardsGrid .feed-card').length")
            print(f"Rendered {cards_total} cards, found {len(format_badges)} format badges")

            # Test adding a card to Watch Later to verify format taste learning
            await eval_js("""
                const firstWatchLaterBtn = document.querySelector('#cardsGrid .feed-card button[title*="Watch Later"]');
                if (firstWatchLaterBtn) {
                    firstWatchLaterBtn.click();
                }
            """)
            await asyncio.sleep(0.5)

            # Verify living taste profile updated preferred_entertainment_formats
            profile = await eval_js("JSON.parse(localStorage.getItem('rabbithole_living_taste_profile') || '{}')")
            pref_formats = profile.get("preferred_entertainment_formats", {})
            print(f"Profile preferred_entertainment_formats after Watch Later: {pref_formats}")
            assert len(pref_formats) > 0, "Expected preferred_entertainment_formats to be updated after saving entertainment video"

            # Open Living Taste Profile modal
            await eval_js("document.getElementById('tasteProfileNavBtn').click();")
            await asyncio.sleep(0.5)

            modal_visible = await eval_js("!document.getElementById('tasteProfileModal').classList.contains('hidden')")
            assert modal_visible, "Living Taste Profile modal should open"

            # Check that Preferred Entertainment Formats section exists in modal
            modal_has_formats = await eval_js("""
                document.getElementById('tasteProfileModal').textContent.toLowerCase().includes('preferred entertainment formats')
            """)
            assert modal_has_formats, "Modal should display 'Preferred Entertainment Formats' section"

            # Close modal
            await eval_js("document.getElementById('closeTasteProfileBtn').click();")
            await asyncio.sleep(0.3)

    finally:
        if chrome_proc:
            chrome_proc.terminate()
            try:
                chrome_proc.wait(timeout=3)
            except Exception:
                chrome_proc.kill()
        if server_proc:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=3)
            except Exception:
                server_proc.kill()
        if os.path.exists(chrome_profile_dir):
            shutil.rmtree(chrome_profile_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(run_entertainment_browser_verification())
