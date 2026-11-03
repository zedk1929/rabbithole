"""
Comprehensive tests for Curiosity Archetypes, Personality-First Taste Profiles,
and YouTube Account Integration.

Covers:
1. Curiosity Archetypes data structures and models.
2. UserPreferences personality fields and validation.
3. Query expansion with archetypes (heuristics and Gemini prompt construction).
4. Relevance Gate archetype-query tolerance.
5. YouTube subscriptions parser (CSV, JSON, plaintext/URLs).
6. Taste profile synthesis (heuristic fallback and mocked Gemini).
7. Taste profile application to UserPreferences and LivingTasteProfile.
8. API Endpoints:
   - GET /api/archetypes
   - POST /api/youtube/import-subscriptions
   - POST /api/youtube/sync-browser (error handling for locked sessions)
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from rabbithole_app.models import (
    CURIOSITY_ARCHETYPES,
    UserPreferences,
    CuriosityQuery,
    CandidateVideo,
    RelevanceAssessment,
    FollowedCreator,
    LivingTasteProfile,
)
from rabbithole_app.query_expander import QueryExpander
from rabbithole_app.relevance_gate import RelevanceGate
from rabbithole_app.youtube_integrator import (
    parse_subscriptions_file,
    synthesize_youtube_taste,
    apply_youtube_sync_to_preferences,
)
from rabbithole_app.server import app

client = TestClient(app)


# ============================================================================
# 1. Models & Archetypes Tests
# ============================================================================

def test_curiosity_archetypes_definitions():
    """Verify that all 6 canonical curiosity archetypes are defined with complete metadata."""
    expected_keys = {
        "mechanist",
        "detective",
        "visual_synthesizer",
        "craft_purist",
        "game_theorist",
        "playful_deconstructor",
    }
    assert set(CURIOSITY_ARCHETYPES.keys()) == expected_keys

    for key, data in CURIOSITY_ARCHETYPES.items():
        assert "name" in data
        assert "icon" in data
        assert "description" in data
        assert "search_angles" in data
        assert isinstance(data["search_angles"], list)
        assert len(data["search_angles"]) >= 3
        assert "default_qualities" in data
        assert isinstance(data["default_qualities"], list)


def test_user_preferences_personality_mode_validation():
    """Verify that UserPreferences supports personality-driven discovery without rigid keyword topics."""
    # User selects only archetypes without typing into interests
    prefs = UserPreferences(
        curiosity_archetypes=["mechanist", "visual_synthesizer"],
        curiosity_statement="I love seeing how intricate mechanisms work",
        intellectual_vibes=["physical_intuition", "first_principles"],
    )
    assert prefs.has_personality_preferences is True
    assert "The Mechanist" in prefs.curiosity_summary
    assert "Visual Synthesizer" in prefs.curiosity_summary
    assert prefs.youtube_connected is False
    assert prefs.youtube_synced_channel_count == 0


def test_user_preferences_youtube_fields():
    """Verify YouTube connection state tracking on UserPreferences."""
    prefs = UserPreferences(
        interests="mechanical engineering",
        youtube_connected=True,
        youtube_synced_channel_count=42,
        youtube_sync_mode="browser_cookies",
    )
    assert prefs.youtube_connected is True
    assert prefs.youtube_synced_channel_count == 42
    assert prefs.youtube_sync_mode == "browser_cookies"


# ============================================================================
# 2. Query Expansion & Serendipity Tests
# ============================================================================

def test_query_expansion_with_archetypes_heuristics():
    """
    Verify heuristic query expansion leverages curiosity archetypes to generate
    cross-disciplinary searches rather than being trapped in setup nouns.
    """
    expander = QueryExpander(api_key="")
    prefs = UserPreferences(
        interests="",  # No explicit topic keywords
        curiosity_archetypes=["mechanist"],
        serendipity_level=3,
    )
    queries = expander.expand_queries(prefs, count=4)

    assert isinstance(queries, list)
    assert len(queries) >= 3

    # Should contain queries derived from the mechanist archetype
    queries_text = " ".join([q.query.lower() for q in queries])
    assert any(term in queries_text for term in ["mechanism", "engineering", "tolerances", "micro mechanical", "actuators"])


def test_query_expansion_with_archetypes_gemini_prompt():
    """
    Verify Gemini query expansion receives personality archetype guidelines
    and does not constrain discovery to rigid topical nouns.
    """
    mock_plan_json = json.dumps({
        "queries": [
            {
                "query": "escapement mechanism watchmaking tolerances documentary",
                "domain": "Horology Engineering",
                "query_type": "first_principles",
                "rationale": "Explore microscopic mechanical timing principles"
            },
            {
                "query": "brass analog tide predicting computer kelvin machine",
                "domain": "Historical Computation",
                "query_type": "niche_history",
                "rationale": "Discover physical harmonic computational machines"
            }
        ]
    })

    expander = QueryExpander(api_key="fake-test-key")

    mock_client = MagicMock()
    mock_interaction = MagicMock()
    mock_interaction.output_text = mock_plan_json
    mock_client.interactions.create.return_value = mock_interaction

    with patch("google.genai.Client", return_value=mock_client):
        prefs = UserPreferences(
            interests="watches",
            curiosity_archetypes=["mechanist"],
            curiosity_statement="Craves physical logic and moving parts under stress",
            serendipity_level=4,
        )
        queries = expander.expand_queries(prefs, count=2)

        assert mock_client.interactions.create.called
        call_input = mock_client.interactions.create.call_args[1]["input"]
        # Verify personality instructions were injected into the prompt
        assert "The Mechanist" in call_input
        assert "Craves physical logic" in call_input

        assert len(queries) == 2
        assert "tide predicting" in queries[1].query.lower()


# ============================================================================
# 3. Relevance Gate Archetype Query Support
# ============================================================================

def test_relevance_gate_supports_archetype_queries():
    """
    Verify that videos matching an inquiry generated for a curiosity archetype
    pass relevance even when their specific subject differs from the initial seed interests.
    """
    gate = RelevanceGate()
    prefs = UserPreferences(
        interests="baseball",
        curiosity_archetypes=["mechanist"],
    )
    # A candidate matching a mechanist inquiry query
    query = CuriosityQuery(
        query="four-bar linkage 3d printed mechanisms",
        domain="Mechanical Engineering",
        query_type="first_principles",
        rationale="Study mechanical linkage motion"
    )
    video = CandidateVideo(
        id="vid_linkage_001",
        title="Understanding Four-Bar Linkages in 3D Printing & Robotics",
        channel="Makers Workbench",
        url="https://youtube.com/watch?v=vid_linkage_001",
        description="A deep dive into how planar four-bar linkages transmit motion cleanly without electronic servos.",
        source_query="four-bar linkage 3d printed mechanisms"
    )

    assessment = gate.assess_candidate(video, prefs, query_context=query)
    assert assessment.is_relevant is True
    assert assessment.status == "supported"


# ============================================================================
# 4. YouTube Subscriptions Parsing Tests
# ============================================================================

def test_parse_subscriptions_csv_takeout_format():
    """Verify parsing Google Takeout subscriptions.csv."""
    csv_content = """Channel Id,Channel Url,Channel Title
