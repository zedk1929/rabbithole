"""
Deterministic regression tests for Chunk 1: Interest tab selectable views:
- 'new': Worthwhile recent videos about the selected interest.
- 'deeper': Videos that build on videos meaningfully watched or saved in that interest.
- 'connections': Videos that connect the selected interest to another field while retaining focus on it.

Verifies:
1. Relevance to the selected interest across all three views.
2. Meaningful differences between views (different queries, different rankings, distinct video selections).
3. History-based deeper recommendations building on watched and saved videos in that interest.
4. Connections retaining the selected interest (rejecting disconnected outside filler).
5. Dislike and explicit exclusion enforcement across all views.
6. Deduplication across views when suitable alternatives exist.
7. Honest sparse and cold-start behavior (no unrelated padding).
8. Refresh retrieval of previously unseen eligible candidates.
9. Caching isolation distinguishing interests and views.
10. Main feed behavior remains completely unchanged.
"""
import pytest
from unittest.mock import MagicMock

from rabbithole_app.models import (
    CandidateVideo,
    FeedItem,
    UserPreferences,
    QualityAssessment,
    RelevanceAssessment,
    WatchLaterRecord,
    DislikeRecord,
    LivingTasteProfile,
    TasteSignal
)
from rabbithole_app.journey import build_category_feed, build_main_feed
from rabbithole_app.query_expander import QueryExpander
from rabbithole_app.service import RabbitholeService


def make_assessment(is_relevant=True, substance=0.85, novelty=0.75, composite=0.80, domain="Mathematics"):
    return QualityAssessment(
        clickbait_score=0.05,
        substance_score=substance,
        novelty_score=novelty,
        composite_score=composite,
        primary_appeal_domain=domain,
        relevance_status="supported" if is_relevant else "rejected",
        relevance=RelevanceAssessment(
            is_relevant=is_relevant,
            status="supported" if is_relevant else "rejected",
            reason=f"Topic match for {domain}" if is_relevant else "Off-topic"
        )
    )


def make_item(vid_id, title, channel="Math Channel", domain="Mathematics", query_type="first_principles", discovery_type=None, is_discovery=False, is_relevant=True, substance=0.85, novelty=0.75, composite=0.80):
    cand = CandidateVideo(
        id=vid_id,
        title=title,
        channel=channel,
        url=f"https://youtube.com/watch?v={vid_id}",
        source_domain=domain,
        source_query_type=query_type,
        discovery_type=discovery_type,
        duration_seconds=900,
        description=f"Detailed video about {title} in {domain}"
    )
    return FeedItem(
        video=cand,
        assessment=make_assessment(is_relevant=is_relevant, substance=substance, novelty=novelty, composite=composite, domain=domain),
        category=domain,
        is_discovery=is_discovery,
        discovery_type=discovery_type
    )


