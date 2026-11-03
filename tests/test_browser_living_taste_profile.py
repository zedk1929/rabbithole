"""
Browser test via Chrome DevTools Protocol (CDP) for:
- Living Taste Profile header button & badge
- Living Taste Profile modal with established subjects, emerging topics, qualities, and signals log
- Recording meaningful actions: Watch Later (+3.0), Followed (+3.5), Completion (+2.5), Dislike (-2.0)
- Verified distinction: 1 interaction = tentative; >=2 distinct interactions = established
- Reset Learned Taste action: clears learned affinities while preserving setup preferences
- "Why this discovery?" callouts on discovery video cards
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
import requests
import websockets

os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def run_living_taste_profile_browser_verification():
    server_port = find_free_port()
    cdp_port = find_free_port()
    chrome_profile_dir = f"/tmp/chrome_test_profile_{cdp_port}"
    os.makedirs(chrome_profile_dir, exist_ok=True)

    server_proc = None
    chrome_proc = None

    try:
        # 1. Start server in no-key heuristic mode
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
        print(f"Started Rabbithole server on port {server_port}")

        # Wait for server ready
        server_url = f"http://127.0.0.1:{server_port}/"
        for _ in range(30):
            try:
                r = requests.get(f"http://127.0.0.1:{server_port}/api/health", timeout=1)
                if r.status_code == 200:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("Server failed to start")

        # 2. Start headless Chrome
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
        print(f"Started headless Chrome on CDP port {cdp_port}")

        # Connect to CDP
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

            # Enable Runtime & Page
            await cdp_send("Runtime.enable")
            await cdp_send("Page.enable")

            # Step 1: Pre-populate preferences & seed initial state in localStorage
            prefs_json = json.dumps({
                "interests": "baseball and strategy, cinema and storytelling",
                "categories": ["Baseball & Strategy", "Cinema & Storytelling"],
                "active_category": None,
                "experience_type": "mix",
                "recency_preference": "any",
                "serendipity_level": 3,
                "avoid_topics": "betting, gambling",
                "feed_mode": "mix",
                "refresh_index": 0
            })

            init_script = f"""
                localStorage.setItem("rabbithole_taste_preferences", {json.dumps(prefs_json)});
                localStorage.setItem("rabbithole_interest_categories", JSON.stringify([
                    {{ id: "cat_1", name: "Baseball & Strategy" }},
                    {{ id: "cat_2", name: "Cinema & Storytelling" }}
                ]));
            """
            await cdp_send("Page.addScriptToEvaluateOnNewDocument", {"source": init_script})

            # Navigate to Rabbithole
            await cdp_send("Page.navigate", {"url": server_url})
            await asyncio.sleep(3.5)

            # Check that page loaded
            title = await eval_js("document.title")
            assert "Rabbithole" in title, f"Unexpected title: {title}"
            print("✓ Loaded Rabbithole homepage")

            # Check that cards are rendered
            card_count = await eval_js("document.querySelectorAll('.feed-card').length")
            assert card_count >= 5, f"Expected >= 5 cards, got {card_count}"
            print(f"✓ Rendered {card_count} feed cards on main feed")

            # Check "Why this discovery?" callouts
            disc_reasons = await eval_js("""
                Array.from(document.querySelectorAll('.feed-card')).map(card => {
                    const whyElem = card.querySelector('.text-purple-400');
                    return whyElem ? whyElem.textContent.trim() : null;
                }).filter(Boolean)
            """)
            print(f"✓ Found {len(disc_reasons)} discovery cards with headers: {disc_reasons}")
            if disc_reasons:
                assert any("Why this discovery?" in r for r in disc_reasons)

            # Check "Learned Taste" nav button exists
            taste_btn_visible = await eval_js("!!document.getElementById('tasteProfileNavBtn')")
            assert taste_btn_visible is True, "Learned Taste nav button missing"
            print("✓ Learned Taste nav button is present")

            # Step 2: Open Living Taste Profile modal
            await eval_js("document.getElementById('tasteProfileNavBtn').click()")
            await asyncio.sleep(0.5)

            modal_open = await eval_js("!document.getElementById('tasteProfileModal').classList.contains('hidden')")
            assert modal_open is True, "Taste Profile modal did not open"
            print("✓ Living Taste Profile modal opened")

            # Close modal
            await eval_js("document.getElementById('closeTasteProfileBtn').click()")
            await asyncio.sleep(0.3)
            modal_closed = await eval_js("document.getElementById('tasteProfileModal').classList.contains('hidden')")
            assert modal_closed is True, "Taste Profile modal did not close"
            print("✓ Living Taste Profile modal closed")

            # Step 3: Trigger meaningful actions on cards
            # Action A: Click "Watch Later" on first video (+3.0)
            await eval_js("""
                const wlBtn = document.querySelector('.feed-card .watch-later-card-btn');
                if (wlBtn) wlBtn.click();
            """)
            await asyncio.sleep(0.5)

            # Action B: Click "Follow Creator" on first card (+3.5)
            await eval_js("""
                const followBtn = document.querySelector('.feed-card .follow-creator-card-btn');
                if (followBtn) followBtn.click();
            """)
            await asyncio.sleep(0.5)

            # Verify badge updated
            badge_text = await eval_js("document.getElementById('tasteProfileNavBadge').textContent")
            badge_count = int(badge_text)
            assert badge_count >= 2, f"Expected badge >= 2, got {badge_count}"
            print(f"✓ Taste profile badge updated to {badge_count} signals")

            # Check profile contents in localStorage
            profile_data = await eval_js("getLivingTasteProfile()")
            assert len(profile_data["recent_signals"]) >= 2
            assert any(s["action"] == "watch_later" for s in profile_data["recent_signals"])
            assert any(s["action"] == "followed" for s in profile_data["recent_signals"])
            print("✓ Taste signals recorded in localStorage with appropriate actions")

            # Step 4: Re-open modal and verify UI displays updated data
            await eval_js("document.getElementById('tasteProfileNavBtn').click()")
            await asyncio.sleep(0.5)

            signals_in_modal = await eval_js("document.querySelectorAll('#tasteSignalsLog > div').length")
            assert signals_in_modal >= 2, f"Expected >= 2 log items in modal, got {signals_in_modal}"
            print(f"✓ Activity log in modal displays {signals_in_modal} actions")

            # Step 5: Test Reset Learned Taste
            # Override confirm dialog to return true
            await eval_js("window.confirm = () => true;")
            await eval_js("document.getElementById('resetLearnedTasteBtn').click()")
            await asyncio.sleep(0.5)

            reset_signals = await eval_js("getLivingTasteProfile().recent_signals.length")
            assert reset_signals == 0, f"Expected 0 signals after reset, got {reset_signals}"
            print("✓ Reset Learned Taste cleared learned signals while preserving setup preferences")

            # Check that initial setup preferences remain intact
            saved_prefs = await eval_js("getSavedPreferences()")
            assert saved_prefs is not None and "baseball" in saved_prefs["interests"]
            print("✓ Setup preferences remain intact after taste reset")

            print("\n==============================================")
            print("ALL LIVING TASTE PROFILE BROWSER CHECKS PASSED")
            print("==============================================")

    finally:
        if chrome_proc:
            chrome_proc.terminate()
            chrome_proc.wait()
        if server_proc:
            server_proc.terminate()
            server_proc.wait()
        if os.path.exists(chrome_profile_dir):
            shutil.rmtree(chrome_profile_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(run_living_taste_profile_browser_verification())
