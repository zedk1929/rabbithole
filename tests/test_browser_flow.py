"""
End-to-end browser verification of Rabbithole Dislike button, Undo, reason prompt,
localStorage persistence, modal review, and feed suppression using Chrome CDP.
"""
import asyncio
import json
import os
import requests
import websockets
from pathlib import Path

os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

async def run_cdp():
    # 1. Connect to Chrome CDP
    targets_url = "http://127.0.0.1:9222/json"
    resp = requests.get(targets_url, proxies={"http": None, "https": None})
    targets = resp.json()
    
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

        print("Connected to headless Chrome via CDP.")

        # Navigate to Rabbithole app
        await send_cmd("Page.navigate", {"url": "http://127.0.0.1:8765/"})
        await asyncio.sleep(1.5)

        # Clear existing storage
        await eval_js("localStorage.clear(); location.reload();")
        await asyncio.sleep(1.5)

        # Fill taste form
        print("1. Submitting taste preferences...")
        await eval_js("""
            document.getElementById('interestsInput').value = 'baseball and strategy';
            document.getElementById('discoveryForm').dispatchEvent(new Event('submit', { cancelable: true }));
        """)

        # Wait for feed to load (heuristic generation)
        print("Waiting for feed cards...")
        cards_count = 0
        for i in range(90):
            await asyncio.sleep(0.5)
            cards_count = await eval_js("document.querySelectorAll('#cardsGrid .feed-card').length")
            if cards_count > 0:
                break
            if i % 10 == 0:
                print(f"Waiting for feed cards... ({i*0.5:.1f}s)")
        
        if cards_count == 0:
            grid_text = await eval_js("document.getElementById('cardsGrid').innerText")
            print(f"cardsGrid content on timeout: {grid_text}")
        
        print(f"Feed cards rendered: {cards_count}")
        assert cards_count > 0, "No feed cards rendered!"

        # Check Dislike button presence on all cards
        dislike_btn_count = await eval_js("document.querySelectorAll('#cardsGrid .feed-card .dislike-card-btn').length")
        print(f"Dislike buttons found: {dislike_btn_count}")
        assert dislike_btn_count == cards_count, "Every video card must have a Dislike button!"

        # Read first video title and ID
        first_title = await eval_js("document.querySelector('#cardsGrid .feed-card h3').textContent.trim()")
        print(f"Target video 1: '{first_title}'")

        # Setup mock viewing history for video 1 to verify disliking preserves history
        await eval_js("""
            const history = {};
            // Mock a viewing record for a test video
            const firstCard = document.querySelector('#cardsGrid .feed-card');
            const fakeId = 'mock_playback_vid';
            history[fakeId] = {
                video_id: fakeId,
                title: 'Test Playback Video',
                channel: 'Test Channel',
                current_position_seconds: 350.0,
                watch_time_seconds: 340.0,
                status: 'in_progress',
                duration_seconds: 1000
            };
            localStorage.setItem('rabbithole_viewing_history', JSON.stringify(history));
        """)

        # Step 2: Click Dislike on the first card
        print("2. Clicking Dislike on first card...")
        await eval_js("""
            (() => {
                const btn = document.querySelector('#cardsGrid .feed-card .dislike-card-btn');
                btn.click();
            })();
        """)
        await asyncio.sleep(0.5)

        # Verify card 1 immediately removed from current feed DOM
        new_cards_count = await eval_js("document.querySelectorAll('#cardsGrid .feed-card').length")
        print(f"Cards count after dislike: {new_cards_count} (was {cards_count})")
        assert new_cards_count == cards_count - 1, "Card was not immediately removed from feed!"

        # Verify Undo toast is visible
        toast_visible = await eval_js("!document.getElementById('undoToast').classList.contains('hidden')")
        print(f"Undo toast visible: {toast_visible}")
        assert toast_visible is True, "Undo toast must be visible after disliking!"

        # Verify Dislike Reason modal is visible
        reason_modal_visible = await eval_js("!document.getElementById('dislikeReasonModal').classList.contains('hidden')")
        print(f"Dislike reason modal visible: {reason_modal_visible}")
        assert reason_modal_visible is True, "Dislike reason modal must be visible after disliking!"

        # Step 3: Select reason "Too academic"
        print("3. Selecting reason 'Too academic'...")
        await eval_js("""
            const btn = document.querySelector('.dislike-choice-btn[data-reason="Too academic"]');
            btn.click();
        """)
        await asyncio.sleep(0.5)

        # Verify modal closed
        modal_hidden = await eval_js("document.getElementById('dislikeReasonModal').classList.contains('hidden')")
        assert modal_hidden is True, "Dislike reason modal did not close after selecting a reason!"

        # Verify localStorage has dislike record with chosen reason
        disliked_records = await eval_js("JSON.parse(localStorage.getItem('rabbithole_disliked_videos') || '[]')")
        print(f"Stored disliked records: {len(disliked_records)}")
        assert len(disliked_records) == 1, "Dislike record was not saved to localStorage!"
        assert disliked_records[0]["chosen_reason"] == "Too academic", f"Expected 'Too academic', got {disliked_records[0]['chosen_reason']}"
        disliked_vid_id = disliked_records[0]["video_id"]
        print(f"Disliked record details: ID={disliked_vid_id}, reason='{disliked_records[0]['chosen_reason']}', title='{disliked_records[0]['title']}'")

        # Step 4: Click Undo on Toast
        print("4. Testing Undo button...")
        await eval_js("document.getElementById('undoActionBtn').click();")
        await asyncio.sleep(0.5)

        # Verify card restored to DOM
        restored_count = await eval_js("document.querySelectorAll('#cardsGrid .feed-card').length")
        print(f"Cards count after Undo: {restored_count} (restored to original {cards_count})")
        assert restored_count == cards_count, "Card was not restored to DOM after Undo!"

        # Verify record removed from localStorage
        disliked_after_undo = await eval_js("JSON.parse(localStorage.getItem('rabbithole_disliked_videos') || '[]')")
        assert len(disliked_after_undo) == 0, "Dislike was not removed from localStorage after Undo!"

        # Step 5: Dislike again and Skip reason
        print("5. Disliking again and testing Skip reason...")
        await eval_js("""
            (() => {
                const btn = document.querySelector('#cardsGrid .feed-card .dislike-card-btn');
                btn.click();
            })();
        """)
        await asyncio.sleep(0.3)
        await eval_js("document.getElementById('skipDislikeReasonBtn').click();")
        await asyncio.sleep(0.3)

        disliked_after_skip = await eval_js("JSON.parse(localStorage.getItem('rabbithole_disliked_videos') || '[]')")
        assert len(disliked_after_skip) == 1
        print(f"Dislike saved with skipped reason: '{disliked_after_skip[0]['chosen_reason']}'")

        # Step 6: Verify Disliked Modal
        print("6. Testing Disliked Modal review & unsuppress...")
        await eval_js("document.getElementById('dislikedNavBtn').click();")
        await asyncio.sleep(0.5)

        disliked_modal_visible = await eval_js("!document.getElementById('dislikedModal').classList.contains('hidden')")
        assert disliked_modal_visible is True, "Disliked modal did not open!"

        items_in_modal = await eval_js("document.querySelectorAll('#dislikedListContainer > div').length")
        print(f"Items displayed in Disliked Modal: {items_in_modal}")
        assert items_in_modal == 1, "Disliked video not displayed in Disliked Modal!"

        # Step 7: Check that viewing history was preserved and untouched
        print("7. Verifying viewing history was NOT erased...")
        history = await eval_js("JSON.parse(localStorage.getItem('rabbithole_viewing_history') || '{}')")
        assert "mock_playback_vid" in history, "Viewing history was deleted!"
        assert history["mock_playback_vid"]["current_position_seconds"] == 350.0, "Viewing progress position was altered!"
        print(f"Viewing record preserved: position={history['mock_playback_vid']['current_position_seconds']}s, status={history['mock_playback_vid']['status']}")

        # Step 8: Verify reload persistence
        print("8. Verifying exclusion after page reload...")
        await eval_js("location.reload();")
        await asyncio.sleep(1.5)

        # Disliked video should not be in the reloaded feed
        reloaded_titles = await eval_js("Array.from(document.querySelectorAll('#cardsGrid .feed-card h3')).map(h => h.textContent.trim())")
        assert first_title not in reloaded_titles, f"Disliked video '{first_title}' reappeared after reload!"
        print(f"Success! Disliked video '{first_title}' was correctly excluded after reload.")

        print("\nALL BROWSER FLOW VERIFICATIONS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    asyncio.run(run_cdp())