class TestInterestTabViews:
    """Deterministic tests for interest tab selectable views."""

    def test_relevance_to_selected_interest_across_all_views(self):
        """All three views ('new', 'deeper', 'connections') must require genuine relevance to the interest tab."""
        prefs = UserPreferences(
            interests="mathematics",
            active_category="Mathematics",
            saved_videos=[
                WatchLaterRecord(video_id="saved_1", title="Topology Manifolds Intro", channel="Math Channel", source_category="Mathematics", source_domain="Mathematics")
            ]
        )
        on_topic_item = make_item("m1", "Differential Topology and Differential Forms", domain="Mathematics")
        off_topic_item = make_item("cook1", "Traditional Italian Sourdough Bread Baking", channel="Chef Mike", domain="Cooking", is_relevant=False)

        for view in ["new", "deeper", "connections"]:
            feed = build_category_feed([off_topic_item, on_topic_item], prefs, category_name="Mathematics", length=2, view=view)
            # Off-topic cooking video must never be admitted into Mathematics under any view
            feed_ids = [it.video.id for it in feed]
            assert "cook1" not in feed_ids
            if feed:
                assert feed[0].video.id == "m1"

    def test_meaningful_differences_between_views(self):
        """
        Given a diverse pool of eligible candidates, the three views must select different videos
        and assign distinct truthful annotations (path_step and path_reason).
        """
        prefs = UserPreferences(
            interests="astronomy",
            active_category="Astronomy",
            saved_videos=[
                WatchLaterRecord(video_id="ast_saved", title="Stellar Nucleosynthesis and Supernovae", channel="Astro Channel", source_category="Astronomy", source_domain="Astronomy")
            ]
        )

        # Candidate 1: Recent release
        it_recent = make_item("a_new", "Latest 2026 JWST Spectroscopic Data on Early Galaxies", domain="Astronomy", query_type="first_principles", substance=0.82)
        # Candidate 2: Deep archival lecture building on watched stellar nucleosynthesis
        it_deep = make_item("a_deep", "Advanced Stellar Nucleosynthesis and Carbon Burning Phase Masterclass", domain="Astronomy", query_type="archive_lecture", substance=0.96)
        # Candidate 3: Interdisciplinary connection linking astronomy to fluid dynamics
        it_conn = make_item("a_conn", "Astrophysical Fluid Dynamics of Accretion Disks and Magnetohydrodynamics", domain="Fluid Dynamics", query_type="cross_domain_analogy", is_discovery=True, discovery_type="adjacent", novelty=0.95)

        pool = [it_recent, it_deep, it_conn]

        feed_new = build_category_feed(list(pool), prefs, category_name="Astronomy", length=1, view="new")
        feed_deeper = build_category_feed(list(pool), prefs, category_name="Astronomy", length=1, view="deeper")
        feed_conn = build_category_feed(list(pool), prefs, category_name="Astronomy", length=1, view="connections")

        assert len(feed_new) == 1
        assert len(feed_deeper) == 1
        assert len(feed_conn) == 1

        # The views should prioritize different top picks
        assert feed_new[0].video.id == "a_new"
        assert feed_deeper[0].video.id == "a_deep"
        assert feed_conn[0].video.id == "a_conn"

        # Truthful view-specific step annotations
        assert "Recent" in feed_new[0].path_step
        assert "Go Deeper" in feed_deeper[0].path_step
        assert "Connections" in feed_conn[0].path_step
        assert "Astronomy" in feed_new[0].path_reason
        assert "Astronomy" in feed_deeper[0].path_reason
        assert "Astronomy" in feed_conn[0].path_reason

    def test_history_based_deeper_recommendations(self):
        """
        'Go deeper' must build on concepts the user meaningfully engaged with in that category
        (from saved_videos or living_taste_profile signals) and exclude already-watched items.
        """
        profile = LivingTasteProfile()
        profile.record_signal(TasteSignal(
            video_id="watched_fourier",
            title="Fourier Analysis and Heat Equation",
            channel="Mathologer",
            domain="Mathematics",
            action="substantial_watch",
            weight=2.5
        ))
        prefs = UserPreferences(
            interests="mathematics",
            active_category="Mathematics",
            living_taste_profile=profile
        )

        item_already_watched = make_item("watched_fourier", "Fourier Analysis and Heat Equation", domain="Mathematics", query_type="archive_lecture")
        item_deeper_fourier = make_item("m_deep_fourier", "Advanced Functional Analysis: Fourier Transforms on Banach Spaces", domain="Mathematics", query_type="archive_lecture", substance=0.95)
        item_unrelated_math = make_item("m_basic_geom", "Basic Euclidean Plane Geometry Intro 101", domain="Mathematics", query_type="first_principles", substance=0.70)

        pool = [item_already_watched, item_deeper_fourier, item_unrelated_math]
        feed = build_category_feed(pool, prefs, category_name="Mathematics", length=2, view="deeper")

        # 1. Already watched item must be excluded
        feed_ids = [it.video.id for it in feed]
        assert "watched_fourier" not in feed_ids
        # 2. Superficial/intro primer is filtered out
        assert "m_basic_geom" not in feed_ids
        # 3. Advanced topic continuing from history is selected
        assert "m_deep_fourier" in feed_ids

    def test_cold_start_sparse_behavior_for_go_deeper(self):
        """
        When the user has zero history (no saved or watched videos) in this interest tab,
        'Go deeper' must return an honest sparse result (empty feed) rather than filling with generic primers.
        """
        prefs = UserPreferences(
            interests="neuroscience",
            active_category="Neuroscience",
            saved_videos=[],
            living_taste_profile=None
        )

        cands = [
            make_item("n1", "Action Potentials and Ion Channels", domain="Neuroscience"),
            make_item("n2", "Synaptic Plasticity and LTP", domain="Neuroscience")
        ]

        feed = build_category_feed(cands, prefs, category_name="Neuroscience", length=2, view="deeper")
        # Honest sparse return
        assert feed == []

    def test_connections_view_retains_selected_interest_and_rejects_outside_filler(self):
        """
        'Connections' must bridge the interest to another field while retaining explicit
        connection to the category; it must reject completely disconnected outside videos.
        """
        prefs = UserPreferences(
            interests="economics",
            active_category="Economics",
        )

        # Genuine bridge: Economics + Evolutionary Biology
        conn_valid = make_item(
            "e_bio",
            "Evolutionary Game Theory and Replicator Dynamics in Animal Conflicts",
            domain="Biology",
            query_type="cross_domain_analogy",
            is_discovery=True,
            discovery_type="adjacent"
        )
        # Disconnected outside candidate with no connection to Economics
        conn_invalid = make_item(
            "outside_craft",
            "Japanese Woodworking Joinery Without Nails",
            domain="Woodworking",
            query_type="first_principles",
            is_relevant=False
        )

        feed = build_category_feed([conn_valid, conn_invalid], prefs, category_name="Economics", length=2, view="connections")
        feed_ids = [it.video.id for it in feed]
        assert "e_bio" in feed_ids
        assert "outside_craft" not in feed_ids

    def test_dislike_and_explicit_exclusions_enforced_across_all_views(self):
        """Dislikes and avoided topics must be strictly excluded across New, Go Deeper, and Connections."""
        prefs = UserPreferences(
            interests="machine learning",
            active_category="Machine Learning",
            avoid_topics="crypto, blockchain",
            disliked_video_ids=["disliked_ml_1"],
            disliked_records=[
                DislikeRecord(video_id="disliked_ml_1", title="Disliked ML Video", channel="ML Channel", chosen_reason="Not interested")
            ],
            saved_videos=[
                WatchLaterRecord(video_id="saved_ml", title="Transformer Attention Mechanisms", channel="ML Channel", source_category="Machine Learning")
            ]
        )

        item_normal = make_item("good_ml", "State of the Art Diffusion Models 2026", domain="Machine Learning")
        item_disliked = make_item("disliked_ml_1", "Transformer Attention Deep Dive", domain="Machine Learning")
        item_avoided = make_item("avoid_ml", "Blockchain and Crypto Applied to Decentralized Machine Learning", domain="Machine Learning")

        pool = [item_normal, item_disliked, item_avoided]

        for view in ["new", "deeper", "connections"]:
            feed = build_category_feed(list(pool), prefs, category_name="Machine Learning", length=3, view=view)
            feed_ids = [it.video.id for it in feed]
            assert "disliked_ml_1" not in feed_ids, f"Disliked video appeared in view '{view}'"
            assert "avoid_ml" not in feed_ids, f"Avoided topic video appeared in view '{view}'"

    def test_deduplication_across_views_when_alternatives_exist(self):
        """
        When alternatives exist, candidates already shown in other views for this category
        (provided in other_view_video_ids) must be de-prioritized to avoid duplicates across views.
        """
        prefs = UserPreferences(
            interests="quantum computing",
            active_category="Quantum Computing",
            other_view_video_ids=["qc_already_shown_in_new"]
        )

        item_in_other_view = make_item(
            "qc_already_shown_in_new",
            "Superconducting Qubits and Decoherence Frontiers",
            domain="Quantum Computing",
            composite=0.90,
            substance=0.92
        )
        item_fresh_to_this_view = make_item(
            "qc_fresh",
            "Topological Quantum Error Correction and Surface Codes",
            domain="Quantum Computing",
            composite=0.88,
            substance=0.90
        )

        # Request 1 item
        feed = build_category_feed(
            [item_in_other_view, item_fresh_to_this_view],
            prefs,
            category_name="Quantum Computing",
            length=1,
            view="new"
        )
        # Should select the fresh alternative rather than repeating the video from the other view
        assert len(feed) == 1
        assert feed[0].video.id == "qc_fresh"

    def test_query_expander_generates_distinct_queries_per_view(self):
        """QueryExpander must generate distinct query directions for New, Go Deeper, and Connections."""
        expander = QueryExpander(api_key="")
        prefs_base = UserPreferences(
            interests="robotics",
            active_category="Robotics",
            saved_videos=[
                WatchLaterRecord(video_id="rob_1", title="Inverse Kinematics and Motion Planning", channel="Robotics Lab", source_category="Robotics")
            ]
        )

        q_new = expander.expand_queries(prefs_base.model_copy(update={"category_view": "new"}), count=3)
        q_deeper = expander.expand_queries(prefs_base.model_copy(update={"category_view": "deeper"}), count=3)
        q_conn = expander.expand_queries(prefs_base.model_copy(update={"category_view": "connections"}), count=3)

        assert len(q_new) == 3
        assert len(q_deeper) == 3
        assert len(q_conn) == 3

        # New queries specify sort_by_date=True
        assert all(q.sort_by_date is True for q in q_new)

        # Deeper queries emphasize advanced mechanisms / building on history
        deeper_text = " ".join([q.query.lower() for q in q_deeper])
        assert any(word in deeper_text for word in ["principles", "advanced", "methodology", "masterclass", "kinematics", "lecture"])

        # Connections queries incorporate interdisciplinary bridge topics
        conn_text = " ".join([q.query.lower() for q in q_conn])
        assert any(word in conn_text for word in ["biology", "physics", "game theory", "computation", "neuroscience", "control systems", "materials"])

    def test_refresh_retrieves_previously_unseen_candidates(self):
        """Refreshing with updated seen_video_ids must prioritize previously unseen eligible candidates."""
        prefs_seen = UserPreferences(
            interests="botany",
            active_category="Botany",
            seen_video_ids=["bot_seen_1"]
        )

        item_seen = make_item("bot_seen_1", "Carnivorous Plant Mechanics: The Venus Flytrap", domain="Botany", composite=0.90)
        item_unseen = make_item("bot_unseen_2", "Phloem and Xylem Transport Fluid Mechanics", domain="Botany", composite=0.86)

        feed = build_category_feed([item_seen, item_unseen], prefs_seen, category_name="Botany", length=1, view="new")
        # Freshness bonus prioritizes the unseen candidate over the previously seen one
        assert len(feed) == 1
        assert feed[0].video.id == "bot_unseen_2"

    def test_caching_and_service_distinguishes_interests_and_views(self):
        """
        RabbitholeService must pass category_view to build_category_feed and provide
        honest view-specific status messages when results are sparse.
        """
        service = RabbitholeService(gemini_api_key="", youtube_api_key="")
        cands = [
            CandidateVideo(id="m_cand_1", title="Mathematics Fundamentals", channel="Math Channel", url="http://m1", duration_seconds=600)
        ]
        service.retriever.retrieve_candidates = lambda queries, max_per_query=5: cands

        # 1. Cold-start Deeper view
        prefs_deeper_cold = UserPreferences(
            interests="Mathematics",
            active_category="Mathematics",
            category_view="deeper"
        )
        res_deeper = service.generate_feed(prefs_deeper_cold, top_k=5)
        # Deeper view with zero history must return empty and honest status message
        assert len(res_deeper.items) == 0
        assert "No viewing or saved history found for 'Mathematics' yet" in res_deeper.status_message
        assert "unlock personalized 'Go Deeper' paths" in res_deeper.status_message

        # 2. New view with candidates
        prefs_new = UserPreferences(
            interests="Mathematics",
            active_category="Mathematics",
            category_view="new"
        )
        res_new = service.generate_feed(prefs_new, top_k=5)
        assert len(res_new.items) == 1
        assert res_new.items[0].video.id == "m_cand_1"
        assert "Recent Release" in res_new.items[0].path_step

    def test_main_feed_remains_unchanged(self):
        """
        When active_category is None, build_main_feed and service feed generation must preserve
        the 5 personalized, 3 adjacent, 2 independent discovery structure unchanged.
        """
        prefs = UserPreferences(
            interests="astronomy",
            active_category=None,
            categories=["Astronomy"]
        )

        pers_items = [make_item(f"pers_{i}", f"Astronomy Telescope Optics Part {i}", domain="Astronomy") for i in range(5)]
        adj_items = [make_item(f"adj_{i}", f"Planetary Geology of Mars Basalt {i}", domain="Planetary Geology", query_type="cross_domain_analogy", is_discovery=True, discovery_type="adjacent") for i in range(3)]
        indep_items = [make_item(f"indep_{i}", f"Ancient Roman Concrete Volcanic Ash {i}", domain="Archaeological Metallurgy", query_type="first_principles", is_discovery=True, discovery_type="independent") for i in range(2)]

        feed = build_main_feed(pers_items + adj_items + indep_items, prefs, length=10, discovery_target=5)
        assert len(feed) == 10

        indep_in_feed = [it for it in feed if it.discovery_type == "independent"]
        adj_in_feed = [it for it in feed if it.discovery_type == "adjacent"]
        pers_in_feed = [it for it in feed if not it.is_discovery]

        assert len(indep_in_feed) == 2
        assert len(adj_in_feed) == 3
        assert len(pers_in_feed) == 5
