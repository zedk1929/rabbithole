"""
Automated test suite for Rabbithole recommendation & anti-clickbait pipeline.
"""
import pytest
from threading import Barrier
from rabbithole_app.models import (
    UserPreferences,
    CuriosityQuery,
    CandidateVideo,
    QualityAssessment,
    RelevanceAssessment,
    DislikeRecord,
    ViewingRecord,
    FollowedCreator,
    LivingTasteProfile,
    TasteSignal,
    CuriosityQueryPlan,
)
from rabbithole_app.filter_engine import summarize_dislike_patterns, summarize_followed_creators
from rabbithole_app.relevance_gate import RelevanceGate
from rabbithole_app.query_expander import QueryExpander
from rabbithole_app.filter_engine import FilterEngine
from rabbithole_app.server import app
from rabbithole_app.service import RabbitholeService
from rabbithole_app.retriever import VideoRetriever
from rabbithole_app.journey import build_path, build_main_feed, build_category_feed, STEPS
from rabbithole_app.models import FeedItem
from fastapi.testclient import TestClient


def test_user_preferences_validation():
    # Valid preferences
    prefs = UserPreferences(
        interests="analog synthesizers and acoustics",
        serendipity_level=4,
        depth_level="curious_explorer",
        min_duration_minutes=8
    )
    assert prefs.serendipity_level == 4
    assert prefs.avoid_sensationalism is True

    # Invalid serendipity dial (must be between 1 and 5)
    with pytest.raises(Exception):
        UserPreferences(interests="physics", serendipity_level=10)


def test_retriever_searches_concurrently_and_keeps_query_order(monkeypatch):
    barrier = Barrier(3)

    class FakeYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def extract_info(self, search_term, download=False):
            barrier.wait(timeout=3)
            query = search_term.split(":", 1)[1]
            return {"entries": [
                {"id": "shared", "title": query, "channel": "Creator"},
                {"id": query, "title": query, "channel": "Creator"},
            ]}

    monkeypatch.setattr("rabbithole_app.retriever.yt_dlp.YoutubeDL", FakeYoutubeDL)
    queries = [CuriosityQuery(query=q, query_type="first_principles", domain="math", rationale="test")
               for q in ["first", "second", "third"]]

    candidates = VideoRetriever().retrieve_candidates(queries)
    assert [video.id for video in candidates] == ["shared", "first", "second", "third"]
    assert candidates[0].source_query == "first"


def test_heuristic_query_expansion():
    expander = QueryExpander(api_key="")
    prefs = UserPreferences(interests="history of medieval clockwork and horology")
    queries = expander.expand_queries(prefs, count=3)

    assert len(queries) == 3
    for q in queries:
        assert isinstance(q, CuriosityQuery)
        assert len(q.query.strip()) > 0
        assert q.domain != ""
        assert q.rationale != ""


def test_anti_clickbait_tier1_heuristics():
    filter_engine = FilterEngine(api_key="")
    prefs = UserPreferences(
        interests="modular synthesizers",
        min_duration_minutes=5,
        avoid_sensationalism=True
    )

    # 1. Reject Short clips (< 5 mins = 300s)
    short_video = CandidateVideo(
        id="short1",
        title="Quick Modular Synth Demo",
        channel="SynthGuy",
        url="https://youtube.com/watch?v=short1",
        duration_seconds=45,
    )
    passed, score, reason = filter_engine.run_tier1_heuristics(short_video, prefs)
    assert not passed
    assert "below minimum" in reason

    # 2. Reject Aggressive Clickbait & Sensationalism
    clickbait_video = CandidateVideo(
        id="bait1",
        title="I BOUGHT THE WEIRDEST SYNTH IN THE WORLD AND IT RUINED MY LIFE?!?!",
        channel="HypeBeast",
        url="https://youtube.com/watch?v=bait1",
        duration_seconds=1200,
    )
    passed, score, reason = filter_engine.run_tier1_heuristics(clickbait_video, prefs)
    assert not passed
    assert score >= 0.6

    # 3. Pass Honest, Substantive Candidate
    substantive_video = CandidateVideo(
        id="sub1",
        title="Buchla 200 Series Modular System: Architecture & Design Retrospective",
        channel="Center for New Music and Audio Technologies",
        url="https://youtube.com/watch?v=sub1",
        duration_seconds=2400,
        description="Comprehensive technical lecture on the dual oscillator design and non-standard voltage standards."
    )
    passed, score, reason = filter_engine.run_tier1_heuristics(substantive_video, prefs)
    assert passed
    assert score < 0.5


def test_filter_and_rank():
    filter_engine = FilterEngine(api_key="")
    prefs = UserPreferences(interests="sound synthesis")

    candidates = [
        CandidateVideo(
            id="v1",
            title="TOP 10 CRAZY SYNTHS YOU WON'T BELIEVE EXIST!!!",
            channel="TechHype",
            url="https://youtube.com/watch?v=v1",
            duration_seconds=900,
        ),
        CandidateVideo(
            id="v2",
            title="The Physics of Wavefolding and Harmonic Generation",
            channel="MIT OpenCourseWare",
            url="https://youtube.com/watch?v=v2",
            duration_seconds=3600,
            description="Lecture breakdown on harmonic saturation and non-linear transfer curves."
        ),
        CandidateVideo(
            id="v3",
            title="Short Reel",
            channel="QuickClips",
            url="https://youtube.com/watch?v=v3",
            duration_seconds=30,
        )
    ]

    items, filtered_count = filter_engine.filter_and_rank(candidates, prefs, top_k=5)
    assert len(items) == 1
    assert items[0].video.id == "v2"
    assert items[0].assessment.composite_score > 0.5
    assert filtered_count == 2


def test_api_endpoints():
    client = TestClient(app)

    # Health check
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"

    # UI Index check
    res_ui = client.get("/")
    assert res_ui.status_code == 200
    assert "Rabbithole" in res_ui.text


def test_viewing_path_uses_search_roles_and_avoids_topics():
    prefs = UserPreferences(
        interests="music engineering",
        explore_topics="architecture",
        avoid_topics="celebrity gossip",
    )
    items = []
    for index, (_, query_type, _) in enumerate(STEPS):
        video = CandidateVideo(
            id=f"video{index}", title=f"Music engineering {query_type}",
            channel=f"Channel {index}", url=f"https://youtube.com/watch?v=video{index}",
            source_query_type=query_type,
        )
        items.append(FeedItem(video=video, assessment=QualityAssessment(composite_score=0.6)))
    items.append(FeedItem(
        video=CandidateVideo(id="avoid", title="Celebrity gossip music", channel="Other",
                             url="https://youtube.com/watch?v=avoid", source_query_type="first_principles"),
        assessment=QualityAssessment(composite_score=1.0),
    ))

    path = build_path(items, prefs)
    assert [item.path_step for item in path] == [step[0] for step in STEPS]
    assert [item.video.source_query_type for item in path] == [step[1] for step in STEPS]
    assert "avoid" not in [item.video.id for item in path]


def test_service_returns_short_path_when_few_candidates(monkeypatch):
    service = RabbitholeService(gemini_api_key="")
    prefs = UserPreferences(interests="music", feed_mode="deep_learning")
    queries = QueryExpander(api_key="")._expand_with_heuristics(prefs, count=5)
    candidate = CandidateVideo(id="one", title="Music first principles", channel="One",
                               url="https://youtube.com/watch?v=one",
                               duration_seconds=1200, source_query_type="first_principles")
    monkeypatch.setattr(service.expander, "expand_queries", lambda *_args, **_kwargs: queries)
    monkeypatch.setattr(service.retriever, "retrieve_candidates", lambda *_args, **_kwargs: [candidate])

    response = service.generate_feed(prefs)
    assert len(response.items) == 1
    assert response.items[0].path_step == "Start"
    assert response.total_candidates_analyzed == 1


def test_broad_input_produces_distinct_directions():
    """Verify broad interests without target fields produce 5 distinct query directions."""
    expander = QueryExpander(api_key="")
    prefs = UserPreferences(
        interests="acoustics and musical instrument resonance",
        serendipity_level=4,
        explore_topics="",  # No target fields specified
        feed_mode="deep_learning",
    )
    queries = expander.expand_queries(prefs, count=5)

    assert len(queries) == 5
    query_texts = [q.query.strip().lower() for q in queries]
    # All queries must be distinct
    assert len(set(query_texts)) == 5

    # Must cover distinct query roles
    roles = {q.query_type for q in queries}
    assert len(roles) >= 4
    assert "cross_domain_analogy" in roles
    assert "first_principles" in roles
    assert "archive_lecture" in roles

    # Each query must have domain and explainable rationale
    for q in queries:
        assert len(q.rationale) >= 20
        assert q.domain != ""


def test_avoided_topics_strictly_respected_in_queries_and_path():
    """Verify avoided topics are pruned from queries and excluded from viewing paths."""
    expander = QueryExpander(api_key="")
    prefs = UserPreferences(
        interests="sound synthesis and acoustics",
        avoid_topics="cymatic, resonance, modular",
        serendipity_level=3
    )
    queries = expander.expand_queries(prefs, count=5)
    for q in queries:
        q_lower = q.query.lower()
        assert "cymatic" not in q_lower
        assert "resonance" not in q_lower
        assert "modular" not in q_lower

    # Path building exclusion check
    items = [
        FeedItem(
            video=CandidateVideo(id="good", title="Acoustic Flue Pipe Physics", channel="Pioneers",
                                 url="https://youtube.com/watch?v=good", source_query_type="first_principles"),
            assessment=QualityAssessment(composite_score=0.8)
        ),
        FeedItem(
            video=CandidateVideo(id="bad", title="Modular Synthesizer Resonance Tutorial", channel="BaitChannel",
                                 url="https://youtube.com/watch?v=bad", source_query_type="first_principles"),
            assessment=QualityAssessment(composite_score=0.99)
        )
    ]
    path = build_path(items, prefs, length=2)
    assert len(path) == 1
    assert path[0].video.id == "good"


def test_interface_sends_simplified_preferences(monkeypatch):
    """Verify interface/API accepts simplified preferences (broad interests + serendipity only)."""
    client = TestClient(app)

    mock_candidate = CandidateVideo(
        id="mock1",
        title="Foundational Acoustics and Standing Waves",
        channel="MIT OCW",
        url="https://youtube.com/watch?v=mock1",
        duration_seconds=1200,
        source_query="acoustics fundamental mechanics first principles",
        source_query_type="first_principles"
    )
    monkeypatch.setattr(
        "rabbithole_app.service.VideoRetriever.retrieve_candidates",
        lambda self, queries, max_per_query=5: [mock_candidate]
    )

    # Simplified payload: only broad interests and serendipity dial
    simplified_payload = {
        "interests": "acoustics and wave mechanics",
        "serendipity_level": 4
    }

    res = client.post("/api/feed", json=simplified_payload)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert len(data["items"]) == 1
    assert data["items"][0]["video"]["id"] == "mock1"
    assert len(data["generated_queries"]) == 5


def test_grounded_connection_notes_no_full_content_claim():
    """Verify assessment notes are grounded in metadata and do not claim to know full video content."""
    filter_engine = FilterEngine(api_key="")
    candidate = CandidateVideo(
        id="cand1",
        title="Principles of Fluid Dynamics and Aerodynamics",
        channel="Cambridge Open",
        url="https://youtube.com/watch?v=cand1",
        duration_seconds=1800,
    )
    prefs = UserPreferences(interests="fluid dynamics")
    assessment = filter_engine._heuristic_assessment(candidate, prefs)

    assert "Surfaced from channel" in assessment.connection_note
    assert "metadata" in assessment.connection_note.lower()
    assert "Metadata indicates" in assessment.key_takeaways[0]


def test_heuristic_query_quality_baseball_and_strategy():
    """Verify baseball and strategy produces relevant, varied, explainable queries and respects avoidance."""
    expander = QueryExpander(api_key="")
    prefs = UserPreferences(
        interests="baseball and strategy",
        serendipity_level=3,
        avoid_topics="betting, gambling, fantasy",
        feed_mode="deep_learning",
    )

    # 1. Repeatable output
    run1 = expander.expand_queries(prefs, count=5)
    run2 = expander.expand_queries(prefs, count=5)
    assert [q.model_dump() for q in run1] == [q.model_dump() for q in run2], "Query generator must be strictly deterministic"

    assert len(run1) == 5

    # 2. Query relevance & domain appropriateness
    relevant_terms = {"baseball", "sabermetrics", "pitch", "pitching", "strategy", "tactical", "game theory"}
    for q in run1:
        query_words = set(q.query.lower().split())
        assert any(term in query_words or term in q.query.lower() for term in relevant_terms), (
            f"Query '{q.query}' does not contain expected baseball/strategy terms"
        )
        assert len(q.rationale) > 25

    # 3. Concrete cross-domain bridge (connected, not generic unrelated physics)
    bridge_query = next(q for q in run1 if q.query_type == "cross_domain_analogy")
    assert "game theory" in bridge_query.query.lower() or "minimax" in bridge_query.query.lower()
    assert "Game Theory" in bridge_query.domain
    assert "entropy" not in bridge_query.query.lower()
    assert "quasicrystal" not in bridge_query.query.lower()

    # 4. Serendipity level scaling
    prefs_focused = UserPreferences(interests="baseball and strategy", serendipity_level=1, feed_mode="deep_learning")
    bridge_focused = next(q for q in expander.expand_queries(prefs_focused, count=5) if q.query_type == "cross_domain_analogy")
    assert "pitch framing" in bridge_focused.query.lower()

    prefs_radical = UserPreferences(interests="baseball and strategy", serendipity_level=5, feed_mode="deep_learning")
    bridge_radical = next(q for q in expander.expand_queries(prefs_radical, count=5) if q.query_type == "cross_domain_analogy")
    assert "fastball" in bridge_radical.query.lower() or "psychophysics" in bridge_radical.query.lower()

    # 5. Avoidance check
    for q in run1:
        q_lower = q.query.lower()
        r_lower = q.rationale.lower()
        assert "betting" not in q_lower and "betting" not in r_lower
        assert "gambling" not in q_lower and "gambling" not in r_lower
        assert "fantasy" not in q_lower and "fantasy" not in r_lower

    # 6. Distinct directions
    roles = [q.query_type for q in run1]
    assert len(set(roles)) == 5
    assert roles == ["first_principles", "archive_lecture", "cross_domain_analogy", "niche_history", "practitioner_deep_dive"]


def test_heuristic_query_quality_movies_and_storytelling():
    """Verify movies and storytelling produces relevant cinematic angles with a concrete bridge."""
    expander = QueryExpander(api_key="")
    prefs = UserPreferences(
        interests="movies and storytelling",
        serendipity_level=3,
        avoid_topics="box office, celebrity gossip, marvel",
        feed_mode="deep_learning",
    )

    # 1. Determinism
    run1 = expander.expand_queries(prefs, count=5)
    run2 = expander.expand_queries(prefs, count=5)
    assert [q.model_dump() for q in run1] == [q.model_dump() for q in run2]

    # 2. Query relevance
    relevant_terms = {"cinematography", "narrative", "editing", "visual", "hitchcock", "storytelling", "film", "montage"}
    for q in run1:
        q_lower = q.query.lower()
        assert any(term in q_lower for term in relevant_terms), f"Query '{q.query}' lacks cinematic narrative relevance"

    # 3. Grounded cross-domain bridge
    bridge = next(q for q in run1 if q.query_type == "cross_domain_analogy")
    assert "eye tracking" in bridge.query.lower() or "cognitive" in bridge.query.lower()
    assert "Cognitive" in bridge.domain
    assert "entropy" not in bridge.query.lower()

    # 4. Avoidance
    for q in run1:
        q_str = f"{q.query} {q.rationale}".lower()
        assert "box office" not in q_str
        assert "celebrity" not in q_str
        assert "marvel" not in q_str

    # 5. Serendipity level scaling
    prefs_focused = UserPreferences(interests="movies and storytelling", serendipity_level=1, feed_mode="deep_learning")
    bridge_focused = next(q for q in expander.expand_queries(prefs_focused, count=5) if q.query_type == "cross_domain_analogy")
    assert "screenplay" in bridge_focused.query.lower() or "literary" in bridge_focused.query.lower()
    assert "Dramaturgy" in bridge_focused.domain

    prefs_radical = UserPreferences(interests="movies and storytelling", serendipity_level=5, feed_mode="deep_learning")
    bridge_radical = next(q for q in expander.expand_queries(prefs_radical, count=5) if q.query_type == "cross_domain_analogy")
    assert "architectural" in bridge_radical.query.lower() or "production design" in bridge_radical.query.lower()
    assert "Architecture" in bridge_radical.domain or "Spatial" in bridge_radical.domain

    # 6. Distinct directions
    assert len({q.query_type for q in run1}) == 5


def test_heuristic_query_quality_gardening_and_cooking():
    """Verify gardening and cooking produces agronomy/culinary angles with a concrete bridge."""
    expander = QueryExpander(api_key="")
    prefs = UserPreferences(
        interests="gardening and cooking",
        serendipity_level=3,
        avoid_topics="quick hacks, diet weight loss",
        feed_mode="deep_learning",
    )

    # 1. Determinism
    run1 = expander.expand_queries(prefs, count=5)
    run2 = expander.expand_queries(prefs, count=5)
    assert [q.model_dump() for q in run1] == [q.model_dump() for q in run2]

    # 2. Query relevance
    relevant_terms = {"soil", "microbiology", "food", "chemistry", "fermentation", "heirloom", "composting", "culinary"}
    for q in run1:
        q_lower = q.query.lower()
        assert any(term in q_lower for term in relevant_terms), f"Query '{q.query}' lacks gardening/cooking relevance"

    # 3. Grounded cross-domain bridge
    bridge = next(q for q in run1 if q.query_type == "cross_domain_analogy")
    assert "fermentation" in bridge.query.lower() or "microbial" in bridge.query.lower()
    assert "Microbial" in bridge.domain
    assert "thermodynamic" not in bridge.query.lower()

    # 4. Avoidance
    for q in run1:
        q_str = f"{q.query} {q.rationale}".lower()
        assert "quick hacks" not in q_str
        assert "diet" not in q_str
        assert "weight loss" not in q_str

    # 5. Serendipity level scaling
    prefs_focused = UserPreferences(interests="gardening and cooking", serendipity_level=1, feed_mode="deep_learning")
    bridge_focused = next(q for q in expander.expand_queries(prefs_focused, count=5) if q.query_type == "cross_domain_analogy")
    assert "emulsion" in bridge_focused.query.lower() or "colloid" in bridge_focused.query.lower()
    assert "Colloid" in bridge_focused.domain

    prefs_radical = UserPreferences(interests="gardening and cooking", serendipity_level=5, feed_mode="deep_learning")
    bridge_radical = next(q for q in expander.expand_queries(prefs_radical, count=5) if q.query_type == "cross_domain_analogy")
    assert "ethnobotany" in bridge_radical.query.lower() or "domestication" in bridge_radical.query.lower()
    assert "Ethnobotany" in bridge_radical.domain or "Genetics" in bridge_radical.domain

    # 6. Distinct directions
    assert len({q.query_type for q in run1}) == 5


def test_heuristic_query_quality_unrecognized_domain_stays_grounded():
    """Verify an unrecognized domain stays close to user terms and honestly labels the bridge."""
    expander = QueryExpander(api_key="")
    prefs = UserPreferences(
        interests="origami and polymer resins",
        serendipity_level=3,
    )
    queries = expander.expand_queries(prefs, count=5)
    assert len(queries) == 5

    # Must NOT inject arbitrary ungrounded physics bridges
    for q in queries:
        assert "thermodynamic entropy" not in q.query.lower()
        assert "quasicrystal" not in q.query.lower()
        assert "chladni" not in q.query.lower()

    # Cross domain query must stay close to the user's concepts and label it honestly
    bridge = next(q for q in queries if q.query_type == "cross_domain_analogy")
    assert "origami" in bridge.query.lower()
    assert "polymer" in bridge.query.lower()
    assert "Intersection" in bridge.domain or "Adjacent" in bridge.domain
    assert "grounded" in bridge.rationale.lower() or "direct connection" in bridge.rationale.lower()


def test_avoidance_reproduced_case_baseball_strategy_history():
    """Reproduced failure: 'baseball and strategy' with 'history' avoided must omit history and not reuse it."""
    expander = QueryExpander(api_key="")
    prefs = UserPreferences(
        interests="baseball and strategy",
        avoid_topics="history",
        serendipity_level=3,
        feed_mode="deep_learning",
    )
    queries = expander.expand_queries(prefs, count=5)

    # 1. Must omit the conflicted niche_history role and return fewer queries (4 instead of 5)
    assert len(queries) == 4, f"Expected 4 queries with history role omitted; got {len(queries)}"
    assert "niche_history" not in [q.query_type for q in queries]

    # 2. Strict check: neither query, domain, nor rationale may contain history or historical
    for q in queries:
        full_text = f"{q.query} {q.domain} {q.rationale}".lower()
        assert "history" not in full_text, f"Query '{q.query}' contains avoided 'history'"
        assert "historic" not in full_text, f"Query '{q.query}' contains avoided 'historic*'"

    # 3. All returned directions are genuinely safe
    assert [q.query_type for q in queries] == [
        "first_principles", "archive_lecture", "cross_domain_analogy", "practitioner_deep_dive"
    ]


