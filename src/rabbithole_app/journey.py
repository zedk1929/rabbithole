"""Build viewing paths and discovery feeds from scored search results.

Supports three distinct discovery experiences:
1. 'mix' (default): A balanced blend of thoughtful depth and creative entertainment.
2. 'entertainment': Engaging, humorous, creative, and well-made finds without academic progression.
3. 'deep_learning': The progressive 5-step exploration path (Start, Deepen, Bridge, Challenge, Continue).
"""
import re
from datetime import datetime, timezone
from typing import List, Set, Optional, Dict, Any, Tuple

from rabbithole_app.models import FeedItem, UserPreferences
from rabbithole_app.relevance_gate import (
    _extract_tokens,
    GENERIC_QUERY_MODIFIERS,
    STOPWORDS,
    DOMAIN_TRIGGERS,
    DOMAIN_LEXICONS,
)


DEEP_LEARNING_STEPS = (
    ("Start", "first_principles", "An accessible starting point connected to your interests."),
    ("Deepen", "archive_lecture", "A deeper source to build on the starting point."),
    ("Bridge", "cross_domain_analogy", "A search path linking your interests to another field."),
    ("Challenge", "niche_history", "A historical angle that may widen the usual framing."),
    ("Continue", "practitioner_deep_dive", "A concrete direction to continue exploring."),
)
STEPS = DEEP_LEARNING_STEPS

ENTERTAINMENT_ROLES = [
    ("Creative Feature", "Selected for creative craft, visual animation, or engaging showcase."),
    ("Comedy & Wit", "Selected for humor, funny blunders, or comedic commentary."),
    ("Lore & Tales", "Selected for bizarre storytelling, strange lore, or dramatic narrative."),
    ("Artisan Craft", "Selected for satisfying craftsmanship, making, or hands-on artistry."),
    ("Wildcard Gem", "Selected as an unexpected, delightful creative tangent."),
]

MIX_ROLES = [
    ("Core Highlight", "An engaging and accessible entry into your subject."),
    ("Creative Craft", "Creative craft, visual animation, or entertaining perspective."),
    ("Deep Dive", "Thoughtful depth and substantive exploration."),
    ("Wit & Lore", "Comedic take, bizarre story, or narrative lore."),
    ("Surprise Bridge", "A lateral connection or surprising discovery tangent."),
]


def _terms(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if len(word) > 3}


_TITLE_FILLER = {
    "about", "beginners", "beginner", "explained", "explanation", "intro",
    "introduction", "learn", "learning", "really", "simple", "tutorial",
    "understanding", "video", "visual", "what", "with",
}


def _title_topic_terms(item: FeedItem) -> set[str]:
    return _terms(item.video.title) - _TITLE_FILLER


def _same_video_topic(left: FeedItem, right: FeedItem) -> bool:
    """Catch repeated primers without merging a distinct application of the topic."""
    from rabbithole_app.filter_engine import are_topics_substantially_duplicate
    return are_topics_substantially_duplicate(left, right)


def _creator_preference_boost(item: FeedItem, preferences: UserPreferences) -> float:
    return 0.20 if any(
        creator.matches(item.video.channel, item.video.channel_id)
        for creator in (preferences.followed_creators or [])
    ) else 0.0


def _topic_repetition_penalty(item: FeedItem, selected: list[FeedItem]) -> float:
    return 0.50 if any(_same_video_topic(item, prior) for prior in selected) else 0.0


def _is_same_subject(it1: FeedItem, it2: FeedItem) -> bool:
    """Checks if two feed items share the same core subject, topic, or domain."""
    if _same_video_topic(it1, it2):
        return True
    dom1 = (it1.video.source_domain or it1.category or "").strip().lower()
    dom2 = (it2.video.source_domain or it2.category or "").strip().lower()
    if dom1 and dom2 and (dom1 == dom2 or (len(dom1) > 4 and dom1 in dom2) or (len(dom2) > 4 and dom2 in dom1)):
        return True
    subj1 = (getattr(it1.assessment, "actual_subject", "") or "").strip().lower()
    subj2 = (getattr(it2.assessment, "actual_subject", "") or "").strip().lower()
    if subj1 and subj2:
        t1 = _terms(subj1)
        t2 = _terms(subj2)
        if len(t1 & t2) >= min(2, max(1, len(t1)), max(1, len(t2))):
            return True
    return False


def _is_same_creator(it1: FeedItem, it2: FeedItem) -> bool:
    """Checks if two items share the same channel or creator."""
    c1 = (it1.video.channel or "").strip().lower()
    c2 = (it2.video.channel or "").strip().lower()
    if c1 and c2 and c1 == c2:
        return True
    cid1 = getattr(it1.video, "channel_id", None)
    cid2 = getattr(it2.video, "channel_id", None)
    if cid1 and cid2 and cid1 == cid2:
        return True
    return False


def _is_same_format(it1: FeedItem, it2: FeedItem) -> bool:
    """Checks if two items share the same content or entertainment format."""
    fmt1 = getattr(it1.assessment, "content_format", None) or getattr(it1.assessment, "entertainment_format", None)
    fmt2 = getattr(it2.assessment, "content_format", None) or getattr(it2.assessment, "entertainment_format", None)
    if fmt1 and fmt2:
        f1 = fmt1.strip().lower()
        f2 = fmt2.strip().lower()
        return bool(f1 and f2 and f1 == f2)
    return False


_RESTORATION_TITLE = re.compile(
    r"\b(restor(?:ation|ations|e|ed|ing)|refurbish(?:ed|ing|ment)?|"
    r"refinish(?:ed|ing)?|renovat(?:e|ed|ing|ion))\b", re.I
)


def _is_restoration(item: FeedItem) -> bool:
    # A channel may describe the format when the title only names the object.
    return bool(_RESTORATION_TITLE.search(f"{item.video.title} {item.video.channel}"))


def _restoration_budget(preferences: UserPreferences) -> int:
    stated_taste = " ".join([
        preferences.interests, preferences.explore_topics,
        *(preferences.categories or []), *(preferences.emerging_interests or []),
    ])
    if _RESTORATION_TITLE.search(stated_taste):
        return 2
    profile = preferences.living_taste_profile
    positive_ids = {
        signal.video_id or signal.title
        for signal in (profile.recent_signals if profile else [])
        if signal.action != "disliked" and _RESTORATION_TITLE.search(signal.title or "")
    }
    return 2 if len(positive_ids) >= 2 else 1


def _within_restoration_budget(item: FeedItem, selected: list[FeedItem], budget: int) -> bool:
    return not _is_restoration(item) or sum(_is_restoration(prior) for prior in selected) < budget


