"""
Simple Baseline Strategy for Rabbithole Viewing Path Evaluation.
Orders eligible candidate videos by their raw search result order.
"""
import re
from typing import List, Tuple, Set
from rabbithole_app.models import CandidateVideo, UserPreferences
from rabbithole_app.filter_engine import FilterEngine


def is_candidate_eligible(
    candidate: CandidateVideo,
    preferences: UserPreferences,
    filter_engine: FilterEngine
) -> Tuple[bool, str]:
    """
    Determines if a candidate video meets basic eligibility criteria:
    1. Passes Tier 1 heuristics (duration, all-caps ratio, sensationalist regex threshold).
    2. Does not match any explicit avoided topics.
    """
    passed, score, reason = filter_engine.run_tier1_heuristics(candidate, preferences)
    if not passed:
        return False, reason

    # Assess candidate through Relevance Gate
    rel = filter_engine.relevance_gate.assess_candidate(candidate, preferences)
    if not rel.is_relevant:
        return False, f"Relevance gate rejected: {rel.reason}"

    # Check avoided topics (case-insensitive substring check)
    avoided = [part.strip().lower() for part in re.split(r"[,;\n]", preferences.avoid_topics) if part.strip()]
    text = f"{candidate.title} {candidate.description or ''}".lower()
    for topic in avoided:
        if topic in text:
            return False, f"Matches avoided topic: '{topic}'"

    return True, "Eligible"


def compute_search_order_baseline(
    candidates: List[CandidateVideo],
    preferences: UserPreferences,
    filter_engine: FilterEngine,
    length: int = 5
) -> List[dict]:
    """
    Builds a baseline viewing path by selecting eligible candidates in their
    raw search result order.
    """
    eligible_items: List[dict] = []
    seen_ids: Set[str] = set()

    for idx, candidate in enumerate(candidates):
        if candidate.id in seen_ids:
            continue

        eligible, reason = is_candidate_eligible(candidate, preferences, filter_engine)
        if eligible:
            seen_ids.add(candidate.id)
            eligible_items.append({
                "baseline_rank": len(eligible_items) + 1,
                "original_search_index": idx,
                "video_id": candidate.id,
                "title": candidate.title,
                "channel": candidate.channel,
                "duration_seconds": candidate.duration_seconds,
                "duration_formatted": candidate.duration_formatted,
                "url": candidate.url,
                "source_query": candidate.source_query,
                "source_query_type": candidate.source_query_type,
                "description_preview": (candidate.description or "")[:200],
            })
            if len(eligible_items) >= length:
                break

    return eligible_items