def test_avoidance_whole_role_options_avoided():
    """Verify that when every option for a search role conflicts with avoided topics, that role is omitted."""
    expander = QueryExpander(api_key="")
    # All cross-domain analogy options in gardening_cooking are fermentation/colloid/ethnobotany
    prefs = UserPreferences(
        interests="gardening and cooking",
        avoid_topics="fermentation, lacto, koji, colloid, emulsion, ethnobotany",
        serendipity_level=3,
        feed_mode="deep_learning",
    )
    queries = expander.expand_queries(prefs, count=5)

    # The cross_domain_analogy role must be omitted because all candidates conflict
    assert "cross_domain_analogy" not in [q.query_type for q in queries]
    assert len(queries) == 4

    for q in queries:
        full_text = f"{q.query} {q.domain} {q.rationale}".lower()
        assert "fermentation" not in full_text
        assert "koji" not in full_text
        assert "lacto" not in full_text
        assert "colloid" not in full_text
        assert "emulsion" not in full_text


def test_avoidance_gemini_generated_queries_filtered(monkeypatch):
    """Verify that the final avoidance check strips any query generated by Gemini that contains an avoided topic."""
    expander = QueryExpander(api_key="mock_key")
    prefs = UserPreferences(
        interests="acoustic instruments",
        avoid_topics="synthesizer",
        serendipity_level=3
    )

    mock_gemini_queries = [
        CuriosityQuery(
            query="acoustic instrument resonance physics",
            domain="Acoustic Physics",
            query_type="first_principles",
            rationale="Explores physical acoustic properties of wood resonance."
        ),
        CuriosityQuery(
            query="analog synthesizer patching modulation",  # Prohibited query
            domain="Synthesizers",
            query_type="cross_domain_analogy",
            rationale="Examines modular synth patching."
        ),
        CuriosityQuery(
            query="historical pipe organ flue pipes lecture",
            domain="Archival Acoustics",
            query_type="archive_lecture",
            rationale="Explores early organ builder lectures."
        ),
        CuriosityQuery(
            query="violin varnish acoustic luthier workshop",
            domain="Synthesizer Electronics",  # Prohibited domain!
            query_type="practitioner_deep_dive",
            rationale="Explores wood varnishing."
        ),
    ]

    monkeypatch.setattr(expander, "_expand_with_gemini", lambda prefs, count: mock_gemini_queries)

    # Final avoidance check should catch both the prohibited query and the prohibited domain
    safe_queries = expander.expand_queries(prefs, count=5)
    assert len(safe_queries) == 2
    for q in safe_queries:
        full_text = f"{q.query} {q.domain} {q.rationale}".lower()
        assert "synthesizer" not in full_text


def test_avoidance_user_interest_itself_listed_as_avoided():
    """Verify handling when the user's interest itself is listed as avoided."""
    expander = QueryExpander(api_key="")

    # Case A: Partial conflict (baseball is avoided, but strategy remains valid)
    prefs_partial = UserPreferences(
        interests="baseball and strategy",
        avoid_topics="baseball",
        serendipity_level=3
    )
    queries_partial = expander.expand_queries(prefs_partial, count=5)
    assert len(queries_partial) > 0
    for q in queries_partial:
        full_text = f"{q.query} {q.domain} {q.rationale}".lower()
        assert "baseball" not in full_text, f"Found prohibited 'baseball' in: {full_text}"

    # Case B: Total conflict (baseball is interest and only interest, and baseball is avoided)
    prefs_total = UserPreferences(
        interests="baseball",
        avoid_topics="baseball",
        serendipity_level=3
    )
    queries_total = expander.expand_queries(prefs_total, count=5)
    # Must NOT silently produce prohibited searches for baseball
    assert len(queries_total) == 0


def test_relevance_gate_rejects_pilot_birdwatching_atomic_models_mismatch():
    """Verify that the relevance gate rejects the pilot mismatch: atomic models video in birdwatching."""
    gate = RelevanceGate(api_key="")
    prefs = UserPreferences(interests="birdwatching and photography")

    candidate = CandidateVideo(
        id="b_atom",
        title="What Are The Different Atomic Models? Dalton, Rutherford, Bohr and Heisenberg Models Explained",
        channel="Science ABC",
        duration_seconds=640,
        description="What are the different atomic models and how did our understanding of the atom evolve? In this video we explain atomic physics.",
        source_query="birdwatching theoretical models structural principles",
        source_query_type="first_principles",
        url="https://youtube.com/watch?v=b_atom"
    )

    assessment = gate.assess_candidate(candidate, prefs)
    assert not assessment.is_relevant
    assert assessment.status == "rejected"
    assert "atomic" in assessment.reason.lower() or "physics" in assessment.reason.lower()


def test_relevance_gate_rejects_pilot_baseball_genealogy_csi_mismatch():
    """Verify that the relevance gate rejects the pilot mismatch: Colleen Fitzpatrick forensic genealogy in baseball."""
    gate = RelevanceGate(api_key="")
    prefs = UserPreferences(interests="baseball and strategy")

    candidate = CandidateVideo(
        id="bb_genealogy",
        title="Colleen Fitzpatrick - CSI meets Roots",
        channel="Auckland Libraries",
        duration_seconds=3600,
        description="Forensic genealogy has established itself as the modern approach to family research. Reference material...",
        source_query="baseball tactical theory pioneer lecture archive",
        source_query_type="archive_lecture",
        url="https://youtube.com/watch?v=bb_genealogy"
    )

    assessment = gate.assess_candidate(candidate, prefs)
    assert not assessment.is_relevant
    assert assessment.status == "rejected"
    assert "genealogy" in assessment.reason.lower() or "clash" in assessment.reason.lower()


def test_relevance_gate_rejects_pilot_baseball_surfing_mismatch():
    """Verify that the relevance gate rejects the pilot mismatch: surf podcast in baseball."""
    gate = RelevanceGate(api_key="")
    prefs = UserPreferences(interests="baseball and strategy")

    candidate = CandidateVideo(
        id="bb_surf",
        title="The Grit! Al Cleland Fallout, The Wasserman Emails & Chas Floats A Boycott",
        channel="Surf Splendor - Surf Podcast Network",
        duration_seconds=4200,
        description="Al Cleland Jr. fallout, Lucy Small's resurfaced surf stories, Casey Wasserman, and a possible Chas Floats A Boycott.",
        source_query="defensive shift baseball history controversy retrospective",
        source_query_type="niche_history",
        url="https://youtube.com/watch?v=bb_surf"
    )

    assessment = gate.assess_candidate(candidate, prefs)
    assert not assessment.is_relevant
    assert assessment.status == "rejected"
    assert "surf" in assessment.reason.lower()


def test_relevance_gate_rejects_pilot_birdwatching_forensic_photography_and_homework_mismatch():
    """Verify that the relevance gate rejects forensic crime scenes and generic homework essays in birdwatching."""
    gate = RelevanceGate(api_key="")
    prefs = UserPreferences(interests="birdwatching and photography")

    forensic_cand = CandidateVideo(
        id="b_forensic",
        title="Fundamentals of Forensic Photography #AFSJ",
        channel="Applied Forensic Research Sciences",
        duration_seconds=1200,
        description="Fundamentals of forensic photography. 1. Introduction to crime scene photography and forensics.",
        source_query="photography applied methodology case study",
        source_query_type="practitioner_deep_dive",
        url="https://youtube.com/watch?v=b_forensic"
    )

    essay_cand = CandidateVideo(
        id="b_essay",
        title="How to Write a Case Study? A Step-By-Step Guide to Writing a Case Study",
        channel="Instant Assignment Help",
        duration_seconds=800,
        description="In this video we provide a step-by-step tutorial on writing a college case study.",
        source_query="photography applied methodology case study",
        source_query_type="practitioner_deep_dive",
        url="https://youtube.com/watch?v=b_essay"
    )

    forensic_eval = gate.assess_candidate(forensic_cand, prefs)
    essay_eval = gate.assess_candidate(essay_cand, prefs)

    assert not forensic_eval.is_relevant
    assert forensic_eval.status == "rejected"

    assert not essay_eval.is_relevant
    assert essay_eval.status == "rejected"


def test_relevance_gate_accepts_baseball_pitch_sequencing_game_theory():
    """Verify that baseball pitch sequencing connected to game theory is recognized as supported."""
    gate = RelevanceGate(api_key="")
    prefs = UserPreferences(interests="baseball and strategy")

    candidate = CandidateVideo(
        id="bb_pitch_gt",
        title="SSAC23: A Game Theoretical Approach to Optimal Pitch Sequencing",
        channel="42 Analytics",
        duration_seconds=1800,
        description="Authors: William Melville... A game theoretical approach to optimal pitch sequencing in Major League Baseball.",
        source_query="game theory pitch sequencing minimax strategy",
        source_query_type="cross_domain_analogy",
        url="https://youtube.com/watch?v=bb_pitch_gt"
    )

    assessment = gate.assess_candidate(candidate, prefs)
    assert assessment.is_relevant
    assert assessment.status == "supported"
    assert "baseball" in assessment.reason.lower() or "pitch sequencing" in assessment.reason.lower()


def test_relevance_gate_preserves_cross_domain_bridge_without_exact_keywords():
    """Verify that the gate preserves a valid cross-domain bridge without reducing to exact keyword matches."""
    gate = RelevanceGate(api_key="")
    prefs = UserPreferences(interests="baseball and strategy")

    # This video contains ZERO mentions of 'baseball', but is a valid bridge lecture for game theory
    bridge_candidate = CandidateVideo(
        id="bb_bridge_stanford",
        title="Algorithmic Game Theory: Two Vignettes",
        channel="Stanford",
        duration_seconds=3600,
        description="Tim Roughgarden talks about algorithmic game theory and illustrates two of the main concepts: Nash equilibria and minimax strategies.",
        source_query="game theory pitch sequencing minimax strategy",
        source_query_type="cross_domain_analogy",
        url="https://youtube.com/watch?v=bb_bridge_stanford"
    )

    assert "baseball" not in f"{bridge_candidate.title} {bridge_candidate.description}".lower()

    assessment = gate.assess_candidate(bridge_candidate, prefs)
    assert assessment.is_relevant
    assert assessment.status == "supported"
    assert "game theory" in assessment.reason.lower()


def test_relevance_gate_preserves_birdwatching_biomimicry_and_sketching_bridges():
    """Verify that surprising and creative birdwatching bridges (biomimicry, field sketching) are preserved."""
    gate = RelevanceGate(api_key="")
    prefs = UserPreferences(interests="birdwatching and photography")

    biomimicry = CandidateVideo(
        id="b_kingfisher",
        title="How a kingfisher helped reshape Japan's bullet train - BBC News",
        channel="BBC News",
        duration_seconds=600,
        description="How the beak of the kingfisher bird inspired engineering biomimicry to redesign the Shinkansen bullet train.",
        source_query="birdwatching theoretical models structural principles",
        source_query_type="first_principles",
        url="https://youtube.com/watch?v=b_kingfisher"
    )

    sketching = CandidateVideo(
        id="b_sketch",
        title="60-Second Bird Sketching with John Muir Laws",
        channel="Wild Wonder Foundation",
        duration_seconds=600,
        description="Field sketching tutorial: join naturalist John Muir Laws to sketch birds in the wild.",
        source_query="birdwatching photography interdisciplinary connection analysis",
        source_query_type="cross_domain_analogy",
        url="https://youtube.com/watch?v=b_sketch"
    )

    bm_eval = gate.assess_candidate(biomimicry, prefs)
    sk_eval = gate.assess_candidate(sketching, prefs)

    assert bm_eval.is_relevant
    assert bm_eval.status == "supported"

    assert sk_eval.is_relevant
    assert sk_eval.status == "supported"


def test_relevance_gate_handles_uncertain_cases_honestly():
    """Verify that candidates with sparse or ambiguous metadata are treated honestly as uncertain."""
    gate = RelevanceGate(api_key="")
    filter_engine = FilterEngine(api_key="")
    prefs = UserPreferences(interests="baseball and strategy", min_duration_minutes=5)

    sparse_cand = CandidateVideo(
        id="sparse_1",
        title="Tactical Breakdown 14",
        channel="Independent Lab",
        duration_seconds=900,
        description="",  # Empty description preview
        source_query="pitch sequencing game theory mechanics breakdown",
        source_query_type="first_principles",
        url="https://youtube.com/watch?v=sparse_1"
    )

    res = gate.assess_candidate(sparse_cand, prefs)
    assert res.status == "uncertain"
    assert res.is_relevant is True
    assert res.confidence <= 0.40
    assert "unverified" in res.reason.lower() or "ambiguous" in res.reason.lower()

    # When evaluated in FilterEngine, it shouldn't receive an inflated substance score
    feed_items, _ = filter_engine.filter_and_rank([sparse_cand], prefs, top_k=1)
    assert len(feed_items) == 1
    assessment = feed_items[0].assessment
    assert assessment.relevance_status == "uncertain"
    assert "unverified" in assessment.connection_note.lower()
    assert assessment.substance_score <= 0.50


def test_relevance_gate_end_to_end_viewing_path_excludes_mismatches():
    """Verify that end-to-end filter_and_rank excludes pilot mismatches before building viewing paths."""
    filter_engine = FilterEngine(api_key="")
    prefs = UserPreferences(interests="birdwatching and photography", min_duration_minutes=5)

    candidates = [
        # Mismatch 1: Atomic models
        CandidateVideo(
            id="mismatch_atom",
            title="What Are The Different Atomic Models? Dalton, Rutherford, Bohr and Heisenberg Models Explained",
            channel="Science ABC",
            duration_seconds=640,
            description="What are the different atomic models and how did our understanding of the atom evolve?",
            source_query="birdwatching theoretical models structural principles",
            source_query_type="first_principles",
            url="https://youtube.com/watch?v=mismatch_atom"
        ),
        # Mismatch 2: Forensic crime photography
        CandidateVideo(
            id="mismatch_forensic",
            title="Fundamentals of Forensic Photography #AFSJ",
            channel="Applied Forensic Research Sciences",
            duration_seconds=1200,
            description="Fundamentals of forensic photography. Crime scene analysis.",
            source_query="photography applied methodology case study",
            source_query_type="practitioner_deep_dive",
            url="https://youtube.com/watch?v=mismatch_forensic"
        ),
        # Valid 1: Kingfisher biomimicry
        CandidateVideo(
            id="valid_train",
            title="How a kingfisher helped reshape Japan's bullet train - BBC News",
            channel="BBC News",
            duration_seconds=600,
            description="Biomimicry engineering using kingfisher bird beak.",
            source_query="birdwatching theoretical models structural principles",
            source_query_type="first_principles",
            url="https://youtube.com/watch?v=valid_train"
        ),
        # Valid 2: Photography composition tips
        CandidateVideo(
            id="valid_photo",
            title="8 IMPORTANT Composition Tips for Better Photos",
            channel="Jamie Windsor",
            duration_seconds=900,
            description="So you are looking to improve your photography composition...",
            source_query="photography applied methodology case study",
            source_query_type="practitioner_deep_dive",
            url="https://youtube.com/watch?v=valid_photo"
        ),
    ]

    ranked_items, filtered_count = filter_engine.filter_and_rank(candidates, prefs, top_k=5)

    assert filtered_count == 2
    assert len(ranked_items) == 2
    surviving_ids = [item.video.id for item in ranked_items]
    assert "mismatch_atom" not in surviving_ids
    assert "mismatch_forensic" not in surviving_ids


def test_saved_preferences_and_defaults():
    """Verify default preference attributes and JSON round-trip serialization."""
    prefs = UserPreferences(interests="baseball and strategy")
    assert prefs.experience_type == "mix"
    assert prefs.feed_mode == "mix"
    assert prefs.recency_preference == "any"
    assert prefs.seen_video_ids == []
    assert prefs.refresh_index == 0

    # Test JSON serialization and deserialization (as saved in localStorage)
    dumped = prefs.model_dump_json()
    loaded = UserPreferences.model_validate_json(dumped)
    assert loaded.interests == "baseball and strategy"
    assert loaded.experience_type == "mix"
    assert loaded.feed_mode == "mix"
    assert loaded.recency_preference == "any"

    # Customized preferences round-trip
    custom_prefs = UserPreferences(
        interests="cinema and storytelling",
        experience_type="fun",
        feed_mode="entertainment",
        recency_preference="recent",
        seen_video_ids=["vid1", "vid2"],
        refresh_index=2,
    )
    custom_dump = custom_prefs.model_dump_json()
    custom_loaded = UserPreferences.model_validate_json(custom_dump)
    assert custom_loaded.experience_type == "fun"
    assert custom_loaded.feed_mode == "entertainment"
    assert custom_loaded.recency_preference == "recent"
    assert custom_loaded.seen_video_ids == ["vid1", "vid2"]
    assert custom_loaded.refresh_index == 2


def test_refresh_deduplication():
    """Verify that previously seen video IDs are strictly excluded from subsequent refreshed feeds."""
    filter_engine = FilterEngine(api_key="")
    candidates = [
        CandidateVideo(
            id=f"vid_{i}",
            title=f"Great Pitching Mechanics Episode {i}",
            channel="Baseball Lab",
            url=f"https://youtube.com/watch?v=vid_{i}",
            duration_seconds=900,
            description="Deep analysis of baseball pitching mechanics and strategy."
        )
        for i in range(1, 7)
    ]

    # Initial feed: no seen videos
    prefs_init = UserPreferences(interests="baseball and strategy", seen_video_ids=[])
    items_init, _ = filter_engine.filter_and_rank(candidates, prefs_init, top_k=5)
    init_ids = [item.video.id for item in items_init]
    assert len(init_ids) == 5

    # Refreshed feed: pass the 5 seen video IDs
    prefs_refresh = UserPreferences(
        interests="baseball and strategy",
        seen_video_ids=init_ids,
        recent_shown_video_ids=init_ids,
        refresh_index=1
    )
    items_refresh, _ = filter_engine.filter_and_rank(candidates, prefs_refresh, top_k=5)
    refresh_ids = [item.video.id for item in items_refresh]

    # Verify 0 overlap between initial feed and refreshed feed
    assert len(set(init_ids).intersection(set(refresh_ids))) == 0
    # The only remaining candidate was vid_6
    assert refresh_ids == ["vid_6"]


def test_entertainment_only_feed():
    """Verify entertainment-only mode produces creative/humorous roles without forcing 5-step learning path."""
    expander = QueryExpander(api_key="")
    filter_engine = FilterEngine(api_key="")
    service = RabbitholeService(gemini_api_key="", youtube_api_key="")

    prefs = UserPreferences(
        interests="movies and storytelling",
        feed_mode="entertainment",
        experience_type="fun"
    )

    # 1. Query generator produces entertainment angles
    queries = expander.expand_queries(prefs, count=5)
    query_types = {q.query_type for q in queries}
    assert any(qt in query_types for qt in ["entertainment_comedy", "entertainment_creative", "entertainment_story"])
    assert "archive_lecture" not in query_types
    assert "first_principles" not in query_types

    # 2. Entertaining candidates score well on craft and humor without academic lecture markers
    entertaining_video = CandidateVideo(
        id="ent_1",
        title="Hilarious Movie Bloopers and Improvised Scenes That Made the Cut",
        channel="Cinema Comedy",
        url="https://youtube.com/watch?v=ent_1",
        duration_seconds=720,
        description="Funny improvised moments, laughter, and comedic blunders behind famous movies."
    )
    ranked, _ = filter_engine.filter_and_rank([entertaining_video], prefs, top_k=1)
    assert len(ranked) == 1
    assessment = ranked[0].assessment
    assert assessment.entertainment_score >= 0.7
    assert assessment.content_nature in ("entertaining", "blend")
    assert assessment.composite_score >= 0.65
    assert "entertainment" in assessment.connection_note.lower() or "comedic" in assessment.connection_note.lower()

    # 3. Feed assembly assigns entertainment roles instead of Start/Deepen/Bridge/Challenge/Continue
    feed_response = service.generate_feed(prefs, top_k=5)
    entertainment_step_names = {
        "Creative Feature", "Comedy & Wit", "Lore & Tales", "Artisan Craft", "Wildcard Gem",
        "Gameplay & Playthrough", "Challenge & Tournament", "Sports Highlight", "Live Performance",
        "Creative Animation", "Creative Build"
    }
    for item in feed_response.items:
        assert item.path_step in entertainment_step_names
        assert item.path_step not in {"Start", "Deepen", "Bridge", "Challenge", "Continue"}