def _filter_pool(items: list[FeedItem], preferences: UserPreferences) -> list[FeedItem]:
    """Excludes items matching avoided topics, completed, disliked, or (on main feed) already seen."""
    avoided = [part.strip().lower() for part in re.split(r"[,;\n]", preferences.avoid_topics) if part.strip()]
    seen_ids = set(preferences.seen_video_ids or [])
    completed_ids = set(preferences.completed_video_ids or [])
    disliked_ids = set(preferences.disliked_video_ids or [])

    pool = []
    for item in items:
        if item.video.id in disliked_ids:
            continue
        if item.video.id in completed_ids:
            continue
        if not preferences.active_category and item.video.id in set(preferences.recent_shown_video_ids):
            continue
        text = f"{item.video.title} {item.video.description or ''}".lower()
        if any(topic in text for topic in avoided):
            continue
        pool.append(item)
    return pool


def build_path(items: list[FeedItem], preferences: UserPreferences, length: int = 5) -> list[FeedItem]:
    """Choose distinct videos by step role, quality, and continuity (5-step progressive path)."""
    pool = _filter_pool(items, preferences)
    selected: list[FeedItem] = []
    used_channels: set[str] = set()
    anchor_terms = _terms(preferences.interests)
    familiar_terms = _terms(preferences.familiar_topics)
    explore_terms = _terms(preferences.explore_topics)
    goal_terms = _terms(preferences.learning_goal)

    for label, query_type, reason in DEEP_LEARNING_STEPS[:length]:
        if not pool:
            break

        def score(item: FeedItem) -> float:
            searchable = _terms(f"{item.video.title} {item.video.description or ''} {item.source_query or item.video.source_query or ''}")
            previous = _terms(selected[-1].video.title) if selected else set()
            role = 0.35 if item.video.source_query_type == query_type else 0.0
            anchor = 0.08 * min(len(searchable & anchor_terms), 2)
            familiar = 0.08 * min(len(searchable & familiar_terms), 2) if label == "Start" else 0.0
            bridge = (0.04 * preferences.serendipity_level) * min(len(searchable & explore_terms), 2) if label == "Bridge" else 0.0
            goal = 0.04 * min(len(searchable & goal_terms), 2)
            continuity = 0.07 * min(len(searchable & previous), 2)
            repeated_channel = 0.12 if item.video.channel in used_channels else 0.0
            return item.assessment.composite_score + role + anchor + familiar + bridge + goal + continuity - repeated_channel

        chosen = max(pool, key=score)
        pool.remove(chosen)
        used_channels.add(chosen.video.channel)
        chosen.path_step = label
        chosen.path_reason = reason if item_matches_role(chosen, query_type) else (
            f"Best available match for this part of the path. {reason}"
        )
        selected.append(chosen)

    return selected


def item_matches_role(item: FeedItem, expected_role: str) -> bool:
    qt = item.video.source_query_type or ""
    return qt == expected_role


def build_entertainment_feed(items: list[FeedItem], preferences: UserPreferences, length: int = 5) -> list[FeedItem]:
    """
    Assembles a pure entertainment feed matching the user's taste.
    Strictly filters for entertainment-first videos; excludes lectures, explainers, and essays.
    Prioritizes creativity, humor, storytelling, gameplay, challenges, sports moments, and performance.
    """
    raw_pool = _filter_pool(items, preferences)
    pool = [item for item in raw_pool if item.assessment.is_entertainment_first]
    selected: list[FeedItem] = []
    used_channels: set[str] = set()
    anchor_terms = _terms(preferences.interests)

    for i in range(min(length, len(pool) + len(selected))):
        if not pool:
            break

        def ent_score(item: FeedItem) -> float:
            ev = item.assessment
            searchable = _terms(f"{item.video.title} {item.video.description or ''}")
            anchor = 0.08 * min(len(searchable & anchor_terms), 2)
            channel_penalty = 0.15 if item.video.channel in used_channels else 0.0

            # Favor entertainment and novelty scores
            base = (ev.entertainment_score * 0.55) + (ev.novelty_score * 0.30) + (ev.composite_score * 0.15)
            return base + anchor - channel_penalty

        chosen = max(pool, key=ent_score)
        pool.remove(chosen)
        used_channels.add(chosen.video.channel)

        fmt = chosen.assessment.entertainment_format
        fmt_labels = {
            "comedy": ("Comedy & Wit", "Selected for comedic wit, humor, or sketch comedy."),
            "gameplay": ("Gameplay & Playthrough", "Selected for engaging gameplay, clutch moments, or speedrun."),
            "challenges": ("Challenge & Tournament", "Selected for high-stakes competition or challenges."),
            "sports_moments": ("Sports Highlight", "Selected for athletic highlights, walk-offs, or buzzer beaters."),
            "performance": ("Live Performance", "Selected for live musical performance or stage craft."),
            "animation": ("Creative Animation", "Selected for artistic animation, CGI, or visual craft."),
            "creative_projects": ("Creative Build", "Selected for satisfying craftsmanship or creative build."),
            "compelling_story": ("Lore & Tales", "Selected for bizarre lore, wild adventures, or narrative storytelling."),
        }
        if fmt and fmt in fmt_labels:
            role_label, default_reason = fmt_labels[fmt]
        else:
            role_label, default_reason = ENTERTAINMENT_ROLES[i % len(ENTERTAINMENT_ROLES)]

        chosen.path_step = role_label
        if chosen.assessment.entertainment_evidence:
            chosen.path_reason = f"Entertainment pick ({role_label}): {chosen.assessment.entertainment_evidence}"
        else:
            chosen.path_reason = default_reason

        selected.append(chosen)

    return selected


def build_mixed_feed(items: list[FeedItem], preferences: UserPreferences, length: int = 5) -> list[FeedItem]:
    """
    Assembles the default discovery feed: a balanced mix of educational depth
    and pure creative entertainment tailored to the viewer's taste.
    """
    pool = _filter_pool(items, preferences)
    selected: list[FeedItem] = []
    used_channels: set[str] = set()
    anchor_terms = _terms(preferences.interests)

    for i in range(min(length, len(pool) + len(selected))):
        if not pool:
            break

        role_label, default_reason = MIX_ROLES[i % len(MIX_ROLES)]

        def mix_score(item: FeedItem) -> float:
            ev = item.assessment
            searchable = _terms(f"{item.video.title} {item.video.description or ''}")
            anchor = 0.08 * min(len(searchable & anchor_terms), 2)
            channel_penalty = 0.15 if item.video.channel in used_channels else 0.0
            return ev.composite_score + anchor - channel_penalty

        chosen = max(pool, key=mix_score)
        pool.remove(chosen)
        used_channels.add(chosen.video.channel)

        chosen.path_step = role_label
        chosen.path_reason = default_reason
        selected.append(chosen)

    return selected


