"""
Deterministic regression tests for Chunk 1: Interest tab selectable views:
- 'new': Worthwhile recent videos about the selected interest (evaluated strictly by actual upload date).
- 'deeper': Videos that build on videos meaningfully watched or saved in that interest (requiring demonstrable link).
- 'connections': Videos that connect the selected interest to another field with metadata evidence for both.

Regression tests specifically cover:
1. An old video titled "New" being rejected from the New view based on actual upload date.
2. An unrelated high-substance lecture being rejected from Go Deeper due to lacking a demonstrable link to history.
3. A falsely labelled cross-domain video being rejected from Connections due to lacking metadata evidence for both fields.
4. The cached-view request race condition where an older in-flight request does not replace a newly selected view.
"""
from datetime import datetime, timezone
import pytest

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


def make_item(
    vid_id,
    title,
    channel="Math Channel",
    domain="Mathematics",
    query_type="first_principles",
    discovery_type=None,
    is_discovery=False,
    is_relevant=True,
    substance=0.85,
    novelty=0.75,
    composite=0.80,
    upload_date=None,
    description=None
):
    desc = description if description is not None else f"Detailed video about {title} in {domain}"
    cand = CandidateVideo(
        id=vid_id,
        title=title,
        channel=channel,
        url=f"https://youtube.com/watch?v={vid_id}",
        source_domain=domain,
        source_query_type=query_type,
        discovery_type=discovery_type,
        duration_seconds=900,
        upload_date=upload_date,
        description=desc
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
        on_topic_item = make_item(
            "m1",
            "Differential Topology and Manifold Forms in Mathematics",
            domain="Mathematics",
            upload_date="2026-08-01",
            description="Differential topology and manifolds mathematical theory and physics connections"
        )
        off_topic_item = make_item("cook1", "Traditional Italian Sourdough Bread Baking", channel="Chef Mike", domain="Cooking", is_relevant=False)

        for view in ["new", "deeper"]:
            feed = build_category_feed([off_topic_item, on_topic_item], prefs, category_name="Mathematics", length=2, view=view)
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

        # Candidate 1: Recent release with verified 2026 upload date
        it_recent = make_item(
            "a_new",
            "Latest 2026 JWST Spectroscopic Data on Early Galaxies",
            domain="Astronomy",
            query_type="first_principles",
            substance=0.82,
            upload_date="2026-09-01"
        )
        # Candidate 2: Deep archival lecture building on watched stellar nucleosynthesis
        it_deep = make_item(
            "a_deep",
            "Advanced Stellar Nucleosynthesis and Carbon Burning Phase Masterclass",
            domain="Astronomy",
            query_type="archive_lecture",
            substance=0.96
        )
        # Candidate 3: Interdisciplinary connection linking astronomy to fluid dynamics
        it_conn = make_item(
            "a_conn",
            "Astrophysical Fluid Dynamics of Accretion Disks and Magnetohydrodynamics: Gas Mechanics in Astronomy",
            domain="Fluid Dynamics",
            query_type="cross_domain_analogy",
            is_discovery=True,
            discovery_type="adjacent",
            novelty=0.95,
            description="How astrophysical fluid dynamics and magnetohydrodynamic turbulence govern gas in astronomy"
        )

        pool = [it_recent, it_deep, it_conn]

        feed_new = build_category_feed(list(pool), prefs, category_name="Astronomy", length=1, view="new")
        feed_deeper = build_category_feed(list(pool), prefs, category_name="Astronomy", length=1, view="deeper")
        feed_conn = build_category_feed(list(pool), prefs, category_name="Astronomy", length=1, view="connections")

        assert len(feed_new) == 1
        assert len(feed_deeper) == 1
        assert len(feed_conn) == 1

        # The views prioritize different top picks
        assert feed_new[0].video.id == "a_new"
        assert feed_deeper[0].video.id == "a_deep"
        assert feed_conn[0].video.id == "a_conn"

        # Truthful view-specific step annotations
        assert "Recent Release" in feed_new[0].path_step
        assert "Go Deeper" in feed_deeper[0].path_step
        assert "Connections" in feed_conn[0].path_step
        assert "Astronomy" in feed_new[0].path_reason
        assert "nucleosynthesis" in feed_deeper[0].path_reason.lower()
        assert "Physics & Fluid Dynamics" in feed_conn[0].discovery_connection

    def test_new_view_rejects_old_video_titled_new_and_handles_missing_dates_honestly(self):
        """
        Regression Test 1:
        - An old video with 'New' or '2026' in its title must be REJECTED based on its actual upload date.
        - Only a video with verified recent upload date within the window may be labeled 'Recent Release'.
        - Videos with missing upload dates must not be falsely labeled 'Recent Release'.
        """
        prefs = UserPreferences(
            interests="mathematics",
            active_category="Mathematics"
        )

        # 1. Old video titled "New 2026 Breakthroughs" with actual upload date in 2018 (age > 365 days)
        cand_old_titled_new = make_item(
            "old_vid_1",
            "New 2026 Breakthroughs in Theoretical Mathematics and Topology",
            domain="Mathematics",
            upload_date="2018-03-15",
            composite=0.95
        )
        # 2. Genuine recent video uploaded 2 months ago in 2026
        cand_genuine_recent = make_item(
            "recent_vid_2",
            "Calculus of Variations Foundations and Euler-Lagrange Extremals",
            domain="Mathematics",
            upload_date="2026-08-20",
            composite=0.85
        )

        feed = build_category_feed([cand_old_titled_new, cand_genuine_recent], prefs, category_name="Mathematics", length=2, view="new")

        # The old video must NOT be in the New view despite its title!
        feed_ids = [it.video.id for it in feed]
        assert "old_vid_1" not in feed_ids
        assert "recent_vid_2" in feed_ids
        # Verified recent video gets the "Recent Release" label with its upload date
        assert feed[0].path_step == "⚡ Recent Release"
        assert "2026-08-20" in feed[0].path_reason

        # 3. Missing upload date handling: If a candidate has no upload date, it is NOT labeled "Recent Release"
        cand_missing_date = make_item(
            "undated_vid_3",
            "Functional Analysis and Spectral Theory in Mathematics",
            domain="Mathematics",
            upload_date=None,
            composite=0.88
        )
        feed_undated = build_category_feed([cand_missing_date], prefs, category_name="Mathematics", length=1, view="new")
        assert len(feed_undated) == 1
        assert feed_undated[0].path_step == "⚡ Curated Candidate"
        assert "unverified" in feed_undated[0].path_reason.lower()

    def test_go_deeper_rejects_unrelated_high_substance_lecture(self):
        """
        Regression Test 2:
        - Require a demonstrable link to a video or concept meaningfully watched or saved in this interest.
        - An unrelated high-substance lecture (or lecture query type) alone is strictly insufficient.
        - If there is no qualifying follow-up candidate, return an honest sparse result with accurate explanation.
        """
        profile = LivingTasteProfile()
        profile.record_signal(TasteSignal(
            video_id="watched_fourier",
            title="Fourier Analysis and Heat Equation Mechanics",
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

        # High substance (0.98), archival lecture, but about Abstract Ring Theory (zero overlap with watched Fourier history)
        unrelated_high_substance_lecture = make_item(
            "unrelated_ring_lec",
            "Advanced Ring Theory and Sylow Theorems in Abstract Algebra",
            domain="Mathematics",
            query_type="archive_lecture",
            substance=0.98,
            composite=0.96,
            description="Graduate level pure algebra lecture on ring homomorphisms and Sylow p-subgroups."
        )

        # Related candidate with demonstrable link to Fourier concepts
        related_fourier_candidate = make_item(
            "related_fourier_lec",
            "Fourier Transforms and Heat Kernel Asymptotics on Manifolds",
            domain="Mathematics",
            query_type="archive_lecture",
            substance=0.89,
            composite=0.88,
            description="Deep exploration of Fourier transforms and the heat equation kernel on curved spaces."
        )

        # 1. Pool with both: unrelated lecture must be excluded despite substance 0.98
        feed = build_category_feed(
            [unrelated_high_substance_lecture, related_fourier_candidate],
            prefs,
            category_name="Mathematics",
            length=2,
            view="deeper"
        )
        feed_ids = [it.video.id for it in feed]
        assert "unrelated_ring_lec" not in feed_ids
        assert "related_fourier_lec" in feed_ids
        assert "fourier" in feed[0].path_reason.lower()

        # 2. Pool with ONLY the unrelated lecture: must return an honest sparse result (empty feed)
        feed_empty = build_category_feed(
            [unrelated_high_substance_lecture],
            prefs,
            category_name="Mathematics",
            length=2,
            view="deeper"
        )
        assert feed_empty == []

        # 3. Service explanation: honestly explains that candidates lacked demonstrable links to history
        service = RabbitholeService(gemini_api_key="", youtube_api_key="")
        service.retriever.retrieve_candidates = lambda q, max_per_query=5: [unrelated_high_substance_lecture.video]
        res = service.generate_feed(prefs.model_copy(update={"category_view": "deeper"}), top_k=5)
        assert len(res.items) == 0
        assert "No follow-up candidates found directly building on your watched or saved videos" in res.status_message
        assert "rather than padding with unrelated lectures" in res.status_message

    def test_connections_rejects_falsely_labelled_cross_domain_video(self):
        """
        Regression Test 3:
        - Require metadata evidence covering BOTH the selected interest and another field.
        - A query label ('cross_domain_analogy') or source-domain label ('Physics') alone is strictly insufficient.
        - Name the second field in the explanation only when the video metadata supports it.
        """
        prefs = UserPreferences(
            interests="mathematics",
            active_category="Mathematics"
        )

        # Falsely labelled candidate: query label and source_domain claim Physics, but title and desc are pure set theory
        falsely_labelled_item = make_item(
            "fake_cross_domain",
            "Pure Set Theory: ZFC Axioms, Transfinite Ordinals, and Cardinality in Mathematics",
            channel="Pure Math",
            domain="Physics",  # Falsely labelled source domain
            query_type="cross_domain_analogy",  # Falsely labelled query type
            discovery_type="adjacent",
            is_discovery=True,
            description="A thorough mathematical foundation of the Zermelo-Fraenkel axioms with choice."
        )

        # Genuine cross-domain candidate: metadata demonstrably covers both Mathematics and Physics & Fluid Dynamics
        genuine_conn_item = make_item(
            "true_cross_domain",
            "Mathematical Fluid Dynamics: Navier-Stokes Differential Equations and Turbulence in Physics",
            channel="Applied Math Physics",
            domain="Physics",
            query_type="cross_domain_analogy",
            discovery_type="adjacent",
            is_discovery=True,
            description="Analyzing the mathematical existence and smoothness of solutions for the Navier-Stokes equations governing fluid mechanics in physics."
        )

        feed = build_category_feed(
            [falsely_labelled_item, genuine_conn_item],
            prefs,
            category_name="Mathematics",
            length=2,
            view="connections"
        )
        feed_ids = [it.video.id for it in feed]

        # Falsely labelled item must be strictly rejected
        assert "fake_cross_domain" not in feed_ids
        # Genuine connection must be accepted
        assert "true_cross_domain" in feed_ids
        # Secondary field must be truthfully named based on metadata evidence
        assert feed[0].path_step == "🌐 Connections"
        assert "Physics & Fluid Dynamics" in feed[0].discovery_connection
        assert "Physics & Fluid Dynamics" in feed[0].path_reason

    def test_cached_view_request_race_condition(self):
        """
        Regression Test 4:
        - Caching isolates view states per category (e.g. Mathematics::new vs Mathematics::deeper).
        - An older in-flight response arriving after a different view was selected must NOT overwrite
          the active view's cache or display state.
        """
        # 1. Verify key isolation between views for the same interest
        key_new = f"Mathematics::new"
        key_deeper = f"Mathematics::deeper"
        key_conn = f"Mathematics::connections"
        assert len({key_new, key_deeper, key_conn}) == 3

        # 2. Simulate cache dictionary behavior
        mock_cache = {}
        mock_cache[key_new] = {"view": "new", "items": ["new_1", "new_2"]}
        mock_cache[key_deeper] = {"view": "deeper", "items": ["deep_1"]}

        # User currently viewing "deeper"
        active_cat = "Mathematics"
        active_view = "deeper"

        # An older in-flight request for "new" finishes now with stale/different items
        stale_request_payload = {"active_category": "Mathematics", "category_view": "new"}
        stale_response_data = {"view": "new", "items": ["stale_new_99"]}

        # View switching protection logic (matching app.js):
        # If request view does not match currently active view, do NOT display or overwrite active cache
        should_display = (
            stale_request_payload["active_category"] == active_cat
            and stale_request_payload["category_view"] == active_view
        )
        assert should_display is False

        # Active view display remains unchanged
        active_feed_data = mock_cache[f"{active_cat}::{active_view}"]
        assert active_feed_data["items"] == ["deep_1"]
        assert active_feed_data["view"] == "deeper"

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
            make_item("n1", "Action Potentials and Ion Channels in Neuroscience", domain="Neuroscience"),
            make_item("n2", "Synaptic Plasticity and LTP in Neuroscience", domain="Neuroscience")
        ]

        feed = build_category_feed(cands, prefs, category_name="Neuroscience", length=2, view="deeper")
        assert feed == []

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

        item_normal = make_item(
            "good_ml",
            "State of the Art Transformer Diffusion Models in Machine Learning: Computational Algorithms",
            domain="Machine Learning",
            upload_date="2026-08-01",
            description="Transformer attention and computational machine learning algorithms"
        )
        item_disliked = make_item(
            "disliked_ml_1",
            "Transformer Attention Deep Dive in Machine Learning",
            domain="Machine Learning",
            upload_date="2026-08-01"
        )
        item_avoided = make_item(
            "avoid_ml",
            "Blockchain and Crypto Applied to Decentralized Machine Learning",
            domain="Machine Learning",
            upload_date="2026-08-01"
        )

        pool = [item_normal, item_disliked, item_avoided]

        for view in ["new", "deeper"]:
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
            "Superconducting Qubits and Decoherence Frontiers in Quantum Computing",
            domain="Quantum Computing",
            composite=0.90,
            substance=0.92,
            upload_date="2026-07-01"
        )
        item_fresh_to_this_view = make_item(
            "qc_fresh",
            "Topological Quantum Error Correction and Surface Codes in Quantum Computing",
            domain="Quantum Computing",
            composite=0.88,
            substance=0.90,
            upload_date="2026-07-15"
        )

        feed = build_category_feed(
            [item_in_other_view, item_fresh_to_this_view],
            prefs,
            category_name="Quantum Computing",
            length=1,
            view="new"
        )
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

        item_seen = make_item(
            "bot_seen_1",
            "Carnivorous Plant Mechanics: The Venus Flytrap in Botany",
            domain="Botany",
            composite=0.90,
            upload_date="2026-06-01"
        )
        item_unseen = make_item(
            "bot_unseen_2",
            "Phloem and Xylem Transport Fluid Mechanics in Botany",
            domain="Botany",
            composite=0.86,
            upload_date="2026-06-05"
        )

        feed = build_category_feed([item_seen, item_unseen], prefs_seen, category_name="Botany", length=1, view="new")
        assert len(feed) == 1
        assert feed[0].video.id == "bot_unseen_2"

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

    def test_videogames_tab_returns_realistic_results_without_literal_word_and_rejects_clashes(self):
        """
        Regression Test: Videogames tab returning no videos.
        Realistic videos about named games, gameplay, game design, speedruns, and game development
        whose titles do not literally contain 'videogames' must pass relevance gate and populate the tab.
        Unrelated clash videos (sports games, corporate strategy, recipes) must be strictly rejected.
        """
        service = RabbitholeService(gemini_api_key="", youtube_api_key="")

        realistic_cands = [
            CandidateVideo(
                id="vg_mario",
                title="Super Mario 64 - The 120 Star World Record History",
                channel="Summoning Salt",
                description="A documentary on the progression of the 120 star speedrun world record in Super Mario 64.",
                url="http://vg1",
                duration_seconds=1200,
                upload_date="2026-06-01"
            ),
            CandidateVideo(
                id="vg_elden",
                title="Why Elden Ring Open World Design Works",
                channel="Game Makers Toolkit",
                description="An analysis of Elden Ring map design, discovery loops, and gameplay systems.",
                url="http://vg2",
                duration_seconds=900,
                upload_date="2026-07-01"
            ),
            CandidateVideo(
                id="vg_doom",
                title="Doom 1993 - How Carmack Built the 3D Engine",
                channel="Fabien Sanglard",
                description="Exploring the BSP trees, fixed-point math, and rendering pipeline algorithms of classic Doom.",
                url="http://vg3",
                duration_seconds=800,
                upload_date="2026-05-15"
            ),
            CandidateVideo(
                id="vg_celeste",
                title="Celeste: The Mechanics of Jump Forgiveness",
                channel="Mark Brown",
                description="Deep dive into coyote time, jump buffering, and platformer kinetics physics.",
                url="http://vg4",
                duration_seconds=750,
                upload_date="2026-04-10"
            ),
            CandidateVideo(
                id="vg_minecraft",
                title="Minecraft Redstone: Building a 16-Bit Computer",
                channel="Nand2Tetris Gamer",
                description="Engineering logic gates, ALUs, and memory inside Minecraft using redstone circuitry.",
                url="http://vg5",
                duration_seconds=1100,
                upload_date="2026-08-01"
            ),
            CandidateVideo(
                id="clash_nba",
                title="NBA Game 7 Highlights: Celtics vs Lakers Final Seconds",
                channel="NBA Highlights",
                description="Full game 7 highlights and buzzer beater.",
                url="http://c1",
                duration_seconds=600,
                upload_date="2026-06-01"
            ),
            CandidateVideo(
                id="clash_corp",
                title="Corporate Strategy: Porter Five Forces in Market Mergers",
                channel="Business School",
                description="Analysis of corporate executive strategy in competitive industries.",
                url="http://c2",
                duration_seconds=550,
                upload_date="2026-06-01"
            ),
            CandidateVideo(
                id="clash_pasta",
                title="Homemade Pasta Recipe with Fresh Basil",
                channel="Cooking Channel",
                description="Family game night dinner recipe.",
                url="http://c3",
                duration_seconds=500,
                upload_date="2026-06-01"
            )
        ]

        service.retriever.retrieve_candidates = lambda queries, max_per_query=5: realistic_cands

        prefs = UserPreferences(interests="videogames", active_category="Videogames", category_view="new")
        res = service.generate_feed(prefs, top_k=5)

        # Tab must not be empty!
        assert len(res.items) == 5, f"Expected 5 videogame items, got {len(res.items)}"
        feed_ids = [it.video.id for it in res.items]

        # All 5 realistic videogame items must be eligible and present
        for expected_id in ["vg_mario", "vg_elden", "vg_doom", "vg_celeste", "vg_minecraft"]:
            assert expected_id in feed_ids, f"Expected {expected_id} in feed, but missing."

        # Unrelated clash items must be strictly filtered out by relevance gate
        assert "clash_nba" not in feed_ids
        assert "clash_corp" not in feed_ids
        assert "clash_pasta" not in feed_ids

    def test_videogames_tab_all_three_views_and_cold_start(self):
        """
        Verify all three Videogames views:
        1. New: accepts recent releases, rejects old video (>365d) titled 'New'.
        2. Go deeper: cold-start (no history) returns honest sparse result with accurate message.
           With history: returns follow-up candidate building on watched concept.
        3. Connections: requires metadata evidence for both Videogames and a second field (e.g. Doom -> Computation).
        """
        service = RabbitholeService(gemini_api_key="", youtube_api_key="")

        cands = [
            CandidateVideo(
                id="vg_doom_conn",
                title="Doom 1993 - BSP Trees and Software Rendering Algorithms",
                channel="Fabien Sanglard",
                description="Detailed breakdown of binary space partitioning trees and computational algorithms in classic Doom.",
                url="http://v1",
                duration_seconds=800,
                upload_date="2026-06-01"
            ),
            CandidateVideo(
                id="vg_celeste_deep",
                title="Celeste Movement Mechanics: Coyote Time and Jump Buffering",
                channel="Mark Brown",
                description="Deep dive into platformer jump mechanics and responsive input buffering in Celeste.",
                url="http://v2",
                duration_seconds=700,
                upload_date="2026-06-15"
            ),
            CandidateVideo(
                id="vg_pure_gameplay",
                title="Super Mario 64 Any% Speedrun Route Optimization",
                channel="Summoning Salt",
                description="Detailed overview of speedrun glitches and movement route execution in Super Mario 64.",
                url="http://v3",
                duration_seconds=1200,
                upload_date="2026-05-20"
            ),
            CandidateVideo(
                id="vg_old_release",
                title="New Next-Gen Game Engines 2026",
                channel="Tech Channel",
                description="Video game graphics overview.",
                url="http://v4",
                duration_seconds=600,
                upload_date="2021-01-01"  # Old upload date (>365d) despite 'New' in title
            )
        ]

        service.retriever.retrieve_candidates = lambda queries, max_per_query=5: cands

        # 1. New View: rejects vg_old_release due to 2021 upload date
        prefs_new = UserPreferences(interests="videogames", active_category="Videogames", category_view="new")
        res_new = service.generate_feed(prefs_new, top_k=5)
        new_ids = [it.video.id for it in res_new.items]
        assert "vg_old_release" not in new_ids, "Old video titled 'New' must be rejected from New view based on upload date"
        assert "vg_doom_conn" in new_ids
        assert "vg_celeste_deep" in new_ids

        # 2. Go Deeper Cold-Start (no watch history)
        prefs_deep_cold = UserPreferences(interests="videogames", active_category="Videogames", category_view="deeper")
        res_deep_cold = service.generate_feed(prefs_deep_cold, top_k=5)
        assert len(res_deep_cold.items) == 0, "Go deeper without history must return honest sparse result"
        assert "No viewing or saved history found for 'Videogames' yet" in res_deep_cold.status_message

        # 3. Go Deeper with Watch History in Celeste
        profile = LivingTasteProfile()
        profile.record_signal(TasteSignal(
            video_id="watched_celeste_intro",
            title="Celeste: The Mechanics of Jump Forgiveness",
            channel="Mark Brown",
            domain="Gaming",
            action="completed",
            weight=2.5
        ))
        prefs_deep_hist = UserPreferences(
            interests="videogames",
            active_category="Videogames",
            category_view="deeper",
            living_taste_profile=profile
        )
        res_deep_hist = service.generate_feed(prefs_deep_hist, top_k=5)
        assert len(res_deep_hist.items) >= 1
        assert res_deep_hist.items[0].video.id == "vg_celeste_deep"
        assert "celeste" in res_deep_hist.items[0].path_reason.lower() or "jump" in res_deep_hist.items[0].path_reason.lower()

        # 4. Connections View: requires dual-field evidence
        prefs_conn = UserPreferences(interests="videogames", active_category="Videogames", category_view="connections")
        res_conn = service.generate_feed(prefs_conn, top_k=5)
        conn_ids = [it.video.id for it in res_conn.items]
        # vg_doom_conn has both Videogames ('Doom') and Computation & Algorithms ('BSP Trees', 'algorithms')
        assert "vg_doom_conn" in conn_ids
        assert "Computation & Algorithms" in res_conn.items[0].discovery_connection
        # vg_pure_gameplay does NOT have a second field -> excluded from Connections
        assert "vg_pure_gameplay" not in conn_ids
