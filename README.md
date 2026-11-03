# Rabbithole 🐇

> **A non-predatory, anti-clickbait discovery engine designed to expand your intellectual horizons.**

Traditional media algorithms maximize click-through rate, emotional agitation, and watch time—trapping you in superficial content loops. **Rabbithole** inverts this: it optimizes for **intellectual substance, conceptual depth, and serendipitous discovery**.

---

## Key Features

1. **Refreshable Discovery Homepage & 3-Question Taste Setup**:
   - **Short Setup (max 3 questions)**: Asks what subjects you tend to enjoy, what kind of experience you want (Balanced Mix, Pure Entertainment, Thoughtful Deep Dive, or Surprising Leaps), and your era preference (Anytime, Recent uploads, or Older archival finds), with the Serendipity Dial (1 to 5).
   - **Local Persistence & Quick Controls**: Saves your preferences in `localStorage` so you don't repeat setup on every visit. A compact Taste Bar lets you edit or reset your preferences anytime.
   - **Prominent Refresh Feed Button**: Searches for genuinely different candidates, rotating search angles across consecutive refreshes while strictly excluding previously seen videos.
   - **Seen History & Deduplication**: Tracks shown video IDs locally across reloads and provides a one-click "Clear History" action. When the available pool is small or exhausted, Rabbithole honestly reports it rather than silently recycling duplicates.
2. **Substantive Content & Pure Entertainment Modes**:
   - **Substantive Does Not Mean Lecture**: The default feed (`mix` mode) balances worthwhile educational videos with well-crafted, funny, or creative entertainment matching the user's taste.
   - **Entertainment Only Mode**: A simple choice (`feed_mode="entertainment"`) that curates creative craft, comedic commentary, animation, and storytelling without forcing candidates into academic 5-step learning paths.
   - **Deep Learning Path Mode**: Retains the 5-step progressive exploration (`Start`, `Deepen`, `Bridge`, `Challenge`, `Continue`) for intensive study sessions.
3. **Deterministic & LLM Query Expansion**:
   - **No-Key Heuristic Mode**: Uses a multi-domain curiosity matrix across diverse subjects (e.g. sports/strategy, cinema/storytelling, gardening/cooking, acoustics, pyrotechnology, tilings, fluids, and cognition) to deterministically generate 5 distinct viewing path angles (`first_principles`, `archive_lecture`, `cross_domain_analogy`, `niche_history`, `practitioner_deep_dive`, `entertainment_comedy`, `entertainment_creative`, `entertainment_story`).
   - **Grounded Cross-Domain Bridges**: Every bridge retains a concrete, explainable connection to the user's interest (e.g., pitch sequencing to game theory, film editing to visual psychophysics, garden produce to microbial fermentation). When no distant bridge is credible, the system stays close to the interest and labels it honestly.
   - **Strict Avoidance**: Keywords matching avoided topics are automatically pruned from all generated queries and path selections.
   - **Gemini Mode (Optional)**: When configured, calls Gemini to generate creative lateral branches from broad tastes without needing user-supplied exploration fields.
4. **Anti-Sensationalism Gatekeeper**:
   - **Tier 1 Heuristic Filtering**: Instantly discards YouTube Shorts (`< 6m`), all-caps titles, emotional trigger words (`SHOCKING`, `RUINED`, `YOU WON'T BELIEVE`), and shallow listicles.
   - **Tier 2 Substance Judge**: Evaluates intellectual substance, craft, entertainment value, and novelty from available metadata (Gemini when configured, high-signal heuristics otherwise).
5. **Serendipity Dial (1 to 5)**:
   - `1 (Deep Dive)`: Focused, rigorous analysis of the exact topic.
   - `3 (Balanced)`: Direct topic plus lateral adjacent subfields.
   - `5 (Radical Serendipity)`: High-entropy, cross-disciplinary conceptual leaps.
6. **"Why Recommended" Grounded Explainability**:
   - Every card shows a short, honest metadata-grounded reason for its selection without claiming to have watched full videos when only metadata was available.