UC12345,http://www.youtube.com/channel/UC12345,3Blue1Brown
UC67890,http://www.youtube.com/channel/UC67890,Veritasium
UC54321,http://www.youtube.com/channel/UC54321,Technology Connections
"""
    channels = parse_subscriptions_file(csv_content, "subscriptions.csv")
    names = [c["name"] for c in channels]
    assert names == ["3Blue1Brown", "Veritasium", "Technology Connections"]


def test_parse_subscriptions_json_format():
    """Verify parsing YouTube Takeout or export JSON formats."""
    json_snippets = """[
        {"snippet": {"title": "Steve Mould"}},
        {"snippet": {"title": "Practical Engineering"}}
    ]"""
    channels = parse_subscriptions_file(json_snippets, "subs.json")
    names = [c["name"] for c in channels]
    assert names == ["Steve Mould", "Practical Engineering"]

    json_simple = '["AlphaPhoenix", "Applied Science"]'
    channels2 = parse_subscriptions_file(json_simple, "subs.json")
    names2 = [c["name"] for c in channels2]
    assert names2 == ["AlphaPhoenix", "Applied Science"]


def test_parse_subscriptions_plaintext_format():
    """Verify parsing raw pasted channel lists, handles, and URLs."""
    raw_text = """
    @3blue1brown
    https://www.youtube.com/@veritasium
    Technology Connections
    # Comment line to ignore
    https://www.youtube.com/c/practicalengineeringchannel
    """
    channels = parse_subscriptions_file(raw_text, "channels.txt")
    names = [c["name"] for c in channels]
    assert "Technology Connections" in names
    assert any("3blue1brown" in c.lower() for c in names)
    assert any("practicalengineering" in c.lower() for c in names)


# ============================================================================
# 5. YouTube Taste Profile Synthesis Tests
# ============================================================================

def test_synthesize_youtube_taste_heuristic_fallback():
    """Verify heuristic synthesis groups recognizable channels when Gemini is unavailable."""
    channels = [
        "3Blue1Brown",
        "Technology Connections",
        "Practical Engineering",
        "Veritasium",
    ]
    result = synthesize_youtube_taste(channels, api_key=None)

    assert result["curiosity_archetypes"] is not None
    assert len(result["curiosity_archetypes"]) >= 1
    assert "mechanist" in result["curiosity_archetypes"] or "visual_synthesizer" in result["curiosity_archetypes"]
    assert len(result["followed_creators"]) >= 4


def test_synthesize_youtube_taste_with_gemini_mock():
    """Verify Gemini-powered synthesis produces rich archetypes and a curiosity statement."""
    mock_json_response = """{
        "curiosity_archetypes": ["mechanist", "visual_synthesizer"],
        "intellectual_vibes": ["first_principles", "physical_intuition", "diagrammatic_clarity"],
        "curiosity_statement": "Drawn to first-principles mechanical design, visual simulations, and understanding microscopic tolerances in everyday engineering.",
        "top_creators": ["3Blue1Brown", "Technology Connections", "Practical Engineering"],
        "explanation": "Your subscriptions reflect a deep mechanist and visual synthesizer archetype."
    }"""

    mock_client = MagicMock()
    mock_interaction = MagicMock()
    mock_interaction.output_text = mock_json_response
    mock_client.interactions.create.return_value = mock_interaction

    with patch("google.genai.Client", return_value=mock_client):
        result = synthesize_youtube_taste(
            ["3Blue1Brown", "Technology Connections", "Practical Engineering"],
            api_key="fake-key"
        )
        assert result["curiosity_archetypes"] == ["mechanist", "visual_synthesizer"]
        assert "first_principles" in result["intellectual_vibes"]
        assert "microscopic tolerances" in result["curiosity_statement"]


def test_apply_youtube_sync_to_preferences():
    """Verify synthesized YouTube taste properly updates UserPreferences and LivingTasteProfile."""
    base_prefs = UserPreferences(interests="general learning")
    synth_profile = {
        "curiosity_archetypes": ["craft_purist", "mechanist"],
        "intellectual_vibes": ["analog_mastery", "workshop_flow"],
        "curiosity_statement": "Hypnotic workshop mastery and intricate mechanical restoration.",
        "followed_creators": [{"name": "Clickspring"}, {"name": "My Mechanics"}],
        "latent_subjects": ["Precision Craft", "Mechanisms"],
        "explanation": "Focused on precision craftsmanship."
    }

    updated_prefs = apply_youtube_sync_to_preferences(
        base_prefs,
        synth_profile,
        channel_count=15,
        sync_mode="browser_cookies"
    )

    assert updated_prefs.youtube_connected is True
    assert updated_prefs.youtube_synced_channel_count == 15
    assert updated_prefs.youtube_sync_mode == "browser_cookies"
    assert updated_prefs.curiosity_archetypes == ["craft_purist", "mechanist"]
    assert updated_prefs.curiosity_statement == "Hypnotic workshop mastery and intricate mechanical restoration."
    creator_names = [fc.name for fc in updated_prefs.followed_creators]
    assert "Clickspring" in creator_names

    # Verify LivingTasteProfile seeded with positive signals
    assert updated_prefs.living_taste_profile is not None
    assert "Clickspring" in updated_prefs.living_taste_profile.followed_creators
    assert "Precision Craft" in updated_prefs.living_taste_profile.subject_affinities


# ============================================================================
# 6. Server Endpoints Verification
# ============================================================================

def test_api_get_archetypes():
    """Verify GET /api/archetypes returns the list of archetypes."""
    res = client.get("/api/archetypes")
    assert res.status_code == 200
    data = res.json()
    assert "archetypes" in data
    assert len(data["archetypes"]) == 6
    keys = [a["id"] for a in data["archetypes"]]
    assert "mechanist" in keys
    assert "detective" in keys
    assert "game_theorist" in keys


def test_api_youtube_import_subscriptions():
    """Verify POST /api/youtube/import-subscriptions parses and synthesizes profile."""
    payload = {
        "content": "3Blue1Brown\nPractical Engineering\nTechnology Connections",
        "filename": "channels.txt"
    }
    res = client.post("/api/youtube/import-subscriptions", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["channel_count"] == 3
    assert "sync_result" in data
    assert "updated_preferences" in data
    assert data["updated_preferences"]["youtube_connected"] is True


def test_api_youtube_sync_browser_locked_or_missing():
    """Verify POST /api/youtube/sync-browser returns clean 400 error when browser session is unavailable."""
    payload = {
        "browser": "nonexistent_mock_browser"
    }
    res = client.post("/api/youtube/sync-browser", json=payload)
    # yt-dlp raises error for unsupported/missing browser, server returns 400 with helpful message
    assert res.status_code == 400
    detail = res.json().get("error") or res.json().get("detail", "")
    assert any(w in detail.lower() for w in ["privacy", "cookie", "could not read", "unsupported", "failed", "import"])
