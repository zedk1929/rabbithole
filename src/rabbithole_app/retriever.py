"""
Multi-Source Candidate Retriever.
Retrieves candidate videos for given curiosity queries.
Uses yt-dlp metadata extraction with proxy bypass, with optional YouTube API fallback.
"""
import os
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional, Set

# Bypass any inactive local network proxies (e.g. system proxy on macOS)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

import yt_dlp
from rabbithole_app.models import CandidateVideo, CuriosityQuery

logger = logging.getLogger(__name__)


class VideoRetriever:
    def __init__(self, youtube_api_key: Optional[str] = None):
        self.youtube_api_key = youtube_api_key or os.getenv("YOUTUBE_API_KEY")

    def retrieve_candidates(
        self,
        queries: List[CuriosityQuery],
        max_per_query: int = 5
    ) -> List[CandidateVideo]:
        """
        Dispatches all curiosity queries and aggregates unique candidate videos.
        Deduplicates by video ID.
        """
        seen_ids: Set[str] = set()
        candidates: List[CandidateVideo] = []

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "proxy": "",
            "socket_timeout": 8,
        }

        def search(q: CuriosityQuery):
            prefix = "ytsearchdate" if q.sort_by_date else "ytsearch"
            search_term = f"{prefix}{max_per_query}:{q.query}"
            try:
                # yt-dlp instances are not shared between worker threads.
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    res = ydl.extract_info(search_term, download=False)
                return (res.get("entries") or []) if res else []
            except Exception as e:
                logger.warning(f"Failed to retrieve results for query '{q.query}': {e}")
                return []

        # Keep query order for deterministic attribution and deduplication while
        # allowing the independent network searches to overlap.
        with ThreadPoolExecutor(max_workers=min(3, len(queries) or 1)) as pool:
            for q, entries in zip(queries, pool.map(search, queries)):
                for entry in entries:
                    if not entry:
                        continue
                    vid_id = entry.get("id")
                    if not vid_id or vid_id in seen_ids:
                        continue

                    # Extract best thumbnail
                    thumb = entry.get("thumbnail")
                    if not thumb and entry.get("thumbnails"):
                        thumb = entry.get("thumbnails")[-1].get("url")

                    title = entry.get("title") or "Untitled"
                    channel = entry.get("channel") or entry.get("uploader") or "Unknown Channel"
                    channel_id = entry.get("channel_id") or entry.get("uploader_id")
                    url = entry.get("url") or f"https://www.youtube.com/watch?v={vid_id}"
                    duration = entry.get("duration")
                    view_count = entry.get("view_count")
                    upload_date = entry.get("upload_date")
                    description = entry.get("description") or ""

                    candidate = CandidateVideo(
                        id=vid_id,
                        title=title,
                        channel=channel,
                        channel_id=channel_id,
                        url=url,
                        thumbnail=thumb,
                        duration_seconds=duration,
                        view_count=view_count,
                        upload_date=upload_date,
                        description=description,
                        source_query=q.query,
                        source_query_type=q.query_type,
                        source_domain=getattr(q, "domain", None),
                        source_rationale=getattr(q, "rationale", None),
                        discovery_type=getattr(q, "discovery_type", None),
                        discovery_rationale=getattr(q, "discovery_rationale", None),
                    )
                    seen_ids.add(vid_id)
                    candidates.append(candidate)
        return candidates