7. **Real Viewing History & Resume Playback**:
   - **YouTube IFrame Player API Tracking**: Tracks playback position, duration, and actual wall-clock time spent playing inside Rabbithole's embedded player (distinguishing videos merely shown from started, in-progress, and completed).
   - **Continue Watching Section**: Prominently displays in-progress videos with progress bars and "X of Y minutes" indicators, offering one-click resume at your exact timestamp.
   - **Viewing History View**: Accessible via the header to review all watched items, filter by status, resume playback, or remove individual/all entries.
   - **Modest Viewing Signals in Discovery**: Automatically avoids recommending completed videos again, provides a modest boost for creators and subjects you actually spend time watching, while keeping explicit user preferences dominant and leaving ample room for unfamiliar subjects. Quick exits (< 15s) and seeking do not trigger false signals.
   - **Local Tracking Only**: Tracks playback strictly inside Rabbithole without requiring Google sign-in or YouTube account access.
8. **Card-Level Dislike & Discovery Feedback Control**:
   - **Immediate Card Removal & Suppression**: Clicking **Dislike** on any card immediately removes it from the current feed and stores its ID so it does not reappear on refresh, reload, or in another feed mode (`mix`, `entertainment`, `deep_learning`).
   - **Undo Option**: A prominent Undo toast enables immediate restoration of accidental clicks, un-suppressing the video and placing it back into the feed at its original position.
   - **Separate Playback History**: Disliking a video strictly suppresses that recommendation without erasing or altering its recorded watch time, progress percentage, or Continue Watching status.
   - **Optional Reason Quick Choices**: Quick prompts allow optional feedback ("Not interested in this topic", "Too academic", "Too shallow", "Don’t like this format", "Bad recommendation") with an instant Skip option. Records store title, channel, recommendation context, chosen reason, and date in local storage.
   - **Review & Manage Dislikes**: A dedicated Disliked Videos modal in the header allows reviewing recorded reasons and restoring individual videos or clearing all dislikes.
   - **Feedback in Future Discovery**: Compact, factual summaries of user dislike patterns are sent to Gemini and used by no-key heuristics to temper rejected formats and styles without banning entire subjects or channels unless explicitly set in preferences.
9. **Homepage Tabs, Main Feed (~10 Videos) & Watch Later**:
   - **Main Feed with Category Variety (~10 Videos)**: The homepage draws across the user's editable interest categories, balancing educational and entertaining content according to taste preferences without allowing one interest to dominate.
   - **Dedicated Category Tabs & Independent Refreshes**: Each interest category has its own tab with a focused feed (~5 videos) and independent refresh button. Switching tabs preserves existing feeds in memory with zero page reloads or jarring flashes.
   - **Horizontal Scroll Carousel for Continue Watching**: Continue Watching uses a sleek horizontal scroll (`overflow-x-auto`) to save vertical space, leaving the full grid layout for viewing history.
   - **Watch Later Button & Storage Tab**: Every video card includes a 🔖 **Watch Later** button (`Saved ✓`). A dedicated **Watch Later** tab in the header displays saved videos with progress indicators, one-click removal, and resume actions.
10. **Curated Discovery Slots & Emerging Interest Promotion**:
   - **1–2 Main Feed Discovery Slots (`✨ Discovery`)**: Main feeds reserve 1 to 2 positions for videos from genuinely new, surprising domains connected by explainable lateral bridges to the user's taste (grounded in metadata, never arbitrary off-topic noise).
   - **Emerging Interest Detection**: Saving a discovery video registers an emerging interest signal.
   - **Strong Evidence Auto-Promotion**: Substantially watching a discovery video (`watch_time_seconds >= 60.0`) or recording multiple positive actions on related discoveries automatically promotes it into a new homepage category tab.
   - **Duplicate Merging & Weak Signal Rejection**: Closely related topics merge into existing categories rather than creating redundant tabs. Weak signals (opening and immediately closing `< 30s` or scrubbing to the end) are rejected.
   - **Tab Management & Dismissal Memory**: Users can rename, edit, or dismiss categories. Dismissed categories are remembered and prevented from immediately reappearing. Dislikes prevent unwanted videos or topics from promotion.
11. **In-App Follow Creator & Taste Context**:
   - **Card-Level Follow/Following Button**: Each video card features a `+ Follow` / `Following ✓` button next to the channel name to follow creators whose work you enjoy.
   - **Followed Creators Management Panel**: Access followed creators from the header (`👤 Followed`) to review saved channels, see stable YouTube channel IDs and follow dates, and unfollow individual or all creators.
   - **Stable Channel ID Persistence**: Uses stable YouTube channel IDs when available (`UC...`), with channel display names as fallback, strictly deduplicating entries and surviving page reloads in `localStorage`.
   - **Taste Signal in Gemini Stages**: Followed creators are sent in user preferences to both Gemini stages (query expansion and candidate evaluation) with explicit guidance:
     > *"A follow means 'I enjoy this creator’s work and want you to understand my taste.' Gemini may occasionally search for or recommend their videos, but should still seek new creators and surprising connections. Following should not guarantee a slot, bypass quality filters, or turn a category tab into a channel feed."*
   - **Zero Account Connection**: Operates entirely within Rabbithole without connecting to or altering your YouTube account or subscriptions.
   - **Offline & Heuristic Resilience**: When Gemini is unavailable, deterministic heuristics incorporate followed creators as modest taste affinity signals without overriding clickbait rejection or channel diversity constraints.