def split_interest_categories(interests: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[,;]|\band\b(?=\s+\w+\s+\w+)", interests) if p.strip()]
    if not parts:
        return [interests.strip()] if interests.strip() else []
    return parts


GENERIC_DISCOVERY_WORDS: set[str] = {
    "system", "systems", "analysis", "theory", "theories", "study", "studies",
    "intro", "introduction", "principles", "principle", "breakdown", "breakdowns",
    "overview", "basics", "case", "applied", "first", "guide", "science",
    "deep", "dive", "video", "essay", "evolution", "revolution", "foundational",
    "perspective", "perspectives", "model", "models", "method", "methods",
    "understanding", "modern", "history", "historical", "fundamentals", "fundamental",
    "investigation", "exploring", "exploration", "explained", "explainer", "talk",
    "lecture", "symposium", "craft", "artisan", "world", "future", "nature",
    "design", "practice", "practices", "review", "concepts", "concept"
}


def _substantive_terms(text: str) -> set[str]:
    """Extracts substantive lowercase terms, excluding stopwords and generic modifiers."""
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in GENERIC_DISCOVERY_WORDS}


def _matches_concept(target_phrase: str, cand_title: str, cand_topic: str, cand_domain: str) -> bool:
    """
    Checks if candidate title, search topic, or domain substantively matches target_phrase
    (saved category or learned taste subject).
    - Ignores generic words (e.g. 'systems', 'theory') so sharing a generic modifier does not falsely reject new fields.
    - Accurately checks single-word categories (e.g. 'Math' matches 'Pure Mathematics' or 'Advanced Calculus in Math').
    """
    target_clean = target_phrase.strip().lower()
    if not target_clean:
        return False

    cand_title_lower = cand_title.lower()
    cand_topic_lower = cand_topic.lower()
    cand_domain_lower = cand_domain.lower()
    cand_full = f"{cand_title_lower} {cand_topic_lower} {cand_domain_lower}"

    # 1. Exact phrase match in domain, title, or topic
    if " " in target_clean:
        if (target_clean in cand_domain_lower or
            target_clean in cand_title_lower or
            target_clean in cand_topic_lower):
            return True

    target_substantive = _substantive_terms(target_clean)
    if not target_substantive:
        # If target has only generic words (e.g. "Theory"), fallback to terms >= 3
        target_substantive = {w for w in re.findall(r"[a-z0-9]+", target_clean) if len(w) >= 3}
    if not target_substantive:
        return False

    cand_substantive = _substantive_terms(cand_full)

    # 2. Single substantive term target (e.g. "Math", "Baseball", "Cinema", or "Game Theory" -> {"game"})
    if len(target_substantive) == 1:
        term = next(iter(target_substantive))
        if term in cand_substantive:
            return True
        # Prefix or stem match for terms >= 4 chars (e.g. "math" in "mathematics", "mathematical")
        if len(term) >= 4:
            for cand_w in cand_substantive:
                if cand_w.startswith(term) or (len(cand_w) >= 4 and term.startswith(cand_w)):
                    return True
        return False

    # 3. Multi substantive term target (e.g. "Machine Learning", "Movies and Storytelling")
    matched = target_substantive & cand_substantive
    required = min(len(target_substantive), 2)
    return len(matched) >= required


def is_new_relative_to_categories(
    item: FeedItem,
    categories: list[str],
    preferences: Optional[UserPreferences] = None
) -> bool:
    """
    Verifies that a discovery video is genuinely new relative to saved categories,
    learned subjects from the living taste profile, and recent viewing history.
    Compares candidate title, search topic, and domain against these areas.
    """
    cand_title = item.video.title or ""
    cand_topic = item.video.source_query or item.source_query or ""
    cand_domain = item.video.source_domain or ""
    cand_id = item.video.id or ""

    # 1. Compare against saved categories
    if categories:
        for cat in categories:
            if _matches_concept(cat, cand_title, cand_topic, cand_domain):
                return False

    # 2. Compare against learned subjects from living taste profile
    if preferences and preferences.living_taste_profile:
        lp = preferences.living_taste_profile
        learned_subs = [s for s, _ in (lp.get_established_subjects() + lp.get_tentative_subjects())]
        for sub in learned_subs:
            if _matches_concept(sub, cand_title, cand_topic, cand_domain):
                return False

    # 3. Compare against recent viewing history and seen items
    if preferences:
        seen_set = set(preferences.seen_video_ids or []) | set(preferences.completed_video_ids or [])
        if cand_id and cand_id in seen_set:
            return False
        if preferences.living_taste_profile:
            recent_seen_ids = {s.video_id for s in preferences.living_taste_profile.recent_signals if s.video_id}
            if cand_id and cand_id in recent_seen_ids:
                return False

    return True


