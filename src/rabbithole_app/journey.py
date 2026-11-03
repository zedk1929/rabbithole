"""Build viewing paths and discovery feeds from scored search results.

Supports three distinct discovery experiences:
1. 'mix' (default): A balanced blend of thoughtful depth and creative entertainment.
2. 'entertainment': Engaging, humorous, creative, and well-made finds without academic progression.
3. 'deep_learning': The progressive 5-step exploration path (Start, Deepen, Bridge, Challenge, Continue).
"""
import re
from typing import List, Set, Optional, Dict, Any

from rabbithole_app.models import FeedItem, UserPreferences


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
    discovery_target: int = 3
) -> list[FeedItem]:
    """
    Assembles a balanced main feed of about 10 videos:
    - Draws from several user categories without letting one interest dominate.
    - Reserves roughly 2-3 slots for genuinely new discovery areas across tiers:
      1 adjacent idea, 1 distant idea, and optionally 1 outside wildcard.
    - Mixes educational depth and creative entertainment matching preferences.
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

    # 1. Resolve user categories
    categories = [c.strip() for c in preferences.categories if c.strip()]
    if not categories:
        categories = split_interest_categories(preferences.interests)
    if not categories:
        categories = [preferences.interests.strip() or "General Discovery"]

    used_channels: set[str] = set()

    # 2. Identify candidate discovery items (cross-domain bridges outside direct category scope)
    discovery_candidates = []
    category_candidates = []

    for item in pool:
        qt = item.video.source_query_type or ""
        disc_type = getattr(item.video, "discovery_type", None)
        is_disc = (qt == "cross_domain_analogy" or item.is_discovery or disc_type in ("adjacent", "distant", "wildcard"))
        if is_disc and is_new_relative_to_categories(item, categories, preferences):
            discovery_candidates.append(item)
        else:
            category_candidates.append(item)

    # 3. Select 2 to 3 discovery items covering diverse tiers (adjacent, distant, wildcard)
    selected_discoveries: list[FeedItem] = []
    num_discoveries = min(discovery_target, len(discovery_candidates))
    if num_discoveries > 0:
        adj_cands = [c for c in discovery_candidates if getattr(c.video, "discovery_type", None) == "adjacent"]
        dist_cands = [c for c in discovery_candidates if getattr(c.video, "discovery_type", None) == "distant"]
        wild_cands = [c for c in discovery_candidates if getattr(c.video, "discovery_type", None) == "wildcard"]
        other_cands = [c for c in discovery_candidates if c not in adj_cands and c not in dist_cands and c not in wild_cands]

        for cand_list in [adj_cands, dist_cands, wild_cands, other_cands]:
            cand_list.sort(
                key=lambda x: (
                    x.assessment.novelty_score * 0.45
                    + x.assessment.composite_score * 0.40
                    - (0.15 if x.video.channel in used_channels else 0.0)
                ),
                reverse=True
            )

        prioritized: list[FeedItem] = []
        restoration_budget = _restoration_budget(preferences)
        for tier_list in [adj_cands, dist_cands, wild_cands, other_cands]:
            if tier_list and len(prioritized) < num_discoveries:
                eligible = [c for c in tier_list if _within_restoration_budget(c, prioritized, restoration_budget)]
                if eligible:
                    prioritized.append(eligible[0])

        if len(prioritized) < num_discoveries:
            discovery_candidates.sort(
                key=lambda x: (
                    x.assessment.novelty_score * 0.45
                    + x.assessment.composite_score * 0.40
                    - (0.15 if x.video.channel in used_channels else 0.0)
                ),
                reverse=True
            )
            for c in discovery_candidates:
                if len(prioritized) >= num_discoveries:
                    break
                if c not in prioritized and _within_restoration_budget(c, prioritized, restoration_budget):
                    prioritized.append(c)

        for disc_item in prioritized:
            disc_item.is_discovery = True
            disc_item.path_step = "✨ Discovery"
            disc_type = getattr(disc_item.video, "discovery_type", None) or ("adjacent" if disc_item.video.source_query_type == "cross_domain_analogy" else "discovery")
            disc_item.discovery_type = disc_type
            disc_domain = disc_item.video.source_domain or "an unexpected field"
            disc_signal = getattr(disc_item.video, "discovery_rationale", None) or disc_item.assessment.connection_note or disc_item.assessment.relevance_reason or f"Surprising lateral bridge connecting your taste to {disc_domain}."
            disc_item.discovery_signal = disc_signal
            disc_item.discovery_connection = disc_signal
            disc_item.path_reason = f"Why this discovery? {disc_signal}"
            selected_discoveries.append(disc_item)
            used_channels.add(disc_item.video.channel)
            pool.remove(disc_item)

    # 4. Fill remaining slots across categories without letting one interest dominate
    remaining_slots = min(length - len(selected_discoveries), len(pool))
    selected_category_items: list[FeedItem] = []

    # Map candidates to best matching category
    cat_bins: dict[str, list[FeedItem]] = {cat: [] for cat in categories}
    unmatched: list[FeedItem] = []

    for item in pool:
        searchable = _terms(f"{item.video.title} {item.video.description or ''} {item.source_query or ''}")
        best_cat = None
        best_overlap = 0
        for cat in categories:
            overlap = len(searchable & _terms(cat))
            if overlap > best_overlap:
                best_overlap = overlap
                best_cat = cat
        if best_cat and best_overlap > 0:
            cat_bins[best_cat].append(item)
        else:
            unmatched.append(item)

    # Max quota per category to avoid one category dominating
    max_per_cat = max(2, (remaining_slots // len(categories)) + 1) if len(categories) > 1 else remaining_slots

    cat_counts = {cat: 0 for cat in categories}
    while len(selected_category_items) < remaining_slots:
        added_in_round = False
        for cat in categories:
            if len(selected_category_items) >= remaining_slots:
                break
            if cat_counts[cat] >= max_per_cat:
                continue

            available_for_cat = [it for it in cat_bins[cat] if it not in selected_category_items]
            if not available_for_cat:
                continue
            distinct_for_cat = [
                it for it in available_for_cat
                if not _topic_repetition_penalty(it, selected_category_items + selected_discoveries)
            ]
            distinct_for_cat = [
                it for it in distinct_for_cat
                if _within_restoration_budget(it, selected_category_items + selected_discoveries, _restoration_budget(preferences))
            ]
            if not distinct_for_cat:
                continue

            seen_ids = set(preferences.seen_video_ids or [])
            def score_func(item: FeedItem) -> float:
                ev = item.assessment
                chan_pen = 0.15 if item.video.channel in used_channels else 0.0
                freshness = 0.08 if item.video.id not in seen_ids else 0.0
                taste = _creator_preference_boost(item, preferences)
                repetition = _topic_repetition_penalty(item, selected_category_items + selected_discoveries)
                return ev.composite_score + freshness + taste - chan_pen - repetition

            best_item = max(distinct_for_cat, key=score_func)
            best_item.category = cat
            if preferences.feed_mode == "deep_learning":
                best_item.path_step = DEEP_LEARNING_STEPS[len(selected_category_items) % len(DEEP_LEARNING_STEPS)][0]
            else:
                best_item.path_step = best_item.path_step or "Highlight"
            best_item.path_reason = f"Curated for your interest in '{cat}'."
            selected_category_items.append(best_item)
            used_channels.add(best_item.video.channel)
            cat_counts[cat] += 1
            added_in_round = True

        if not added_in_round:
            leftover = [it for it in pool if it not in selected_category_items]
            if not leftover:
                break
            while leftover and len(selected_category_items) < remaining_slots:
                within_budget = [
                    it for it in leftover
                    if _within_restoration_budget(it, selected_category_items + selected_discoveries, _restoration_budget(preferences))
                ]
                distinct_leftover = [
                    it for it in within_budget
                    if not _topic_repetition_penalty(it, selected_category_items + selected_discoveries)
                ]
                def fallback_category(item: FeedItem) -> str:
                    return next((cat for cat in categories if item in cat_bins[cat]), categories[0])

                if not within_budget:
                    break
                it = max(
                    distinct_leftover or within_budget,
                    key=lambda x: x.assessment.composite_score
                    + _creator_preference_boost(x, preferences)
                    - _topic_repetition_penalty(x, selected_category_items + selected_discoveries)
                    - (0.15 * cat_counts[fallback_category(x)]),
                )
                leftover.remove(it)
                it.category = fallback_category(it)
                cat_counts[it.category] += 1
                if preferences.feed_mode == "deep_learning":
                    it.path_step = DEEP_LEARNING_STEPS[len(selected_category_items) % len(DEEP_LEARNING_STEPS)][0]
                else:
                    it.path_step = it.path_step or "Highlight"
                it.path_reason = f"Curated for your viewing taste."
                selected_category_items.append(it)
                used_channels.add(it.video.channel)
            break

    # 5. Interleave discovery items into the feed (e.g. at slots 2, 5, 8)
    final_feed: list[FeedItem] = []
    cat_idx = 0
    disc_idx = 0

    total_target = len(selected_category_items) + len(selected_discoveries)
    disc_positions = {2, 5, 8} if len(selected_discoveries) >= 3 else ({2, 6} if len(selected_discoveries) == 2 else {3})
    for pos in range(total_target):
        if (pos in disc_positions or cat_idx >= len(selected_category_items)) and disc_idx < len(selected_discoveries):
            final_feed.append(selected_discoveries[disc_idx])
            disc_idx += 1
        elif cat_idx < len(selected_category_items):
            final_feed.append(selected_category_items[cat_idx])
            cat_idx += 1
        elif disc_idx < len(selected_discoveries):
            final_feed.append(selected_discoveries[disc_idx])
            disc_idx += 1

    return final_feed


def build_category_feed(
    items: list[FeedItem],
    preferences: UserPreferences,
    category_name: str,
    length: int = 5
) -> list[FeedItem]:
    """
    Builds a focused feed of ~5 videos for a specific interest tab.
    - Strictly filters out disliked and completed videos.
    - Requires verified on-topic relevance for the category.
    - Prefers fresh unseen videos (+0.08 freshness bonus), but allows previously
      shown videos to return when fresh candidates are sparse or exhausted.
    - Followed creators (+0.12) and living taste profile qualities (+0.06) influence
      ranking WITHIN the tab without admitting off-topic videos.
    - Never pads with random videos if pool has fewer than length items.
    """
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

    seen_ids = set(preferences.seen_video_ids or [])
    followed_names = {fc.name.lower() for fc in (preferences.followed_creators or [])}
    followed_ch_ids = {fc.channel_id for fc in (preferences.followed_creators or []) if fc.channel_id}

    # Living taste profile preferred qualities
    pref_qualities = set()
    if preferences.living_taste_profile:
        pref_qualities = {q.lower() for q, _ in preferences.living_taste_profile.get_top_qualities(limit=3)}

    selected: list[FeedItem] = []
    used_channels: set[str] = set()

    def cat_item_score(item: FeedItem) -> float:
        ev = item.assessment
        chan_pen = 0.15 if item.video.channel in used_channels else 0.0

        # Freshness bonus: prioritize unseen videos, but allow seen videos to return
        freshness = 0.08 if item.video.id not in seen_ids else 0.0

        # Followed creator ranking boost within the tab
        is_followed = (
            item.video.channel.lower() in followed_names
            or (item.video.channel_id and item.video.channel_id in followed_ch_ids)
        )
        followed_boost = 0.12 if is_followed else 0.0

        # Quality alignment boost from living taste profile
        quality_boost = 0.0
        if pref_qualities:
            content_lower = f"{item.video.title} {item.video.description or ''}".lower()
            if any(q in content_lower for q in pref_qualities):
                quality_boost = 0.06

        if preferences.feed_mode == "entertainment":
            base = (ev.entertainment_score * 0.55) + (ev.novelty_score * 0.30) + (ev.composite_score * 0.15)
        elif preferences.feed_mode == "deep_learning":
            base = (ev.substance_score * 0.50) + (ev.composite_score * 0.30) + (ev.novelty_score * 0.20)
        else:
            base = ev.composite_score

        return base + freshness + followed_boost + quality_boost - chan_pen - _topic_repetition_penalty(item, selected)

    target_count = min(length, len(pool))
    restoration_budget = _restoration_budget(preferences)
    for i in range(target_count):
        if not pool:
            break
        distinct_pool = [item for item in pool if not _topic_repetition_penalty(item, selected)]
        within_budget = [item for item in distinct_pool if _within_restoration_budget(item, selected, restoration_budget)]
        if not within_budget:
            within_budget = [item for item in pool if _within_restoration_budget(item, selected, restoration_budget)]
        if not within_budget:
            break
        chosen = max(within_budget, key=cat_item_score)
        pool.remove(chosen)
        used_channels.add(chosen.video.channel)

        chosen.category = category_name
        chosen.is_discovery = False
        chosen.discovery_type = None

        if preferences.feed_mode == "entertainment":
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
            chosen.path_reason = f"Focused in '{category_name}': {default_reason}"
        elif preferences.feed_mode == "deep_learning":
            role_label, _, default_reason = DEEP_LEARNING_STEPS[i % len(DEEP_LEARNING_STEPS)]
            chosen.path_step = role_label
            chosen.path_reason = f"Focused in '{category_name}': {default_reason}"
        else:
            role_label, default_reason = MIX_ROLES[i % len(MIX_ROLES)]
            chosen.path_step = role_label
            chosen.path_reason = f"Focused in '{category_name}': {default_reason}"

        selected.append(chosen)

    return selected
