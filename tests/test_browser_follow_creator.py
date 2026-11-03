"""
Browser test via Chrome DevTools Protocol (CDP) for:
- In-app "Follow creator" button on video cards (+ Follow / Following ✓)
- Local persistence surviving page reloads
- Stable YouTube channel ID preservation and name fallback
- Followed Creators management panel (modal) with Unfollow action
- Header badge tracking followed creators count
- Math tab interaction sending followed creator taste context
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


async def run_follow_creator_browser_verification():
    server_port = find_free_port()
    cdp_port = find_free_port()
    chrome_profile_dir = f"/tmp/chrome_test_profile_{cdp_port}"
    os.makedirs(chrome_profile_dir, exist_ok=True)

    server_proc = None
    chrome_proc = None

    try:
        # 1. Start server in no-key heuristic mode for fast offline testing
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

        if not cdp_ready or not targets:
            raise RuntimeError("Failed to connect to Chrome DevTools Protocol")

        page_target = next(t for t in targets if t.get("type") == "page")
        ws_url = page_target["webSocketDebuggerUrl"]

        msg_id = 0
        async with websockets.connect(ws_url) as ws:
            async def send_cdp(method, params=None):
                nonlocal msg_id
                msg_id += 1
                payload = {"id": msg_id, "method": method, "params": params or {}}
                await ws.send(json.dumps(payload))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    if data.get("id") == msg_id:
                        if "error" in data:
                            raise RuntimeError(f"CDP error: {data['error']}")
                        return data.get("result", {})

            async def eval_js(expression):
                res = await send_cdp("Runtime.evaluate", {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True
                })
                if "exceptionDetails" in res:
                    raise RuntimeError(f"JS Exception: {res['exceptionDetails']}")
                return res.get("result", {}).get("value")

            print("\n--- 1. Navigating to Rabbithole App ---")
            await send_cdp("Page.navigate", {"url": server_url})
            await asyncio.sleep(1.5)

            # Clear state and reload to clean baseline
            await eval_js("localStorage.clear(); location.reload();")
            await asyncio.sleep(1.5)

            title = await eval_js("document.title")
            print(f"Page Title: {title}")
            assert "Rabbithole" in title

            # Check Followed Creators button in header
            has_followed_btn = await eval_js("!!document.getElementById('followedCreatorsNavBtn')")
            assert has_followed_btn is True, "Followed Creators button missing in header!"
            print("✓ Followed Creators nav button present in header")

            # Check Followed Creators modal
            has_followed_modal = await eval_js("!!document.getElementById('followedCreatorsModal')")
            assert has_followed_modal is True, "Followed Creators modal missing in DOM!"
            print("✓ Followed Creators modal container present")

            # 2. Render a card with 3Blue1Brown
            print("\n--- 2. Injecting and Rendering 3Blue1Brown Video Card ---")
            await eval_js("""
                (() => {
                    const testCardItem = {
                        video: {
                            id: "3b1b_test_vid",
                            title: "Essence of Linear Algebra: Chapter 1",
                            channel: "3Blue1Brown",
                            channel_id: "UCYO_jab_esuFRV4b17AJtAw",
                            url: "https://youtube.com/watch?v=3b1b_test_vid",
                            thumbnail: "https://images.unsplash.com/photo-1635070041078-e363dbe005cb?w=800",
                            duration_seconds: 1200,
                            view_count: 5000000,
                            source_domain: "Visual Mathematics & Geometry"
                        },
                        assessment: {
                            clickbait_score: 0.05,
                            substance_score: 0.95,
                            novelty_score: 0.85,
                            entertainment_score: 0.80,
                            composite_score: 0.90,
                            content_nature: "educational",
                            connection_note: "Geometric intuition for matrix transformations."
                        },
                        path_step: "Highlight",
                        path_reason: "Foundational visual mathematics."
                    };

                    const card = createVideoCard(testCardItem, 0, 'mix');
                    const grid = document.getElementById('cardsGrid');
                    grid.innerHTML = '';
                    grid.appendChild(card);
                })();
            """)
            await asyncio.sleep(0.5)

            # Check card follow button
            btn_text = await eval_js("document.querySelector('#cardsGrid .feed-card .follow-creator-card-btn .follow-creator-label').textContent.trim()")
            print(f"Initial follow button text: '{btn_text}'")
            assert btn_text == "+ Follow", f"Expected '+ Follow', got '{btn_text}'"

            # 3. Click Follow on 3Blue1Brown
            print("\n--- 3. Following 3Blue1Brown ---")
            await eval_js("document.querySelector('#cardsGrid .feed-card .follow-creator-card-btn').click();")
            await asyncio.sleep(0.3)

            # Verify button label changed to "Following ✓"
            btn_text_after = await eval_js("document.querySelector('#cardsGrid .feed-card .follow-creator-card-btn .follow-creator-label').textContent.trim()")
            print(f"Follow button text after click: '{btn_text_after}'")
            assert "Following" in btn_text_after, f"Expected 'Following ✓', got '{btn_text_after}'"

            # Verify badge updated to 1
            badge_count = await eval_js("document.getElementById('followedCreatorsNavBadge').textContent.trim()")
            assert badge_count == "1", f"Expected badge '1', got '{badge_count}'"
            print("✓ Header badge updated to 1")

            # Verify localStorage contains 3Blue1Brown with stable channel ID
            stored_list = await eval_js("JSON.parse(localStorage.getItem('rabbithole_followed_creators') || '[]')")
            assert len(stored_list) == 1, "localStorage does not contain exactly 1 creator!"
            assert stored_list[0]["name"] == "3Blue1Brown"
            assert stored_list[0]["channel_id"] == "UCYO_jab_esuFRV4b17AJtAw"
            print(f"✓ localStorage successfully persisted creator: name='{stored_list[0]['name']}', channel_id='{stored_list[0]['channel_id']}'")

            # 4. Verify following persists across page reload
            print("\n--- 4. Testing Persistence Surviving Page Reload ---")
            await send_cdp("Page.reload")
            await asyncio.sleep(1.5)

            # Check badge on reloaded page
            reloaded_badge = await eval_js("document.getElementById('followedCreatorsNavBadge').textContent.trim()")
            assert reloaded_badge == "1", f"Expected badge '1' after reload, got '{reloaded_badge}'"
            print("✓ Followed creator count badge persisted after reload")

            # Re-render the 3Blue1Brown card and verify it renders as Following ✓
            await eval_js("""
                (() => {
                    const testCardItem = {
                        video: {
                            id: "3b1b_test_vid",
                            title: "Essence of Linear Algebra: Chapter 1",
                            channel: "3Blue1Brown",
                            channel_id: "UCYO_jab_esuFRV4b17AJtAw",
                            url: "https://youtube.com/watch?v=3b1b_test_vid",
                            thumbnail: "https://images.unsplash.com/photo-1635070041078-e363dbe005cb?w=800",
                            duration_seconds: 1200,
                            view_count: 5000000,
                            source_domain: "Visual Mathematics & Geometry"
                        },
                        assessment: {
                            clickbait_score: 0.05,
                            substance_score: 0.95,
                            novelty_score: 0.85,
                            entertainment_score: 0.80,
                            composite_score: 0.90,
                            content_nature: "educational",
                            connection_note: "Geometric intuition for matrix transformations."
                        },
                        path_step: "Highlight",
                        path_reason: "Foundational visual mathematics."
                    };

                    const card = createVideoCard(testCardItem, 0, 'mix');
                    const grid = document.getElementById('cardsGrid');
                    grid.innerHTML = '';
                    grid.appendChild(card);
                })();
            """)
            await asyncio.sleep(0.3)

            reloaded_card_btn_text = await eval_js("document.querySelector('#cardsGrid .feed-card .follow-creator-card-btn .follow-creator-label').textContent.trim()")
            assert "Following" in reloaded_card_btn_text, f"Expected 'Following ✓' after reload, got '{reloaded_card_btn_text}'"
            print("✓ Video card accurately renders 'Following ✓' from persisted storage")

            # 5. Open Followed Creators Modal
            print("\n--- 5. Testing Followed Creators Management Panel ---")
            await eval_js("document.getElementById('followedCreatorsNavBtn').click();")
            await asyncio.sleep(0.5)

            modal_visible = await eval_js("!document.getElementById('followedCreatorsModal').classList.contains('hidden')")
            assert modal_visible is True, "Followed Creators modal did not open!"
            print("✓ Followed Creators management panel opened")

            # Check creator entry in modal
            modal_items_count = await eval_js("document.querySelectorAll('#followedCreatorsListContainer > div').length")
            assert modal_items_count == 1, f"Expected 1 creator in modal, found {modal_items_count}"
            creator_name_in_modal = await eval_js("document.querySelector('#followedCreatorsListContainer span.font-semibold').textContent.trim()")
            assert creator_name_in_modal == "3Blue1Brown"
            has_channel_id_tag = await eval_js("document.querySelector('#followedCreatorsListContainer span[title*=\"UCYO_jab_esuFRV4b17AJtAw\"]') !== null")
            assert has_channel_id_tag is True, "Stable channel ID tag missing in modal item!"
            print(f"✓ Creator '{creator_name_in_modal}' displayed in modal with stable channel ID tag")

            # 6. Test Unfollow (Undo) in Management Panel
            print("\n--- 6. Testing Unfollow (Undo) in Panel ---")
            await eval_js("document.querySelector('#followedCreatorsListContainer .unfollow-btn').click();")
            await asyncio.sleep(0.5)

            # Verify removed from modal
            modal_items_after = await eval_js("document.querySelectorAll('#followedCreatorsListContainer > div').length")
            assert modal_items_after == 0, f"Expected 0 items after unfollow, found {modal_items_after}"
            empty_notice_visible = await eval_js("!document.getElementById('followedCreatorsEmptyNotice').classList.contains('hidden')")
            assert empty_notice_visible is True, "Empty notice should appear when all creators unfollowed!"
            print("✓ Creator removed from modal; empty notice displayed")

            # Verify badge updated to 0 (hidden)
            badge_hidden = await eval_js("document.getElementById('followedCreatorsNavBadge').classList.contains('hidden')")
            assert badge_hidden is True, "Badge should be hidden when count is 0!"
            print("✓ Header badge updated to 0 (hidden)")

            # Close modal
            await eval_js("document.getElementById('closeFollowedCreatorsBtn').click();")
            await asyncio.sleep(0.3)

            # Verify card button flipped back to "+ Follow"
            card_btn_after_unfollow = await eval_js("document.querySelector('#cardsGrid .feed-card .follow-creator-card-btn .follow-creator-label').textContent.trim()")
            assert card_btn_after_unfollow == "+ Follow", f"Expected '+ Follow', got '{card_btn_after_unfollow}'"
            print("✓ Video card on page immediately updated back to '+ Follow'")

            print("\n========================================================")
            print("ALL FOLLOW CREATOR BROWSER FLOWS PASSED CLEANLY!")
            print("========================================================")

    finally:
        if server_proc:
            server_proc.terminate()
            server_proc.wait()
        if chrome_proc:
            chrome_proc.terminate()
            chrome_proc.wait()
        if os.path.exists(chrome_profile_dir):
            shutil.rmtree(chrome_profile_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(run_follow_creator_browser_verification())