def build_main_feed(
    items: list[FeedItem],
    preferences: UserPreferences,
    length: int = 10,
    discovery_target: int = 5
) -> list[FeedItem]:
    """
    Assembles a balanced main feed targeting:
    - 5 personalized picks: drawing from categories, seed interests, or established living taste profile subjects.
      As real viewing evidence accumulates, the influence of initial typed preferences is reduced.
    - 3 adjacent discoveries: lateral cross-domain branches connecting from active interests.
    - 2 independent discoveries: broad, unfamiliar subjects and formats generated outside initial interests and taste profile.
    
    Includes an honest fallback when a lane lacks suitable candidates, preserves entertainment
    as a valid outcome (without requiring educational justification), prevents consecutive duplicate
    subjects/creators/formats, and provides truthful descriptions for independent discoveries.
    """
    raw_pool = _filter_pool(items, preferences)
    if not raw_pool:
        return []

    # In pure entertainment mode, strictly enforce entertainment-first for all slots
    if preferences.feed_mode == "entertainment":
        pool = [item for item in raw_pool if item.assessment.is_entertainment_first]
    else:
        pool = list(raw_pool)

    if not pool:
        return []

    # 1. Target Lane Quotas
    # For a roughly 10-video feed: 5 personalized, 3 adjacent, 2 independent
    if discovery_target == 0:
        target_pers = length
        target_adj = 0
        target_indep = 0
    elif discovery_target == 2:
        target_pers = length - 2
        target_adj = 1
        target_indep = 1
    elif discovery_target == 3:
        target_pers = length - 3
        target_adj = 2
        target_indep = 1
    elif length >= 10:
        target_pers = round(length * 0.5)
        target_adj = round(length * 0.3)
        target_indep = length - target_pers - target_adj
    elif length >= 5:
        target_pers = max(2, round(length * 0.5))
        target_adj = max(1, round(length * 0.3))
        target_indep = max(1, length - target_pers - target_adj)
    else:
        target_pers = length
        target_adj = 0
        target_indep = 0

    seen_ids = set(preferences.seen_video_ids or [])
    restoration_budget = _restoration_budget(preferences)

    initial_categories = [c.strip() for c in preferences.categories if c.strip()]
    if not initial_categories:
        initial_categories = split_interest_categories(preferences.interests)
    if not initial_categories:
        initial_categories = [preferences.interests.strip() or "General Discovery"]

    # 2. Partition pool into candidate lanes
    def is_independent_candidate(it: FeedItem) -> bool:
        dtype = (getattr(it.video, "discovery_type", None) or it.discovery_type or "").lower()
        qtype = (it.video.source_query_type or "").lower()
        return dtype in ("independent", "wildcard") or qtype == "independent_discovery"

    def is_adjacent_candidate(it: FeedItem) -> bool:
        dtype = (getattr(it.video, "discovery_type", None) or it.discovery_type or "").lower()
        qtype = (it.video.source_query_type or "").lower()
        is_disc = (
            dtype in ("adjacent", "distant")
            or qtype in ("cross_domain_analogy", "lateral_discovery")
            or (it.is_discovery and not is_independent_candidate(it))
        )
        return is_disc and is_new_relative_to_categories(it, initial_categories, preferences)

    indep_pool = [it for it in pool if is_independent_candidate(it)]
    adj_pool = [it for it in pool if is_adjacent_candidate(it) and it not in indep_pool]
    pers_pool = [it for it in pool if it not in indep_pool and it not in adj_pool]

    selected_indep: list[FeedItem] = []
    selected_adj: list[FeedItem] = []
    selected_pers: list[FeedItem] = []
    used_ids: set[str] = set()

    # 3. Select Independent Discoveries (target: 2)
    # Evaluate for basic quality, watchability, freshness, duplication, explicit dislikes, and avoided topics.
    # Preserve entertainment as a valid outcome without requiring educational justification.
    def indep_score(it: FeedItem) -> float:
        ev = it.assessment
        primary_quality = max(ev.substance_score, ev.entertainment_score)
        freshness = 0.08 if it.video.id not in seen_ids else 0.0
        return (primary_quality * 0.45) + (ev.novelty_score * 0.35) - (ev.clickbait_score * 0.40) + freshness

    indep_sorted = sorted(indep_pool, key=indep_score, reverse=True)
    for it in indep_sorted:
        if len(selected_indep) >= target_indep:
            break
        if it.video.id in used_ids:
            continue
        if not _within_restoration_budget(it, selected_indep + selected_adj + selected_pers, restoration_budget):
            continue
        if any(_same_video_topic(it, prior) for prior in selected_indep):
            continue

        it.is_discovery = True
        it.discovery_type = "independent"
        it.path_step = "✨ Discovery"

        # Truthful reason describing what the video offers rather than inventing a connection to preferences
        subj = getattr(it.assessment, "actual_subject", "") or ""
        ang = getattr(it.assessment, "specific_angle", "") or ""
        rationale = it.video.source_rationale or getattr(it.video, "discovery_rationale", "") or ""
        domain = it.video.source_domain or ""
        clean_t = it.video.title.split("|")[0].split("—")[0].strip()

        if subj and ang:
            truthful_desc = f"Independent discovery in {domain or subj}: {subj} — {ang}."
        elif subj:
            truthful_desc = f"Independent discovery in {domain or subj}: {subj}."
        elif rationale:
            truthful_desc = f"Independent discovery: {rationale}"
        else:
            truthful_desc = f"Independent discovery: Exploring {clean_t} outside your usual topics."

        it.discovery_signal = truthful_desc
        it.discovery_connection = truthful_desc
        it.path_reason = f"Why this discovery? {truthful_desc}"

        selected_indep.append(it)
        used_ids.add(it.video.id)

    # 4. Select Adjacent Discoveries (target: 3)
    def adj_score(it: FeedItem) -> float:
        ev = it.assessment
        primary_quality = max(ev.substance_score, ev.entertainment_score)
        freshness = 0.08 if it.video.id not in seen_ids else 0.0
        return (primary_quality * 0.45) + (ev.novelty_score * 0.35) - (ev.clickbait_score * 0.40) + freshness

    adj_sorted = sorted(adj_pool, key=adj_score, reverse=True)
    for it in adj_sorted:
        if len(selected_adj) >= target_adj:
            break
        if it.video.id in used_ids:
            continue
        if not _within_restoration_budget(it, selected_indep + selected_adj + selected_pers, restoration_budget):
            continue
        if any(_same_video_topic(it, prior) for prior in (selected_indep + selected_adj)):
            continue

        it.is_discovery = True
        it.discovery_type = getattr(it.video, "discovery_type", None) or "adjacent"
        it.path_step = "✨ Discovery"
        disc_domain = it.video.source_domain or "an unexpected field"
        disc_signal = getattr(it.video, "discovery_rationale", None) or it.assessment.connection_note or it.assessment.relevance_reason or f"Surprising lateral bridge connecting your taste to {disc_domain}."
        it.discovery_signal = disc_signal
        it.discovery_connection = disc_signal
        it.path_reason = f"Why this discovery? {disc_signal}"

        selected_adj.append(it)
        used_ids.add(it.video.id)

    # 5. Select Personalized Picks (target: 5)
    # Reduce the influence of initial typed preferences as real viewing evidence accumulates.
    # A few interactions remain tentative; repeated meaningful watches, saves, and dislikes change the mix over time.
    lp = preferences.living_taste_profile
    established_subs = [s for s, _ in lp.get_established_subjects(min_count=2)] if lp else []
    meaningful_actions = {"substantial_watch", "completed", "watch_later"}
    real_evidence_count = sum(1 for s in (lp.recent_signals if lp else []) if s.action in meaningful_actions)

    all_personalized_topics = list(initial_categories)
    for es in established_subs:
        if es not in all_personalized_topics:
            all_personalized_topics.append(es)

    topic_bins: dict[str, list[FeedItem]] = {top: [] for top in all_personalized_topics}
    unmatched_pers: list[FeedItem] = []

    for item in pers_pool:
        if item.video.id in used_ids:
            continue
        searchable = _terms(f"{item.video.title} {item.video.description or ''} {item.source_query or ''}")
        best_top = None
        best_overlap = 0
        for top in all_personalized_topics:
            overlap = len(searchable & _terms(top))
            if overlap > best_overlap:
                best_overlap = overlap
                best_top = top
        if best_top and best_overlap > 0:
            topic_bins[best_top].append(item)
        else:
            unmatched_pers.append(item)

    # Quota weighting between established taste subjects and initial typed categories
    topic_quotas: dict[str, int] = {}
    if established_subs and real_evidence_count >= 2:
        initial_decay_weight = max(0.20, 1.0 / (1.0 + 0.25 * real_evidence_count))
        established_weight = 1.0 - initial_decay_weight
        est_slots = min(len(established_subs) * 2, max(1, round(target_pers * established_weight)))
        init_slots = max(1, target_pers - est_slots)

        per_est = max(1, (est_slots + len(established_subs) - 1) // len(established_subs))
        for es in established_subs:
            topic_quotas[es] = per_est
        per_init = max(1, (init_slots + len(initial_categories) - 1) // len(initial_categories))
        for ic in initial_categories:
            topic_quotas[ic] = per_init
    else:
        per_init = max(1, (target_pers + len(initial_categories) - 1) // len(initial_categories))
        for ic in initial_categories:
            topic_quotas[ic] = per_init

    def pers_score(it: FeedItem) -> float:
        ev = it.assessment
        primary_quality = max(ev.substance_score, ev.entertainment_score)
        freshness = 0.08 if it.video.id not in seen_ids else 0.0
        taste = _creator_preference_boost(it, preferences)
        return (primary_quality * 0.50) + (ev.novelty_score * 0.25) - (ev.clickbait_score * 0.40) + freshness + taste

    topic_counts: dict[str, int] = {top: 0 for top in all_personalized_topics}
    while len(selected_pers) < target_pers:
        added_in_round = False
        for top in all_personalized_topics:
            if len(selected_pers) >= target_pers:
                break
            if topic_counts.get(top, 0) >= topic_quotas.get(top, target_pers):
                continue
            candidates_for_top = [
                it for it in topic_bins[top]
                if it.video.id not in used_ids
                and _within_restoration_budget(it, selected_indep + selected_adj + selected_pers, restoration_budget)
            ]
            if not candidates_for_top:
                continue
            distinct_for_top = [
                it for it in candidates_for_top
                if not any(_same_video_topic(it, prior) for prior in (selected_indep + selected_adj + selected_pers))
            ]
            eligible = distinct_for_top if distinct_for_top else candidates_for_top
            best_it = max(eligible, key=pers_score)
            best_it.category = top
            best_it.is_discovery = False
            best_it.discovery_type = None
            best_it.path_reason = f"Curated for your ongoing interest in '{top}'."
            selected_pers.append(best_it)
            used_ids.add(best_it.video.id)
            topic_counts[top] = topic_counts.get(top, 0) + 1
            added_in_round = True

        if not added_in_round:
            leftover = [
                it for it in (unmatched_pers + [it for sublist in topic_bins.values() for it in sublist])
                if it.video.id not in used_ids
                and _within_restoration_budget(it, selected_indep + selected_adj + selected_pers, restoration_budget)
            ]
            if not leftover:
                break
            distinct_leftover = [
                it for it in leftover
                if not any(_same_video_topic(it, prior) for prior in (selected_indep + selected_adj + selected_pers))
            ]
            eligible = distinct_leftover if distinct_leftover else leftover
            best_it = max(eligible, key=pers_score)
            best_it.category = best_it.category or all_personalized_topics[0]
            best_it.is_discovery = False
            best_it.discovery_type = None
            best_it.path_reason = "Curated for your viewing taste."
            selected_pers.append(best_it)
            used_ids.add(best_it.video.id)

    # 6. Honest Fallback
    # If any lane lacked suitable candidates, backfill honestly from remaining qualified candidates
    total_selected = len(selected_pers) + len(selected_adj) + len(selected_indep)
    if total_selected < length:
        leftover_all = [
            it for it in pool
            if it.video.id not in used_ids
            and _within_restoration_budget(it, selected_indep + selected_adj + selected_pers, restoration_budget)
        ]
        distinct_leftover = [
            it for it in leftover_all
            if not any(_same_video_topic(it, prior) for prior in (selected_indep + selected_adj + selected_pers))
        ]
        leftover_sorted = sorted(
            leftover_all,
            key=lambda x: (
                1 if x in distinct_leftover else 0,
                max(x.assessment.substance_score, x.assessment.entertainment_score) * 0.5 + x.assessment.novelty_score * 0.3 - x.assessment.clickbait_score * 0.4
            ),
            reverse=True
        )
        for it in leftover_sorted:
            if len(selected_pers) + len(selected_adj) + len(selected_indep) >= length:
                break
            if is_adjacent_candidate(it):
                it.is_discovery = True
                it.discovery_type = getattr(it.video, "discovery_type", None) or "adjacent"
                it.path_step = "✨ Discovery"
                disc_domain = it.video.source_domain or "an unexpected field"
                disc_signal = getattr(it.video, "discovery_rationale", None) or it.assessment.connection_note or it.assessment.relevance_reason or f"Surprising lateral bridge connecting your taste to {disc_domain}."
                it.discovery_signal = disc_signal
                it.discovery_connection = disc_signal
                it.path_reason = f"Why this discovery? {disc_signal}"
                selected_adj.append(it)
            elif is_independent_candidate(it):
                it.is_discovery = True
                it.discovery_type = "independent"
                it.path_step = "✨ Discovery"
                subj = getattr(it.assessment, "actual_subject", "") or ""
                ang = getattr(it.assessment, "specific_angle", "") or ""
                rationale = it.video.source_rationale or getattr(it.video, "discovery_rationale", "") or ""
                domain = it.video.source_domain or ""
                clean_t = it.video.title.split("|")[0].split("—")[0].strip()

                if subj and ang:
                    truthful_desc = f"Independent discovery in {domain or subj}: {subj} — {ang}."
                elif subj:
                    truthful_desc = f"Independent discovery in {domain or subj}: {subj}."
                elif rationale:
                    truthful_desc = f"Independent discovery: {rationale}"
                else:
                    truthful_desc = f"Independent discovery: Exploring {clean_t} outside your usual topics."

                it.discovery_signal = truthful_desc
                it.discovery_connection = truthful_desc
                it.path_reason = f"Why this discovery? {truthful_desc}"
                selected_indep.append(it)
            else:
                it.is_discovery = False
                it.discovery_type = None
                it.path_reason = "Curated for your viewing taste."
                selected_pers.append(it)
            used_ids.add(it.video.id)

    # 7. Anti-Clustering Interleaving & Sequencing
    # Prevent one subject, creator, or format from filling consecutive slots.
    final_feed: list[FeedItem] = []
    available_pools = {
        "pers": list(selected_pers),
        "adj": list(selected_adj),
        "indep": list(selected_indep),
    }

    # Desired slot lane blueprint for balanced discovery pacing:
    # 5 pers, 3 adj, 2 indep across 10 slots
    blueprint = ["pers", "pers", "adj", "pers", "indep", "pers", "adj", "pers", "indep", "adj"]

    def can_place(cand: FeedItem, prev: Optional[FeedItem]) -> bool:
        if not prev:
            return True
        if _is_same_creator(cand, prev):
            return False
        if _is_same_subject(cand, prev):
            return False
        if _is_same_format(cand, prev):
            return False
        return True

    for pos in range(length):
        total_remaining = sum(len(p) for p in available_pools.values())
        if total_remaining == 0:
            break

        desired_lane = blueprint[pos % len(blueprint)]
        lane_order = [desired_lane] + [l for l in ["pers", "adj", "indep"] if l != desired_lane]

        prev_item = final_feed[-1] if final_feed else None
        chosen_item = None
        chosen_lane = None

        # 1st attempt: Find candidate in desired or alternative lanes that satisfies full anti-clustering
        for lane in lane_order:
            for item in available_pools[lane]:
                if can_place(item, prev_item):
                    chosen_item = item
                    chosen_lane = lane
                    break
            if chosen_item:
                break

        # 2nd attempt: If no item avoids all 3 clashes, at least avoid creator and subject
        if not chosen_item and prev_item:
            for lane in lane_order:
                for item in available_pools[lane]:
                    if not _is_same_creator(item, prev_item) and not _is_same_subject(item, prev_item):
                        chosen_item = item
                        chosen_lane = lane
                        break
                if chosen_item:
                    break

        # 3rd attempt: Pick any remaining item avoiding duplicate video ID
        if not chosen_item:
            for lane in lane_order:
                if available_pools[lane]:
                    chosen_item = available_pools[lane][0]
                    chosen_lane = lane
                    break

        if chosen_item and chosen_lane:
            available_pools[chosen_lane].remove(chosen_item)
            final_feed.append(chosen_item)

    for idx, item in enumerate(final_feed):
        if not item.is_discovery:
            if preferences.feed_mode == "deep_learning":
                item.path_step = DEEP_LEARNING_STEPS[idx % len(DEEP_LEARNING_STEPS)][0]
            else:
                item.path_step = item.path_step or "Highlight"
        else:
            if item.discovery_type == "independent":
                item.path_step = "🧭 Independent Discovery"
            else:
                item.path_step = "✨ Discovery"

    return final_feed


RECENT_UPLOAD_WINDOW_DAYS = 365


def parse_candidate_upload_date(upload_date_str: Optional[str]) -> Optional[datetime]:
    """Parse candidate upload date into a timezone-aware UTC datetime."""
    if not upload_date_str:
        return None
    val = str(upload_date_str).strip()
    if "T" in val:
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
    clean_val = val.replace("-", "")[:8]
    if len(clean_val) == 8 and clean_val.isdigit():
        try:
            return datetime.strptime(clean_val, "%Y%m%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return None


def get_candidate_age_days(upload_date_str: Optional[str]) -> Optional[int]:
    """Returns the age in days of a candidate, or None if date is missing or unparseable."""
    dt = parse_candidate_upload_date(upload_date_str)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    return max(0, (now - dt).days)


INTERDISCIPLINARY_DOMAINS: Dict[str, Set[str]] = {
    "Biology & Ecology": {
        "biology", "biological", "evolution", "evolutionary", "ecology", "ecological",
        "organism", "organisms", "genetics", "genetic", "dna", "mycelial", "mycology",
        "biomechanics", "cellular", "photosynthesis", "species", "natural selection",
        "zoology", "marine biology", "wildlife", "animal conflicts"
    },
    "Physics & Fluid Dynamics": {
        "physics", "thermodynamics", "quantum", "astrophysics", "mechanics",
        "magnetohydrodynamics", "gravity", "gravitational", "optics", "relativity",
        "electromagnetism", "fluid dynamics", "aerodynamics", "kinetics", "entropy",
        "particle physics", "superconductivity", "wave propagation"
    },
    "Game Theory & Decision Science": {
        "game theory", "nash equilibrium", "replicator dynamics", "hawk dove",
        "payoff matrix", "strategic conflict", "minimax", "prisoner dilemma",
        "mechanism design", "social choice", "decision theory"
    },
    "Computation & Algorithms": {
        "computation", "computational", "algorithm", "algorithms", "turing machine",
        "complexity theory", "automata", "neural network", "machine learning",
        "information theory", "data structure", "cryptography", "error correction"
    },
    "Neuroscience & Cognitive Science": {
        "neuroscience", "neural", "brain", "synapse", "synaptic", "cortex",
        "neuron", "neurons", "cognitive", "action potential", "neurobiology",
        "cognition", "sensory perception"
    },
    "Music & Acoustics": {
        "music", "musical", "acoustics", "harmonics", "composition", "timbre",
        "temperament", "crab canon", "polyphony", "sound synthesis", "psychoacoustics",
        "fugue", "counterpoint"
    },
    "Architecture & Structural Engineering": {
        "architecture", "architectural", "structural engineering", "materials science",
        "concrete", "structural design", "tensile strength", "cantilever", "facade engineering",
        "civil engineering", "structural mechanics"
    },
    "Economics & Market Dynamics": {
        "economics", "economy", "macroeconomics", "microeconomics", "market dynamics",
        "inflation", "monetary policy", "supply and demand", "pricing mechanism",
        "central bank", "econometrics"
    }
}


def _detect_interdisciplinary_connection(it: FeedItem, category_name: str, category_tokens: Set[str]) -> Optional[str]:
    """
    Requires demonstrable metadata evidence in the candidate's actual title and description
    covering both:
    1. The selected interest category.
    2. A distinct secondary field.

    A query label, source-domain label, or generic keyword alone is strictly insufficient.
    Returns the supported second field name, or None if evidence for both fields is absent.
    """
    title = (it.video.title or "").lower()
    desc = (it.video.description or "").lower()
    content_text = f"{title} {desc}"
    content_tokens = _extract_tokens(content_text)

    cat_lower = category_name.lower().strip()

    # 1. Evidence of selected interest in video metadata
    known_triggers = set()
    for dom_name, trigs in DOMAIN_TRIGGERS.items():
        if any(trig in cat_lower for trig in trigs):
            known_triggers.update(trigs)
            if dom_name in DOMAIN_LEXICONS:
                known_triggers.update([t for t in DOMAIN_LEXICONS[dom_name] if len(t) >= 4])

    has_cat_evidence = (
        bool(category_tokens & content_tokens)
        or (cat_lower in content_text)
        or any(trig in content_text for trig in known_triggers if len(trig) >= 4)
    )
    if not has_cat_evidence:
        return None

    # 2. Evidence of distinct second field in video metadata
    for field_name, field_terms in INTERDISCIPLINARY_DOMAINS.items():
        field_tokens = _extract_tokens(field_name)
        if bool(category_tokens & field_tokens) or (cat_lower in field_name.lower()):
            continue
        if any(tok in field_name.lower() for tok in category_tokens):
            continue

        matching_terms = [t for t in field_terms if t in content_text]
        if matching_terms:
            return field_name

    return None


def build_category_feed(
    items: list[FeedItem],
    preferences: UserPreferences,
    category_name: str,
    length: int = 5,
    view: Optional[str] = None
) -> list[FeedItem]:
    """
    Builds a focused feed of ~5 videos for a specific interest tab across 3 selectable views:
    - 'new': Worthwhile recent videos about the selected interest.
    - 'deeper': Videos that build on videos watched or saved in that interest.
    - 'connections': Videos that connect the selected interest to another field while retaining focus on it.

    Enforces:
    - Relevance to the selected interest (rejects off-topic filler).
    - Meaningful differences between views in which videos are selected.
    - Deduplication: avoids showing the same video across views when alternatives exist.
    - Strict dislike and exclusion enforcement.
    - Honest sparse / cold-start results instead of unrelated padding.
    """
    current_view = (view or preferences.category_view or "new").lower().strip()
    raw_pool = _filter_pool(items, preferences)
    if not raw_pool:
        return []

    # In pure entertainment mode, strictly enforce entertainment-first
    if preferences.feed_mode == "entertainment":
        pool = [item for item in raw_pool if item.assessment.is_entertainment_first]
    else:
        pool = list(raw_pool)

    if not pool:
        return []

    category_lower = category_name.lower().strip()
    category_tokens = _extract_tokens(category_name) - GENERIC_QUERY_MODIFIERS
    seen_ids = set(preferences.seen_video_ids or [])
    other_view_ids = set(preferences.other_view_video_ids or [])
    followed_names = {fc.name.lower() for fc in (preferences.followed_creators or [])}
    followed_ch_ids = {fc.channel_id for fc in (preferences.followed_creators or []) if fc.channel_id}

    pref_qualities = set()
    if preferences.living_taste_profile:
        pref_qualities = {q.lower() for q, _ in preferences.living_taste_profile.get_top_qualities(limit=3)}

    # Identify history items (watched or saved) belonging to this category
    watched_or_saved_ids = set()
    watched_or_saved_titles = []
    watched_or_saved_domains = []

    for sv in (preferences.saved_videos or []):
        cat_match = (
            (sv.source_category and category_lower in sv.source_category.lower())
            or (sv.source_domain and category_lower in sv.source_domain.lower())
            or (sv.title and any(tok in sv.title.lower() for tok in category_tokens))
        )
        if cat_match:
            watched_or_saved_ids.add(sv.video_id)
            if sv.title:
                watched_or_saved_titles.append(sv.title)
            if sv.source_domain:
                watched_or_saved_domains.append(sv.source_domain)

    if preferences.living_taste_profile:
        for s in preferences.living_taste_profile.recent_signals:
            if s.action in ("substantial_watch", "completed", "watch_later"):
                dom_match = s.domain and (category_lower in s.domain.lower() or s.domain.lower() in category_lower)
                title_match = s.title and any(tok in s.title.lower() for tok in category_tokens)
                if dom_match or title_match:
                    if s.video_id:
                        watched_or_saved_ids.add(s.video_id)
                    if s.title:
                        watched_or_saved_titles.append(s.title)
                    if s.domain:
                        watched_or_saved_domains.append(s.domain)

    for top in (preferences.watched_topics or []):
        if category_lower in top.lower() or top.lower() in category_lower:
            watched_or_saved_domains.append(top)

    has_category_history = bool(watched_or_saved_ids or watched_or_saved_titles or watched_or_saved_domains)

    # 1. COLD-START / SPARSE HANDLING FOR 'GO DEEPER'
    # If the user has no viewing or saved history in this category, return an honest sparse result (empty)
    # rather than padding with unrelated or arbitrary videos.
    if current_view == "deeper" and not has_category_history:
        return []

    # 2. CANDIDATE FILTERING PER VIEW
    history_text = " ".join(watched_or_saved_titles + watched_or_saved_domains).lower()
    hist_tokens = _extract_tokens(history_text)
    specific_hist_terms = (hist_tokens - category_tokens - GENERIC_QUERY_MODIFIERS - STOPWORDS) - _TITLE_FILLER

    def is_eligible_for_view(it: FeedItem) -> bool:
        full_text = f"{it.video.title} {it.video.channel or ''} {it.video.description or ''} {it.video.source_domain or ''} {it.category or ''} {getattr(it.assessment, 'primary_appeal_domain', '') or ''}".lower()
        cand_tokens = _extract_tokens(full_text)

        rel = getattr(it.assessment, "relevance", None)
        rel_is_relevant = getattr(rel, "is_relevant", None)
        is_rel_supported = rel_is_relevant if rel_is_relevant is not None else (getattr(it.assessment, "relevance_status", "supported") != "rejected")

        # Base requirement for all views: must retain genuine relevance to category
        has_cat_connection = (
            bool(category_tokens & cand_tokens)
            or (category_lower in full_text)
            or (it.category and category_lower in it.category.lower())
            or is_rel_supported
        )
        if not has_cat_connection:
            return False

        if current_view == "new":
            # 1. NEW: Use actual upload date to decide if recent
            # Do not infer recency from title words or hard-coded years
            age_days = get_candidate_age_days(it.video.upload_date)
            if age_days is not None and age_days > RECENT_UPLOAD_WINDOW_DAYS:
                return False
            return True

        elif current_view == "deeper":
            # 2. GO DEEPER: Require demonstrable link to watched or saved history in this interest
            if it.video.id in watched_or_saved_ids:
                return False
            primer_words = {"beginner", "beginners", "intro", "introduction", "101", "basics", "for beginners"}
            title_words = set(re.findall(r"[a-z0-9]+", it.video.title.lower()))
            if primer_words & title_words:
                return False

            cand_tokens_clean = (_extract_tokens(full_text) - category_tokens - GENERIC_QUERY_MODIFIERS - STOPWORDS) - _TITLE_FILLER
            overlap_tokens = cand_tokens_clean & specific_hist_terms
            if not overlap_tokens:
                # High substance score or lecture query alone is strictly insufficient
                return False
            return True

        elif current_view == "connections":
            # 3. CONNECTIONS: Require demonstrable metadata evidence covering both the selected interest and another field
            connected_field = _detect_interdisciplinary_connection(it, category_name, category_tokens)
            return connected_field is not None

        else:
            return True

    eligible_pool = [it for it in pool if is_eligible_for_view(it)]
    if not eligible_pool:
        return []

    # 3. VIEW-SPECIFIC SCORING & RANKING
    def view_item_score(it: FeedItem) -> float:
        ev = it.assessment
        full_text = f"{it.video.title} {it.video.channel or ''} {it.video.description or ''} {it.video.source_domain or ''}".lower()

        # Deduplication penalty: prioritize candidates not seen in other views when alternatives exist
        other_view_pen = 0.25 if it.video.id in other_view_ids else 0.0

        chan_pen = 0.15 if it.video.channel in used_channels else 0.0

        is_followed = (
            it.video.channel.lower() in followed_names
            or (it.video.channel_id and it.video.channel_id in followed_ch_ids)
        )
        followed_boost = 0.12 if is_followed else 0.0

        quality_boost = 0.0
        if pref_qualities:
            content_lower = f"{it.video.title} {it.video.description or ''}".lower()
            if any(q in content_lower for q in pref_qualities):
                quality_boost = 0.06

        if current_view == "new":
            freshness = 0.15 if it.video.id not in seen_ids else 0.0
            age_days = get_candidate_age_days(it.video.upload_date)
            if age_days is not None and age_days <= RECENT_UPLOAD_WINDOW_DAYS:
                recency_boost = 0.30 * max(0.20, 1.0 - (age_days / RECENT_UPLOAD_WINDOW_DAYS))
            else:
                recency_boost = 0.0
            base = (ev.composite_score * 0.40) + (ev.substance_score * 0.35) + recency_boost + freshness

        elif current_view == "deeper":
            cand_tokens_clean = (_extract_tokens(full_text) - category_tokens - GENERIC_QUERY_MODIFIERS - STOPWORDS) - _TITLE_FILLER
            overlap_count = len(cand_tokens_clean & specific_hist_terms)
            history_boost = min(0.35, 0.12 * overlap_count)
            base = (ev.substance_score * 0.55) + (ev.composite_score * 0.25) + history_boost

        elif current_view == "connections":
            base = (ev.novelty_score * 0.45) + (ev.composite_score * 0.35)

        else:
            base = ev.composite_score

        return base + followed_boost + quality_boost - chan_pen - other_view_pen - _topic_repetition_penalty(it, selected)

    selected: list[FeedItem] = []
    used_channels: set[str] = set()
    target_count = min(length, len(eligible_pool))
    restoration_budget = _restoration_budget(preferences)

    for i in range(target_count):
        if not eligible_pool:
            break

        # Preference 1: Distinct topic & within restoration budget & fresh to this view
        fresh_alternatives = [it for it in eligible_pool if it.video.id not in other_view_ids]
        candidate_pool = fresh_alternatives if fresh_alternatives else eligible_pool

        distinct_pool = [it for it in candidate_pool if not _topic_repetition_penalty(it, selected)]
        within_budget = [it for it in distinct_pool if _within_restoration_budget(it, selected, restoration_budget)]
        if not within_budget:
            within_budget = [it for it in candidate_pool if _within_restoration_budget(it, selected, restoration_budget)]
        if not within_budget:
            within_budget = [it for it in eligible_pool if _within_restoration_budget(it, selected, restoration_budget)]
        if not within_budget:
            break

        chosen = max(within_budget, key=view_item_score)
        eligible_pool.remove(chosen)
        used_channels.add(chosen.video.channel)

        chosen.category = category_name

        if current_view == "new":
            chosen.is_discovery = False
            chosen.discovery_type = None
            age_days = get_candidate_age_days(chosen.video.upload_date)
            # Only label a video "Recent Release" when its date supports that claim
            if age_days is not None and age_days <= RECENT_UPLOAD_WINDOW_DAYS:
                role_label = "⚡ Recent Release"
                date_str = str(chosen.video.upload_date).split("T")[0]
                chosen.path_reason = f"Focused in '{category_name}': Recent upload ({date_str}) covering {chosen.video.title}."
            else:
                role_label = "⚡ Curated Candidate"
                chosen.path_reason = f"Focused in '{category_name}': Curated video covering {chosen.video.title} (upload date unverified)."
            chosen.path_step = role_label

        elif current_view == "deeper":
            chosen.is_discovery = False
            chosen.discovery_type = None
            chosen.path_step = "🔍 Go Deeper"
            cand_tokens_clean = (_extract_tokens(f"{chosen.video.title} {chosen.video.description or ''}") - category_tokens - GENERIC_QUERY_MODIFIERS - STOPWORDS) - _TITLE_FILLER
            overlap_tokens = sorted(list(cand_tokens_clean & specific_hist_terms))
            if overlap_tokens:
                concept_desc = ", ".join(overlap_tokens[:2])
                chosen.path_reason = f"Focused in '{category_name}': Advanced deep-dive building on '{concept_desc}' from your viewing history."
            else:
                chosen.path_reason = f"Focused in '{category_name}': Advanced deep-dive building on your category engagement with {chosen.video.title}."

        elif current_view == "connections":
            chosen.is_discovery = True
            chosen.discovery_type = "adjacent"
            chosen.path_step = "🌐 Connections"
            second_field = _detect_interdisciplinary_connection(chosen, category_name, category_tokens)
            if second_field:
                chosen.discovery_connection = f"Interdisciplinary bridge connecting '{category_name}' to {second_field}."
                chosen.path_reason = f"Focused in '{category_name}': Interdisciplinary connection linking {category_name} to {second_field}."
            else:
                chosen.discovery_connection = f"Interdisciplinary bridge connecting '{category_name}'."
                chosen.path_reason = f"Focused in '{category_name}': Cross-domain exploration covering {chosen.video.title}."

        else:
            chosen.is_discovery = False
            chosen.discovery_type = None
            chosen.path_step = "Highlight"
            chosen.path_reason = f"Focused in '{category_name}': Curated for your interest."

        selected.append(chosen)

    return selected
