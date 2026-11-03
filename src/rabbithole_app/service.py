"""
Rabbithole Discovery Service.
Coordinates query expansion, candidate retrieval, anti-clickbait filtering, and feed curation.
"""
import os
import logging
from time import perf_counter
from typing import Optional

from rabbithole_app.models import (
    UserPreferences,
    FeedResponse,
    CuriosityQuery,
)
from rabbithole_app.query_expander import QueryExpander
from rabbithole_app.retriever import VideoRetriever
from rabbithole_app.filter_engine import FilterEngine
from rabbithole_app.journey import (
    build_path,
    build_mixed_feed,
    build_entertainment_feed,
    build_main_feed,
    build_category_feed,
)

logger = logging.getLogger(__name__)


def _clean_search_seed(preferences: UserPreferences) -> str:
    """Extracts a clean, short (1-3 word) topic suitable for search queries."""
    if preferences.active_category:
        return preferences.active_category.strip()
    if preferences.living_taste_profile and preferences.living_taste_profile.subject_affinities:
        top_subs = sorted(
            preferences.living_taste_profile.subject_affinities.items(),
            key=lambda x: x[1],
            reverse=True
        )
        if top_subs:
            return top_subs[0][0].strip()
    if preferences.categories:
        for c in preferences.categories:
            if c and len(c.split()) <= 4:
                return c.strip()
    raw = (preferences.interests or "").strip()
    if raw and len(raw.split()) <= 4:
        return raw
    if preferences.curiosity_archetypes:
        arch = preferences.curiosity_archetypes[0]
        arch_map = {
            "mechanist": "mechanical engineering",
            "detective": "forensic investigations",
            "visual_synthesizer": "visual mathematics",
            "craft_purist": "artisan craftsmanship",
            "game_theorist": "game theory",
            "playful_deconstructor": "satire comedy"
        }
        if arch in arch_map:
            return arch_map[arch]
    return "curiosity exploration"


