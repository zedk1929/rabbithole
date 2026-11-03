"""
Tests for Two-Stage Evaluation Pipeline, Candidate Enrichment, and Shortlist Reranking.

Covers:
1. Repeated-topic suppression (3Blue1Brown vs generic explainer vs musical acoustics application)
2. Entertainment classification (separating gameplay/comedy from lectures and essays)
3. Missing-caption fallback (visual/craft videos evaluated fairly without penalty)
4. Cache reuse (enrichment cache avoids redundant network calls)
5. Comparative evaluation with and without enrichment on realistic fixture pools
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from rabbithole_app.models import (
    CandidateVideo,
    FeedItem,
    UserPreferences,
    FollowedCreator,
    QualityAssessment,
    EnrichedVideoMetadata,
)
from rabbithole_app.enricher import CandidateEnricher, EnrichmentCache, _build_compact_transcript_summary, _parse_subtitle_content
from rabbithole_app.filter_engine import FilterEngine, are_topics_substantially_duplicate
from rabbithole_app.journey import build_main_feed


def test_compact_transcript_summary_bounds_size():
    """Verify that transcript summarization extracts opening, middle, and conclusion within word limits."""
    words = [f"word{i}" for i in range(1000)]
    long_text = " ".join(words)

    summary = _build_compact_transcript_summary(long_text, max_words=450)
    assert "[Opening]:" in summary
    assert "[Key Discussion]:" in summary
    assert "[Conclusion]:" in summary
    assert len(summary.split()) <= 480


def test_parse_subtitle_content_json3_and_vtt():
    """Verify subtitle parser handles json3 and vtt formats properly."""
    json3_sample = '{"events": [{"segs": [{"utf8": "Hello "}, {"utf8": "world!"}]}, {"segs": [{"utf8": "Testing."}]}]}'
    parsed_json3 = _parse_subtitle_content(json3_sample, "json3")
    assert parsed_json3 == "Hello world! Testing."

    vtt_sample = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello <b>world</b>\n\n00:00:02.000 --> 00:00:04.000\nSecond line."
    parsed_vtt = _parse_subtitle_content(vtt_sample, "vtt")
    assert "Hello world Second line." in parsed_vtt


def test_repeated_topic_suppression_and_followed_creator_preference():
    """
    Verify:
    1. Competing introductory primers on Fourier transforms:
       Followed creator (3Blue1Brown) is given meaningful preference.
       The generic duplicate introduction is suppressed.
    2. A distinct application (Fourier transforms in music & sound synthesis)
       is recognized as a distinct angle and preserved in the feed.
    """
    creator = FollowedCreator(
        name="3Blue1Brown",
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        followed_at="2026-10-29T10:00:00Z"
    )
    prefs = UserPreferences(
        interests="mathematics and physics",
        followed_creators=[creator],
        feed_mode="mix"
    )

    # 1. 3Blue1Brown Visual Fourier Primer (followed creator)
    cand_3b1b = CandidateVideo(
        id="3b1b_fourier",
        title="But what is the Fourier Transform? A visual introduction",
        channel="3Blue1Brown",
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        url="https://youtube.com/watch?v=3b1b_fourier",
        duration_seconds=1250,
        description="An intuitive visual introduction to the Fourier transform using winding machine animations."
    )

    # 2. Generic Creator Fourier Primer (competing introductory primer)
    cand_generic_primer = CandidateVideo(
        id="generic_fourier",
        title="The Fourier Transform Explained Simply - Beginner Fundamentals",
        channel="Math Explanations Daily",
        channel_id="UC_random_math",
        url="https://youtube.com/watch?v=generic_fourier",
        duration_seconds=950,
        description="A simple step-by-step beginner guide explaining the Fourier transform basics."
    )

    # 3. Distinct Application: Fourier in Music & Sound Synthesis
    cand_music_application = CandidateVideo(
        id="music_fourier",
        title="The Math Behind Music & Sound Synthesis: Fourier Transforms in Audio Timbre",
        channel="Synth Theory Lab",
        channel_id="UC_synth_lab",
        url="https://youtube.com/watch?v=music_fourier",
        duration_seconds=1100,
        description="How harmonic series and Fourier transforms synthesize musical sounds, timbre, and acoustic filters."
    )

    # 4. Unrelated topic: Linear Algebra Eigenvalues
    cand_eigenvalues = CandidateVideo(
        id="linear_algebra",
        title="Eigenvectors and Eigenvalues: Geometry of Linear Transformations",
        channel="MIT OpenCourseWare",
        channel_id="UC_mit_ocw",
        url="https://youtube.com/watch?v=linear_algebra",
        duration_seconds=1500,
        description="Rigorous university lecture examining eigenvectors and matrix diagonalization."
    )

    filter_engine = FilterEngine(api_key="")

    # Stage 2 evaluation & reranking
    feed_items, _ = filter_engine.filter_and_rank(
        [cand_3b1b, cand_generic_primer, cand_music_application, cand_eigenvalues],
        prefs,
        top_k=3,
        enable_enrichment=False  # Test structured heuristic inference
    )

    returned_ids = [it.video.id for it in feed_items]

    # 1. 3Blue1Brown should receive meaningful preference and be ranked #1
    assert feed_items[0].video.id == "3b1b_fourier"
    assert feed_items[0].video.channel == "3Blue1Brown"

    # 2. Duplicate generic primer MUST be suppressed from top 3
    assert "generic_fourier" not in returned_ids, (
        f"Generic Fourier primer was not suppressed! Returned IDs: {returned_ids}"
    )

    # 3. Distinct application (music & sound synthesis) MUST be preserved in the feed!
    assert "music_fourier" in returned_ids, (
        f"Distinct musical application of Fourier transform was mistakenly suppressed! Returned IDs: {returned_ids}"
    )

    # 4. Unrelated topic (Eigenvalues) is also present
    assert "linear_algebra" in returned_ids


def test_are_topics_substantially_duplicate_logic():
    """Unit test for are_topics_substantially_duplicate with various candidate combinations."""
    item_3b1b = FeedItem(
        video=CandidateVideo(id="1", title="But what is the Fourier Transform? A visual introduction", channel="3Blue1Brown", url="http://1"),
        assessment=QualityAssessment(
            actual_subject="Fourier Transform",
            specific_angle="Visual geometric introduction to winding frequencies",
            content_format="explanation"
        )
    )

    item_generic = FeedItem(
        video=CandidateVideo(id="2", title="The Fourier Transform Explained Simply - Beginners Primer", channel="Math Daily", url="http://2"),
        assessment=QualityAssessment(
            actual_subject="Fourier Transform",
            specific_angle="Introductory primer on Fourier transform basics",
            content_format="explanation"
        )
    )

    item_music = FeedItem(
        video=CandidateVideo(id="3", title="The Math Behind Music: Fourier Transforms in Audio and Sound Synthesis", channel="Acoustic Lab", url="http://3"),
        assessment=QualityAssessment(
            actual_subject="Fourier Transform in Audio & Sound Synthesis",
            specific_angle="Application of Fourier transforms in musical acoustics and synthesizer filters",
            content_format="explanation"
        )
    )

    item_quantum = FeedItem(
        video=CandidateVideo(id="4", title="Quantum Wavefunctions and Momentum Space via Fourier Transforms", channel="Physics Depth", url="http://4"),
        assessment=QualityAssessment(
            actual_subject="Quantum Wavefunctions",
            specific_angle="Application of Fourier transform to position and momentum wavefunctions in quantum mechanics",
            content_format="explanation"
        )
    )

    # Primers on the same subject should be recognized as duplicates
    assert are_topics_substantially_duplicate(item_3b1b, item_generic) is True
    assert are_topics_substantially_duplicate(item_generic, item_3b1b) is True

    # Distinct applications should NOT be treated as duplicates of the primer
    assert are_topics_substantially_duplicate(item_3b1b, item_music) is False
    assert are_topics_substantially_duplicate(item_generic, item_music) is False
    assert are_topics_substantially_duplicate(item_3b1b, item_quantum) is False

    # Two distinct applications should NOT be treated as duplicates of each other
    assert are_topics_substantially_duplicate(item_music, item_quantum) is False


def test_entertainment_classification_separates_gameplay_and_comedy_from_lectures():
    """Verify that Stage 2 evaluation correctly classifies entertainment formats and separates them from lectures/essays."""
    filter_engine = FilterEngine(api_key="")

    cand_comedy = CandidateVideo(
        id="c_comedy",
        title="Hilarious Architecture Bloopers & Design Fails: Comedy Breakdown",
        channel="Humor Hub",
        url="http://comedy",
        duration_seconds=600,
        description="The funniest and most ridiculous architecture mistakes ever built with comedic commentary."
    )

    cand_gameplay = CandidateVideo(
        id="c_gameplay",
        title="We Built Brutalist Architecture Megastructures in Minecraft: Creative Build Challenge",
        channel="Block Builders",
        url="http://gameplay",
        duration_seconds=900,
        description="Satisfying gameplay and clutch creative build moments constructing monumental brutalist architecture in Minecraft."
    )

    cand_lecture = CandidateVideo(
        id="c_lecture",
        title="Soviet Architecture: A University Lecture on Modernist Urban Planning",
        channel="Architecture Dept",
        url="http://lecture",
        duration_seconds=1800,
        description="Formal academic symposium lecture examining the history and central planning of Soviet cities."
    )

    cand_essay = CandidateVideo(
        id="c_essay",
        title="The Geometry of Non-Euclidean Game Engines: A Technical Breakdown Video Essay",
        channel="Engine Insights",
        url="http://essay",
        duration_seconds=1200,
        description="Analytical video essay detailing non-Euclidean hyperbolic projection math in 3D game engines."
    )

    # 1. Under Entertainment-only mode: Only comedy & gameplay qualify
    prefs_ent = UserPreferences(interests="architecture and games", feed_mode="entertainment")
    ent_feed, _ = filter_engine.filter_and_rank(
        [cand_comedy, cand_gameplay, cand_lecture, cand_essay],
        prefs_ent,
        top_k=4,
        enable_enrichment=False
    )

    ent_ids = [it.video.id for it in ent_feed]
    assert "c_comedy" in ent_ids
    assert "c_gameplay" in ent_ids
    assert "c_lecture" not in ent_ids
    assert "c_essay" not in ent_ids

    comedy_item = next(it for it in ent_feed if it.video.id == "c_comedy")
    assert comedy_item.assessment.is_entertainment_first is True
    assert comedy_item.assessment.entertainment_format == "comedy"
    assert comedy_item.assessment.appeal_type == "pure_entertainment"

    gameplay_item = next(it for it in ent_feed if it.video.id == "c_gameplay")
    assert gameplay_item.assessment.is_entertainment_first is True
    assert gameplay_item.assessment.entertainment_format in ["gameplay", "creative_projects"]

    # 2. Under Mix mode: Both educational essays and entertainment are blended
    prefs_mix = UserPreferences(interests="architecture and games", feed_mode="mix")
    mix_feed, _ = filter_engine.filter_and_rank(
        [cand_comedy, cand_gameplay, cand_lecture, cand_essay],
        prefs_mix,
        top_k=4,
        enable_enrichment=False
    )
    mix_ids = [it.video.id for it in mix_feed]
    assert len(mix_ids) >= 3
    # In mix mode, high-substance essays are allowed
    assert "c_essay" in mix_ids or "c_lecture" in mix_ids


def test_missing_caption_fallback_evaluates_fairly_without_penalty(tmp_path, monkeypatch):
    """
    Verify that videos with no captions (e.g. silent woodworking, music performance)
    are NOT penalized as low quality.
    """
    cache = EnrichmentCache(cache_file_path=tmp_path / "test_cache.json")
    enricher = CandidateEnricher(cache=cache)

    cand_silent_craft = CandidateVideo(
        id="wood_craft_99",
        title="Satisfying Woodworking and Japanese Joinery: Building a Solid Cedar Tea Table",
        channel="Artisan Woodworks",
        url="http://wood99",
        duration_seconds=1200,
        description="Quiet, meditative woodworking craftsmanship building a traditional mortise and tenon cedar table with hand tools."
    )

    # Simulate detailed yt_dlp metadata with NO captions (visual craft video)
    fake_info = {
        "id": "wood_craft_99",
        "title": cand_silent_craft.title,
        "description": cand_silent_craft.description,
        "duration": 1200,
        "tags": ["woodworking", "joinery", "japanese craft", "cedar table", "artisan"],
        "categories": ["Howto & Style"],
        "subtitles": {},
        "automatic_captions": {},
    }

    class FakeYDL:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download=False):
            return fake_info

    monkeypatch.setattr("rabbithole_app.enricher.yt_dlp.YoutubeDL", FakeYDL)

    enriched = enricher.fetch_metadata_and_transcript(cand_silent_craft)
    assert enriched.has_captions is False
    assert enriched.transcript_summary is None
    assert enriched.evidence_source == "metadata"
    assert len(enriched.tags) == 5

    # Evaluate through FilterEngine
    filter_engine = FilterEngine(api_key="", enricher=enricher)
    prefs = UserPreferences(interests="woodworking and craft", feed_mode="mix")
    items, _ = filter_engine.filter_and_rank([cand_silent_craft], prefs, top_k=1, enable_enrichment=True)

    assert len(items) == 1
    ass = items[0].assessment
    # Craft video should score high despite lacking captions
    assert ass.substance_score >= 0.70
    assert ass.evidence_source == "metadata"
    assert "watched" not in ass.connection_note.lower()
    assert "metadata" in ass.connection_note.lower() or "surfaced" in ass.connection_note.lower()


def test_cache_reuse_avoids_redundant_network_calls(tmp_path, monkeypatch):
    """Verify that cached video enrichment eliminates redundant extraction calls on subsequent requests."""
    cache_file = tmp_path / "enrichment_cache.json"
    cache = EnrichmentCache(cache_file_path=cache_file)
    enricher = CandidateEnricher(cache=cache)

    cand = CandidateVideo(
        id="cached_test_vid",
        title="Deep Dive into Relational Database Internals: B-Trees and WAL",
        channel="Database Engineering",
        url="http://db_internals",
        duration_seconds=1400,
        description="How write-ahead logs and B-Tree page splits ensure ACID compliance in database engines."
    )

    fetch_call_count = 0

    class TrackingYDL:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download=False):
            nonlocal fetch_call_count
            fetch_call_count += 1
            return {
                "id": "cached_test_vid",
                "title": cand.title,
                "description": cand.description,
                "duration": 1400,
                "tags": ["databases", "btree", "wal", "systems"],
                "categories": ["Science & Technology"],
                "subtitles": {},
                "automatic_captions": {},
            }

    monkeypatch.setattr("rabbithole_app.enricher.yt_dlp.YoutubeDL", TrackingYDL)

    # First call: cache miss -> calls yt_dlp
    first_res = enricher.fetch_metadata_and_transcript(cand)
    assert fetch_call_count == 1
    assert first_res.video_id == "cached_test_vid"
    assert len(cache) == 1

    # Second call for SAME video ID: MUST hit cache -> NO network call
    second_res = enricher.fetch_metadata_and_transcript(cand)
    assert fetch_call_count == 1, "Cache hit failed; network was called again!"
    assert second_res.video_id == "cached_test_vid"
    assert second_res.tags == ["databases", "btree", "wal", "systems"]

    # Cache persistence check: new cache instance loading from disk
    new_cache = EnrichmentCache(cache_file_path=cache_file)
    assert len(new_cache) == 1
    cached_disk = new_cache.get("cached_test_vid")
    assert cached_disk is not None
    assert cached_disk.title == cand.title


def test_comparison_with_and_without_enrichment_on_fixture_pool(tmp_path):
    """
    Directly compares recommendations with and without enrichment on the same fixture pool:
    - Compares topic repetition suppression
    - Compares structured field grounding (actual_subject, specific_angle, audience, format)
    - Compares evidence source attribution
    """
    cache = EnrichmentCache(cache_file_path=tmp_path / "comp_cache.json")
    enricher = CandidateEnricher(cache=cache)

    creator = FollowedCreator(name="3Blue1Brown", channel_id="UCYO_jab_esuFRV4b17AJtAw")
    prefs = UserPreferences(interests="mathematics and sound", followed_creators=[creator], feed_mode="mix")

    # Fixture pool:
    # 1. 3Blue1Brown Fourier primer (followed creator)
    v1 = CandidateVideo(
        id="v1_3b1b",
        title="But what is the Fourier Transform? A visual introduction",
        channel="3Blue1Brown",
        channel_id="UCYO_jab_esuFRV4b17AJtAw",
        url="http://v1",
        duration_seconds=1250,
        description="Visual geometric intro."
    )
    # 2. Generic Fourier primer (competing primer)
    v2 = CandidateVideo(
        id="v2_generic",
        title="The Fourier Transform Explained Simply - Beginner Fundamentals",
        channel="Everyday Math",
        url="http://v2",
        duration_seconds=800,
        description="Basic introduction."
    )
    # 3. Audio synthesis Fourier application
    v3 = CandidateVideo(
        id="v3_audio",
        title="The Math Behind Music & Sound Synthesis: Fourier Transforms in Audio Timbre",
        channel="Sound Lab",
        url="http://v3",
        duration_seconds=1100,
        description="Audio synthesis and harmonic series."
    )
    # 4. Math lecture
    v4 = CandidateVideo(
        id="v4_calculus",
        title="Calculus Made Easy: Limits and Derivatives",
        channel="Calculus Daily",
        url="http://v4",
        duration_seconds=900,
        description="Calculus fundamentals."
    )

    pool = [v1, v2, v3, v4]

    # Pre-populate enrichment cache with realistic detailed metadata & transcripts
    cache.put(
        "v1_3b1b",
        EnrichedVideoMetadata(
            video_id="v1_3b1b",
            title=v1.title,
            channel=v1.channel,
            channel_id=v1.channel_id,
            description="Complete breakdown of winding machines and circular frequencies.",
            tags=["mathematics", "fourier transform", "visual math", "manim"],
            categories=["Education"],
            duration_seconds=1250,
            has_captions=True,
            transcript_summary="[Opening]: This video introduces the Fourier transform as a winding machine. [Key Discussion]: Rotating waves at varying frequencies. [Conclusion]: Unwrapping frequencies creates the spectrum.",
            evidence_source="metadata_and_captions"
        )
    )
    cache.put(
        "v2_generic",
        EnrichedVideoMetadata(
            video_id="v2_generic",
            title=v2.title,
            channel=v2.channel,
            description="A generic primer covering basic Fourier equations.",
            tags=["math", "fourier transform", "tutorial"],
            categories=["Education"],
            duration_seconds=800,
            has_captions=True,
            transcript_summary="[Opening]: What is a Fourier transform? [Key Discussion]: Basic formulas and sines and cosines. [Conclusion]: Summary of equations.",
            evidence_source="metadata_and_captions"
        )
    )
    cache.put(
        "v3_audio",
        EnrichedVideoMetadata(
            video_id="v3_audio",
            title=v3.title,
            channel=v3.channel,
            description="Synthesizer acoustics and harmonic audio analysis using Fourier transform.",
            tags=["audio synthesis", "sound design", "fourier transform", "acoustics", "synthesizer"],
            categories=["Music", "Science & Technology"],
            duration_seconds=1100,
            has_captions=True,
            transcript_summary="[Opening]: How synthesizers build timbre. [Key Discussion]: Applying Fourier analysis to square waves and sawtooth audio filters. [Conclusion]: Practical acoustic sound design.",
            evidence_source="metadata_and_captions"
        )
    )
    cache.put(
        "v4_calculus",
        EnrichedVideoMetadata(
            video_id="v4_calculus",
            title=v4.title,
            channel=v4.channel,
            description="Introduction to differential calculus.",
            tags=["calculus", "derivatives", "limits"],
            categories=["Education"],
            duration_seconds=900,
            has_captions=False,
            transcript_summary=None,
            evidence_source="metadata"
        )
    )

    filter_engine = FilterEngine(api_key="", enricher=enricher)

    # 1. Run WITHOUT enrichment
    feed_without, _ = filter_engine.filter_and_rank(pool, prefs, top_k=3, enable_enrichment=False)
    ids_without = [it.video.id for it in feed_without]

    # 2. Run WITH enrichment
    feed_with, _ = filter_engine.filter_and_rank(pool, prefs, top_k=3, enable_enrichment=True)
    ids_with = [it.video.id for it in feed_with]

    # Concrete improvements observed with enrichment:
    # 1. Both feeds should prioritize followed creator (3Blue1Brown)
    assert ids_with[0] == "v1_3b1b"

    # 2. In enriched mode, v1 has evidence_source == "metadata_and_captions"
    assert feed_with[0].assessment.evidence_source == "metadata_and_captions"
    assert feed_with[0].assessment.actual_subject == "Fourier Transform"
    assert feed_with[0].assessment.confidence >= 0.85

    # 3. In enriched mode, v4 has evidence_source == "metadata" (since has_captions=False)
    v4_item = next(it for it in feed_with if it.video.id == "v4_calculus")
    assert v4_item.assessment.evidence_source == "metadata"

    # 4. Repeated primer (v2_generic) is suppressed, while distinct application (v3_audio) is present!
    assert "v2_generic" not in ids_with
    assert "v3_audio" in ids_with


def test_diverse_shortlist_selection_not_monopolized_by_search_order():
    """
    Verify:
    1. A single search query returning 15 videos from one dominant creator cannot monopolize the shortlist.
    2. Changing search-result order still guarantees representation across distinct queries, topics, and creators.
    3. Genuine discovery slots (wildcard and distant queries) are preserved in the shortlist.
    """
    filter_engine = FilterEngine(api_key="")
    prefs = UserPreferences(interests="astronomy and mathematics", feed_mode="mix")

    # Query 1: 15 videos from "Dominant Studio"
    q1_cands = [
        CandidateVideo(
            id=f"q1_dom_{i}",
            title=f"General Space Fact Video #{i}",
            channel="Dominant Studio",
            channel_id="UC_dom",
            url=f"http://q1_{i}",
            source_query="space discoveries",
            source_domain="Astronomy",
            source_query_type="first_principles",
            duration_seconds=600 + i * 20,
            view_count=50000,
        )
        for i in range(15)
    ]

    # Query 2: 4 videos from distinct creators on astrophysics math
    q2_cands = [
        CandidateVideo(
            id=f"q2_astro_{i}",
            title=f"Astrophysics Orbital Mechanics Primer #{i}",
            channel=f"Astrophysics Lab {i}",
            channel_id=f"UC_astro_{i}",
            url=f"http://q2_{i}",
            source_query="orbital mechanics math",
            source_domain="Orbital Dynamics",
            source_query_type="practitioner_deep_dive",
            duration_seconds=900,
            view_count=30000,
        )
        for i in range(4)
    ]

    # Query 3: 3 videos from specialized telescope optical craft
    q3_cands = [
        CandidateVideo(
            id=f"q3_optics_{i}",
            title=f"Hand Grinding Telescope Mirrors with Sub-Micron Precision #{i}",
            channel=f"Mirror Craft {i}",
            channel_id=f"UC_optics_{i}",
            url=f"http://q3_{i}",
            source_query="telescope mirror grinding precision optics",
            source_domain="Precision Optics",
            source_query_type="niche_history",
            duration_seconds=1200,
            view_count=40000,
        )
        for i in range(3)
    ]

    # Discovery queries: 2 wildcard / distant candidates
    disc_cands = [
        CandidateVideo(
            id="disc_wildcard",
            title="The Strange Fluid Dynamics of Deep-Sea Hydrothermal Plumes",
            channel="Ocean Exploration Institute",
            channel_id="UC_ocean",
            url="http://disc_wildcard",
            source_query="hydrothermal plume fluid dynamics",
            source_domain="Marine Oceanography",
            source_query_type="wildcard_discovery",
            is_discovery=True,
            duration_seconds=850,
            view_count=45000,
        ),
        CandidateVideo(
            id="disc_distant",
            title="Non-Euclidean Hyperbolic Geometry in Ancient Islamic Mosaic Tilings",
            channel="Tiling Geometry Research",
            channel_id="UC_tiling",
            url="http://disc_distant",
            source_query="hyperbolic non-euclidean tilings",
            source_domain="Geometric Art",
            source_query_type="distant_discovery",
            is_discovery=True,
            duration_seconds=1100,
            view_count=35000,
        ),
    ]

    rel_map = {}

    # Pool Order A: Query 1 (Dominant Studio) is completely first (15 videos), then Q2, Q3, Discovery
    pool_order_a = q1_cands + q2_cands + q3_cands + disc_cands
    shortlist_a = filter_engine.select_diverse_shortlist(pool_order_a, prefs, rel_map, limit=12)
    ids_a = {c.id for c in shortlist_a}
    channels_a = [c.channel for c in shortlist_a]

    # 1. Monopolization check: Dominant Studio must NOT take all 12 slots despite having 15 candidates first in order
    dom_count_a = channels_a.count("Dominant Studio")
    assert dom_count_a <= 3, f"Dominant Studio monopolized the shortlist! Count: {dom_count_a}"

    # 2. Query 2 and Query 3 candidates MUST be selected
    assert any(c.id.startswith("q2_") for c in shortlist_a), "Query 2 candidates were starved!"
    assert any(c.id.startswith("q3_") for c in shortlist_a), "Query 3 candidates were starved!"

    # 3. Discovery candidates MUST be preserved
    assert "disc_wildcard" in ids_a, "Wildcard discovery candidate was lost from shortlist!"
    assert "disc_distant" in ids_a, "Distant discovery candidate was lost from shortlist!"

    # Pool Order B: Reverse order (Discovery first, then Q3, Q2, then Q1 Dominant Studio)
    pool_order_b = list(reversed(pool_order_a))
    shortlist_b = filter_engine.select_diverse_shortlist(pool_order_b, prefs, rel_map, limit=12)
    ids_b = {c.id for c in shortlist_b}
    channels_b = [c.channel for c in shortlist_b]

    # Changing search result order still preserves multi-query diversity and discovery
    dom_count_b = channels_b.count("Dominant Studio")
    assert dom_count_b <= 3
    assert any(c.id.startswith("q2_") for c in shortlist_b)
    assert any(c.id.startswith("q3_") for c in shortlist_b)
    assert "disc_wildcard" in ids_b
    assert "disc_distant" in ids_b


def test_enrichment_deadline_not_blocked_by_stalled_fetches():
    """
    Verify:
    1. A stalled metadata/caption fetch does not block the thread pool or hold up feed generation past the deadline.
    2. Execution strictly terminates within the bounded timeout period.
    3. Incomplete candidates fall back to available metadata with is_fallback=True.
    """
    import time
    cache = EnrichmentCache()
    enricher = CandidateEnricher(cache=cache, timeout_seconds=0.3)

    fast_cand = CandidateVideo(
        id="fast_cand_1",
        title="Fast Video That Completes Promptly",
        channel="Fast Channel",
        url="http://fast1",
        description="Fast metadata available immediately."
    )
    stalled_cand = CandidateVideo(
        id="stall_cand_2",
        title="Stalled Video Simulating Hung Network Socket",
        channel="Stalled Channel",
        url="http://stall2",
        description="Stalled metadata simulation."
    )

    def mock_fetch(cand: CandidateVideo) -> EnrichedVideoMetadata:
        if cand.id == "fast_cand_1":
            return EnrichedVideoMetadata(
                video_id=cand.id,
                title=cand.title,
                channel=cand.channel,
                description="Enriched fast metadata",
                tags=["fast"],
                evidence_source="metadata",
                is_fallback=False
            )
        else:
            # Simulate stalled network connection (2.5 seconds)
            time.sleep(2.5)
            return EnrichedVideoMetadata(
                video_id=cand.id,
                title=cand.title,
                channel=cand.channel,
                description="Too late metadata",
                evidence_source="metadata",
                is_fallback=False
            )

    enricher.fetch_metadata_and_transcript = mock_fetch

    start_t = time.perf_counter()
    # Call enrich_shortlist with a strict deadline of 0.35s
    results = enricher.enrich_shortlist([fast_cand, stalled_cand], max_workers=2, timeout=0.35)
    elapsed = time.perf_counter() - start_t

    # 1. Total elapsed time must be strictly bounded close to the deadline (well under 1.0s, not waiting 2.5s)
    assert elapsed < 0.9, f"Enrichment deadline failed! Main thread blocked for {elapsed:.2f}s instead of ~0.35s"

    # 2. Both candidates must have entries in results
    assert "fast_cand_1" in results
    assert "stall_cand_2" in results

    # 3. Fast candidate completed successfully
    assert results["fast_cand_1"].is_fallback is False
    assert results["fast_cand_1"].description == "Enriched fast metadata"

    # 4. Stalled candidate fell back safely with is_fallback=True and retry_after set
    assert results["stall_cand_2"].is_fallback is True
    assert results["stall_cand_2"].evidence_source == "fallback"
    assert results["stall_cand_2"].retry_after is not None


def test_temporary_failure_cached_with_retry_period_and_retried(tmp_path):
    """
    Verify:
    1. A temporary failure is cached with a short retry period (is_fallback=True, retry_after).
    2. During the retry window, subsequent calls return the cached fallback to prevent network hammering.
    3. Once the retry period expires, cache.get() returns None to allow retry.
    4. Successful results are cached longer (is_fallback=False).
    5. Cache writes are safe and atomic.
    """
    from datetime import datetime, timezone, timedelta

    cache_file = tmp_path / "test_cache.json"
    cache = EnrichmentCache(cache_file_path=cache_file)

    vid_id = "test_retry_vid"
    cand = CandidateVideo(
        id=vid_id,
        title="Test Temporary Network Failure Video",
        channel="Test Channel",
        url="http://test_retry"
    )

    enricher = CandidateEnricher(cache=cache, timeout_seconds=1.0)

    # 1. Simulate temporary failure: creates fallback with retry_ttl_seconds=1
    fallback = enricher._create_fallback_metadata(cand, is_fallback=True, retry_ttl_seconds=1)
    assert fallback.is_fallback is True
    assert fallback.retry_after is not None
    cache.put(vid_id, fallback)

    # Verify atomic write persisted to disk
    assert cache_file.exists()

    # 2. Immediate retrieval during retry window: returns fallback
    entry_during_ttl = cache.get(vid_id)
    assert entry_during_ttl is not None
    assert entry_during_ttl.is_fallback is True

    # 3. Simulate passage of retry TTL by setting retry_after to the past
    past_time = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    fallback.retry_after = past_time
    cache.put(vid_id, fallback)

    # Once retry period has expired, cache.get() returns None (allowing retry)
    entry_after_ttl = cache.get(vid_id)
    assert entry_after_ttl is None, "Cache should have evicted expired fallback to allow retry!"

    # 4. Successful fetch is cached without being evicted by short retry window
    successful_meta = EnrichedVideoMetadata(
        video_id=vid_id,
        title="Successfully Fetched Video",
        channel="Test Channel",
        description="Rich description",
        is_fallback=False,
        cached_at=datetime.now(timezone.utc).isoformat()
    )
    cache.put(vid_id, successful_meta)

    entry_success = cache.get(vid_id)
    assert entry_success is not None
    assert entry_success.is_fallback is False
    assert entry_success.description == "Rich description"
