"""
YouTube Account Integrator & Taste Profiler.

Supports importing and syncing YouTube account data:
1. Local browser session cookies via yt-dlp (Chrome, Brave, Firefox, Safari, Edge)
2. Google Takeout subscriptions.csv or watch-history.json
3. Plaintext channel lists / URLs

Analyzes subscriptions and viewing patterns with Gemini to infer the user's
Curiosity Archetypes, Intellectual Vibes, Followed Creators, and Living Taste Profile,
enabling Rabbithole to break outside of traditional keyword boxes.
"""
import os
import re
import csv
import json
import logging
from io import StringIO
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

import yt_dlp
from rabbithole_app.models import (
    UserPreferences,
    FollowedCreator,
    LivingTasteProfile,
    CURIOSITY_ARCHETYPES,
)

logger = logging.getLogger(__name__)


def parse_subscriptions_file(file_content: str, filename: str = "") -> List[Dict[str, str]]:
    """
    Parses subscriptions from:
    - Google Takeout 'subscriptions.csv' (Channel Id, Channel Url, Channel Title)
    - JSON array of channels
    - Plaintext lines of channel names or URLs
    """
    channels: List[Dict[str, str]] = []
    text = file_content.strip()
    if not text:
        return []

    # 1. Try JSON
    if text.startswith("[") or text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        title = item.get("snippet", {}).get("title") or item.get("title") or item.get("name")
                        cid = item.get("id") or item.get("channelId") or item.get("channel_id")
                        url = item.get("url") or (f"https://youtube.com/channel/{cid}" if cid else None)
                        if title:
                            channels.append({"name": str(title).strip(), "channel_id": str(cid or ""), "url": str(url or "")})
                    elif isinstance(item, str) and item.strip():
                        channels.append({"name": item.strip(), "channel_id": "", "url": ""})
                if channels:
                    return channels
            elif isinstance(data, dict):
                items = data.get("items") or data.get("subscriptions") or []
                for item in items:
                    title = item.get("snippet", {}).get("title") or item.get("title") or item.get("name")
                    cid = item.get("id") or item.get("channelId") or item.get("channel_id")
                    if title:
                        channels.append({"name": str(title).strip(), "channel_id": str(cid or ""), "url": ""})
                if channels:
                    return channels
        except Exception as e:
            logger.debug(f"JSON subscriptions parse error: {e}")

    # 1b. Try Netscape HTTP Cookie File (e.g. exported via browser extension)
    if "# Netscape HTTP Cookie File" in text or "# HTTP Cookie File" in text:
        import tempfile
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
                tf.write(text)
                tf_path = tf.name
            ydl_cookie_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
                "cookiefile": tf_path,
                "socket_timeout": 8,
            }
            with yt_dlp.YoutubeDL(ydl_cookie_opts) as ydl:
                res = ydl.extract_info("https://www.youtube.com/feed/channels", download=False)
                if res and res.get("entries"):
                    for entry in res["entries"][:100]:
                        if not entry:
                            continue
                        name = entry.get("title") or entry.get("uploader") or entry.get("channel")
                        cid = entry.get("id") or entry.get("channel_id") or entry.get("uploader_id")
                        if name:
                            channels.append({"name": str(name).strip(), "channel_id": str(cid or ""), "url": ""})
            try:
                os.unlink(tf_path)
            except Exception:
                pass
            if channels:
                return channels
        except Exception as e:
            logger.debug(f"Netscape cookies extraction error: {e}")

    # 2. Try CSV (Google Takeout subscriptions.csv format)
    try:
        reader = csv.reader(StringIO(text))
        rows = list(reader)
        if len(rows) > 1:
            header = [h.strip().lower() for h in rows[0]]
            title_idx = -1
            id_idx = -1
            url_idx = -1

            for i, h in enumerate(header):
                if "title" in h or "name" in h:
                    title_idx = i
                elif "id" in h:
                    id_idx = i
                elif "url" in h:
                    url_idx = i

            if title_idx >= 0:
                for row in rows[1:]:
                    if len(row) > title_idx and row[title_idx].strip():
                        name = row[title_idx].strip()
                        cid = row[id_idx].strip() if id_idx >= 0 and len(row) > id_idx else ""
                        url = row[url_idx].strip() if url_idx >= 0 and len(row) > url_idx else ""
                        channels.append({"name": name, "channel_id": cid, "url": url})
                if channels:
                    return channels
    except Exception as e:
        logger.debug(f"CSV subscriptions parse error: {e}")

    # 3. Plaintext fallback (one channel per line, comma-separated, or pasted from youtube.com/feed/channels)
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    noise_keywords = ["subscriber", "subscribed", "videos", "notification", "•", "manage", "subscriptions"]
    seen_names = set()
    for line in lines:
        if line.startswith("#") or line.startswith("//"):
            continue
        # Skip UI noise from YouTube page copy (e.g. '@handle • 2M subscribers • 150 videos')
        lower_line = line.lower()
        if any(noise in lower_line for noise in noise_keywords) and len(line.split()) > 1:
            continue
        if lower_line in {"subscribed", "subscribe", "notifications", "manage", "channels", "home", "explore"}:
            continue

        # Split by comma if single line with commas
        if "," in line and len(lines) == 1:
            for part in line.split(","):
                p_clean = part.strip().lstrip("@")
                if p_clean and p_clean.lower() not in seen_names:
                    channels.append({"name": p_clean, "channel_id": "", "url": ""})
                    seen_names.add(p_clean.lower())
        else:
            clean = re.sub(r"^https?://(?:www\.)?youtube\.com/(?:c/|channel/|@)?", "", line).strip()
            clean = clean.lstrip("@").strip()
            if clean and clean.lower() not in seen_names and len(clean) >= 2:
                channels.append({"name": clean, "channel_id": "", "url": line if line.startswith("http") else ""})
                seen_names.add(clean.lower())

    return channels