class RabbitholeService:
    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        youtube_api_key: Optional[str] = None
    ):
        self.gemini_api_key = gemini_api_key if gemini_api_key is not None else os.getenv("GEMINI_API_KEY")
        self.expander = QueryExpander(api_key=self.gemini_api_key)
        self.retriever = VideoRetriever(youtube_api_key=youtube_api_key)
        self.filter_engine = FilterEngine(api_key=self.gemini_api_key)

    def generate_feed(self, preferences: UserPreferences, query_count: int = 5, top_k: Optional[int] = None) -> FeedResponse:
        """
        End-to-end feed generation pipeline:
        1. Expand interests into curiosity queries.
        2. Retrieve candidate videos across queries.
        3. Run two-tier anti-clickbait and intellectual substance filtering.
        4. Assemble enriched feed:
           - If active_category is specified: curated focused category feed (~5 videos).
           - If no active_category (Main Feed): balanced multi-category discovery feed (~10 videos)
             with 1-2 discovery slots and mixed content nature according to preferences.
        """
        logger.info(f"Generating feed for interests: '{preferences.interests}' (category={preferences.active_category}, serendipity={preferences.serendipity_level})")

        # A focused tab must be evaluated against that tab's subject. Keeping the
        # full setup interests here lets unrelated videos from other tabs pass
        # the relevance gate and receive a misleading category label.
        feed_preferences = (
            preferences.model_copy(update={
                "interests": preferences.active_category,
                "categories": [preferences.active_category],
            })
            if preferences.active_category else preferences
        )

        # Determine target length
        if preferences.active_category:
            target_len = top_k if top_k is not None else 5
        elif top_k is not None and top_k <= 5 and not preferences.categories:
            target_len = top_k
        else:
            target_len = top_k if top_k is not None else 10

        started_at = perf_counter()

        # Step 1: Query Expansion
        queries = self.expander.expand_queries(feed_preferences, count=query_count)

        # When followed creators are present (e.g. from YouTube sync), include a query for followed creators
        # so normal content from their subscribed channels naturally seeds the candidate pool!
        if feed_preferences.followed_creators and not feed_preferences.active_category:
            idx = preferences.refresh_index % len(feed_preferences.followed_creators)
            fc = feed_preferences.followed_creators[idx]
            q_type = "entertainment_comedy" if preferences.feed_mode == "entertainment" else "practitioner_deep_dive"
            queries.append(CuriosityQuery(
                query=f"{fc.name} popular funny entertaining" if preferences.feed_mode == "entertainment" else f"{fc.name} best videos",
                domain=fc.name,
                query_type=q_type,
                rationale=f"High-appeal content from followed creator {fc.name}."
            ))

        if preferences.recency_preference == "recent":
            for query in queries[:2]:
                query.sort_by_date = True

        queries_at = perf_counter()

        # Step 2: Candidate Retrieval
        candidates = self.retriever.retrieve_candidates(queries, max_per_query=8)
        retrieval_at = perf_counter()
        total_candidates = len(candidates)
        logger.info(f"Retrieved {total_candidates} candidate videos across {len(queries)} queries.")

        # Step 3: Filtering & Scoring
        feed_items, filtered_count = self.filter_engine.filter_and_rank(
            candidates,
            feed_preferences,
            top_k=30
        )
        ranking_at = perf_counter()

        # Step 3b: Entertainment-only secondary targeted search if pool has too few entertainment-first candidates
        if preferences.feed_mode == "entertainment":
            ent_candidates = [it for it in feed_items if it.assessment.is_entertainment_first]
            if len(ent_candidates) < target_len:
                logger.info(
                    f"Entertainment pool had only {len(ent_candidates)} verified entertainment-first items (target {target_len}). "
                    "Triggering targeted secondary search across alternative entertainment formats..."
                )
                existing_formats = {it.assessment.entertainment_format for it in ent_candidates if it.assessment.entertainment_format}
                retry_formats = [
                    fmt for fmt in [
                        "gameplay", "challenges", "sports_moments", "performance",
                        "animation", "creative_projects", "compelling_story", "comedy"
                    ]
                    if fmt not in existing_formats
                ]

                topic = _clean_search_seed(feed_preferences)
                retry_queries = []

                # If followed creators are present, also search for their entertaining uploads
                if feed_preferences.followed_creators:
                    for fc in feed_preferences.followed_creators[:2]:
                        retry_queries.append(CuriosityQuery(
                            query=f"{fc.name} funny moments highlights",
                            domain=fc.name,
                            query_type="entertainment_comedy",
                            rationale=f"Entertaining content from followed creator {fc.name}."
                        ))

                for fmt in retry_formats[:3]:
                    if fmt == "gameplay":
                        retry_queries.append(CuriosityQuery(
                            query=f"{topic} gameplay clutch moments playthrough",
                            domain=f"{topic.title()} Gameplay",
                            query_type="entertainment_gameplay",
                            rationale=f"Secondary entertainment search for {topic} gameplay."
                        ))
                    elif fmt == "challenges":
                        retry_queries.append(CuriosityQuery(
                            query=f"{topic} extreme challenges tournament competition",
                            domain=f"{topic.title()} Challenges",
                            query_type="entertainment_challenge",
                            rationale=f"Secondary entertainment search for {topic} challenges."
                        ))
                    elif fmt == "sports_moments":
                        retry_queries.append(CuriosityQuery(
                            query=f"{topic} greatest moments clutch highlights",
                            domain=f"{topic.title()} Highlights",
                            query_type="entertainment_sports",
                            rationale=f"Secondary entertainment search for {topic} sports highlights."
                        ))
                    elif fmt == "performance":
                        retry_queries.append(CuriosityQuery(
                            query=f"{topic} live musical performance acoustic showcase",
                            domain=f"{topic.title()} Performance",
                            query_type="entertainment_performance",
                            rationale=f"Secondary entertainment search for {topic} live performance."
                        ))
                    elif fmt == "animation":
                        retry_queries.append(CuriosityQuery(
                            query=f"{topic} 3d animated short film CGI animation",
                            domain=f"{topic.title()} Animation",
                            query_type="entertainment_creative",
                            rationale=f"Secondary entertainment search for {topic} animation."
                        ))
                    elif fmt == "creative_projects":
                        retry_queries.append(CuriosityQuery(
                            query=f"{topic} creative inventions satisfying build",
                            domain=f"{topic.title()} Creative Build",
                            query_type="entertainment_creative",
                            rationale=f"Secondary entertainment search for {topic} creative builds."
                        ))
                    elif fmt == "compelling_story":
                        retry_queries.append(CuriosityQuery(
                            query=f"{topic} bizarre unbelievable stories wild lore",
                            domain=f"{topic.title()} Lore",
                            query_type="entertainment_story",
                            rationale=f"Secondary entertainment search for {topic} lore."
                        ))
                    elif fmt == "comedy":
                        retry_queries.append(CuriosityQuery(
                            query=f"{topic} standup comedic sketch bloopers funniest moments",
                            domain=f"{topic.title()} Comedy",
                            query_type="entertainment_comedy",
                            rationale=f"Secondary entertainment search for {topic} comedy."
                        ))

                if retry_queries:
                    retry_candidates = self.retriever.retrieve_candidates(retry_queries, max_per_query=4)
                    total_candidates += len(retry_candidates)
                    retry_feed_items, retry_filtered = self.filter_engine.filter_and_rank(
                        retry_candidates,
                        feed_preferences,
                        top_k=20
                    )
                    filtered_count += retry_filtered
                    existing_ids = {it.video.id for it in feed_items}
                    for rit in retry_feed_items:
                        if rit.video.id not in existing_ids:
                            feed_items.append(rit)
                            existing_ids.add(rit.video.id)
                    queries.extend(retry_queries)

        # Step 3c: Category tab secondary targeted search if pool has too few on-topic candidates
        if preferences.active_category and len(feed_items) < target_len:
            cat_topic = preferences.active_category.strip()
            logger.info(
                f"Category tab '{cat_topic}' pool had only {len(feed_items)} items (target {target_len}). "
                "Triggering targeted secondary search for the category..."
            )
            cat_retry_queries = [
                CuriosityQuery(
                    query=f"{cat_topic} foundational concepts visual breakdown",
                    domain=f"{cat_topic.title()} Fundamentals",
                    query_type="first_principles",
                    rationale=f"Secondary targeted search for {cat_topic} fundamentals."
                ),
                CuriosityQuery(
                    query=f"{cat_topic} advanced practical demonstration masterclass",
                    domain=f"{cat_topic.title()} Masterclass",
                    query_type="practitioner_deep_dive",
                    rationale=f"Secondary targeted search for {cat_topic} masterclass."
                ),
                CuriosityQuery(
                    query=f"{cat_topic} documentary evolution deep dive",
                    domain=f"{cat_topic.title()} History & Evolution",
                    query_type="niche_history",
                    rationale=f"Secondary targeted search for {cat_topic} documentary."
                ),
            ]
            cat_retry_cands = self.retriever.retrieve_candidates(cat_retry_queries, max_per_query=4)
            total_candidates += len(cat_retry_cands)
            cat_retry_items, cat_retry_filtered = self.filter_engine.filter_and_rank(
                cat_retry_cands,
                feed_preferences,
                top_k=20
            )
            filtered_count += cat_retry_filtered
            existing_ids = {it.video.id for it in feed_items}
            for rit in cat_retry_items:
                if rit.video.id not in existing_ids:
                    feed_items.append(rit)
                    existing_ids.add(rit.video.id)
            queries.extend(cat_retry_queries)

        # Step 4: Feed Construction
        if preferences.active_category:
            path = build_category_feed(
                feed_items,
                feed_preferences,
                category_name=preferences.active_category,
                length=target_len,
                view=preferences.category_view or "new"
            )
        elif top_k == 5 and preferences.feed_mode == "deep_learning" and not preferences.categories:
            path = build_path(feed_items, preferences, length=target_len)
        elif top_k == 5 and preferences.feed_mode == "entertainment" and not preferences.categories:
            path = build_entertainment_feed(feed_items, preferences, length=target_len)
        elif top_k == 5 and not preferences.categories:
            path = build_mixed_feed(feed_items, preferences, length=target_len)
        else:
            # Main feed: 5 personalized, 3 adjacent discoveries, 2 independent discoveries
            path = build_main_feed(feed_items, preferences, length=target_len, discovery_target=5)

        logger.info(
            "Feed timing: queries %.1fs, YouTube search %.1fs, scoring %.1fs, total %.1fs",
            queries_at - started_at,
            retrieval_at - queries_at,
            ranking_at - retrieval_at,
            perf_counter() - started_at,
        )

        pool_exhausted = len(path) < target_len and (len(candidates) > 0 or len(preferences.recent_shown_video_ids) > 0)
        discovery_count = sum(1 for item in path if item.is_discovery)

        if preferences.feed_mode == "entertainment" and len(path) < target_len:
            pool_exhausted = True
            tab_desc = f" for '{preferences.active_category}'" if preferences.active_category else ""
            if len(path) == 0:
                status_msg = (
                    f"No verified entertainment-first videos found{tab_desc}. "
                    "The candidate pool contained only lectures, explainers, or previously seen items. "
                    "Rabbithole avoided diluting your Entertainment-only feed with lectures. Try exploring another angle."
                )
            else:
                status_msg = (
                    f"Returned a shorter feed of {len(path)} verified entertainment-first video{'s' if len(path) > 1 else ''}{tab_desc} "
                    f"(requested {target_len}). The candidate pool lacked enough varied, non-lecture entertainment videos "
                    "to fill the feed without diluting your Entertainment-only preference."
                )
        elif preferences.active_category and len(path) < target_len:
            pool_exhausted = True
            tab_name = preferences.active_category
            view = preferences.category_view or "new"
            if view == "deeper" and len(path) == 0:
                status_msg = (
                    f"No viewing or saved history found for '{tab_name}' yet. "
                    "Watch or save videos in this interest tab to unlock personalized 'Go Deeper' paths building on what you've learned."
                )
            elif view == "deeper":
                status_msg = (
                    f"Returned a focused feed of {len(path)} deeper video{'s' if len(path) > 1 else ''} for '{tab_name}' "
                    f"(requested {target_len}). Rabbithole avoided padding with superficial or introductory filler."
                )
            elif view == "connections":
                status_msg = (
                    f"Returned a focused feed of {len(path)} verified interdisciplinary connection{'s' if len(path) > 1 else ''} for '{tab_name}' "
                    f"(requested {target_len}). Rabbithole avoided padding with disconnected or single-topic filler."
                )
            elif len(path) == 0:
                status_msg = (
                    f"No on-topic videos found for '{tab_name}' meeting Rabbithole's standards. "
                    "Rabbithole avoided padding your tab with unrelated recommendations. Try exploring another angle."
                )
            else:
                status_msg = (
                    f"Returned a focused feed of {len(path)} video{'s' if len(path) > 1 else ''} for '{tab_name}' "
                    f"(requested {target_len}). After targeted searches, fewer than {target_len} verified on-topic videos met our "
                    "quality and variety standards without padding with unrelated content."
                )
        elif pool_exhausted:
            if len(path) == 0:
                status_msg = (
                    f"Candidate pool exhausted after filtering {len(preferences.recent_shown_video_ids)} previously seen videos. "
                    "No new fresh videos met the relevance and quality standards for this feed. Try clearing your history or exploring a new angle."
                )
            else:
                status_msg = (
                    f"Candidate pool partially exhausted: found {len(path)} varied video{'s' if len(path) > 1 else ''} "
                    f"(requested {target_len}). Other candidates were unavailable or repeated similar content; "
                    f"{len(preferences.recent_shown_video_ids)} previously seen videos were excluded."
                )
        else:
            mode_desc = (
                "entertainment feed" if preferences.feed_mode == "entertainment"
                else "deep discovery path" if preferences.feed_mode == "deep_learning"
                else "discovery feed"
            )
            tab_desc = f" for '{preferences.active_category}'" if preferences.active_category else " main feed"
            seen_note = f" Excluded {len(preferences.recent_shown_video_ids)} previously seen videos." if preferences.seen_video_ids else ""
            status_msg = (
                f"Curated a {len(path)}-video {mode_desc}{tab_desc} from {total_candidates} candidates across {len(queries)} exploratory queries.{seen_note}"
            )

        return FeedResponse(
            items=path,
            generated_queries=queries,
            total_candidates_analyzed=total_candidates,
            filtered_out_count=filtered_count,
            seen_count=len(preferences.seen_video_ids),
            pool_exhausted=pool_exhausted,
            status_message=status_msg,
            discovery_count=discovery_count,
        )

    def branch_out(self, branch_topic: str, base_preferences: UserPreferences) -> FeedResponse:
        """
        Creates a new exploratory feed centered around a specific tangent or branch.
        """
        branch_prefs = UserPreferences(
            interests=branch_topic,
            familiar_topics=base_preferences.familiar_topics,
            explore_topics=base_preferences.explore_topics,
            avoid_topics=base_preferences.avoid_topics,
            learning_goal=base_preferences.learning_goal,
            serendipity_level=min(base_preferences.serendipity_level + 1, 5),
            depth_level=base_preferences.depth_level,
            min_duration_minutes=base_preferences.min_duration_minutes,
            max_duration_minutes=base_preferences.max_duration_minutes,
            avoid_sensationalism=base_preferences.avoid_sensationalism,
            seed_topic=branch_topic,
            experience_type=base_preferences.experience_type,
            feed_mode=base_preferences.feed_mode,
            recency_preference=base_preferences.recency_preference,
            seen_video_ids=base_preferences.seen_video_ids,
            refresh_index=base_preferences.refresh_index + 1,
            recent_queries=base_preferences.recent_queries,
            recent_shown_video_ids=base_preferences.recent_shown_video_ids,
            completed_video_ids=base_preferences.completed_video_ids,
            watched_channels=base_preferences.watched_channels,
            watched_topics=base_preferences.watched_topics,
            disliked_video_ids=base_preferences.disliked_video_ids,
            disliked_records=base_preferences.disliked_records,
            categories=base_preferences.categories,
            dismissed_categories=base_preferences.dismissed_categories,
            emerging_interests=base_preferences.emerging_interests,
            watch_later_video_ids=base_preferences.watch_later_video_ids,
            followed_creators=base_preferences.followed_creators,
        )
        return self.generate_feed(branch_prefs, top_k=5)