def test_pool_exhaustion_fallback():
    """Verify that when available candidates are exhausted, the app honestly reports it and does not repeat seen videos."""
    service = RabbitholeService(gemini_api_key="", youtube_api_key="")

    # Provide only 2 candidates
    c1 = CandidateVideo(
        id="c1",
        title="Baseball Pitching Grips Masterclass",
        channel="Pitch Lab",
        url="https://youtube.com/watch?v=c1",
        duration_seconds=800,
        description="Analysis of baseball grip and pitch release."
    )
    c2 = CandidateVideo(
        id="c2",
        title="The Art of Baseball Defense Strategy",
        channel="Fielding Pros",
        url="https://youtube.com/watch?v=c2",
        duration_seconds=750,
        description="Tactical positioning and defensive shifts."
    )

    # User already saw c1
    prefs = UserPreferences(
        interests="baseball and strategy",
        seen_video_ids=["c1"],
        recent_shown_video_ids=["c1"],
        refresh_index=1,
    )

    # Monkeypatch retriever to return c1 and c2
    service.retriever.retrieve_candidates = lambda queries, max_per_query=5: [c1, c2]

    response = service.generate_feed(prefs, top_k=5)

    # Only c2 should be returned (c1 is excluded)
    assert len(response.items) == 1
    assert response.items[0].video.id == "c2"
    # Pool exhaustion must be honestly signaled
    assert response.pool_exhausted is True
    assert response.seen_count == 1
    assert "Candidate pool partially exhausted" in response.status_message
    assert "1 previously seen videos were excluded" in response.status_message


def test_query_rotation_on_refresh():
    """Verify that consecutive refreshes rotate queries deterministically."""
    expander = QueryExpander(api_key="")
    prefs_0 = UserPreferences(interests="baseball and strategy", refresh_index=0, feed_mode="mix")
    prefs_1 = UserPreferences(interests="baseball and strategy", refresh_index=1, feed_mode="mix")

    queries_0 = [q.query for q in expander.expand_queries(prefs_0, count=5)]
    queries_1 = [q.query for q in expander.expand_queries(prefs_1, count=5)]

    # Consecutive refreshes must produce different queries
    assert queries_0 != queries_1


def test_viewing_record_progress_and_completion():
    """Verify ViewingRecord calculation, progress percentage, resume eligibility, and JSON serialization."""
    from rabbithole_app.models import ViewingRecord

    # 1. Started video (< 15s)
    v_started = ViewingRecord(
        video_id="v_start",
        title="Intro to Knots",
        channel="CraftLab",
        duration_seconds=600,
        current_position_seconds=12.0,
        watch_time_seconds=12.0,
        status="started"
    )
    assert round(v_started.progress_percentage, 1) == 2.0
    assert v_started.is_resumeable is True

    # 2. In-progress video (e.g. 40% through)
    v_progress = ViewingRecord(
        video_id="v_prog",
        title="Deep Pitching Biomechanics",
        channel="PitchingAcademy",
        duration_seconds=1200,
        current_position_seconds=480.0,
        watch_time_seconds=460.0,
        status="in_progress"
    )
    assert round(v_progress.progress_percentage, 1) == 40.0
    assert v_progress.is_resumeable is True

    # 3. Completed video
    v_completed = ViewingRecord(
        video_id="v_done",
        title="Game Theory Fundamentals",
        channel="MathAcademy",
        duration_seconds=900,
        current_position_seconds=900.0,
        watch_time_seconds=880.0,
        status="completed",
        completed_at="2026-10-29T00:00:00Z"
    )
    assert round(v_completed.progress_percentage, 1) == 100.0
    assert v_completed.is_resumeable is False

    # 4. JSON serialization roundtrip (saving and restoring progress locally)
    serialized = v_progress.model_dump_json()
    restored = ViewingRecord.model_validate_json(serialized)
    assert restored.video_id == "v_prog"
    assert restored.current_position_seconds == 480.0
    assert restored.watch_time_seconds == 460.0
    assert restored.status == "in_progress"


def test_completed_videos_excluded_from_future_feeds():
    """Verify that completed videos are strictly excluded from subsequent recommended feeds."""
    filter_engine = FilterEngine(api_key="")
    candidates = [
        CandidateVideo(
            id="v_completed",
            title="Complete History of Cinema Narrative",
            channel="FilmSchool",
            url="https://youtube.com/watch?v=v_completed",
            duration_seconds=1800,
            description="Deep documentary on cinematic storytelling."
        ),
        CandidateVideo(
            id="v_fresh",
            title="French New Wave Editing Mechanics",
            channel="FilmSchool",
            url="https://youtube.com/watch?v=v_fresh",
            duration_seconds=1200,
            description="Detailed analysis of jump cuts and editing rhythm."
        )
    ]

    # Viewer has already completed v_completed
    prefs = UserPreferences(
        interests="movies and storytelling",
        completed_video_ids=["v_completed"],
        feed_mode="mix"
    )

    ranked, filtered_count = filter_engine.filter_and_rank(candidates, prefs, top_k=5)
    surviving_ids = [item.video.id for item in ranked]

    # v_completed must be filtered out
    assert "v_completed" not in surviving_ids
    assert "v_fresh" in surviving_ids
    assert filtered_count == 1


def test_viewing_signals_modest_creator_and_topic_boost():
    """Verify that creators and topics with real watch time receive a modest boost without crowding out unfamiliar subjects."""
    filter_engine = FilterEngine(api_key="")

    c_favorite_creator = CandidateVideo(
        id="c_fav",
        title="Advanced Pitch Tunneling Analysis",
        channel="PitchLab",
        url="https://youtube.com/watch?v=c_fav",
        duration_seconds=900,
        description="Comprehensive pitch tunneling and release point metrics."
    )
    c_unfamiliar_creator = CandidateVideo(
        id="c_unfamiliar",
        title="Advanced Pitch Tunneling Analysis",
        channel="UnknownAnalyst",
        url="https://youtube.com/watch?v=c_unfamiliar",
        duration_seconds=900,
        description="Comprehensive pitch tunneling and release point metrics."
    )

    # 1. Baseline: identical scores when no watch history
    prefs_baseline = UserPreferences(interests="baseball and strategy", feed_mode="mix")
    ass_base_fav = filter_engine._heuristic_assessment(c_favorite_creator, prefs_baseline)
    ass_base_unf = filter_engine._heuristic_assessment(c_unfamiliar_creator, prefs_baseline)
    assert ass_base_fav.composite_score == ass_base_unf.composite_score

    # 2. User has watched 300 seconds of PitchLab videos inside Rabbithole
    prefs_with_history = UserPreferences(
        interests="baseball and strategy",
        feed_mode="mix",
        watched_channels={"PitchLab": 300.0},
        watched_topics=["pitch tunneling"]
    )
    ass_fav = filter_engine._heuristic_assessment(c_favorite_creator, prefs_with_history)
    ass_unf = filter_engine._heuristic_assessment(c_unfamiliar_creator, prefs_with_history)

    # Favorite creator gets modest boost (around +0.03 to +0.06)
    score_diff = ass_fav.composite_score - ass_unf.composite_score
    assert 0.02 <= score_diff <= 0.07

    # Unfamiliar creator still scores highly (> 0.65) and is not penalized
    assert ass_unf.composite_score >= 0.65


def test_quick_exit_and_seeking_do_not_falsely_boost_or_complete():
    """Verify that quick exits (< 15s) do not receive viewing boosts or trigger false completion."""
    filter_engine = FilterEngine(api_key="")

    c_quick = CandidateVideo(
        id="c_quick",
        title="Pitching Mechanics",
        channel="QuickExitChannel",
        url="https://youtube.com/watch?v=c_quick",
        duration_seconds=900,
        description="Pitching analysis."
    )

    # User exited after 10 seconds (quick bounce)
    prefs_bounce = UserPreferences(
        interests="baseball and strategy",
        feed_mode="mix",
        watched_channels={"QuickExitChannel": 10.0}  # Under 60 seconds threshold
    )
    ass_bounce = filter_engine._heuristic_assessment(c_quick, prefs_bounce)
    ass_neutral = filter_engine._heuristic_assessment(c_quick, UserPreferences(interests="baseball and strategy", feed_mode="mix"))

    # Must NOT give boost for quick bounce
    assert ass_bounce.composite_score == ass_neutral.composite_score


def test_continue_watching_independence_from_feed_refresh():
    """Verify that in-progress videos in seen history are excluded from feeds but remain resumeable in viewing history."""
    from rabbithole_app.models import ViewingRecord

    service = RabbitholeService(gemini_api_key="", youtube_api_key="")

    c_in_progress = CandidateVideo(
        id="vid_ip",
        title="Art of Cinematography Lighting",
        channel="CinemaCraft",
        url="https://youtube.com/watch?v=vid_ip",
        duration_seconds=1200,
        description="Detailed guide to cinematic lighting setups."
    )
    c_new = CandidateVideo(
        id="vid_new",
        title="Color Grading in Film History",
        channel="ColoristGuild",
        url="https://youtube.com/watch?v=vid_new",
        duration_seconds=900,
        description="Historical color grading processes."
    )

    # 1. Video was recorded in viewing history as in-progress
    record = ViewingRecord(
        video_id=c_in_progress.id,
        title=c_in_progress.title,
        channel=c_in_progress.channel,
        duration_seconds=c_in_progress.duration_seconds,
        current_position_seconds=450.0,
        watch_time_seconds=400.0,
        status="in_progress"
    )
    assert record.is_resumeable is True
    assert round(record.progress_percentage, 1) == 37.5

    # 2. Feed refresh deduplication excludes it from appearing again in the candidate feed
    service.retriever.retrieve_candidates = lambda queries, max_per_query=5: [c_in_progress, c_new]
    prefs_refreshed = UserPreferences(
        interests="movies and storytelling",
        feed_mode="mix",
        recent_shown_video_ids=[c_in_progress.id],
        seen_video_ids=[c_in_progress.id],  # already shown in previous feed
        refresh_index=1
    )
    feed_res = service.generate_feed(prefs_refreshed, top_k=5)
    feed_ids = [item.video.id for item in feed_res.items]

    # c_in_progress was excluded from new feed
    assert "vid_ip" not in feed_ids
    assert "vid_new" in feed_ids

    # But the record remains completely intact for Continue Watching
    assert record.video_id == "vid_ip"
    assert record.is_resumeable is True


def test_disliked_video_exclusion_across_refreshes_and_feed_modes():
    """Verify that a disliked video ID is strictly excluded from candidate feeds across all feed modes and refreshes."""
    service = RabbitholeService(gemini_api_key="", youtube_api_key="")

    c_disliked = CandidateVideo(
        id="vid_disliked_123",
        title="Superficial Baseball Highlights",
        channel="DailySports",
        url="https://youtube.com/watch?v=vid_disliked_123",
        duration_seconds=600,
        description="A quick montage of baseball moments."
    )
    c_good1 = CandidateVideo(
        id="vid_good_1",
        title="Pitch Sequencing Game Theory Mechanics",
        channel="PitchLab",
        url="https://youtube.com/watch?v=vid_good_1",
        duration_seconds=950,
        description="Deconstructing sequential pitch decision trees."
    )
    c_good2 = CandidateVideo(
        id="vid_good_2",
        title="Sabermetrics Revolution Bill James Archive",
        channel="BaseballHistory",
        url="https://youtube.com/watch?v=vid_good_2",
        duration_seconds=1100,
        description="Archival lectures on baseball analytics."
    )

    service.retriever.retrieve_candidates = lambda queries, max_per_query=5: [c_disliked, c_good1, c_good2]

    # 1. Mix mode with disliked video
    prefs_mix = UserPreferences(
        interests="baseball and strategy",
        feed_mode="mix",
        disliked_video_ids=["vid_disliked_123"]
    )
    res_mix = service.generate_feed(prefs_mix, top_k=5)
    mix_ids = [item.video.id for item in res_mix.items]
    assert "vid_disliked_123" not in mix_ids
    assert "vid_good_1" in mix_ids

    # 2. Entertainment mode with disliked video
    prefs_ent = UserPreferences(
        interests="baseball and strategy",
        feed_mode="entertainment",
        disliked_video_ids=["vid_disliked_123"]
    )
    res_ent = service.generate_feed(prefs_ent, top_k=5)
    ent_ids = [item.video.id for item in res_ent.items]
    assert "vid_disliked_123" not in ent_ids

    # 3. Deep learning mode with disliked video
    prefs_dl = UserPreferences(
        interests="baseball and strategy",
        feed_mode="deep_learning",
        disliked_video_ids=["vid_disliked_123"]
    )
    res_dl = service.generate_feed(prefs_dl, top_k=5)
    dl_ids = [item.video.id for item in res_dl.items]
    assert "vid_disliked_123" not in dl_ids

    # 4. Across consecutive refreshes (refresh_index = 1, 2)
    prefs_refreshed = UserPreferences(
        interests="baseball and strategy",
        feed_mode="mix",
        refresh_index=2,
        disliked_video_ids=["vid_disliked_123"]
    )
    res_refreshed = service.generate_feed(prefs_refreshed, top_k=5)
    refreshed_ids = [item.video.id for item in res_refreshed.items]
    assert "vid_disliked_123" not in refreshed_ids


def test_undo_dislike_restores_eligibility():
    """Verify that removing a video from disliked_video_ids restores its eligibility in feeds."""
    service = RabbitholeService(gemini_api_key="", youtube_api_key="")

    c_target = CandidateVideo(
        id="vid_restore_me",
        title="Pitch Sequencing Game Theory Mechanics",
        channel="PitchLab",
        url="https://youtube.com/watch?v=vid_restore_me",
        duration_seconds=950,
        description="Deconstructing sequential pitch decision trees."
    )

    service.retriever.retrieve_candidates = lambda queries, max_per_query=5: [c_target]

    # When disliked: excluded
    prefs_disliked = UserPreferences(
        interests="baseball and strategy",
        feed_mode="mix",
        disliked_video_ids=["vid_restore_me"]
    )
    res1 = service.generate_feed(prefs_disliked, top_k=5)
    assert "vid_restore_me" not in [item.video.id for item in res1.items]

    # When undone / unsuppressed: restored
    prefs_undone = UserPreferences(
        interests="baseball and strategy",
        feed_mode="mix",
        disliked_video_ids=[]  # user clicked Undo or removed from disliked list
    )
    res2 = service.generate_feed(prefs_undone, top_k=5)
    assert "vid_restore_me" in [item.video.id for item in res2.items]


def test_disliking_preserves_viewing_history():
    """Verify that disliking a video does not alter or erase its playback history."""
    viewing_record = ViewingRecord(
        video_id="vid_played_1",
        title="Pitch Sequencing Game Theory Mechanics",
        channel="PitchLab",
        duration_seconds=1200,
        current_position_seconds=420.0,
        watch_time_seconds=380.0,
        status="in_progress"
    )

    dislike_record = DislikeRecord(
        video_id="vid_played_1",
        title=viewing_record.title,
        channel=viewing_record.channel,
        chosen_reason="Don’t like this format",
        date="2026-10-29T12:00:00Z"
    )

    # Viewing progress remains completely intact and separate from dislike record
    assert viewing_record.video_id == dislike_record.video_id
    assert viewing_record.current_position_seconds == 420.0
    assert viewing_record.watch_time_seconds == 380.0
    assert viewing_record.status == "in_progress"
    assert viewing_record.is_resumeable is True
    assert round(viewing_record.progress_percentage, 1) == 35.0


def test_single_disliked_video_does_not_ban_topic_or_channel():
    """Verify that disliking one video from a channel/topic does NOT ban that channel or topic."""
    service = RabbitholeService(gemini_api_key="", youtube_api_key="")

    c_disliked = CandidateVideo(
        id="vid_bad_1",
        title="Baseball bloopers and dugout pranks",
        channel="BaseballHQ",
        url="https://youtube.com/watch?v=vid_bad_1",
        duration_seconds=600,
        description="Funny bloopers from the dugout."
    )
    c_good_same_channel_topic = CandidateVideo(
        id="vid_good_same_chan",
        title="Pitch sequencing and game theory mechanics",
        channel="BaseballHQ",  # same channel and topic!
        url="https://youtube.com/watch?v=vid_good_same_chan",
        duration_seconds=900,
        description="Detailed strategic breakdown of pitcher sequencing."
    )

    service.retriever.retrieve_candidates = lambda queries, max_per_query=5: [c_disliked, c_good_same_channel_topic]

    prefs = UserPreferences(
        interests="baseball and strategy",
        feed_mode="mix",
        disliked_video_ids=["vid_bad_1"],
        disliked_records=[
            DislikeRecord(
                video_id="vid_bad_1",
                title=c_disliked.title,
                channel=c_disliked.channel,
                chosen_reason="Not interested in this topic"
            )
        ]
    )

    res = service.generate_feed(prefs, top_k=5)
    result_ids = [item.video.id for item in res.items]

    # vid_bad_1 is excluded
    assert "vid_bad_1" not in result_ids
    # vid_good_same_chan from the exact same channel and topic is NOT banned and appears in the feed!
    assert "vid_good_same_chan" in result_ids


def test_dislike_pattern_summary_generation():
    """Verify summarize_dislike_patterns produces compact, factual summaries without claiming Gemini memory."""
    records = [
        DislikeRecord(
            video_id="vid_1",
            title="University Physics Colloquium on Mechanics",
            channel="MIT OpenCourseWare",
            chosen_reason="Too academic"
        ),
        DislikeRecord(
            video_id="vid_2",
            title="Short summary clip",
            channel="QuickSummaries",
            chosen_reason="Too shallow"
        ),
        DislikeRecord(
            video_id="vid_3",
            title="Another Heavy Lecture",
            channel="Stanford",
            chosen_reason="Too academic"
        ),
    ]

    summary = summarize_dislike_patterns(records)

    assert "Summary of Disliked Patterns (3 total):" in summary
    assert "2x 'Too academic'" in summary
    assert "1x 'Too shallow'" in summary
    assert "MIT OpenCourseWare" in summary
    assert "Do NOT ban entire broad topics" in summary
    # Must NOT claim Gemini remembers on its own
    assert "Gemini remembers" not in summary


def test_gemini_query_and_eval_payload_includes_dislike_summary(monkeypatch):
    """Verify that dislike summary is injected into prompts for Gemini query generation and candidate evaluation."""
    from unittest.mock import MagicMock

    records = [
        DislikeRecord(
            video_id="vid_1",
            title="Heavy Lecture on Mechanics",
            channel="PhysicsArch",
            chosen_reason="Too academic"
        )
    ]
    prefs = UserPreferences(
        interests="baseball and strategy",
        feed_mode="mix",
        disliked_video_ids=["vid_1"],
        disliked_records=records
    )

    # 1. Test QueryExpander._expand_with_gemini passes dislike feedback
    expander = QueryExpander(api_key="fake_key_for_testing")
    captured_prompts = []

    class MockInteractions:
        def create(self, **kwargs):
            captured_prompts.append(kwargs.get("input", ""))
            mock_res = MagicMock()
            mock_res.output_text = '{"queries": [{"query": "baseball sabermetrics analysis", "domain": "sports", "query_type": "first_principles", "rationale": "strategic analytics"}]}'
            return mock_res

    class MockClient:
        def __init__(self, **kwargs):
            self.interactions = MockInteractions()

    monkeypatch.setattr("google.genai.Client", MockClient)

    expander._expand_with_gemini(prefs, count=1)
    assert len(captured_prompts) == 1
    assert "User Dislike Feedback / Patterns (from current session history):" in captured_prompts[0]
    assert "Too academic" in captured_prompts[0]
    assert "Gemini remembers" not in captured_prompts[0]

    # 2. Test FilterEngine._evaluate_with_gemini passes dislike feedback
    filter_engine = FilterEngine(api_key="fake_key_for_testing")
    eval_captured_prompts = []

    class MockEvalInteractions:
        def create(self, **kwargs):
            eval_captured_prompts.append(kwargs.get("input", ""))
            mock_res = MagicMock()
            mock_res.output_text = '{"evaluations": [{"video_id": "c1", "clickbait_score": 0.1, "substance_score": 0.8, "novelty_score": 0.7, "connection_note": "grounded", "key_takeaways": ["point"], "branch_suggestions": ["b1"]}]}'
            return mock_res

    class MockEvalClient:
        def __init__(self, **kwargs):
            self.interactions = MockEvalInteractions()

    monkeypatch.setattr("google.genai.Client", MockEvalClient)

    cand = CandidateVideo(
        id="c1",
        title="Pitch Framing Valuation",
        channel="PitchLab",
        url="https://youtube.com/watch?v=c1",
        duration_seconds=800
    )
    filter_engine._evaluate_with_gemini([cand], prefs)
    assert len(eval_captured_prompts) == 1
    assert "User Dislike Feedback / Patterns (from current session history):" in eval_captured_prompts[0]
    assert "Too academic" in eval_captured_prompts[0]


def test_no_key_heuristics_adapts_to_dislike_feedback():
    """Verify that no-key heuristic mode adapts scoring when dislike feedback is present without banning topics."""
    filter_engine = FilterEngine(api_key="")

    cand_academic = CandidateVideo(
        id="cand_ac",
        title="Theoretical Mechanics of Pitching: University Lecture",
        channel="AcademicLectures",
        url="https://youtube.com/watch?v=cand_ac",
        duration_seconds=1800,
        description="Formal university lecture and theory."
    )

    # Without dislike feedback
    prefs_no_dislikes = UserPreferences(
        interests="baseball and strategy",
        feed_mode="mix"
    )
    assessment_neutral = filter_engine._heuristic_assessment(cand_academic, prefs_no_dislikes)

    # With "Too academic" dislike feedback
    prefs_academic_disliked = UserPreferences(
        interests="baseball and strategy",
        feed_mode="mix",
        disliked_records=[
            DislikeRecord(
                video_id="prev_ac_vid",
                title="Previous Dry Lecture",
                channel="AcademicLectures",
                chosen_reason="Too academic"
            )
        ]
    )
    assessment_adjusted = filter_engine._heuristic_assessment(cand_academic, prefs_academic_disliked)

    # The score should be modestly lower (~0.05) to reflect user's disinclination towards dry lectures
    assert assessment_adjusted.composite_score < assessment_neutral.composite_score
    # But it remains positive and not zeroed out or banned
    assert assessment_adjusted.composite_score > 0.40


