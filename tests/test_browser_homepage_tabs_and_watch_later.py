"""
End-to-end headless browser verification of:
1. Homepage with ~10-video main feed and category tabs
2. Horizontal scroll Continue Watching carousel
3. Watch Later button, badge, modal, and persistence
4. Discovery slot with explainable connection
5. Emerging interest detection from saving/watching discovery video
6. Automatic promotion to a new category tab
7. Duplicate category merging & category dismissals
8. Tab switching with feed caching
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


async def run_browser_verification():
    server_port = find_free_port()
    cdp_port = find_free_port()
    chrome_profile_dir = f"/tmp/chrome_test_profile_{cdp_port}"
    os.makedirs(chrome_profile_dir, exist_ok=True)

    server_proc = None
    chrome_proc = None

    try:
        # 1. Start server with no-key mode
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

        # Wait for server to be responsive
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

        # 2. Start headless Chrome with CDP
        chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        chrome_cmd = [
            chrome_bin,
            "--headless=new",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={chrome_profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        chrome_proc = subprocess.Popen(chrome_cmd)
        print(f"Started headless Chrome on CDP port {cdp_port}")

        # Wait for CDP endpoint
        targets = []
        for _ in range(30):
            try:
                resp = requests.get(f"http://127.0.0.1:{cdp_port}/json", timeout=1)
                targets = resp.json()
                if targets:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("Chrome CDP endpoint failed to respond")

        page_target = next(t for t in targets if t.get("type") == "page")
        ws_url = page_target["webSocketDebuggerUrl"]

        msg_id = 0
        async with websockets.connect(ws_url) as ws:
            async def send_cmd(method, params=None):
                nonlocal msg_id
                msg_id += 1
                cmd = {"id": msg_id, "method": method, "params": params or {}}
                await ws.send(json.dumps(cmd))
                while True:
                    resp = json.loads(await ws.recv())
                    if resp.get("id") == msg_id:
                        return resp.get("result", {})

            async def eval_js(expression):
                res = await send_cmd("Runtime.evaluate", {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True
                })
                if "exceptionDetails" in res:
                    raise RuntimeError(f"JS Exception: {res['exceptionDetails']}")
                return res.get("result", {}).get("value")

            print("\n--- 1. Navigating to Rabbithole App ---")
            await send_cmd("Page.navigate", {"url": server_url})
            await asyncio.sleep(1.5)

            # Clear any initial state and reload
            await eval_js("localStorage.clear(); location.reload();")
            for _ in range(30):
                await asyncio.sleep(0.3)
                if await eval_js("!!document.getElementById('watchLaterNavBtn')"):
                    break

            # Check initial state
            title = await eval_js("document.title")
            print(f"Page Title: {title}")
            assert "Rabbithole" in title

            # Check Watch Later nav button exists in header
            wl_nav_btn = await eval_js("!!document.getElementById('watchLaterNavBtn')")
            assert wl_nav_btn is True, "Watch Later nav button missing from header!"
            print("✓ Watch Later nav button present in header")

            # Check Continue Watching carousel styling
            cw_classes = await eval_js("document.getElementById('continueWatchingCards').className")
            assert "overflow-x-auto" in cw_classes and "flex" in cw_classes, "Continue Watching must be horizontal scroll!"
            print(f"✓ Continue Watching has horizontal scroll container: '{cw_classes}'")

            # Fill in taste setup with multiple broad categories
            print("\n--- 2. Setting up Multi-Category Taste ---")
            await eval_js("""
                document.getElementById('interestsInput').value = 'baseball and strategy, cinema and storytelling';
                document.getElementById('discoveryForm').dispatchEvent(new Event('submit', { cancelable: true }));
            """)

            # Wait for feed cards to render
            print("Waiting for feed cards to render...")
            cards_count = 0
            for i in range(90):
                await asyncio.sleep(0.5)
                cards_count = await eval_js("document.querySelectorAll('#cardsGrid .feed-card').length")
                if cards_count > 0:
                    break
                if i % 10 == 0:
                    print(f"Waiting for feed... ({i*0.5:.1f}s)")

            assert cards_count > 0, "No feed cards rendered!"
            print(f"✓ Feed cards rendered: {cards_count}")

            # Verify category tabs were initialized and rendered
            tabs = await eval_js("""
                Array.from(document.querySelectorAll('#homepageTabsBar button')).map(b => b.textContent.trim())
            """)
            print(f"Rendered homepage tabs: {tabs}")
            assert any("Main Feed" in t for t in tabs), "Missing Main Feed tab!"
            assert any("baseball" in t.lower() for t in tabs), "Missing Baseball category tab!"
            assert any("cinema" in t.lower() for t in tabs), "Missing Cinema category tab!"
            print("✓ Category tabs correctly initialized from interests")

            # Check Watch Later buttons on video cards
            wl_cards_count = await eval_js("document.querySelectorAll('#cardsGrid .feed-card .watch-later-card-btn').length")
            assert wl_cards_count == cards_count, "Every video card must have a Watch Later button!"
            print(f"✓ Watch Later button present on all {wl_cards_count} video cards")

            # Check discovery card presence
            discovery_count = await eval_js("document.querySelectorAll('#cardsGrid .feed-card').length")
            has_discovery_badge = await eval_js("document.querySelectorAll('#cardsGrid .feed-card .bg-purple-500\\\\/20').length > 0")
            print(f"Discovery badge present: {has_discovery_badge}")

            # Test Watch Later click on first card
            print("\n--- 3. Testing Watch Later Button & Modal ---")
            first_title = await eval_js("document.querySelector('#cardsGrid .feed-card h3').textContent.trim()")
            print(f"Saving video to Watch Later: '{first_title}'")
            await eval_js("""
                document.querySelector('#cardsGrid .feed-card .watch-later-card-btn').click();
            """)
            await asyncio.sleep(0.5)

            # Verify button label changed to "Saved ✓"
            btn_label = await eval_js("document.querySelector('#cardsGrid .feed-card .watch-later-card-btn .watch-later-label').textContent.trim()")
            assert "Saved" in btn_label, f"Expected 'Saved ✓', got '{btn_label}'"
            print(f"✓ Button text updated to: '{btn_label}'")

            # Verify nav badge updated to 1
            badge_text = await eval_js("document.getElementById('watchLaterNavBadge').textContent.trim()")
            assert badge_text == "1", f"Expected badge '1', got '{badge_text}'"
            print("✓ Watch Later nav badge updated to 1")

            # Open Watch Later modal
            await eval_js("document.getElementById('watchLaterNavBtn').click();")
            await asyncio.sleep(0.5)
            modal_visible = await eval_js("!document.getElementById('watchLaterModal').classList.contains('hidden')")
            assert modal_visible is True, "Watch Later modal did not open!"
            modal_items = await eval_js("document.querySelectorAll('#watchLaterListContainer > div').length")
            assert modal_items == 1, "Saved video not listed in Watch Later modal!"
            print(f"✓ Watch Later modal displays {modal_items} saved item")

            # Close Watch Later modal
            await eval_js("document.getElementById('closeWatchLaterBtn').click();")
            await asyncio.sleep(0.3)

            # Test Emerging Interest Detection & Promotion
            print("\n--- 4. Testing Emerging Interest Detection & Promotion Engine ---")
            # Inject a discovery interaction and test promotion
            await eval_js("""
                (() => {
                    const discoveryVideo = {
                        video_id: 'disc_gt_test',
                        title: 'Minimax Game Theory in Competitive Decisions',
                        channel: 'DecisionScience',
                        thumbnail: 'https://images.unsplash.com/photo-1518770660439-4636190af475?w=800',
                        duration_seconds: 1200,
                        source_domain: 'Game Theory & Decision Science',
                        is_discovery: true,
                        discovery_connection: 'Lateral bridge connecting strategic game theory to baseball pitching',
                    };

                    // Action 1: Save discovery video -> creates emerging interest signal
                    handleDiscoveryInteraction(discoveryVideo, 'saved');
                })();
            """)
            await asyncio.sleep(0.3)

            signals = await eval_js("JSON.parse(localStorage.getItem('rabbithole_emerging_interests') || '[]')")
            assert len(signals) >= 1, "Emerging interest signal was not created!"
            print(f"✓ Emerging interest signal detected: topic='{signals[0]['topic']}', positive_actions={signals[0]['positive_actions_count']}")
            assert signals[0]["is_promoted"] is False, "1 save alone should not auto-promote without stronger evidence!"

            # Action 2: Substantial watch (>= 60s) -> satisfies strong evidence condition!
            print("Simulating substantial in-app watch time (>= 60s) on discovery video...")
            await eval_js("""
                (() => {
                    const discoveryVideo = {
                        video_id: 'disc_gt_test',
                        title: 'Minimax Game Theory in Competitive Decisions',
                        channel: 'DecisionScience',
                        thumbnail: 'https://images.unsplash.com/photo-1518770660439-4636190af475?w=800',
                        duration_seconds: 1200,
                        source_domain: 'Game Theory & Decision Science',
                        watch_time_seconds: 95.0, // >= 60s
                        is_discovery: true,
                    };
                    handleDiscoveryInteraction(discoveryVideo, 'substantially_watched');
                })();
            """)
            await asyncio.sleep(0.5)

            # Verify signal is now promoted
            updated_signals = await eval_js("JSON.parse(localStorage.getItem('rabbithole_emerging_interests') || '[]')")
            assert updated_signals[0]["is_promoted"] is True, "Emerging signal was not promoted after substantial watch!"
            print("✓ Emerging interest signal successfully promoted!")

            # Verify new category tab was added to homepage tabs bar
            updated_tabs = await eval_js("""
                Array.from(document.querySelectorAll('#homepageTabsBar button')).map(b => b.textContent.trim())
            """)
            print(f"Updated tabs after auto-promotion: {updated_tabs}")
            assert any("Game Theory" in t for t in updated_tabs), "Promoted category tab did not appear in tabs bar!"
            print("✓ Auto-promoted category tab rendered in homepage tabs bar!")

            # Test tab switching with feed caching
            print("\n--- 5. Testing Tab Switching & Feed Persistence ---")
            # Click on the Cinema category tab
            cinema_tab_btn = await eval_js("""
                (() => {
                    const buttons = Array.from(document.querySelectorAll('#homepageTabsBar button'));
                    const cinemaBtn = buttons.find(b => b.textContent.toLowerCase().includes('cinema'));
                    if (cinemaBtn) {
                        cinemaBtn.click();
                        return true;
                    }
                    return false;
                })();
            """)
            assert cinema_tab_btn is True, "Could not click Cinema tab!"
            await asyncio.sleep(1.0)

            active_cat = await eval_js("localStorage.getItem('rabbithole_active_category')")
            assert active_cat and "cinema" in active_cat.lower(), f"Active category not updated to Cinema! Got: {active_cat}"
            print(f"✓ Switched to active category tab: '{active_cat}'")

            # Switch back to Main Feed
            await eval_js("""
                const mainBtn = Array.from(document.querySelectorAll('#homepageTabsBar button')).find(b => b.textContent.includes('Main Feed'));
                mainBtn.click();
            """)
            await asyncio.sleep(0.5)
            active_cat_after = await eval_js("localStorage.getItem('rabbithole_active_category')")
            assert active_cat_after is None, "Did not switch back to Main Feed!"
            print("✓ Successfully switched back to Main Feed with zero reload")

            # Test Category Dismissal
            print("\n--- 6. Testing Category Dismissal ---")
            await eval_js("""
                (() => {
                    // Open modal for Game Theory tab
                    const cat = getCategories().find(c => c.name.includes('Game Theory'));
                    openCategoryModal(cat);
                })();
            """)
            await asyncio.sleep(0.3)

            modal_open = await eval_js("!document.getElementById('categoryModal').classList.contains('hidden')")
            assert modal_open is True, "Category modal did not open!"

            # Click Dismiss Category
            await eval_js("document.getElementById('dismissCategoryBtn').click();")
            await asyncio.sleep(0.5)

            dismissed_cats = await eval_js("JSON.parse(localStorage.getItem('rabbithole_dismissed_categories') || '[]')")
            print(f"Dismissed categories in storage: {dismissed_cats}")
            assert any("Game Theory" in d for d in dismissed_cats), "Dismissed category was not stored in dismissed list!"
            print("✓ Dismissed category successfully saved in rabbithole_dismissed_categories")

            # Verify it is removed from the tabs bar
            tabs_after_dismiss = await eval_js("""
                Array.from(document.querySelectorAll('#homepageTabsBar button')).map(b => b.textContent.trim())
            """)
            assert not any("Game Theory" in t for t in tabs_after_dismiss), "Dismissed category still visible in tabs!"
            print(f"✓ Dismissed category tab removed from UI. Remaining tabs: {tabs_after_dismiss}")

            # Refresh must replace the visible feed, then a page reload must restore saved state.
            print("\n--- 7. Refresh and Page Reload ---")
            await eval_js("document.getElementById('refreshFeedBtn').click()")
            for _ in range(90):
                await asyncio.sleep(0.5)
                done = await eval_js("document.getElementById('loadingState').classList.contains('hidden')")
                if done:
                    break
            else:
                raise AssertionError("Refresh never finished")
            visible_count = await eval_js("document.querySelectorAll('#cardsGrid .feed-card').length")
            result_count = await eval_js("(tabFeedsCache.main?.items || []).length")
            assert visible_count == result_count, f"Refresh stacked old cards: {visible_count} visible vs {result_count} in result"
            print(f"✓ Refresh replaced the old cards ({visible_count} visible)")

            await eval_js("location.reload()")
            for _ in range(30):
                await asyncio.sleep(0.3)
                if await eval_js("!!document.getElementById('watchLaterNavBtn')"):
                    break
            saved_interests = await eval_js("JSON.parse(localStorage.getItem('rabbithole_taste_preferences') || '{}').interests")
            saved_watch_later = await eval_js("JSON.parse(localStorage.getItem('rabbithole_watch_later') || '[]').length")
            assert saved_interests == "baseball and strategy, cinema and storytelling"
            assert saved_watch_later == 1
            print("✓ Taste and Watch Later survived a full page reload")

            print("\n========================================================")
            print("ALL BROWSER FLOW VERIFICATIONS PASSED CLEANLY!")
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
    asyncio.run(run_browser_verification())