def fetch_browser_youtube_data(browser: str = "chrome", limit: int = 50) -> Dict[str, Any]:
    """
    Extracts subscriptions and recent recommended videos from local browser cookies using yt-dlp.
    Supports 'chrome', 'brave', 'safari', 'firefox', 'edge'.
    """
    browser_clean = browser.lower().strip()
    valid_browsers = {"chrome", "brave", "safari", "firefox", "edge"}
    if browser_clean not in valid_browsers:
        return {
            "success": False,
            "error": f"Unsupported or unavailable browser: '{browser}'. Supported browsers are: {', '.join(sorted(valid_browsers))}.",
            "is_permission_error": False,
            "channels": [],
            "recommended_titles": []
        }

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "cookiesfrombrowser": (browser_clean,),
        "socket_timeout": 8,
    }

    channels: List[Dict[str, str]] = []
    recommended_titles: List[str] = []
    last_error_msg = ""

    # 1. Fetch subscribed channels
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # yt-dlp feed/channels endpoint
            res = ydl.extract_info("https://www.youtube.com/feed/channels", download=False)
            if res and res.get("entries"):
                for entry in res["entries"][:limit]:
                    if not entry:
                        continue
                    name = entry.get("title") or entry.get("uploader") or entry.get("channel")
                    cid = entry.get("id") or entry.get("channel_id") or entry.get("uploader_id")
                    url = entry.get("url") or (f"https://youtube.com/channel/{cid}" if cid else None)
                    if name:
                        channels.append({"name": name.strip(), "channel_id": str(cid or ""), "url": str(url or "")})
    except Exception as e:
        last_error_msg = str(e)
        logger.warning(f"Could not fetch subscriptions via browser cookies ({browser_clean}): {e}")

    # 2. Fetch sample home feed recommendations to understand recent viewing
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            home_res = ydl.extract_info("https://www.youtube.com", download=False)
            if home_res and home_res.get("entries"):
                for entry in home_res["entries"][:15]:
                    if entry and entry.get("title"):
                        recommended_titles.append(entry["title"].strip())
    except Exception as e:
        logger.debug(f"Home recommendations extraction note: {e}")

    if not channels and not recommended_titles:
        is_tcc = any(k in last_error_msg.lower() for k in ["cookies database", "permission", "permitted", "cookieloaderror", "filenotfounderror"])
        err_msg = (
            f"macOS System Privacy prevents reading {browser_clean.title()}'s cookie database. "
            "Please use the 'Import CSV or Channel List' tab to paste channel names or upload your Takeout file."
            if is_tcc else
            f"Could not read YouTube session from {browser_clean.title()}. Ensure you are logged into YouTube in {browser_clean.title()} or use file import."
        )
        return {
            "success": False,
            "error": err_msg,
            "is_permission_error": is_tcc,
            "channels": [],
            "recommended_titles": []
        }

    return {
        "success": True,
        "browser": browser_clean,
        "channels": channels,
        "recommended_titles": recommended_titles
    }