12. **Interactive Branching Trees**:
   - Click "Branch Out" on any discovered concept to jump directly into a focused sub-rabbit-hole.
13. **Distraction-Free Embedded Player**:
   - Watch selected videos without being dumped into YouTube's algorithmic recommendation sidebar.

---

## Quickstart

### 1. Run the Web Application
Run any of the following from the project directory:

```bash
./start.sh
```
or
```bash
uv run run.py
```
or
```bash
.venv/bin/python run.py
```

Then open your browser to:
👉 **`http://127.0.0.1:8000`**

### 2. Run the Automated Tests
```bash
uv run pytest
```

### 3. Run the Viewing Path Evaluation Workflow
Compare the 5-step Rabbithole viewing paths against the raw Search Order Baseline across 10 diverse curiosity profiles:

```bash
# Run with deterministic offline fixtures:
uv run python eval/run_eval.py --source fixture

# Or run live with fresh search retrieval:
uv run python eval/run_eval.py --source live
```

This generates:
- [`eval/results/rating_sheet.md`](file:///Users/lcooney/Downloads/Rabbithole%20app/eval/results/rating_sheet.md): Human-ready rating sheet (Markdown with blank scoring fields).
- [`eval/results/rating_sheet.csv`](file:///Users/lcooney/Downloads/Rabbithole%20app/eval/results/rating_sheet.csv): Human-ready rating sheet (CSV for spreadsheets).
- [`eval/results/evaluation_run.json`](file:///Users/lcooney/Downloads/Rabbithole%20app/eval/results/evaluation_run.json): Full candidate pools, app paths, baseline paths, audit execution states, and fixture provenance.
See [`eval/README.md`](file:///Users/lcooney/Downloads/Rabbithole%20app/eval/README.md) for full instructions and the 1–5 scoring rubric.

---

## Architecture Overview

```
src/rabbithole_app/
├── models.py          # Pydantic schemas for preferences, queries, and assessments
├── query_expander.py  # Lateral query generation (Gemini + multi-domain deterministic heuristics)
├── retriever.py       # Candidate video retrieval (yt-dlp)
├── filter_engine.py   # Two-tier anti-clickbait and substance evaluation engine
├── journey.py         # Ordered five-step viewing path from scored candidates
├── service.py         # End-to-end orchestration and branching logic
├── server.py          # FastAPI REST API & static file server
└── static/            # Clean distraction-free web client (HTML/Tailwind/JS)
```

---

## Current Limits & Candidate Pool Behavior

Search uses yt-dlp metadata. The current quality scores estimate substance and novelty from titles, descriptions, and duration; they do not analyze full video content. The path is a structured recommendation using search provenance and metadata, not a verified learning progression. A YouTube Data API fallback is not yet implemented.

**Candidate Pool Limitations**:
- When running in no-key heuristic mode, search queries are generated deterministically from domain ontologies.
- For narrow or specific interest combinations with high refresh counts, the retriever may exhaust unseen videos in the search results. Rather than silently recycling previously seen or disliked videos, Rabbithole detects pool exhaustion, shows all eligible unique candidates, and surfaces a clear diagnostic notification indicating the pool has been exhausted.
- Video playback tracking operates inside the embedded iframe and stores state locally in the user's browser. Claims regarding recommendation quality remain deferred until human judge evaluations are completed on the blank rating sheets.

---

## Optional: Configuring Google Gemini API Key

Rabbithole is fully functional immediately using its built-in deterministic heuristic engine. For LLM-driven query expansion and reasoning, add your Gemini API key from [Google AI Studio](https://aistudio.google.com/):

```bash
printf "Enter GEMINI_API_KEY (typing hidden): " && read -s val && echo && echo "GEMINI_API_KEY=$val" >> ~/.env && echo "Saved."
```