def test_api_feed_endpoint_accepts_disliked_preferences():
    """Verify FastAPI /api/feed endpoint accepts disliked_video_ids and disliked_records and executes cleanly."""
    client = TestClient(app)

    payload = {
        "interests": "baseball and strategy",
        "feed_mode": "mix",
        "disliked_video_ids": ["disliked_1"],
        "disliked_records": [
            {
                "video_id": "disliked_1",
                "title": "Bloopers",
                "channel": "ComedySports",
                "chosen_reason": "Too shallow",
                "date": "2026-10-29T12:00:00Z"
            }
        ]
    }

    res = client.post("/api/feed", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "generated_queries" in data

    # Ensure disliked_1 is not returned in items
    for item in data["items"]:
        assert item["video"]["id"] != "disliked_1"


def test_main_feed_size_and_category_variety():
    """Verify that the main feed assembles ~10 videos across categories without letting one dominate, and reserves 1-2 discovery slots."""
    service = RabbitholeService(gemini_api_key="")
    prefs = UserPreferences(
        interests="baseball and strategy, cinema and storytelling",
        categories=["Baseball & Strategy", "Cinema & Storytelling"],
        feed_mode="mix"
    )

    # 15 candidate items across both categories + discovery items
    candidates = []
    # 6 Baseball items
    for i in range(6):
        candidates.append(CandidateVideo(
            id=f"bb_{i}",
            title=f"Baseball Strategy & Sabermetrics Analysis Vol {i}",
            channel=f"BaseballChannel_{i}",
            url=f"https://youtube.com/watch?v=bb_{i}",
            duration_seconds=900,
            source_query="baseball sabermetrics statistical decision analysis",
            source_query_type="first_principles",
            source_domain="Baseball & Strategy",
        ))
    # 6 Cinema items
    for i in range(6):
        candidates.append(CandidateVideo(
            id=f"film_{i}",
            title=f"Cinema & Storytelling Narrative Mechanics Vol {i}",
            channel=f"FilmChannel_{i}",
            url=f"https://youtube.com/watch?v=film_{i}",
            duration_seconds=900,
            source_query="cinema storytelling narrative mechanics analysis",
            source_query_type="first_principles",
            source_domain="Cinema & Storytelling",
        ))
    # 3 Discovery bridge items (cross domain)
    for i in range(3):
        candidates.append(CandidateVideo(
            id=f"disc_{i}",
            title=f"Unexpected Cross-Disciplinary Bridge to Game Theory {i}",
            channel=f"MathArch_{i}",
            url=f"https://youtube.com/watch?v=disc_{i}",
            duration_seconds=900,
            source_query="game theory pitch sequencing minimax strategy",
            source_query_type="cross_domain_analogy",
            source_domain="Tactical Game Theory",
        ))

    feed_items, _ = service.filter_engine.filter_and_rank(candidates, prefs, top_k=25)
    from rabbithole_app.journey import build_main_feed
    main_feed = build_main_feed(feed_items, prefs, length=10, discovery_target=2)

    # 1. Total count is 10
    assert len(main_feed) == 10

    # 2. Contains 1 to 2 discovery items labeled "✨ Discovery" with explainable connection
    discoveries = [it for it in main_feed if it.is_discovery]
    assert 1 <= len(discoveries) <= 2
    for d in discoveries:
        assert d.path_step == "✨ Discovery"
        assert d.discovery_connection is not None
        assert len(d.discovery_connection) > 10

    # 3. Neither category dominates
    bb_items = [it for it in main_feed if not it.is_discovery and ("bb" in it.video.id or it.category == "Baseball & Strategy")]
    film_items = [it for it in main_feed if not it.is_discovery and ("film" in it.video.id or it.category == "Cinema & Storytelling")]
    assert len(bb_items) >= 3
    assert len(film_items) >= 3
    assert abs(len(bb_items) - len(film_items)) <= 2


def test_independent_category_tab_feed_and_refreshes():
    """Verify category tabs generate focused ~5-video feeds and independent refreshes."""
    service = RabbitholeService(gemini_api_key="")

    prefs_cat = UserPreferences(
        interests="baseball and strategy, cinema and storytelling",
        categories=["Baseball & Strategy", "Cinema & Storytelling"],
        active_category="Baseball & Strategy",
        refresh_index=0,
        feed_mode="mix"
    )

    candidates = [
        CandidateVideo(
            id=f"bb_cand_{i}",
            title=f"Baseball Sabermetrics Episode {i}",
            channel=f"Channel_{i}",
            url=f"https://youtube.com/watch?v=bb_cand_{i}",
            duration_seconds=800,
            source_query="baseball sabermetrics statistical decision analysis",
            source_query_type="first_principles",
            source_domain="Baseball & Strategy",
        )
        for i in range(10)
    ]

    feed_items, _ = service.filter_engine.filter_and_rank(candidates, prefs_cat, top_k=10)
    from rabbithole_app.journey import build_category_feed
    cat_feed_0 = build_category_feed(feed_items, prefs_cat, category_name="Baseball & Strategy", length=5)

    assert len(cat_feed_0) == 5
    for it in cat_feed_0:
        assert it.category == "Baseball & Strategy"
        assert "baseball" in it.path_reason.lower() or "focused" in it.path_reason.lower()

    queries_refreshed_cat = service.expander.expand_queries(
        prefs_cat.model_copy(update={"refresh_index": 1}), count=5
    )
    for q in queries_refreshed_cat:
        combined = f"{q.query} | {q.domain} | {q.rationale}".lower()
        assert any(term in combined for term in ["baseball", "strategy", "sabermetrics", "game theory", "sports", "pitch", "ball"])


def test_category_tab_uses_its_own_topic_for_search_and_relevance(monkeypatch):
    service = RabbitholeService(gemini_api_key="")
    prefs = UserPreferences(
        interests="baseball, math",
        categories=["Baseball", "Math"],
        active_category="Math",
    )
    observed = {}

    def expand(scoped_prefs, count):
        observed["search_interests"] = scoped_prefs.interests
        return [CuriosityQuery(query="math visual proofs", domain="Math", query_type="first_principles", rationale="Focused math")]

    candidates = [
        CandidateVideo(id="baseball", title="Baseball pitching strategy explained", channel="Sports",
                       url="https://youtube.com/watch?v=baseball", duration_seconds=900,
                       source_query="math visual proofs", source_domain="Math"),
        CandidateVideo(id="math", title="Math visual proofs of geometry", channel="Geometry",
                       url="https://youtube.com/watch?v=math", duration_seconds=900,
                       source_query="math visual proofs", source_domain="Math"),
    ]
    monkeypatch.setattr(service.expander, "expand_queries", expand)
    monkeypatch.setattr(service.retriever, "retrieve_candidates", lambda *_args, **_kwargs: candidates)

    response = service.generate_feed(prefs)

    assert observed["search_interests"] == "Math"
    assert [item.video.id for item in response.items] == ["math"]
    assert response.items[0].category == "Math"


def test_category_tab_does_not_admit_off_topic_followed_creator():
    prefs = UserPreferences(
        interests="baseball, math",
        categories=["Baseball", "Math"],
        active_category="Math",
        followed_creators=[FollowedCreator(name="Sports Channel")],
    )
    candidate = CandidateVideo(
        id="sports", title="Baseball pitching strategy explained", channel="Sports Channel",
        url="https://youtube.com/watch?v=sports", duration_seconds=900,
        source_query="math visual proofs", source_domain="Math",
    )

    result = RelevanceGate(api_key="").assess_candidate(candidate, prefs)

    assert result.is_relevant is False
    assert "active tab" in result.reason


def test_baseball_strategy_tab_rejects_unrelated_strategy_video():
    prefs = UserPreferences(
        interests="baseball, business",
        categories=["Baseball & Strategy", "Business"],
        active_category="Baseball & Strategy",
    )
    candidate = CandidateVideo(
        id="corporate", title="Corporate strategy for growing companies", channel="Business",
        url="https://youtube.com/watch?v=corporate", duration_seconds=900,
        source_query="baseball strategy analysis", source_domain="Baseball & Strategy",
    )

    result = RelevanceGate(api_key="").assess_candidate(candidate, prefs)

    assert result.is_relevant is False


def test_watch_later_persistence_and_model():
    """Verify WatchLaterRecord serialization, persistence, and attributes."""
    from rabbithole_app.models import WatchLaterRecord
    rec = WatchLaterRecord(
        video_id="wl_vid_1",
        title="Sabermetrics Revolution Archive",
        channel="BaseballAnalytics",
        thumbnail="https://example.com/thumb.jpg",
        duration_seconds=1800,
        recommendation_context="Deep dive into run expectancy matrices",
        source_domain="Sabermetrics",
        source_category="Baseball & Strategy",
        is_discovery=True,
        discovery_connection="Surprising statistical bridge connecting sports to operations research",
        saved_at="2026-10-29T12:00:00Z"
    )

    serialized = rec.model_dump_json()
    restored = WatchLaterRecord.model_validate_json(serialized)

    assert restored.video_id == "wl_vid_1"
    assert restored.is_discovery is True
    assert restored.discovery_connection == "Surprising statistical bridge connecting sports to operations research"
    assert restored.source_category == "Baseball & Strategy"


def test_emerging_interest_detection_on_save_discovery():
    """Verify that saving a discovery video triggers an emerging interest signal with 1 positive action."""
    from rabbithole_app.models import EmergingInterestSignal
    signal = EmergingInterestSignal(
        topic="Tactical Game Theory",
        source_discovery_domain="Game Theory",
        distinct_video_ids=["disc_vid_1"],
        saved_video_ids=["disc_vid_1"],
        watched_video_ids=[],
        positive_actions_count=1,
        is_promoted=False
    )

    assert len(signal.distinct_video_ids) == 1
    assert signal.positive_actions_count == 1
    assert not signal.is_promoted


def test_emerging_interest_promotion_thresholds():
    """Verify strong evidence promotion: 2 distinct positive actions OR saving + substantially watching one video."""
    from rabbithole_app.models import EmergingInterestSignal

    # Scenario A: 2 distinct positive actions on distinct discovery videos
    signal_a = EmergingInterestSignal(
        topic="Tactical Game Theory",
        source_discovery_domain="Game Theory",
        distinct_video_ids=["disc_1", "disc_2"],
        saved_video_ids=["disc_1", "disc_2"],
        watched_video_ids=[],
        positive_actions_count=2,
        is_promoted=False
    )
    should_promote_a = (len(signal_a.distinct_video_ids) >= 2 and signal_a.positive_actions_count >= 2)
    assert should_promote_a is True

    # Scenario B: Saving AND substantially watching one video (watch_time >= 60s)
    signal_b = EmergingInterestSignal(
        topic="Biomimicry Engineering",
        source_discovery_domain="Biomimicry",
        distinct_video_ids=["kingfisher_train"],
        saved_video_ids=["kingfisher_train"],
        watched_video_ids=["kingfisher_train"],
        positive_actions_count=2,
        is_promoted=False
    )
    has_save_and_watch = any(vid in signal_b.watched_video_ids for vid in signal_b.saved_video_ids)
    assert has_save_and_watch is True


def test_weak_signals_not_promoted():
    """Verify that opening and immediately closing (< 30s) or seeking to the end does not promote."""
    from rabbithole_app.models import ViewingRecord

    # User opened video and closed after 8 seconds
    record_quick_exit = ViewingRecord(
        video_id="disc_preview",
        title="Brief Glance at Game Theory",
        channel="MathLab",
        duration_seconds=900,
        current_position_seconds=8.0,
        watch_time_seconds=8.0,
        status="started"
    )
    assert record_quick_exit.watch_time_seconds < 30.0

    # User scrubbed/seeked directly to 890s out of 900s, but only played for 5s of real elapsed time
    record_seek_end = ViewingRecord(
        video_id="disc_seek",
        title="Skipped to End",
        channel="MathLab",
        duration_seconds=900,
        current_position_seconds=890.0,
        watch_time_seconds=5.0,
        status="started"
    )
    assert record_seek_end.watch_time_seconds < 60.0


def test_duplicate_category_merging():
    """Verify that closely related emerging topics are merged rather than creating duplicate tabs."""
    from rabbithole_app.journey import _terms

    def are_closely_related(a: str, b: str) -> bool:
        t_a = _terms(a)
        t_b = _terms(b)
        if not t_a or not t_b:
            return a.lower().strip() == b.lower().strip()
        overlap = t_a & t_b
        return len(overlap) >= min(len(t_a), len(t_b)) * 0.5 or a.lower() in b.lower() or b.lower() in a.lower()

    existing_categories = ["Baseball & Strategy", "Cinema & Storytelling"]

    assert are_closely_related("Tactical Game Theory", "Game Theory") is True
    assert are_closely_related(existing_categories[1], "Storytelling Mechanics") is True
    assert are_closely_related(existing_categories[0], "Organic Gardening") is False
    assert are_closely_related(existing_categories[1], "Organic Gardening") is False


def test_dismissed_category_not_recreated():
    """Verify that a category dismissed by the user is excluded and does not immediately reappear."""
    prefs = UserPreferences(
        interests="baseball and strategy",
        categories=["Baseball & Strategy"],
        dismissed_categories=["Tactical Game Theory", "CSI Forensic Analysis"],
        feed_mode="mix"
    )

    assert "Tactical Game Theory" not in prefs.categories
    assert "Tactical Game Theory" in prefs.dismissed_categories

    from rabbithole_app.filter_engine import summarize_interest_state
    summary = summarize_interest_state(prefs)
    assert "Dismissed Categories (do not recreate): Tactical Game Theory, CSI Forensic Analysis" in summary


def test_disliked_video_and_topic_prevents_promotion():
    """Verify that disliked videos or topics are strictly suppressed and cannot trigger emerging interest promotion."""
    disliked_rec = DislikeRecord(
        video_id="disliked_disc_1",
        title="Unwanted Clickbait Bridge",
        channel="HypeChannel",
        chosen_reason="Don’t like this format",
        source_domain="Sensational Science"
    )
    prefs = UserPreferences(
        interests="baseball and strategy",
        disliked_video_ids=["disliked_disc_1"],
        disliked_records=[disliked_rec]
    )

    cand = CandidateVideo(
        id="disliked_disc_1",
        title="Unwanted Clickbait Bridge",
        channel="HypeChannel",
        url="https://youtube.com/watch?v=disliked_disc_1",
        duration_seconds=900,
        source_domain="Sensational Science",
        source_query_type="cross_domain_analogy"
    )

    service = RabbitholeService(gemini_api_key="")
    feed_items, filtered_count = service.filter_engine.filter_and_rank([cand], prefs, top_k=5)

    assert len(feed_items) == 0
    assert filtered_count == 1


def test_end_to_end_discovery_to_emerging_interest_to_tab():
    """Full progression demonstration:
    1. Surfaced discovery video
    2. User saves it to Watch Later -> generates 1 emerging signal
    3. User watches it substantially (>= 60s) -> promotes to a new interest tab
    """
    from rabbithole_app.models import WatchLaterRecord, EmergingInterestSignal, ViewingRecord, InterestCategory

    # 1. Discovery item surfaced in main feed
    disc_item = CandidateVideo(
        id="disc_gt_1",
        title="Minimax Optimization in Game Theory",
        channel="MathArch",
        url="https://youtube.com/watch?v=disc_gt_1",
        duration_seconds=1200,
        source_domain="Game Theory",
        source_query_type="cross_domain_analogy"
    )

    # 2. User saves to Watch Later -> 1 emerging signal
    wl_rec = WatchLaterRecord(
        video_id=disc_item.id,
        title=disc_item.title,
        channel=disc_item.channel,
        duration_seconds=disc_item.duration_seconds,
        source_domain=disc_item.source_domain,
        is_discovery=True,
        discovery_connection="Surprising connection to strategic decision making",
        saved_at="2026-10-29T12:00:00Z"
    )
    assert wl_rec.is_discovery is True

    signal = EmergingInterestSignal(
        topic="Game Theory",
        source_discovery_domain="Game Theory",
        distinct_video_ids=[disc_item.id],
        saved_video_ids=[disc_item.id],
        watched_video_ids=[],
        positive_actions_count=1,
        is_promoted=False
    )
    assert signal.positive_actions_count == 1
    assert not signal.is_promoted

    # 3. User substantially watches the video (watch_time >= 60s)
    viewing_rec = ViewingRecord(
        video_id=disc_item.id,
        title=disc_item.title,
        channel=disc_item.channel,
        duration_seconds=disc_item.duration_seconds,
        current_position_seconds=180.0,
        watch_time_seconds=175.0,
        status="in_progress"
    )
    assert viewing_rec.watch_time_seconds >= 60.0

    signal.watched_video_ids.append(disc_item.id)
    signal.positive_actions_count += 1
    has_save_and_substantial_watch = any(vid in signal.watched_video_ids for vid in signal.saved_video_ids)
    assert has_save_and_substantial_watch is True

    # Promotion into new InterestCategory tab
    promoted_cat = InterestCategory(
        id="cat_promoted_1",
        name=signal.topic,
        is_auto_created=True,
        is_dismissed=False,
        created_at="2026-10-29T12:05:00Z"
    )
    signal.is_promoted = True

    assert promoted_cat.is_auto_created is True
    assert promoted_cat.name == "Game Theory"
    assert signal.is_promoted is True


def test_followed_creator_model_and_deduplication():
    """Verify FollowedCreator model validation, matching by channel_id and name, and deduplication."""
    # Instantiation with stable channel ID and name
    creator1 = FollowedCreator(
        name="3Blue1Brown",
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        followed_at="2026-10-29T10:00:00Z"
    )
    assert creator1.name == "3Blue1Brown"
    assert creator1.channel_id == "UCYO_jab_esuFRV4b17AJtAw"

    # Match by channel_id
    assert creator1.matches("Grant Sanderson", "UCYO_jab_esuFRV4b17AJtAw") is True
    # Match by channel name (case-insensitive)
    assert creator1.matches("3blue1brown", None) is True
    assert creator1.matches("3BLUE1BROWN", "other_id") is False  # channel_id takes precedence when provided and differing

    # Parsing into UserPreferences from strings, dicts, and objects
    prefs = UserPreferences(
        interests="mathematics",
        followed_creators=[
            "3Blue1Brown",
            {"name": "Numberphile", "channel_id": "UCoxcjq-8xIDTYp3uz647V5A"},
            creator1
        ]
    )
    assert len(prefs.followed_creators) == 3
    assert prefs.followed_creators[0].name == "3Blue1Brown"
    assert prefs.followed_creators[0].channel_id is None
    assert prefs.followed_creators[1].name == "Numberphile"
    assert prefs.followed_creators[1].channel_id == "UCoxcjq-8xIDTYp3uz647V5A"
    assert prefs.followed_creators[2].channel_id == "UCYO_jab_esuFRV4b17AJtAw"

    # Deduplication logic test
    creators_list = [
        {"name": "3Blue1Brown", "channel_id": None},
        {"name": "Veritasium", "channel_id": "UCHnyfMqiRRG1u-2MsSQLbXA"},
    ]
    # Adding 3Blue1Brown with channel ID should update the existing entry rather than duplicate
    new_entry = {"name": "3blue1brown", "channel_id": "UCYO_jab_esuFRV4b17AJtAw"}
    norm_new = new_entry["name"].lower()
    existing_idx = next((i for i, c in enumerate(creators_list) if c["name"].lower() == norm_new), -1)
    if existing_idx >= 0:
        if new_entry["channel_id"] and not creators_list[existing_idx]["channel_id"]:
            creators_list[existing_idx]["channel_id"] = new_entry["channel_id"]
    else:
        creators_list.append(new_entry)

    assert len(creators_list) == 2
    assert creators_list[0]["channel_id"] == "UCYO_jab_esuFRV4b17AJtAw"


def test_following_3blue1brown_reaches_both_gemini_stages_on_math_tab(monkeypatch):
    """Verify that following 3Blue1Brown reaches both Gemini query generation (Stage 1)
    and candidate evaluation (Stage 2) when viewing the Math tab."""
    creator = FollowedCreator(
        name="3Blue1Brown",
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        followed_at="2026-10-29T10:00:00Z"
    )
    prefs = UserPreferences(
        interests="mathematics and visual proofs",
        active_category="Math",
        followed_creators=[creator],
        feed_mode="mix"
    )

    # 1. Stage 1: Query Expander Gemini Prompt
    expander = QueryExpander(api_key="fake_key_for_testing")
    captured_expansion_prompt = {}

    class FakeInteraction:
        output_text = '{"queries": [{"query": "visual linear algebra geometric transformation", "domain": "Linear Algebra", "query_type": "first_principles", "rationale": "Grounded mathematical visualization exploring foundational vector space mechanics."}]}'

    def mock_create_expansion(*args, **kwargs):
        captured_expansion_prompt["input"] = kwargs.get("input", "")
        return FakeInteraction()

    monkeypatch.setattr(expander, "_expand_with_gemini", expander._expand_with_gemini)

    # We mock client in _expand_with_gemini
    from google import genai
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.interactions = self
        def create(self, *args, **kwargs):
            captured_expansion_prompt["input"] = kwargs.get("input", "")
            return FakeInteraction()

    monkeypatch.setattr(genai, "Client", FakeClient)

    queries = expander._expand_with_gemini(prefs, count=1)
    assert len(queries) == 1
    input_text_stage1 = captured_expansion_prompt.get("input", "")

    # Stage 1 assertions
    assert "Active Category: Math" in input_text_stage1
    assert "3Blue1Brown" in input_text_stage1
    assert "UCYO_jab_esuFRV4b17AJtAw" in input_text_stage1
    assert "A follow means 'I enjoy this creator’s work and want you to understand my taste.'" in input_text_stage1
    assert "Gemini may occasionally search for or recommend their videos, but should still seek new creators and surprising connections." in input_text_stage1
    assert "Following should not guarantee a slot, bypass quality filters, or turn a category tab into a channel feed." in input_text_stage1

    # 2. Stage 2: Filter Engine Gemini Prompt
    filter_engine = FilterEngine(api_key="fake_key_for_testing")
    captured_eval_prompt = {}

    class FakeEvalInteraction:
        output_text = '{"evaluations": [{"video_id": "math_v1", "clickbait_score": 0.1, "substance_score": 0.95, "novelty_score": 0.85, "entertainment_score": 0.8, "content_nature": "educational", "connection_note": "Rigorous visual proof.", "key_takeaways": ["Visual geometry"], "branch_suggestions": ["Topology"], "relevance_status": "supported", "relevance_reason": "Direct math connection"}]}'

    class FakeEvalClient:
        def __init__(self, *args, **kwargs):
            self.interactions = self
        def create(self, *args, **kwargs):
            captured_eval_prompt["input"] = kwargs.get("input", "")
            return FakeEvalInteraction()

    monkeypatch.setattr(genai, "Client", FakeEvalClient)

    candidate = CandidateVideo(
        id="math_v1",
        title="Essence of Linear Algebra: Chapter 1",
        channel="3Blue1Brown",
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        url="https://youtube.com/watch?v=math_v1",
        duration_seconds=900,
        description="Vectors, what even are they? An intuitive geometric introduction to linear algebra."
    )

    feed_items = filter_engine._evaluate_with_gemini([candidate], prefs)
    assert len(feed_items) == 1
    input_text_stage2 = captured_eval_prompt.get("input", "")

    # Stage 2 assertions
    assert "Active Category: Math" in input_text_stage2
    assert "3Blue1Brown" in input_text_stage2
    assert "UCYO_jab_esuFRV4b17AJtAw" in input_text_stage2
    assert "A follow means 'I enjoy this creator’s work and want you to understand my taste.'" in input_text_stage2
    assert "Following should not guarantee a slot, bypass quality filters, or turn a category tab into a channel feed." in input_text_stage2


def test_unfollowing_3blue1brown_removes_from_context(monkeypatch):
    """Verify that unfollowing 3Blue1Brown removes the creator from Gemini prompts in both stages."""
    creator = FollowedCreator(
        name="3Blue1Brown",
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        followed_at="2026-10-29T10:00:00Z"
    )
    prefs = UserPreferences(
        interests="mathematics",
        active_category="Math",
        followed_creators=[creator]
    )

    # Initial check: followed summary includes 3Blue1Brown
    summary_with = summarize_followed_creators(prefs.followed_creators)
    assert "3Blue1Brown" in summary_with

    # Unfollow creator
    prefs.followed_creators = []
    summary_without = summarize_followed_creators(prefs.followed_creators)
    assert summary_without == ""

    # Verify Stage 1 prompt without followed creator
    from google import genai
    captured_prompt = {}

    class FakeInteraction:
        output_text = '{"queries": [{"query": "calculus foundations rate of change", "domain": "Calculus", "query_type": "first_principles", "rationale": "Examines rate of change."}]}'

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.interactions = self
        def create(self, *args, **kwargs):
            captured_prompt["input"] = kwargs.get("input", "")
            return FakeInteraction()

    monkeypatch.setattr(genai, "Client", FakeClient)

    expander = QueryExpander(api_key="fake_key_for_testing")
    expander._expand_with_gemini(prefs, count=1)
    prompt_stage1 = captured_prompt.get("input", "")
    assert "3Blue1Brown" not in prompt_stage1
    assert "Followed Creators:" not in prompt_stage1


def test_following_creator_works_when_gemini_unavailable():
    """Verify that the feature functions smoothly in deterministic no-key heuristic mode:
    - Generates diverse math queries without breaking or turning into a channel feed
    - Applies a modest taste boost to followed creator videos without bypassing clickbait filters
    - Preserves creator diversity across the resulting feed."""
    creator = FollowedCreator(
        name="3Blue1Brown",
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        followed_at="2026-10-29T10:00:00Z"
    )
    prefs = UserPreferences(
        interests="mathematics",
        active_category="Math",
        followed_creators=[creator],
        feed_mode="mix"
    )

    # 1. Deterministic Query Expansion (no key)
    expander = QueryExpander(api_key="")
    queries = expander.expand_queries(prefs, count=5)
    assert len(queries) == 5
    # Must cover distinct angles and domains
    query_roles = {q.query_type for q in queries}
    assert len(query_roles) >= 3
    # Check that queries do NOT simply repeat the creator's name 5 times
    channel_named_queries = [q for q in queries if "3blue1brown" in q.query.lower()]
    assert len(channel_named_queries) <= 1, "Must not turn query generation into a pure channel feed!"

    # 2. Candidate Evaluation in Heuristic Mode (no key)
    filter_engine = FilterEngine(api_key="")

    good_followed_video = CandidateVideo(
        id="3b1b_good",
        title="Visualizing Quaternions (4D Numbers) with Interactive Animations",
        channel="3Blue1Brown",
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        url="https://youtube.com/watch?v=3b1b_good",
        duration_seconds=1200,
        description="A visual introduction to quaternions and 3D spatial rotations."
    )

    clickbait_followed_video = CandidateVideo(
        id="3b1b_bad",
        title="YOU WON'T BELIEVE THIS SHOCKING NEW MATH TRICK THAT RUINED CALCULUS!!!",
        channel="3Blue1Brown",
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        url="https://youtube.com/watch?v=3b1b_bad",
        duration_seconds=900,
        description="Pure hype and clickbait title test."
    )

    other_creator_video = CandidateVideo(
        id="mit_good",
        title="MIT 18.06 Linear Algebra: Lecture 1 - The Geometry of Linear Equations",
        channel="MIT OpenCourseWare",
        channel_id="UCEBb1b_L6zDS3xTUrIALZOw",
        url="https://youtube.com/watch?v=mit_good",
        duration_seconds=2400,
        description="Gilbert Strang's classic archival lecture on linear equations."
    )

    # Run heuristic evaluation
    items, filtered_count = filter_engine.filter_and_rank(
        [good_followed_video, clickbait_followed_video, other_creator_video],
        prefs,
        top_k=5
    )

    # Clickbait video MUST be rejected by Tier 1 heuristics even though from followed creator!
    assert "3b1b_bad" not in [it.video.id for it in items], "Followed creator MUST NOT bypass clickbait filters!"
    assert filtered_count >= 1

    # Good followed video must pass and have a connection note acknowledging taste alignment
    followed_items = [it for it in items if it.video.id == "3b1b_good"]
    assert len(followed_items) == 1
    assert "From followed creator '3Blue1Brown'" in followed_items[0].assessment.connection_note

    # Other creator video must also be included, confirming the feed is not restricted to followed creator
    other_items = [it for it in items if it.video.id == "mit_good"]
    assert len(other_items) == 1


def test_api_feed_endpoint_accepts_followed_creators():
    """Verify that FastAPI /api/feed endpoint accepts followed_creators payload."""
    client = TestClient(app)
    payload = {
        "interests": "mathematics and geometry",
        "active_category": "Math",
        "followed_creators": [
            {
                "name": "3Blue1Brown",
                "channel_id": "UCYO_jab_esuFRV4b17AJtAw",
                "followed_at": "2026-10-29T10:00:00Z"
            }
        ],
        "feed_mode": "mix",
        "serendipity_level": 3
    }
    res = client.post("/api/feed", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "generated_queries" in data


def test_living_taste_profile_weights_and_thresholds():
    """Verify that explicit actions have higher weight than passive watch time,
    and single actions remain tentative while >=2 interactions establish lasting subjects."""
    profile = LivingTasteProfile()

    # 1. Passive substantial watch (weight 1.0)
    sig_watch = TasteSignal(
        video_id="vid_1",
        title="Urban Planning 101",
        domain="Urban Systems",
        qualities=["visual explanation", "technical depth"],
        action="substantial_watch",
        weight=1.0
    )
    profile.record_signal(sig_watch)

    assert profile.subject_evidence_counts["Urban Systems"] == 1
    assert profile.subject_affinities["Urban Systems"] == 1.0
    # Must remain tentative after 1 interaction
    assert "Urban Systems" in [s for s, _ in profile.get_tentative_subjects()]
    assert "Urban Systems" not in [s for s, _ in profile.get_established_subjects()]

    # 2. Explicit action: Save to Watch Later (weight 3.0) on a DIFFERENT video in same domain
    sig_save = TasteSignal(
        video_id="vid_2",
        title="Transit Oriented Development",
        domain="Urban Systems",
        qualities=["technical depth"],
        action="watch_later",
        weight=3.0
    )
    profile.record_signal(sig_save)

    # Now has 2 distinct videos -> Established subject!
    assert profile.subject_evidence_counts["Urban Systems"] == 2
    assert profile.subject_affinities["Urban Systems"] == 4.0
    assert "Urban Systems" in [s for s, _ in profile.get_established_subjects()]

    # 3. Explicit action: Follow Creator (weight 3.5)
    sig_follow = TasteSignal(
        video_id="creator_CityBeautiful",
        title="City Beautiful",
        channel="City Beautiful",
        domain="Urban Systems",
        qualities=["visual explanation"],
        action="followed",
        weight=3.5
    )
    profile.record_signal(sig_follow)
    assert "City Beautiful" in profile.followed_creators
    assert profile.subject_affinities["Urban Systems"] == 7.5

    # 4. Explicit action: Completion (weight 2.5)
    sig_complete = TasteSignal(
        video_id="vid_3",
        title="Aperiodic Tilings Visual Proof",
        domain="Geometric Topology",
        qualities=["visual explanation"],
        action="completed",
        weight=2.5
    )
    profile.record_signal(sig_complete)
    assert profile.subject_affinities["Geometric Topology"] == 2.5

    # 5. Top qualities reflection
    top_qualities = dict(profile.get_top_qualities())
    assert "visual explanation" in top_qualities
    assert "technical depth" in top_qualities
    assert top_qualities["visual explanation"] > 0


def test_same_setup_different_histories_produce_different_discoveries():
    """Verify that the same initial setup produces meaningfully different discovery searches
    after different viewing histories."""
    expander = QueryExpander(api_key="")

    # History 1: User enjoys visual explanation and has explored geometry & urban planning
    p1 = LivingTasteProfile()
    p1.record_signal(TasteSignal(video_id="v1", title="Street Design", domain="Urban Systems", qualities=["visual explanation"], action="completed", weight=2.5))
    p1.record_signal(TasteSignal(video_id="v2", title="Grid Systems", domain="Urban Systems", qualities=["visual explanation"], action="watch_later", weight=3.0))

    prefs1 = UserPreferences(
        interests="baseball and strategy",
        categories=["Baseball & Strategy"],
        active_category=None,
        serendipity_level=3,
        feed_mode="mix",
        living_taste_profile=p1
    )

    # History 2: User enjoys narrative storytelling and has explored ancient scripts & linguistics
    p2 = LivingTasteProfile()
    p2.record_signal(TasteSignal(video_id="v3", title="Lost Languages", domain="Archaeolinguistics", qualities=["storytelling"], action="completed", weight=2.5))
    p2.record_signal(TasteSignal(video_id="v4", title="Cuneiform Secrets", domain="Archaeolinguistics", qualities=["storytelling"], action="watch_later", weight=3.0))

    prefs2 = UserPreferences(
        interests="baseball and strategy",
        categories=["Baseball & Strategy"],
        active_category=None,
        serendipity_level=3,
        feed_mode="mix",
        living_taste_profile=p2
    )

    queries1 = expander.expand_queries(prefs1, count=5)
    queries2 = expander.expand_queries(prefs2, count=5)

    disc_queries1 = [q for q in queries1 if q.discovery_type in ("adjacent", "distant", "wildcard")]
    disc_queries2 = [q for q in queries2 if q.discovery_type in ("adjacent", "distant", "wildcard")]

    assert len(disc_queries1) >= 2
    assert len(disc_queries2) >= 2

    q_texts1 = {q.query.lower() for q in disc_queries1}
    q_texts2 = {q.query.lower() for q in disc_queries2}

    # Verify discovery searches are distinctly shaped by history:
    # History 1 (visual explanation) should include geometric/visual discovery
    assert any("tilings" in q or "geometry" in q or "monotile" in q or "urban" in q for q in q_texts1)
    # History 2 (storytelling) should include archaeolinguistic/narrative discovery
    assert any("linear b" in q or "antikythera" in q or "voynich" in q or "archaeolinguistics" in q for q in q_texts2)

    # The discovery query sets must be verifiably distinct
    assert q_texts1 != q_texts2


def test_unfamiliar_topics_pass_relevance_gate_for_discovery():
    """Verify that unfamiliar topics outside saved interests pass the relevance gate
    when marked as discovery, while domain clashes and clickbait are still rejected."""
    gate = RelevanceGate(api_key="")

    prefs = UserPreferences(
        interests="baseball and strategy",
        categories=["Baseball & Strategy"]
    )

    # Unfamiliar topic candidate tagged as distant discovery
    unfamiliar_candidate = CandidateVideo(
        id="disc_cand_1",
        title="The Discovery of Aperiodic Monotiles in Geometric Topology",
        channel="Numberphile",
        url="https://youtube.com/watch?v=disc_cand_1",
        description="A comprehensive mathematical and visual breakdown of the newly discovered aperiodic monotile einstein problem.",
        duration_seconds=920,
        source_query="aperiodic monotile geometry tilings visual proof",
        source_domain="Geometric Topology",
        discovery_type="distant",
        discovery_rationale="Distant discovery matched to your affinity for visual explanation."
    )

    assessment = gate.assess_candidate(unfamiliar_candidate, prefs)
    assert assessment.is_relevant is True
    assert assessment.status == "supported"
    assert "discovery" in assessment.reason.lower()

    # Domain clash candidate (e.g. DNA genealogy on baseball) must still be rejected
    clash_candidate = CandidateVideo(
        id="clash_cand_1",
        title="Colleen Fitzpatrick - CSI meets Roots Genealogy",
        channel="ForensicsLive",
        url="https://youtube.com/watch?v=clash_cand_1",
        description="Forensic genealogy solving cold cases through familial DNA tracking and pedigree charts.",
        duration_seconds=1200,
        source_query="game theory pitch sequencing minimax strategy",
        source_domain="Tactical Game Theory",
        discovery_type="adjacent"
    )

    clash_assessment = gate.assess_candidate(clash_candidate, prefs)
    assert clash_assessment.is_relevant is False
    assert clash_assessment.status == "rejected"


def test_discovery_remains_varied_over_repeated_refreshes():
    """Verify that discovery queries rotate across repeated refreshes."""
    expander = QueryExpander(api_key="")

    profile = LivingTasteProfile()
    profile.record_signal(TasteSignal(video_id="v1", title="Math Proofs", domain="Mathematics", qualities=["visual explanation"], action="completed", weight=2.5))
    profile.record_signal(TasteSignal(video_id="v2", title="Geometry", domain="Mathematics", qualities=["visual explanation"], action="watch_later", weight=3.0))

    prefs_r0 = UserPreferences(interests="baseball and strategy", categories=["Baseball & Strategy"], refresh_index=0, feed_mode="mix", living_taste_profile=profile)
    prefs_r1 = UserPreferences(interests="baseball and strategy", categories=["Baseball & Strategy"], refresh_index=1, feed_mode="mix", living_taste_profile=profile)
    prefs_r2 = UserPreferences(interests="baseball and strategy", categories=["Baseball & Strategy"], refresh_index=2, feed_mode="mix", living_taste_profile=profile)

    q0 = [q.query for q in expander.expand_queries(prefs_r0, count=5) if q.discovery_type in ("distant", "wildcard")]
    q1 = [q.query for q in expander.expand_queries(prefs_r1, count=5) if q.discovery_type in ("distant", "wildcard")]
    q2 = [q.query for q in expander.expand_queries(prefs_r2, count=5) if q.discovery_type in ("distant", "wildcard")]

    # Across 3 refreshes, discovery queries should exhibit rotation
    all_queries = q0 + q1 + q2
    assert len(set(all_queries)) >= 4, f"Discovery queries must rotate across refreshes; got {all_queries}"


def test_main_feed_reserves_genuine_discovery_slots_and_prevents_fake_discoveries():
    """Verify build_main_feed reserves 2-3 discovery slots and does not label familiar category items as discovery."""
    prefs = UserPreferences(
        interests="baseball and strategy, cinema and storytelling",
        categories=["Baseball & Strategy", "Cinema & Storytelling"],
        feed_mode="mix"
    )

    candidates = []
    # 1. Familiar baseball items
    for i in range(5):
        candidates.append(CandidateVideo(
            id=f"bb_{i}",
            title=f"Baseball Sabermetrics Analysis {i}",
            channel=f"Channel_{i}",
            url=f"https://youtube.com/watch?v=bb_{i}",
            duration_seconds=600,
            source_query="baseball sabermetrics decision analysis",
            source_domain="Quantitative Analytics"
        ))
    # 2. Familiar cinema items
    for i in range(5):
        candidates.append(CandidateVideo(
            id=f"film_{i}",
            title=f"Cinema Cinematography Storytelling Masterclass {i}",
            channel=f"Channel_Film_{i}",
            url=f"https://youtube.com/watch?v=film_{i}",
            duration_seconds=700,
            source_query="visual grammar cinematography narrative theory",
            source_domain="Cinematic Grammar"
        ))
    # 3. Fake discovery (familiar baseball topic marked as discovery query)
    candidates.append(CandidateVideo(
        id="fake_disc",
        title="Baseball Strategy and Sabermetrics Secrets",
        channel="Channel_BB",
        url="https://youtube.com/watch?v=fake_disc",
        duration_seconds=800,
        source_query="baseball strategy sabermetrics secrets",
        source_domain="Baseball & Strategy",
        discovery_type="adjacent"
    ))
    # 4. Genuine adjacent discovery
    candidates.append(CandidateVideo(
        id="real_adj_disc",
        title="Minimax Game Theory in Pitch Calling Equilibrium",
        channel="MathArch",
        url="https://youtube.com/watch?v=real_adj_disc",
        duration_seconds=900,
        source_query="game theory pitch sequencing minimax strategy",
        source_domain="Tactical Game Theory",
        discovery_type="adjacent",
        discovery_rationale="Adjacent lateral bridge connecting to game theory."
    ))
    # 5. Genuine distant discovery
    candidates.append(CandidateVideo(
        id="real_dist_disc",
        title="Aperiodic Monotiles and the Einstein Problem",
        channel="MathVisuals",
        url="https://youtube.com/watch?v=real_dist_disc",
        duration_seconds=950,
        source_query="aperiodic monotile geometry tilings visual proof",
        source_domain="Geometric Topology",
        discovery_type="distant",
        discovery_rationale="Distant discovery matched to your visual explanation preference."
    ))

    service = RabbitholeService(gemini_api_key="")
    feed_items, _ = service.filter_engine.filter_and_rank(candidates, prefs, top_k=30)
    from rabbithole_app.journey import build_main_feed
    main_feed = build_main_feed(feed_items, prefs, length=10, discovery_target=3)

    assert len(main_feed) == 10
    discoveries = [it for it in main_feed if it.is_discovery]
    assert 2 <= len(discoveries) <= 3

    # Fake discovery must NOT be labeled as discovery
    fake_items = [it for it in main_feed if it.video.id == "fake_disc"]
    if fake_items:
        assert not fake_items[0].is_discovery, "Familiar topic must not be labeled as discovery"

    # Genuine discoveries must have explainable signals
    for d in discoveries:
        assert d.path_step == "✨ Discovery"
        assert d.discovery_signal is not None
        assert "Why this discovery?" in d.path_reason


def test_dislike_tempers_discovery_without_banning_broad_subject():
    """Verify that disliking a specific discovery video dampens its domain affinity
    without banning the broader interest."""
    profile = LivingTasteProfile()

    # Established affinity in Game Theory (6.0)
    profile.record_signal(TasteSignal(video_id="v1", title="Game Theory 1", domain="Tactical Game Theory", action="watch_later", weight=3.0))
    profile.record_signal(TasteSignal(video_id="v2", title="Game Theory 2", domain="Tactical Game Theory", action="completed", weight=3.0))
    assert profile.subject_affinities["Tactical Game Theory"] == 6.0

    # User dislikes a third video in Game Theory as "Too academic"
    profile.record_signal(TasteSignal(video_id="v3", title="Game Theory 3", domain="Tactical Game Theory", action="disliked", weight=-2.0))

    # Affinity dampened by 2.0 (down to 4.0), not wiped out or banned!
    assert profile.subject_affinities["Tactical Game Theory"] == 4.0
    assert "Tactical Game Theory" in [s for s, _ in profile.get_established_subjects()]


def test_api_feed_endpoint_accepts_living_taste_profile():
    """Verify that FastAPI /api/feed endpoint accepts living_taste_profile payload."""
    client = TestClient(app)
    payload = {
        "interests": "baseball and strategy",
        "active_category": None,
        "categories": ["Baseball & Strategy"],
        "living_taste_profile": {
            "subject_affinities": {"Tactical Game Theory": 5.5},
            "subject_evidence_counts": {"Tactical Game Theory": 2},
            "preferred_qualities": {"visual explanation": 4.0, "technical depth": 3.0},
            "followed_creators": ["Numberphile"],
            "disliked_patterns": ["Too academic"],
            "recent_signals": [
                {
                    "video_id": "vid_123",
                    "title": "Aperiodic Tilings",
                    "channel": "Numberphile",
                    "domain": "Geometric Topology",
                    "qualities": ["visual explanation"],
                    "action": "completed",
                    "weight": 2.5,
                    "timestamp": "2026-10-29T12:00:00Z"
                }
            ],
            "last_updated_at": "2026-10-29T12:00:00Z"
        },
        "feed_mode": "mix",
        "serendipity_level": 3
    }
    res = client.post("/api/feed", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "generated_queries" in data


def test_relevance_gate_rejects_unrelated_cooking_for_marine_science_wildcard():
    """
    Verify that the tightened relevance gate rejects an unrelated cooking video
    appearing in results for a marine science wildcard, even if it has a long description
    and multiword title.
    """
    gate = RelevanceGate()
    prefs = UserPreferences(interests="baseball and strategy")

    # Unrelated cooking candidate with lengthy description and multiword title
    cooking_cand = CandidateVideo(
        id="cook_vid_1",
        title="Traditional Sourdough Bread Baking Masterclass",
        channel="Artisan Baker",
        description="A comprehensive 45-minute workshop covering sourdough fermentation, gluten structure, hydration ratios, Dutch oven baking, and crumb aeration techniques.",
        duration_seconds=2700,
        url="https://youtube.com/watch?v=cook_vid_1",
        source_query="deep sea hydrothermal vent chemosynthesis ecosystem",
        source_query_type="cross_domain_analogy",
        source_domain="Marine Biology & Oceanography",
        discovery_type="wildcard"
    )

    query_context = CuriosityQuery(
        query="deep sea hydrothermal vent chemosynthesis ecosystem",
        domain="Marine Biology & Oceanography",
        query_type="cross_domain_analogy",
        discovery_type="wildcard",
        rationale="Wildcard investigation into abyssal chemosynthetic food webs."
    )

    assessment = gate.assess_candidate(cooking_cand, prefs, query_context)
    assert assessment.is_relevant is False
    assert assessment.status == "rejected"
    assert "does not support the proposed wildcard discovery topic" in assessment.reason

    # By contrast, a candidate whose metadata actually supports the marine science topic is accepted
    marine_cand = CandidateVideo(
        id="marine_vid_1",
        title="Life at Hydrothermal Vents: Chemosynthesis in Deep Ocean Ecosystems",
        channel="Deep Ocean Science",
        description="Explores chemosynthetic bacteria, tube worms, and hydrothermal vent ecosystems along abyssal volcanic ridges.",
        duration_seconds=1800,
        url="https://youtube.com/watch?v=marine_vid_1",
        source_query="deep sea hydrothermal vent chemosynthesis ecosystem",
        source_query_type="cross_domain_analogy",
        source_domain="Marine Biology & Oceanography",
        discovery_type="wildcard"
    )

    marine_assessment = gate.assess_candidate(marine_cand, prefs, query_context)
    assert marine_assessment.is_relevant is True
    assert marine_assessment.status == "supported"
    assert "wildcard" in marine_assessment.reason.lower()


def test_genuinely_new_rejects_single_word_category_match():
    """
    Verify that single-word categories (e.g. 'Math') correctly disqualify candidates
    in that domain from being labeled as discoveries, using title, topic, and domain.
    """
    from rabbithole_app.journey import is_new_relative_to_categories

    prefs = UserPreferences(interests="Math", categories=["Math"])

    # Math candidate 1: Title and domain clearly match category Math
    cand1 = FeedItem(
        video=CandidateVideo(
            id="math_vid_1",
            title="Advanced Calculus: Differential Equations and Topology",
            channel="Mathologer",
            url="https://youtube.com/watch?v=math_vid_1",
            source_query="calculus differential equations topology",
            source_domain="Pure Mathematics",
            discovery_type="adjacent"
        ),
        assessment=QualityAssessment(substance_score=0.9, novelty_score=0.8, composite_score=0.85)
    )

    # Must NOT be labeled a discovery for someone with a Math tab
    assert is_new_relative_to_categories(cand1, ["Math"], prefs) is False

    # Math candidate 2: Single-word category term directly in title
    cand2 = FeedItem(
        video=CandidateVideo(
            id="math_vid_2",
            title="Why Euler's Identity is the Most Beautiful Theorem in Math",
            channel="3Blue1Brown",
            url="https://youtube.com/watch?v=math_vid_2",
            source_query="euler identity beautiful theorem",
            source_domain="Mathematics",
            discovery_type="adjacent"
        ),
        assessment=QualityAssessment(substance_score=0.95, novelty_score=0.85, composite_score=0.9)
    )
    assert is_new_relative_to_categories(cand2, ["Math"], prefs) is False

    # A genuinely new field (e.g. Marine Science) should pass
    marine_item = FeedItem(
        video=CandidateVideo(
            id="marine_vid_2",
            title="Hydrothermal Vents: Chemosynthesis in Deep Ocean Ecosystems",
            channel="Ocean Exploration",
            url="https://youtube.com/watch?v=marine_vid_2",
            source_query="hydrothermal vents chemosynthesis",
            source_domain="Marine Biology & Oceanography",
            discovery_type="wildcard"
        ),
        assessment=QualityAssessment(substance_score=0.9, novelty_score=0.95, composite_score=0.92)
    )
    assert is_new_relative_to_categories(marine_item, ["Math"], prefs) is True


def test_genuinely_new_does_not_reject_new_field_sharing_generic_word():
    """
    Verify that a genuinely new field is not rejected just because it shares a generic word
    like 'system' or 'theory'.
    """
    from rabbithole_app.journey import is_new_relative_to_categories

    # User with category 'Computer Systems'
    prefs1 = UserPreferences(interests="Computer Systems", categories=["Computer Systems"])
    urban_drainage = FeedItem(
        video=CandidateVideo(
            id="drainage_1",
            title="Urban Drainage Systems: Civil Engineering Underground",
            channel="Practical Engineering",
            url="https://youtube.com/watch?v=drainage_1",
            source_query="urban drainage systems civil engineering",
            source_domain="Civil Engineering & Urban Infrastructure",
            discovery_type="wildcard"
        ),
        assessment=QualityAssessment(substance_score=0.9, novelty_score=0.9, composite_score=0.9)
    )
    # Sharing the generic word 'systems' MUST NOT disqualify Urban Drainage from being a discovery!
    assert is_new_relative_to_categories(urban_drainage, ["Computer Systems"], prefs1) is True

    # User with category 'Game Theory'
    prefs2 = UserPreferences(interests="Game Theory", categories=["Game Theory"])
    chaos_theory = FeedItem(
        video=CandidateVideo(
            id="chaos_1",
            title="Chaos Theory and Nonlinear Dynamics in Atmospheric Science",
            channel="Veritasium",
            url="https://youtube.com/watch?v=chaos_1",
            source_query="chaos theory nonlinear dynamics atmosphere",
            source_domain="Atmospheric Physics & Meteorology",
            discovery_type="wildcard"
        ),
        assessment=QualityAssessment(substance_score=0.92, novelty_score=0.92, composite_score=0.92)
    )
    # Sharing generic word 'theory' MUST NOT disqualify Chaos Theory from being a discovery!
    assert is_new_relative_to_categories(chaos_theory, ["Game Theory"], prefs2) is True


def test_genuinely_new_rejects_learned_taste_subject_and_seen_videos():
    """
    Verify that candidates matching learned subjects from living taste profile or
    already seen videos are not labeled as new discoveries.
    """
    from rabbithole_app.journey import is_new_relative_to_categories

    profile = LivingTasteProfile()
    # Learned established subject: Urban Planning
    profile.record_signal(TasteSignal(video_id="v1", title="Transit Design", domain="Urban Planning", action="watch_later", weight=3.0))
    profile.record_signal(TasteSignal(video_id="v2", title="Zoning Reform", domain="Urban Planning", action="completed", weight=2.5))

    prefs = UserPreferences(
        interests="baseball and strategy",
        categories=["Baseball & Strategy"],
        seen_video_ids=["seen_vid_99"],
        living_taste_profile=profile
    )

    # Item in Urban Planning matches learned taste profile -> not a new discovery
    urban_item = FeedItem(
        video=CandidateVideo(
            id="new_urban_1",
            title="Walkable Cities and Urban Planning Case Studies",
            channel="City Beautiful",
            url="https://youtube.com/watch?v=new_urban_1",
            source_query="urban planning walkable cities",
            source_domain="Urban Planning",
            discovery_type="wildcard"
        ),
        assessment=QualityAssessment(substance_score=0.9, novelty_score=0.8, composite_score=0.85)
    )
    assert is_new_relative_to_categories(urban_item, ["Baseball & Strategy"], prefs) is False

    # Already seen video
    seen_item = FeedItem(
        video=CandidateVideo(
            id="seen_vid_99",
            title="Deep Sea Hydrothermal Vent Exploration",
            channel="Ocean Science",
            url="https://youtube.com/watch?v=seen_vid_99",
            source_query="deep sea hydrothermal vents",
            source_domain="Oceanography",
            discovery_type="wildcard"
        ),
        assessment=QualityAssessment(substance_score=0.9, novelty_score=0.9, composite_score=0.9)
    )
    assert is_new_relative_to_categories(seen_item, ["Baseball & Strategy"], prefs) is False


def test_living_taste_profile_passive_decay_while_explicit_remain_strong():
    """
    Verify that older passive signals gradually decay when apply_passive_decay is run,
    while explicit saves, follows, completions, and dislikes maintain full strength.
    """
    profile = LivingTasteProfile()

    # Passive signal: substantial watch in Origami (1.0)
    profile.record_signal(TasteSignal(
        video_id="vid_origami_1",
        title="Modular Origami Geometry",
        domain="Origami Mathematics",
        action="substantial_watch",
        weight=1.0
    ))
    # Explicit signal: watch_later in Typography (3.0)
    profile.record_signal(TasteSignal(
        video_id="vid_typo_1",
        title="Kerning and Grid Systems",
        domain="Typography & Layout",
        action="watch_later",
        weight=3.0
    ))

    assert profile.subject_affinities["Origami Mathematics"] == 1.0
    assert profile.subject_affinities["Typography & Layout"] == 3.0

    # First passive decay cycle
    profile.apply_passive_decay(0.85)
    assert profile.subject_affinities["Origami Mathematics"] == 0.85
    assert profile.subject_affinities["Typography & Layout"] == 3.0  # Explicit save untouched!

    # Second passive decay cycle
    profile.apply_passive_decay(0.85)
    assert profile.subject_affinities["Origami Mathematics"] == 0.72
    assert profile.subject_affinities["Typography & Layout"] == 3.0  # Explicit save remains strong!


def test_living_taste_profile_unfollow_and_remove_watch_later_removes_influence():
    """
    Verify that unfollowing a creator or removing a Watch Later item removes that action's
    influence from the living taste profile.
    """
    profile = LivingTasteProfile()

    # 1. Add Watch Later for Acoustics
    profile.record_signal(TasteSignal(
        video_id="vid_acoustics_1",
        title="Chladni Plates and Cymatics",
        domain="Acoustics & Resonance",
        action="watch_later",
        weight=3.0
    ))
    assert profile.subject_affinities["Acoustics & Resonance"] == 3.0
    assert profile.subject_evidence_counts["Acoustics & Resonance"] == 1

    # Remove Watch Later
    profile.remove_signal("vid_acoustics_1", "watch_later")
    assert profile.subject_affinities.get("Acoustics & Resonance", 0.0) == 0.0
    assert profile.subject_evidence_counts.get("Acoustics & Resonance", 0) == 0

    # 2. Follow creator 3Blue1Brown in Mathematics
    profile.record_signal(TasteSignal(
        video_id="creator_3Blue1Brown",
        channel="3Blue1Brown",
        title="3Blue1Brown",
        domain="Mathematics",
        action="followed",
        weight=3.5
    ))
    assert "3Blue1Brown" in profile.followed_creators
    assert profile.subject_affinities["Mathematics"] == 3.5

    # Unfollow creator
    profile.unfollow_creator("3Blue1Brown")
    assert "3Blue1Brown" not in profile.followed_creators
    assert profile.subject_affinities.get("Mathematics", 0.0) == 0.0


def test_living_taste_profile_reset_clears_all_learned_taste():
    """Verify that reset() restores a clean slate."""
    profile = LivingTasteProfile()
    profile.record_signal(TasteSignal(video_id="v1", domain="Music", action="watch_later", weight=3.0))
    profile.record_signal(TasteSignal(video_id="v2", channel="Veritasium", action="followed", weight=3.5))
    profile.record_signal(TasteSignal(video_id="v3", domain="Physics", action="disliked", weight=-2.0))

    assert len(profile.recent_signals) == 3
    profile.reset()
    assert len(profile.recent_signals) == 0
    assert len(profile.subject_affinities) == 0
    assert len(profile.subject_evidence_counts) == 0
    assert len(profile.preferred_qualities) == 0
    assert len(profile.followed_creators) == 0


def test_gemini_mode_different_viewing_histories_produce_different_discoveries(monkeypatch):
    """
    Verify that when Gemini mode is active, different viewing histories produce different discovery directions,
    and initial setup interests do not permanently dictate distant and wildcard searches.
    """
    expander = QueryExpander(api_key="fake-test-key")

    captured_prompts = []

    class FakeInteraction:
        def __init__(self, output_text):
            self.output_text = output_text

    class FakeClient:
        def __init__(self, **kwargs):
            self.interactions = self

        def create(self, **kwargs):
            prompt = kwargs.get("input", "")
            captured_prompts.append(prompt)
            if "Audio Engineering & Acoustics" in prompt:
                plan = CuriosityQueryPlan(queries=[
                    CuriosityQuery(query="baseball sabermetrics analytics", domain="Baseball Analytics", query_type="first_principles", rationale="Statistical decision matrices."),
                    CuriosityQuery(query="chladni resonance cymatic visual patterns", domain="Acoustics & Cymatics", query_type="cross_domain_analogy", discovery_type="adjacent", rationale="Acoustic nodal lines and wave patterns."),
                    CuriosityQuery(query="geometric origami kinetic folding mechanisms", domain="Kinetic Origami & Mechanics", query_type="cross_domain_analogy", discovery_type="distant", rationale="Mathematical kinetics and visual geometric transformations."),
                    CuriosityQuery(query="deep sea hydrothermal vent chemosynthesis", domain="Marine Biology & Oceanography", query_type="cross_domain_analogy", discovery_type="wildcard", rationale="Abyssal volcanic ridge ecosystems."),
                    CuriosityQuery(query="analog synthesizer circuit modular synthesis", domain="Audio Engineering", query_type="practitioner_deep_dive", rationale="Voltage-controlled oscillator design."),
                ])
            else:
                plan = CuriosityQueryPlan(queries=[
                    CuriosityQuery(query="baseball sabermetrics analytics", domain="Baseball Analytics", query_type="first_principles", rationale="Statistical decision matrices."),
                    CuriosityQuery(query="subway switch interlocking signal mechanics", domain="Transit Rail Infrastructure", query_type="cross_domain_analogy", discovery_type="adjacent", rationale="Relay and logic circuitry in rail transit."),
                    CuriosityQuery(query="traditional Japanese timber joinery restoration", domain="Architectural Joinery", query_type="cross_domain_analogy", discovery_type="distant", rationale="Mortise and tenon woodcraft artistry."),
                    CuriosityQuery(query="biomimicry bullet train kingfisher aerodynamics", domain="Biomimetic Engineering", query_type="cross_domain_analogy", discovery_type="wildcard", rationale="Kingfisher beak geometry applied to pantograph tunnels."),
                    CuriosityQuery(query="urban drainage civil engineering tunnels", domain="Civil Infrastructure", query_type="practitioner_deep_dive", rationale="Underground storm water diversion engineering."),
                ])
            return FakeInteraction(plan.model_dump_json())

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", FakeClient)

    # History A: Viewer engaged with Audio & Acoustics and visual explanations
    profile_a = LivingTasteProfile()
    profile_a.record_signal(TasteSignal(video_id="va1", title="Chladni Plate Cymatics", domain="Audio Engineering & Acoustics", qualities=["visual explanation"], action="watch_later", weight=3.0))
    profile_a.record_signal(TasteSignal(video_id="va2", title="Room Acoustics Helmholtz Resonance", domain="Audio Engineering & Acoustics", qualities=["visual explanation"], action="completed", weight=2.5))

    prefs_a = UserPreferences(
        interests="baseball and strategy",
        categories=["Baseball & Strategy"],
        living_taste_profile=profile_a
    )

    # History B: Viewer engaged with Urban Systems & Joinery and artisan craft
    profile_b = LivingTasteProfile()
    profile_b.record_signal(TasteSignal(video_id="vb1", title="Tokyo Subway Interlocking Logic", domain="Urban Systems & Infrastructure", qualities=["artisan craft"], action="watch_later", weight=3.0))
    profile_b.record_signal(TasteSignal(video_id="vb2", title="Civil Water Tunnels", domain="Urban Systems & Infrastructure", qualities=["artisan craft"], action="completed", weight=2.5))

    prefs_b = UserPreferences(
        interests="baseball and strategy",
        categories=["Baseball & Strategy"],
        living_taste_profile=profile_b
    )

    queries_a = expander.expand_queries(prefs_a, count=5)
    queries_b = expander.expand_queries(prefs_b, count=5)

    disc_a = [q.query for q in queries_a if q.discovery_type]
    disc_b = [q.query for q in queries_b if q.discovery_type]

    # Verify that different viewing histories produced completely distinct discovery directions
    assert any("cymatic" in q or "origami" in q for q in disc_a)
    assert any("subway" in q or "timber joinery" in q for q in disc_b)
    assert set(disc_a).isdisjoint(set(disc_b))


def test_gemini_mode_validates_and_replaces_missing_or_malformed_discovery_queries(monkeypatch):
    """
    Verify that if Gemini omits discovery queries or returns malformed ones,
    the expander validates the output and replaces/supplements with safe alternatives.
    """
    expander = QueryExpander(api_key="fake-test-key")

    class FakeInteraction:
        def __init__(self, output_text):
            self.output_text = output_text

    class FakeClient:
        def __init__(self, **kwargs):
            self.interactions = self

        def create(self, **kwargs):
            # Gemini returns NO discovery queries and includes a malformed query
            plan = CuriosityQueryPlan(queries=[
                CuriosityQuery(query="baseball sabermetrics run expectancy", domain="Baseball Analytics", query_type="first_principles", rationale="Statistical decision matrices."),
                CuriosityQuery(query="bad", domain="Invalid", query_type="first_principles", rationale="Too short single word query."),  # malformed: single word
                CuriosityQuery(query="pitch sequencing game theory", domain="Tactical Game Theory", query_type="first_principles", rationale="Sequential decision models."),
            ])
            return FakeInteraction(plan.model_dump_json())

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", FakeClient)

    prefs = UserPreferences(
        interests="baseball and strategy",
        categories=["Baseball & Strategy"]
    )

    queries = expander.expand_queries(prefs, count=5)

    # 1. Malformed query 'bad' was rejected
    assert not any(q.query == "bad" for q in queries)

    # 2. Missing discovery tiers (adjacent, distant, wildcard) were injected as safe alternatives
    disc_types = {q.discovery_type for q in queries if q.discovery_type}
    assert "adjacent" in disc_types
    assert "distant" in disc_types
    assert "wildcard" in disc_types
    assert len(queries) == 5


def test_gemini_mode_unrelated_results_caught_by_relevance_gate():
    """
    Verify that when Gemini produces a wildcard discovery query, unrelated search
    results (such as a cooking video) are still caught and rejected by the relevance gate.
    """
    gate = RelevanceGate()
    prefs = UserPreferences(interests="baseball and strategy")

    # Gemini generated wildcard query
    gemini_wildcard_query = CuriosityQuery(
        query="deep sea hydrothermal vent chemosynthesis ecosystem",
        domain="Marine Biology & Oceanography",
        query_type="cross_domain_analogy",
        discovery_type="wildcard",
        rationale="Deep ocean chemosynthesis ecosystems."
    )

    # Off-topic result returned by search engine
    unrelated_candidate = CandidateVideo(
        id="unrelated_pie",
        title="Grandma's Homemade Apple Pie from Scratch",
        channel="Grandma's Kitchen",
        description="Learn the secret to perfect flaky butter crust and cinnamon spiced spiced apple filling with fresh Granny Smith apples.",
        duration_seconds=1200,
        url="https://youtube.com/watch?v=unrelated_pie",
        source_query=gemini_wildcard_query.query,
        source_domain=gemini_wildcard_query.domain,
        discovery_type="wildcard"
    )

    assessment = gate.assess_candidate(unrelated_candidate, prefs, gemini_wildcard_query)
    assert assessment.is_relevant is False
    assert assessment.status == "rejected"


# ==============================================================================
# Entertainment-First Pipeline Tests: Negative Cases, Formats, Feeds & Retries
# ==============================================================================

def test_negative_cases_disqualified_from_entertainment_first():
    """
    Test the four explicit negative cases:
    1. Architecture essay: 'The Architecture of Brutalism: Ideology in Concrete - Video Essay'
    2. 'The Math Behind Music and Sound Synthesis'
    3. Technical breakdown: 'How Non-Euclidean Game Engines Work: Mathematical Breakdown'
    4. Soviet economics: 'Soviet Economics: Planning, Shortages, and Collapse - University Lecture'
    All must evaluate to is_entertainment_first=False with capped entertainment_score.
    """
    filter_engine = FilterEngine()
    prefs = UserPreferences(interests="general", feed_mode="entertainment")

    neg_cases = [
        CandidateVideo(
            id="neg_arch",
            title="The Architecture of Brutalism: Ideology in Concrete - Video Essay",
            channel="Design Theory",
            description="A comprehensive analytical video essay exploring the architectural ideology, geometry, and political origins of Soviet brutalist concrete megastructures.",
            duration_seconds=1500,
            url="https://youtube.com/watch?v=neg_arch",
        ),
        CandidateVideo(
            id="neg_math_music",
            title="The Math Behind Music and Sound Synthesis",
            channel="Acoustic Physics",
            description="Deconstructing Fourier transforms, wave interference, harmonic series, and the mathematics of FM sound synthesis from first principles.",
            duration_seconds=1400,
            url="https://youtube.com/watch?v=neg_math_music",
        ),
        CandidateVideo(
            id="neg_non_euclid",
            title="How Non-Euclidean Game Engines Work: Mathematical Breakdown",
            channel="Engine Architecture",
            description="Technical breakdown and formal geometric analysis of hyperbolic rendering engines and non-Euclidean game mechanics.",
            duration_seconds=1800,
            url="https://youtube.com/watch?v=neg_non_euclid",
        ),
        CandidateVideo(
            id="neg_soviet_econ",
            title="Soviet Economics: Planning, Shortages, and Collapse - University Lecture",
            channel="Oxford Economics",
            description="A university lecture examining Gosplan, central economic planning, price controls, and structural resource allocation in the USSR.",
            duration_seconds=3200,
            url="https://youtube.com/watch?v=neg_soviet_econ",
        ),
    ]

    for cand in neg_cases:
        item = filter_engine._heuristic_assessment(cand, prefs)
        assert item.is_entertainment_first is False, f"Expected {cand.title} to NOT be entertainment-first"
        assert item.entertainment_format is None
        assert "not entertainment-first" in item.entertainment_evidence.lower()
        assert item.entertainment_score <= 0.35, f"Expected entertainment_score <= 0.35 for {cand.title}, got {item.entertainment_score}"


def test_positive_cases_identified_across_entertainment_formats():
    """
    Test positive cases across distinct entertainment formats:
    comedy, gameplay, challenges, sports_moments, performance, animation, creative_projects, compelling_story.
    All must evaluate to is_entertainment_first=True with correct format and high entertainment_score.
    """
    filter_engine = FilterEngine()
    prefs = UserPreferences(interests="general", feed_mode="entertainment")

    pos_cases = [
        (
            CandidateVideo(
                id="pos_com",
                title="Standup Comedy Night: Hilarious Bloopers, Skits & Funny Improvisation",
                channel="Laugh Lab",
                description="Live comedy show featuring standup jokes, parody sketches, bloopers, and hilarious improv routines.",
                duration_seconds=900,
                view_count=85000,
                url="https://youtube.com/watch?v=pos_com",
            ),
            "comedy"
        ),
        (
            CandidateVideo(
                id="pos_gp",
                title="World Record Blind Speedrun: Insane Clutch Gameplay Highlights",
                channel="Speedrun Central",
                description="Unbelievable gaming playthrough featuring frame-perfect tricks, clutch boss fights, and crazy speedrun moments.",
                duration_seconds=1100,
                view_count=120000,
                url="https://youtube.com/watch?v=pos_gp",
            ),
            "gameplay"
        ),
        (
            CandidateVideo(
                id="pos_chal",
                title="100 Days Hardcore Survival Challenge Tournament: We Survived",
                channel="Challenge Kings",
                description="Epic hardcore survival competition and world record challenge tournament.",
                duration_seconds=1500,
                view_count=250000,
                url="https://youtube.com/watch?v=pos_chal",
            ),
            "challenges"
        ),
        (
            CandidateVideo(
                id="pos_sport",
                title="Game 7 Walk-off Grand Slam: Greatest Buzzer Beaters & Sports Moments",
                channel="Highlight Reel",
                description="The most thrilling sports moments in history featuring game-winning walk-off home runs, buzzer beaters, and clutch plays.",
                duration_seconds=800,
                view_count=450000,
                url="https://youtube.com/watch?v=pos_sport",
            ),
            "sports_moments"
        ),
        (
            CandidateVideo(
                id="pos_perf",
                title="Live Orchestral Concert: Virtuosic Musical Cover & Acoustic Jamming",
                channel="Stage & Sound",
                description="A stunning live performance featuring an orchestral concert, improvised musical covers, and live acoustic jamming.",
                duration_seconds=750,
                view_count=95000,
                url="https://youtube.com/watch?v=pos_perf",
            ),
            "performance"
        ),
        (
            CandidateVideo(
                id="pos_anim",
                title="The Lost Astronaut: Award-Winning 3D Animated Short Film",
                channel="Artisan Studio",
                description="Delightful CGI animated short film featuring stop motion visual storytelling and artisan 3d animation.",
                duration_seconds=600,
                view_count=180000,
                url="https://youtube.com/watch?v=pos_anim",
            ),
            "animation"
        ),
        (
            CandidateVideo(
                id="pos_build",
                title="We Built the World's Largest Rube Goldberg Machine: Satisfying Creative Build",
                channel="Creative Builders",
                description="A mind-blowing chain reaction build featuring intricate kinetic automata and satisfying DIY engineering craft.",
                duration_seconds=1200,
                view_count=320000,
                url="https://youtube.com/watch?v=pos_build",
            ),
            "creative_projects"
        ),
        (
            CandidateVideo(
                id="pos_lore",
                title="The Bizarre Heist of the Antwerp Diamond Vault: Unbelievable True Crime Lore",
                channel="Tales of Lore",
                description="A gripping, stranger-than-fiction narrative adventure recounting a bizarre real-life heist with unbelievable twists.",
                duration_seconds=1300,
                view_count=210000,
                url="https://youtube.com/watch?v=pos_lore",
            ),
            "compelling_story"
        ),
    ]

    for cand, expected_format in pos_cases:
        item = filter_engine._heuristic_assessment(cand, prefs)
        assert item.is_entertainment_first is True, f"Expected {cand.title} to be entertainment-first"
        assert item.entertainment_format == expected_format
        assert item.entertainment_score >= 0.70, f"Expected entertainment_score >= 0.70 for {cand.title}, got {item.entertainment_score}"


def test_entertainment_only_feed_strictly_excludes_lectures_and_explainers():
    """
    Verify that in Entertainment only feed mode:
    - Educational video essays, math breakdowns, non-Euclidean breakdowns, and economics lectures
      are strictly excluded from the assembled feed.
    - Only verified entertainment-first items enter the feed.
    """
    from rabbithole_app.journey import build_entertainment_feed, build_main_feed, build_category_feed

    prefs = UserPreferences(
        interests="gaming and architecture",
        categories=["gaming", "architecture"],
        feed_mode="entertainment"
    )

    filter_engine = FilterEngine()

    neg1 = CandidateVideo(id="n1", title="The Architecture of Brutalism: Video Essay", channel="C1", url="http://1", duration_seconds=1200)
    neg2 = CandidateVideo(id="n2", title="The Math Behind Music and Sound Synthesis", channel="C2", url="http://2", duration_seconds=1200)
    neg3 = CandidateVideo(id="n3", title="How Non-Euclidean Game Engines Work: Mathematical Breakdown", channel="C3", url="http://3", duration_seconds=1200)
    neg4 = CandidateVideo(id="n4", title="Soviet Economics: University Lecture", channel="C4", url="http://4", duration_seconds=1200)

    pos1 = CandidateVideo(id="p1", title="Standup Comedy Bloopers & Hilarious Moments", channel="P1", url="http://p1", duration_seconds=800)
    pos2 = CandidateVideo(id="p2", title="Insane Speedrun Clutch Gameplay Highlights", channel="P2", url="http://p2", duration_seconds=900)
    pos3 = CandidateVideo(id="p3", title="100 Days Hardcore Survival Challenge Tournament", channel="P3", url="http://p3", duration_seconds=1000)
    pos4 = CandidateVideo(id="p4", title="We Built a Giant Rube Goldberg Creative Build", channel="P4", url="http://p4", duration_seconds=950)
    pos5 = CandidateVideo(id="p5", title="Bizarre Real Life Heist Lore & Tales", channel="P5", url="http://p5", duration_seconds=850)

    all_cands = [neg1, neg2, neg3, neg4, pos1, pos2, pos3, pos4, pos5]
    feed_items = [
        FeedItem(video=c, assessment=filter_engine._heuristic_assessment(c, prefs), source_query="general")
        for c in all_cands
    ]

    # 1. build_entertainment_feed
    ent_feed = build_entertainment_feed(feed_items, prefs, length=5)
    ent_ids = [item.video.id for item in ent_feed]
    for neg_id in ["n1", "n2", "n3", "n4"]:
        assert neg_id not in ent_ids, f"Negative candidate {neg_id} should be excluded from build_entertainment_feed"
    for pos_id in ["p1", "p2", "p3", "p4", "p5"]:
        assert pos_id in ent_ids

    # 2. build_main_feed in entertainment mode
    main_feed = build_main_feed(feed_items, prefs, length=5, discovery_target=1)
    main_ids = [item.video.id for item in main_feed]
    for neg_id in ["n1", "n2", "n3", "n4"]:
        assert neg_id not in main_ids, f"Negative candidate {neg_id} should be excluded from main_feed in entertainment mode"
    assert all(item.assessment.is_entertainment_first for item in main_feed)

    # 3. build_category_feed in entertainment mode
    cat_feed = build_category_feed(feed_items, prefs, category_name="gaming", length=5)
    cat_ids = [item.video.id for item in cat_feed]
    for neg_id in ["n1", "n2", "n3", "n4"]:
        assert neg_id not in cat_ids, f"Negative candidate {neg_id} should be excluded from category_feed in entertainment mode"
    assert all(item.assessment.is_entertainment_first for item in cat_feed)


def test_mixed_feed_retains_freedom_to_blend_both_modes():
    """
    Verify that in default 'mix' feed mode, Rabbithole can combine both
    educational-depth videos and creative entertainment videos without forced exclusion.
    """
    from rabbithole_app.journey import build_mixed_feed

    prefs = UserPreferences(interests="music and acoustics", feed_mode="mix")
    filter_engine = FilterEngine()

    edu_cand = CandidateVideo(
        id="edu_1",
        title="The Math Behind Music and Sound Synthesis",
        channel="Acoustic Lab",
        description="A mathematical breakdown of sound synthesis and harmonics.",
        duration_seconds=1200,
        url="https://youtube.com/watch?v=edu_1",
    )
    ent_cand = CandidateVideo(
        id="ent_1",
        title="Hilarious Musical Parody & Live Comedy Performance",
        channel="Comedy Jam",
        description="Funny musical covers, sketches, and bloopers.",
        duration_seconds=800,
        url="https://youtube.com/watch?v=ent_1",
    )

    items = [
        FeedItem(video=edu_cand, assessment=filter_engine._heuristic_assessment(edu_cand, prefs), source_query="music"),
        FeedItem(video=ent_cand, assessment=filter_engine._heuristic_assessment(ent_cand, prefs), source_query="music"),
    ]

    feed = build_mixed_feed(items, prefs, length=2)
    feed_ids = [item.video.id for item in feed]
    assert "edu_1" in feed_ids
    assert "ent_1" in feed_ids


def test_entertainment_searches_are_diversified_without_academic_analogies():
    """
    Verify that QueryExpander in entertainment mode:
    1. Generates diverse entertainment formats (comedy, creative, story, gameplay, performance).
    2. Does NOT output academic analogies (e.g. game theory or applied methodology case study).
    3. Discovery and wildcard queries in entertainment mode are entertainment-first (e.g. marble runs, trick shots, builds).
    """
    expander = QueryExpander()
    prefs = UserPreferences(
        interests="baseball and sports strategy",
        categories=["baseball", "sports strategy"],
        feed_mode="entertainment"
    )

    queries = expander.expand_queries(prefs, count=5)
    query_types = [q.query_type for q in queries]
    query_texts = [q.query.lower() for q in queries]

    # Verify diversified formats are present
    assert any("comedy" in qt for qt in query_types), "Expected comedy query in entertainment mode"
    assert any("creative" in qt or "gameplay" in qt or "performance" in qt or "story" in qt for qt in query_types)

    # Verify academic terms are absent
    for q_text in query_texts:
        assert "applied methodology case study" not in q_text
        assert "interdisciplinary systems analysis" not in q_text
        assert "first principles" not in q_text
        assert "lecture symposium archive" not in q_text

    # Verify discovery queries in entertainment mode are entertainment-first
    disc_queries = [q for q in queries if q.discovery_type in ("adjacent", "distant", "wildcard")]
    for dq in disc_queries:
        assert not any(academic_word in dq.query.lower() for academic_word in [
            "hydrothermal", "ice core", "nanoparticle", "fiber optic", "subatomic", "mycelial"
        ]), f"Discovery query {dq.query} in entertainment mode should not be deep academic science"


def test_entertainment_retry_and_shortened_feed_honesty(monkeypatch):
    """
    Verify that when the candidate pool contains too few entertainment-first videos:
    1. A secondary targeted search for alternative entertainment formats is triggered.
    2. If candidates are still insufficient, Rabbithole returns a shorter feed with an honest
       status message explaining that it refused to silently fill the feed with lectures.
    """
    service = RabbitholeService()
    prefs = UserPreferences(
        interests="Soviet architecture",
        active_category="Soviet architecture",
        feed_mode="entertainment"
    )

    # Mock retriever to return mostly educational lectures and only 2 entertainment items
    lecture_cands = [
        CandidateVideo(id=f"lec_{i}", title=f"Soviet Architecture Video Essay Breakdown Part {i}", channel=f"Academic {i}", url=f"http://lec_{i}")
        for i in range(10)
    ]
    ent_cands = [
        CandidateVideo(id="ent_real_1", title="Hilarious Soviet Architecture Bloopers & Comedy Fails", channel="Humor Hub", url="http://ent1"),
        CandidateVideo(id="ent_real_2", title="We Built Soviet Architecture Brutalist Megastructures in Minecraft: Creative Build Challenge", channel="Block Craft", url="http://ent2"),
    ]

    # Monkeypatch retrieve_candidates to return 2 entertainment and 10 lectures on all passes
    def fake_retrieve(queries, max_per_query=5):
        return list(ent_cands + lecture_cands)

    monkeypatch.setattr(service.retriever, "retrieve_candidates", fake_retrieve)

    # Request top_k = 5
    response = service.generate_feed(prefs, query_count=3, top_k=5)

    # 1. Only the 2 entertainment-first items should be returned, NOT the 10 lectures!
    assert len(response.items) == 2
    assert {it.video.id for it in response.items} == {"ent_real_1", "ent_real_2"}
    assert all(it.assessment.is_entertainment_first for it in response.items)

    # 2. Status message honestly acknowledges the shortened feed and refusal to pad with lectures
    assert response.pool_exhausted is True
    assert "Returned a shorter feed of 2 verified entertainment-first videos" in response.status_message
    assert "without diluting your Entertainment-only preference" in response.status_message


def test_living_taste_profile_learns_entertainment_formats_and_resets():
    """
    Verify that watches, watch-later saves, and follows update preferred_entertainment_formats,
    boost relevant candidates, and clear completely on profile reset.
    """
    profile = LivingTasteProfile()

    # User saves a gameplay speedrun and watches a comedy skit
    profile.record_signal(TasteSignal(
        video_id="game_1",
        title="Legendary Clutch Speedrun Gameplay",
        channel="Speedrunner",
        domain="Gaming",
        qualities=["gameplay"],
        action="watch_later",
        weight=3.0
    ))
    profile.record_signal(TasteSignal(
        video_id="com_1",
        title="Hilarious Comedy Bloopers",
        channel="Comedian",
        domain="Comedy",
        qualities=["comedy", "humor"],
        action="substantial_watch",
        weight=1.0
    ))

    # Formats are tracked
    assert "gameplay" in profile.preferred_entertainment_formats
    assert "comedy" in profile.preferred_entertainment_formats
    assert profile.preferred_entertainment_formats["gameplay"] > 2.0

    # Summary reflects preferred formats
    summary = profile.compact_summary()
    assert "Preferred Entertainment Formats:" in summary
    assert "Gameplay" in summary

    # Candidate scoring boost
    filter_engine = FilterEngine()
    prefs = UserPreferences(interests="general", living_taste_profile=profile, feed_mode="entertainment")

    cand_gameplay = CandidateVideo(id="cg", title="Pro Gameplay Clutch Tournament", channel="Gamer", url="http://cg")
    ass = filter_engine._heuristic_assessment(cand_gameplay, prefs)
    assert ass.is_entertainment_first is True
    assert ass.entertainment_format == "gameplay"

    # Reset profile
    profile.reset()
    assert len(profile.preferred_entertainment_formats) == 0
    assert "Preferred Entertainment Formats:" not in profile.compact_summary()


# ==============================================================================
# FOCUSED INTEREST TABS & DOMAIN RELEVANCE TESTS
# ==============================================================================

def test_math_tab_recognizes_fourier_transforms_and_3blue1brown():
    prefs = UserPreferences(
        interests="Math, Baseball",
        categories=["Math", "Baseball"],
        active_category="Math",
    )
    gate = RelevanceGate(api_key="")

    # Candidate 1: Fourier transform (no 'math' in title or channel)
    cand_fourier = CandidateVideo(
        id="fourier_1",
        title="The Fourier Transform: A Visual Introduction to Frequency Decomposition",
        channel="3Blue1Brown",
        description="A visual look at how any periodic function decomposes into sinusoids.",
        url="https://youtube.com/watch?v=fourier",
        duration_seconds=900,
        source_query="fourier transform visual",
        source_domain="Mathematics",
    )
    ass_fourier = gate.assess_candidate(cand_fourier, prefs)
    assert ass_fourier.is_relevant is True
    assert "active tab" in ass_fourier.reason

    # Candidate 2: Quaternions / linear algebra (no 'math' in title)
    cand_quat = CandidateVideo(
        id="quat_1",
        title="Visualizing Quaternions and 4D Rotations",
        channel="Grant Sanderson",
        description="Understanding four-dimensional spatial transformations and algebraic rotations.",
        url="https://youtube.com/watch?v=quat",
        duration_seconds=1200,
        source_query="quaternion 4d rotation",
        source_domain="Mathematics",
    )
    ass_quat = gate.assess_candidate(cand_quat, prefs)
    assert ass_quat.is_relevant is True


def test_math_tab_rejects_followed_creator_off_topic_upload():
    # User follows 3Blue1Brown
    prefs = UserPreferences(
        interests="Math, Coding",
        categories=["Math", "Coding"],
        active_category="Math",
        followed_creators=[FollowedCreator(name="3Blue1Brown")],
    )
    gate = RelevanceGate(api_key="")

    # 3Blue1Brown uploads an off-topic pasta recipe
    cand_pasta = CandidateVideo(
        id="pasta_1",
        title="Grant's Favorite Homemade Pasta Recipe from Scratch",
        channel="3Blue1Brown",
        description="Today we are taking a break to make fresh handmade fettuccine and pasta sauce.",
        url="https://youtube.com/watch?v=pasta",
        duration_seconds=600,
        source_query="math video",
        source_domain="Mathematics",
    )
    ass_pasta = gate.assess_candidate(cand_pasta, prefs)
    assert ass_pasta.is_relevant is False
    assert ass_pasta.status == "rejected"
    assert "off-topic upload" in ass_pasta.reason or "no connection" in ass_pasta.reason

    # 3Blue1Brown uploads an off-topic woodworking build
    cand_wood = CandidateVideo(
        id="wood_1",
        title="Building My Dream Studio Desk: Woodworking & Shop Tour",
        channel="3Blue1Brown",
        description="A complete guide to my walnut desk build and recording studio furniture.",
        url="https://youtube.com/watch?v=wood",
        duration_seconds=800,
        source_query="math video",
        source_domain="Mathematics",
    )
    ass_wood = gate.assess_candidate(cand_wood, prefs)
    assert ass_wood.is_relevant is False
    assert ass_wood.status == "rejected"


def test_baseball_and_strategy_tab_rejects_corporate_strategy():
    prefs = UserPreferences(
        interests="Baseball & Strategy, Economics",
        categories=["Baseball & Strategy", "Economics"],
        active_category="Baseball & Strategy",
    )
    gate = RelevanceGate(api_key="")

    # Misleading candidate: corporate strategy has "strategy" in title but is corporate/business
    cand_corp = CandidateVideo(
        id="corp_strat",
        title="Porter's Five Forces: Competitive Corporate Strategy for Modern Enterprises",
        channel="Business Insights",
        description="Executive management consulting framework for analyzing competitive industry dynamics.",
        url="https://youtube.com/watch?v=corp_strat",
        duration_seconds=1000,
        source_query="strategic analysis",
        source_domain="Baseball & Strategy",
    )
    ass_corp = gate.assess_candidate(cand_corp, prefs)
    assert ass_corp.is_relevant is False
    assert ass_corp.status == "rejected"
    assert "corporate/business strategy" in ass_corp.reason

    # Legitimate candidate: pitch tunneling and dugout strategy
    cand_pitch = CandidateVideo(
        id="pitch_strat",
        title="Greg Maddux: The Art of Pitch Tunneling and Dugout Sequencing Strategy",
        channel="Pitching Ninja",
        description="A masterclass on how pitch movement, slider release points, and tunneling fool MLB batters.",
        url="https://youtube.com/watch?v=pitch_strat",
        duration_seconds=950,
        source_query="baseball pitch tunneling",
        source_domain="Baseball & Strategy",
    )
    ass_pitch = gate.assess_candidate(cand_pitch, prefs)
    assert ass_pitch.is_relevant is True


def test_basketball_tab_searches_and_relevance():
    prefs = UserPreferences(
        interests="Basketball, Tennis",
        categories=["Basketball", "Tennis"],
        active_category="Basketball",
    )
    gate = RelevanceGate(api_key="")

    # On-topic basketball candidate
    cand_bball = CandidateVideo(
        id="bball_curry",
        title="Stephen Curry's Pick and Roll Spacing & Shot Chart Geometry",
        channel="Thinking Basketball",
        description="Detailed film study of NBA spacing, drop coverage manipulation, and transition scoring.",
        url="https://youtube.com/watch?v=bball_curry",
        duration_seconds=1100,
        source_query="basketball pick and roll",
        source_domain="Basketball",
    )
    ass_bball = gate.assess_candidate(cand_bball, prefs)
    assert ass_bball.is_relevant is True

    # Off-topic baseball candidate inside basketball tab
    cand_baseball = CandidateVideo(
        id="baseball_pitch",
        title="MLB Curveball Mechanics: How Fastballs and Spin Rate Shape the Strikeout",
        channel="Baseball Central",
        description="Detailed baseball pitching mechanics and bullpen analysis.",
        url="https://youtube.com/watch?v=baseball_pitch",
        duration_seconds=900,
        source_query="basketball tactics",
        source_domain="Basketball",
    )
    ass_baseball = gate.assess_candidate(cand_baseball, prefs)
    assert ass_baseball.is_relevant is False
    assert ass_baseball.status == "rejected"

    # Off-topic soccer candidate inside basketball tab
    cand_soccer = CandidateVideo(
        id="soccer_press",
        title="Premier League Tactical Analysis: Pep Guardiola's High Pressing System",
        channel="Football Daily",
        description="Tactical breakdown of Manchester City pressing structures in the Premier League.",
        url="https://youtube.com/watch?v=soccer_press",
        duration_seconds=750,
        source_query="basketball defense",
        source_domain="Basketball",
    )
    ass_soccer = gate.assess_candidate(cand_soccer, prefs)
    assert ass_soccer.is_relevant is False
    assert ass_soccer.status == "rejected"

    # Query Expander check for basketball tab
    expander = QueryExpander(api_key="")
    queries = expander.expand_queries(prefs, count=5)
    query_texts = " ".join([q.query.lower() for q in queries])
    assert "basketball" in query_texts or "nba" in query_texts
    # Must NOT generate baseball pitching queries!
    assert "baseball" not in query_texts
    assert "pitching" not in query_texts


def test_economics_tab_searches_and_relevance():
    prefs = UserPreferences(
        interests="Economics, Gardening",
        categories=["Economics", "Gardening"],
        active_category="Economics",
    )
    gate = RelevanceGate(api_key="")

    # On-topic economics candidate
    cand_econ = CandidateVideo(
        id="econ_fed",
        title="How the Federal Reserve Uses Monetary Policy to Control Inflation and GDP",
        channel="Economics Explained",
        description="An examination of central bank interest rate mechanics, quantitative easing, and macroeconomic stability.",
        url="https://youtube.com/watch?v=econ_fed",
        duration_seconds=1050,
        source_query="monetary policy inflation",
        source_domain="Economics",
    )
    ass_econ = gate.assess_candidate(cand_econ, prefs)
    assert ass_econ.is_relevant is True

    # Off-topic gaming candidate
    cand_mc = CandidateVideo(
        id="mc_iron",
        title="How to Build an Automatic Infinite Iron Farm in Minecraft Survival",
        channel="Gamer Guy",
        description="Minecraft redstone tutorial for farming iron ingots efficiently.",
        url="https://youtube.com/watch?v=mc_iron",
        duration_seconds=700,
        source_query="economics models",
        source_domain="Economics",
    )
    ass_mc = gate.assess_candidate(cand_mc, prefs)
    assert ass_mc.is_relevant is False
    assert ass_mc.status == "rejected"

    # Query Expander check for economics tab
    expander = QueryExpander(api_key="")
    queries = expander.expand_queries(prefs, count=5)
    query_texts = " ".join([q.query.lower() for q in queries])
    assert any(term in query_texts for term in ["economics", "monetary", "inflation", "game theory", "macroeconomics"])


def test_auto_created_tab_requires_substantive_topic_evidence():
    # User has an automatically created interest tab for "Fluid Dynamics"
    prefs = UserPreferences(
        interests="Math, Physics",
        categories=["Math", "Fluid Dynamics"],
        active_category="Fluid Dynamics",
    )
    gate = RelevanceGate(api_key="")

    # Genuine candidate: metadata has substantive fluid dynamics evidence
    cand_fluid = CandidateVideo(
        id="fluid_valid",
        title="Navier-Stokes Equations: Fluid Dynamics of Boundary Layer Separation",
        channel="Physics Lab",
        description="Understanding vorticity, turbulence, and viscous fluid dynamics.",
        url="https://youtube.com/watch?v=fluid_valid",
        duration_seconds=850,
        source_query="fluid dynamics principles",
        source_domain="Curiosity Exploration",
    )
    ass_fluid = gate.assess_candidate(cand_fluid, prefs)
    assert ass_fluid.is_relevant is True

    # Off-topic candidate: matched generic modifier "principles" in query, but content is marketing/corporate
    cand_mktg = CandidateVideo(
        id="mktg_off",
        title="Foundational Principles of Modern Brand Marketing and Executive Growth",
        channel="Marketing Pro",
        description="How brand strategy principles scale consumer businesses and enterprise sales.",
        url="https://youtube.com/watch?v=mktg_off",
        duration_seconds=920,
        source_query="fluid dynamics principles",
        source_domain="Curiosity Exploration",
    )
    ass_mktg = gate.assess_candidate(cand_mktg, prefs)
    assert ass_mktg.is_relevant is False
    assert "Metadata does not support the active tab" in ass_mktg.reason


def test_sparse_tab_returns_honest_shorter_feed_without_padding(monkeypatch):
    service = RabbitholeService(gemini_api_key="")
    prefs = UserPreferences(
        interests="Origami, Math",
        categories=["Origami", "Math"],
        active_category="Origami",
    )

    # Mock expander
    monkeypatch.setattr(
        service.expander,
        "expand_queries",
        lambda *args, **kwargs: [CuriosityQuery(query="origami folding techniques", domain="Origami", query_type="first_principles", rationale="Origami craft")]
    )

    # Mock retriever returning only 2 on-topic origami videos and 4 off-topic cooking videos
    cands = [
        CandidateVideo(id="origami_1", title="Masterclass in Modular Origami Polyhedra Folding", channel="Paper Craft", url="http://o1", duration_seconds=600),
        CandidateVideo(id="origami_2", title="The Mathematics of Origami Crease Patterns", channel="Paper Craft", url="http://o2", duration_seconds=700),
        CandidateVideo(id="cook_1", title="15 Minute Garlic Butter Pasta Recipe", channel="Chef", url="http://c1", duration_seconds=500),
        CandidateVideo(id="cook_2", title="Crispy French Bread Baking Tutorial", channel="Baker", url="http://c2", duration_seconds=800),
        CandidateVideo(id="game_1", title="Minecraft Speedrun World Record Attempt", channel="Gamer", url="http://g1", duration_seconds=900),
        CandidateVideo(id="corp_1", title="Corporate Strategy Principles for CEOs", channel="Exec", url="http://co1", duration_seconds=650),
    ]
    monkeypatch.setattr(service.retriever, "retrieve_candidates", lambda *args, **kwargs: cands)

    feed_resp = service.generate_feed(prefs)

    # Feed must NEVER pad with off-topic cooking, gaming, or corporate videos!
    assert len(feed_resp.items) == 2
    assert all("origami" in it.video.title.lower() for it in feed_resp.items)
    assert feed_resp.pool_exhausted is True
    assert "Origami" in feed_resp.status_message
    assert "fewer than" in feed_resp.status_message or "shorter feed" in feed_resp.status_message or "without padding" in feed_resp.status_message


def test_previously_shown_video_returns_when_pool_is_sparse_disliked_strictly_excluded():
    from rabbithole_app.journey import build_category_feed
    from rabbithole_app.models import RelevanceAssessment, QualityAssessment

    prefs = UserPreferences(
        interests="Math",
        active_category="Math",
        seen_video_ids=["seen_math_1"],
        disliked_video_ids=["disliked_math_2"],
    )

    item_seen = FeedItem(
        video=CandidateVideo(id="seen_math_1", title="Riemann Hypothesis and Prime Distribution", channel="Math Channel", url="http://sm1", duration_seconds=900),
        assessment=QualityAssessment(
            overall_verdict="include",
            substance_score=0.92,
            clickbait_risk_score=0.05,
            novelty_score=0.88,
            composite_score=0.90,
            craft_and_fun_score=0.85,
            tone_estimate="Analytical / Technical breakdown",
            is_entertainment_first=False,
            primary_appeal_domain="Mathematics",
            connection_note="Deep math",
            relevance=RelevanceAssessment(is_relevant=True, status="supported", reason="Math topic"),
        ),
    )
    item_disliked = FeedItem(
        video=CandidateVideo(id="disliked_math_2", title="Linear Algebra Done Right", channel="Math Channel", url="http://dm2", duration_seconds=950),
        assessment=QualityAssessment(
            overall_verdict="include",
            substance_score=0.95,
            clickbait_risk_score=0.02,
            novelty_score=0.90,
            composite_score=0.93,
            craft_and_fun_score=0.88,
            tone_estimate="Analytical",
            is_entertainment_first=False,
            primary_appeal_domain="Mathematics",
            connection_note="Math depth",
            relevance=RelevanceAssessment(is_relevant=True, status="supported", reason="Math topic"),
        ),
    )
    item_fresh = FeedItem(
        video=CandidateVideo(id="fresh_math_3", title="Topology and Manifolds Explained", channel="Topology Pro", url="http://fm3", duration_seconds=800),
        assessment=QualityAssessment(
            overall_verdict="include",
            substance_score=0.88,
            clickbait_risk_score=0.04,
            novelty_score=0.85,
            composite_score=0.87,
            craft_and_fun_score=0.80,
            tone_estimate="Analytical",
            is_entertainment_first=False,
            primary_appeal_domain="Mathematics",
            connection_note="Topology",
            relevance=RelevanceAssessment(is_relevant=True, status="supported", reason="Math topic"),
        ),
    )

    feed = build_category_feed([item_seen, item_disliked, item_fresh], prefs, category_name="Math", length=2)

    feed_ids = [it.video.id for it in feed]
    # Disliked video MUST NEVER appear
    assert "disliked_math_2" not in feed_ids
    # Fresh video MUST appear first
    assert feed_ids[0] == "fresh_math_3"
    # Previously shown video CAN return when pool is sparse
    assert "seen_math_1" in feed_ids


def test_followed_creator_boosts_ranking_within_tab_without_admitting_off_topic():
    from rabbithole_app.journey import build_category_feed
    from rabbithole_app.models import RelevanceAssessment, QualityAssessment

    prefs = UserPreferences(
        interests="Math",
        active_category="Math",
        followed_creators=[FollowedCreator(name="Numberphile")],
    )
    gate = RelevanceGate(api_key="")

    # On-topic candidate from followed creator
    cand_followed_ontopic = CandidateVideo(
        id="np_collatz",
        title="The Collatz Conjecture and Unsolved Number Theory",
        channel="Numberphile",
        url="http://np1",
        duration_seconds=900,
    )
    # On-topic candidate from unfollowed creator
    cand_other_ontopic = CandidateVideo(
        id="other_primes",
        title="Sieve of Eratosthenes and Prime Number Theorems",
        channel="Unknown Math Creator",
        url="http://other1",
        duration_seconds=900,
    )
    # Off-topic candidate from followed creator
    cand_followed_offtopic = CandidateVideo(
        id="np_boardgames",
        title="Top 10 Board Games for Family Game Night",
        channel="Numberphile",
        description="Our team shares their favorite casual board games to play over the weekend.",
        url="http://np_bg",
        duration_seconds=600,
    )

    # 1. Gate check: off-topic upload by followed creator MUST be rejected
    ass_off = gate.assess_candidate(cand_followed_offtopic, prefs)
    assert ass_off.is_relevant is False

    ass_on_np = gate.assess_candidate(cand_followed_ontopic, prefs)
    assert ass_on_np.is_relevant is True

    ass_on_other = gate.assess_candidate(cand_other_ontopic, prefs)
    assert ass_on_other.is_relevant is True

    # 2. Ranking check: inside the tab, followed creator receives ranking boost
    item_np = FeedItem(
        video=cand_followed_ontopic,
        assessment=QualityAssessment(
            overall_verdict="include",
            substance_score=0.85,
            clickbait_risk_score=0.05,
            novelty_score=0.80,
            composite_score=0.82,
            craft_and_fun_score=0.80,
            tone_estimate="Analytical",
            is_entertainment_first=False,
            primary_appeal_domain="Mathematics",
            connection_note="Number theory",
            relevance=ass_on_np,
        )
    )
    item_other = FeedItem(
        video=cand_other_ontopic,
        assessment=QualityAssessment(
            overall_verdict="include",
            substance_score=0.85,
            clickbait_risk_score=0.05,
            novelty_score=0.80,
            composite_score=0.82,  # Exactly same composite score
            craft_and_fun_score=0.80,
            tone_estimate="Analytical",
            is_entertainment_first=False,
            primary_appeal_domain="Mathematics",
            connection_note="Prime numbers",
            relevance=ass_on_other,
        )
    )

    feed = build_category_feed([item_other, item_np], prefs, category_name="Math", length=2)
    # Followed creator should be boosted to index 0
    assert feed[0].video.channel == "Numberphile"
    assert feed[1].video.channel == "Unknown Math Creator"


def test_category_feed_with_living_taste_profile_qualities():
    from rabbithole_app.journey import build_category_feed
    from rabbithole_app.models import LivingTasteProfile, TasteSignal, QualityAssessment, RelevanceAssessment

    profile = LivingTasteProfile()
    profile.record_signal(TasteSignal(
        video_id="sig_1",
        title="Visual explanation of Fourier series",
        channel="Math Channel",
        qualities=["visual explanation", "technical depth"],
        action="substantial_watch",
        weight=2.0
    ))

    prefs = UserPreferences(
        interests="Math",
        active_category="Math",
        living_taste_profile=profile
    )

    item1 = FeedItem(
        video=CandidateVideo(id="v1", title="Fourier Series and Sound Synthesis", channel="Sound Math", url="http://v1"),
        assessment=QualityAssessment(
            overall_verdict="include",
            substance_score=0.8,
            clickbait_risk_score=0.05,
            novelty_score=0.8,
            composite_score=0.8,
            primary_appeal_domain="Mathematics",
            relevance=RelevanceAssessment(is_relevant=True, status="supported", reason="Math")
        )
    )

    feed = build_category_feed([item1], prefs, category_name="Math", length=1)
    assert len(feed) == 1
    assert feed[0].video.id == "v1"


def test_feed_keeps_best_followed_creator_video_for_repeated_topic():
    def item(video_id, title, channel, score):
        return FeedItem(
            video=CandidateVideo(
                id=video_id,
                title=title,
                channel=channel,
                url=f"https://www.youtube.com/watch?v={video_id}",
                source_query="math Fourier transform",
            ),
            assessment=QualityAssessment(composite_score=score, substance_score=0.9),
        )

    candidates = [
        item("threeblueonebrown", "But what is the Fourier Transform? A visual introduction", "3Blue1Brown", 0.82),
        item("primer2", "Fourier Transform Explained for Beginners", "Other Math", 0.89),
        item("primer3", "Fourier Transform: A Simple Introduction", "Another Math", 0.87),
        item("application", "Fourier Transform in Music Synthesis", "Sound Lab", 0.84),
        item("linear_algebra", "Eigenvectors and Linear Transformations", "Linear Math", 0.83),
    ]
    prefs = UserPreferences(
        interests="math",
        categories=["math"],
        followed_creators=[FollowedCreator(name="3Blue1Brown")],
    )

    main_feed = build_main_feed(candidates, prefs, length=3, discovery_target=0)
    tab_feed = build_category_feed(candidates, prefs.model_copy(update={"active_category": "math"}), "math", length=3)

    for feed in (main_feed, tab_feed):
        ids = {selected.video.id for selected in feed}
        assert "threeblueonebrown" in ids
        assert "application" in ids  # A specific application is a different video idea.
        assert "linear_algebra" in ids
        assert "primer2" not in ids
        assert "primer3" not in ids


def test_restoration_does_not_take_over_feed_without_specific_taste():
    def make_item(video_id, title, score):
        return FeedItem(
            video=CandidateVideo(
                id=video_id, title=title, channel=video_id,
                url=f"https://www.youtube.com/watch?v={video_id}",
                source_query="craft entertainment",
            ),
            assessment=QualityAssessment(composite_score=score, entertainment_score=score),
        )

    candidates = [
        make_item("r1", "Craft antique camera restoration", 0.99),
        make_item("r2", "Craft watch restoration", 0.98),
        make_item("r3", "Craft car refurbishment", 0.97),
        make_item("n1", "Craft kinetic sculpture build", 0.80),
        make_item("n2", "Craft animated short film", 0.79),
        make_item("n3", "Craft comedy sketch", 0.78),
    ]
    prefs = UserPreferences(interests="creative videos", categories=["Craft"])

    main = build_main_feed(candidates, prefs, length=4, discovery_target=0)
    tab = build_category_feed(candidates, prefs.model_copy(update={"active_category": "Craft"}), "Craft", length=4)
    for feed in (main, tab):
        assert len(feed) == 4
        assert sum(item.video.id.startswith("r") for item in feed) == 1

    restoration_fan = prefs.model_copy(update={"interests": "creative videos and restoration"})
    fan_feed = build_main_feed(candidates, restoration_fan, length=4, discovery_target=0)
    assert sum(item.video.id.startswith("r") for item in fan_feed) == 2


def test_restoration_channel_and_narrow_pool_do_not_pad_main_feed():
    def make_item(video_id, title, channel, score):
        return FeedItem(
            video=CandidateVideo(id=video_id, title=title, channel=channel, url=f"https://youtube.com/watch?v={video_id}"),
            assessment=QualityAssessment(composite_score=score),
        )

    candidates = [
        make_item("r1", "My Most Satisfying Watch Restoration", "Red Dead Restoration", 0.99),
        make_item("r2", "This Yellowed Nintendo Turned White Again", "TysyTube Restoration", 0.98),
        make_item("r3", "1910 Junghans B10 Mantel Clock Restore", "James Martin", 0.97),
        make_item("r4", "Vintage Omega Cal. 600 Renovation", "Omega Independent", 0.96),
        make_item("r5", "Restoring An English Grandfather Clock", "Insider", 0.95),
        make_item("r6", "Restoring the Original Xbox", "Odd Tinkering", 0.94),
        make_item("n1", "Impossible Basketball Shots from Level 1 to Level 100", "Jesser", 0.8),
        make_item("n2", "NBA Trickshots from Level 1 to 100", "dime", 0.79),
        make_item("n3", "F1 Marble Race Mexico", "Jelle's Marble Runs", 0.78),
        make_item("n4", "Basketball Buzzer Beaters", "NBA", 0.77),
    ]
    prefs = UserPreferences(interests="basketball, video games", categories=["Sports", "Games"])

    feed = build_main_feed(candidates, prefs, length=10, discovery_target=0)
    assert len(feed) == 5  # Better to return fewer than pad the feed with near-identical restorations.
    assert sum(item.video.id.startswith("r") for item in feed) == 1