def synthesize_youtube_taste(
    channels: List[Dict[str, str]],
    sample_titles: List[str] = [],
    api_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    Synthesizes Curiosity Archetypes, Intellectual Vibes, and Taste Profile
    from a user's subscribed YouTube channels and sample recommendations.
    Uses Gemini when api_key is configured; falls back to heuristic pattern matching.
    """
    if not channels and not sample_titles:
        return {
            "curiosity_archetypes": ["mechanist", "visual_synthesizer"],
            "intellectual_vibes": ["first_principles", "technical_depth"],
            "curiosity_statement": "Curious explorer interested in how complex systems work from first principles.",
            "followed_creators": [],
            "latent_subjects": ["Engineering & Mechanics", "Science & Mathematics"],
            "summary": "Imported profile with baseline exploration archetypes."
        }

    normalized_channels: List[Dict[str, str]] = []
    for c in channels:
        if isinstance(c, dict):
            name = c.get("name") or c.get("title") or ""
            if name:
                normalized_channels.append({"name": str(name).strip(), "channel_id": str(c.get("channel_id", "")), "url": str(c.get("url", ""))})
        elif isinstance(c, str) and c.strip():
            normalized_channels.append({"name": c.strip(), "channel_id": "", "url": ""})

    channel_names = [c["name"] for c in normalized_channels][:60]

    # If Gemini API key is available, use LLM synthesis
    effective_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if effective_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=effective_key,
                http_options=types.HttpOptions(client_args={"trust_env": False, "timeout": 15.0})
            )

            prompt = f"""
You are the Chief Curiosity Profiler for 'Rabbithole'.
Analyze this viewer's YouTube subscriptions and recent video titles:

Subscribed Channels ({len(channel_names)}):
{', '.join(channel_names)}

Sample Watched / Recommended Titles:
{chr(10).join(f"- {t}" for t in sample_titles[:10]) if sample_titles else 'None provided'}

Recognized Curiosity Archetypes (pick 2 or 3 that best describe their cognitive drive):
- 'mechanist': The Mechanist / Tinkerer (physical logic, mechanisms, tolerances, engineering, how things work)
- 'detective': The Archival Detective (historical mysteries, forgotten disputes, technical dead ends, paper trails)
- 'visual_synthesizer': The Visual Synthesizer (geometric models, spatial animation, topological diagrams, aesthetic math)
- 'craft_purist': The Craft Purist / Flow State (artisan mastery, restoration, woodworking, zero-filler workshop process)
- 'game_theorist': The Strategic Game Theorist (hidden incentives, psychological bluffs, rule exploits, asymmetric decisions)
- 'playful_deconstructor': The Playful Deconstructor (clever satire, witty deconstructions, creative chaos, inventive builds)

Recognized Intellectual Vibes (pick 2 to 4):
- 'first_principles', 'hypnotic_flow', 'bizarre_history', 'deep_tactical', 'absurdist_wit', 'technical_depth', 'mastery_process'

Return valid JSON with the following schema:
{{
  "curiosity_archetypes": ["mechanist", "craft_purist"],
  "intellectual_vibes": ["first_principles", "mastery_process"],
  "curiosity_statement": "1-2 sentences capturing their underlying curiosity itch rather than merely naming topics.",
  "top_creators": ["TopChannel1", "TopChannel2", "TopChannel3"],
  "latent_subjects": ["Domain 1", "Domain 2", "Domain 3"],
  "explanation": "2 sentences explaining what was learned about their viewing personality and how Rabbithole will use this to explore outside their box."
}}
"""
            interaction = client.interactions.create(
                model="gemini-3.5-flash-lite",
                system_instruction="You are a brilliant cognitive profiler. Output strictly valid JSON matching the requested schema.",
                input=prompt,
                response_format={"type": "text", "mime_type": "application/json"}
            )

            raw_resp = (interaction.output_text or "").strip()
            # Clean markdown fences
            if raw_resp.startswith("```"):
                raw_resp = re.sub(r"^```(?:json)?\n?", "", raw_resp, flags=re.IGNORECASE)
                raw_resp = re.sub(r"\n?```$", "", raw_resp)

            parsed = json.loads(raw_resp)

            # Build followed creators objects
            fc_list: List[Dict[str, str]] = []
            seen_names = set()
            top_names = parsed.get("top_creators", [])
            for c in normalized_channels:
                name = c.get("name", "")
                if name and name in top_names and name not in seen_names:
                    fc_list.append({"name": name, "channel_id": c.get("channel_id", "")})
                    seen_names.add(name)

            # Fallback to top channels if top_names didn't match directly
            for c in normalized_channels[:6]:
                name = c.get("name", "")
                if name and name not in seen_names:
                    fc_list.append({"name": name, "channel_id": c.get("channel_id", "")})
                    seen_names.add(name)
                if len(fc_list) >= 5:
                    break

            return {
                "curiosity_archetypes": parsed.get("curiosity_archetypes", ["mechanist", "visual_synthesizer"]),
                "intellectual_vibes": parsed.get("intellectual_vibes", ["first_principles", "technical_depth"]),
                "curiosity_statement": parsed.get("curiosity_statement", "Curious explorer interested in physical and conceptual systems."),
                "followed_creators": fc_list,
                "latent_subjects": parsed.get("latent_subjects", ["Engineering & Mechanics", "Science & Mathematics"]),
                "summary": parsed.get("explanation", f"Analyzed {len(channels)} channels and configured personalized curiosity archetypes.")
            }
        except Exception as e:
            logger.warning(f"Gemini YouTube taste synthesis failed: {e}. Falling back to heuristic profiling.")

    # Heuristic Rule-Based Profiling Fallback
    text_corpus = " ".join(channel_names + sample_titles).lower()

    scores: Dict[str, int] = {k: 0 for k in CURIOSITY_ARCHETYPES}
    if any(w in text_corpus for w in ["engineer", "mechanic", "tech", "build", "toleran", "maker", "hardware"]):
        scores["mechanist"] += 4
    if any(w in text_corpus for w in ["history", "mystery", "documentary", "archive", "investig", "dispute", "lost"]):
        scores["detective"] += 4
    if any(w in text_corpus for w in ["math", "animat", "3blue", "visual", "physics", "geometry", "model"]):
        scores["visual_synthesizer"] += 4
    if any(w in text_corpus for w in ["wood", "craft", "restor", "knife", "japan", "hand", "forge", "artisan"]):
        scores["craft_purist"] += 4
    if any(w in text_corpus for w in ["chess", "strategy", "game", "poker", "tactical", "bluff", "esport"]):
        scores["game_theorist"] += 4
    if any(w in text_corpus for w in ["comedy", "funny", "fail", "parody", "chaotic", "unhinged", "absurd"]):
        scores["playful_deconstructor"] += 4

    top_archetypes = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)[:2]
    if scores[top_archetypes[0]] == 0:
        top_archetypes = ["mechanist", "visual_synthesizer"]

    fc_list = [{"name": c["name"], "channel_id": c.get("channel_id", "")} for c in normalized_channels[:6]]

    return {
        "curiosity_archetypes": top_archetypes,
        "intellectual_vibes": ["first_principles", "technical_depth"],
        "curiosity_statement": f"Curiosity profile aligned with {CURIOSITY_ARCHETYPES[top_archetypes[0]]['name']}.",
        "followed_creators": fc_list,
        "latent_subjects": ["Engineering & Physical Logic", "Science & Systems"],
        "summary": f"Imported {len(channels)} YouTube channels and derived your curiosity archetypes."
    }


def apply_youtube_sync_to_preferences(
    preferences: UserPreferences,
    sync_result: Dict[str, Any],
    channel_count: int = 0,
    sync_mode: str = "browser_cookies"
) -> UserPreferences:
    """
    Applies synthesized YouTube profile data to a UserPreferences instance.
    Updates archetypes, vibes, curiosity statement, followed creators, and living taste profile.
    """
    # Archetypes & statement
    curiosity_archetypes = sync_result.get("curiosity_archetypes") or preferences.curiosity_archetypes
    intellectual_vibes = sync_result.get("intellectual_vibes") or preferences.intellectual_vibes
    curiosity_statement = sync_result.get("curiosity_statement") or preferences.curiosity_statement

    # Merge followed creators
    existing_creators = list(preferences.followed_creators)
    existing_names = {fc.name.lower() for fc in existing_creators}
    for item in sync_result.get("followed_creators", []):
        name = item.get("name") if isinstance(item, dict) else str(item)
        if name and name.lower() not in existing_names:
            cid = item.get("channel_id") if isinstance(item, dict) else None
            existing_creators.append(FollowedCreator(name=name, channel_id=cid, followed_at=datetime.now(timezone.utc).isoformat()))
            existing_names.add(name.lower())

    # Seed or update living taste profile
    taste_profile = preferences.living_taste_profile or LivingTasteProfile()
    for sub in sync_result.get("latent_subjects", []):
        taste_profile.imported_subject_affinities[sub] = 2.0
        taste_profile.imported_subject_evidence_counts[sub] = 2
        taste_profile.subject_affinities[sub] = round(taste_profile.subject_affinities.get(sub, 0.0) + 2.0, 2)
        taste_profile.subject_evidence_counts[sub] = taste_profile.subject_evidence_counts.get(sub, 0) + 2

    for fc in existing_creators[:6]:
        if fc.name not in taste_profile.followed_creators:
            taste_profile.followed_creators.append(fc.name)

    taste_profile.last_updated_at = datetime.now(timezone.utc).isoformat()

    updated = preferences.model_copy(
        update={
            "interests": curiosity_statement or preferences.interests,
            "curiosity_archetypes": curiosity_archetypes,
            "intellectual_vibes": intellectual_vibes,
            "curiosity_statement": curiosity_statement,
            "followed_creators": existing_creators,
            "living_taste_profile": taste_profile,
            "youtube_connected": True,
            "youtube_synced_channel_count": channel_count,
            "youtube_sync_mode": sync_mode
        }
    )
    return updated
