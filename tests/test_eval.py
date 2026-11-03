"""
Tests for the Rabbithole Viewing Path Evaluation Workflow.
Verifies profile integrity, baseline behavior, rating sheet blanks,
reproducibility, and secret sanitization.
"""
import json
import re
import pytest
from pathlib import Path

from rabbithole_app.models import UserPreferences, CandidateVideo
from rabbithole_app.filter_engine import FilterEngine
from eval.baseline import compute_search_order_baseline, is_candidate_eligible
from eval.evaluate import EvaluationRunner, sanitize_data
from eval.rating_sheet import generate_markdown_rating_sheet, generate_csv_rating_sheet


@pytest.fixture
def repo_root():
    return Path(__file__).resolve().parent.parent


def test_evaluation_profiles_schema_and_diversity(repo_root):
    profiles_path = repo_root / "eval" / "profiles.json"
    assert profiles_path.exists(), "eval/profiles.json must exist"

    with open(profiles_path, "r", encoding="utf-8") as f:
        profiles = json.load(f)

    assert len(profiles) == 10, "Must contain exactly 10 evaluation preference profiles"

    ids = set()
    categories = set()
    has_broad = False
    has_unfamiliar = False
    only_broad_and_serendipity_count = 0

    for p in profiles:
        assert "id" in p
        assert p["id"] not in ids, f"Duplicate profile ID: {p['id']}"
        ids.add(p["id"])

        cat = p.get("category", "")
        categories.add(cat)
        if cat == "broad_interest":
            has_broad = True
        elif cat == "unfamiliar_field":
            has_unfamiliar = True

        # Must validate against UserPreferences schema
        prefs = UserPreferences(**p["preferences"])
        assert len(prefs.interests.strip()) > 0
        assert 1 <= prefs.serendipity_level <= 5

        # Redesign requirement: Rabbithole seeks directions on its own without user-specified target fields
        assert prefs.explore_topics == "", f"Profile '{p['id']}' must not specify target explore fields"

        # Check if profile uses only broad interests and serendipity
        if (
            not prefs.familiar_topics
            and not prefs.explore_topics
            and not prefs.avoid_topics
            and not prefs.learning_goal
        ):
            only_broad_and_serendipity_count += 1

    assert has_broad, "Evaluation set must include broad interest profiles"
    assert has_unfamiliar, "Evaluation set must include profiles requesting unfamiliar fields"
    assert only_broad_and_serendipity_count >= 5, (
        f"Expected at least 5 profiles to use only broad interests and serendipity; found {only_broad_and_serendipity_count}"
    )


def test_baseline_filters_ineligible_and_preserves_order():
    filter_engine = FilterEngine(api_key=None)
    prefs = UserPreferences(
        interests="fluid dynamics",
        avoid_topics="sensational disaster, political climate debate",
        min_duration_minutes=8
    )

    candidates = [
        # Candidate 0: Too short (45s < 8m) -> Ineligible
        CandidateVideo(
            id="v0", title="Short Fluid Demo", channel="QuickVids",
            url="https://youtube.com/watch?v=v0", duration_seconds=45,
            source_query="fluid dynamics basics", source_query_type="first_principles"
        ),
        # Candidate 1: Matches avoided topic -> Ineligible
        CandidateVideo(
            id="v1", title="Sensational Disaster Storm Ahead", channel="NewsWire",
            url="https://youtube.com/watch?v=v1", duration_seconds=1200,
            source_query="fluid dynamics turbulence", source_query_type="first_principles"
        ),
        # Candidate 2: Clickbait ALL CAPS -> Ineligible
        CandidateVideo(
            id="v2", title="THIS CRAZY VORTEX EXPERIMENT BROKE MY BRAIN COMPLETELY?!?!", channel="ClickClack",
            url="https://youtube.com/watch?v=v2", duration_seconds=900,
            source_query="fluid dynamics vortex", source_query_type="first_principles"
        ),
        # Candidate 3: Eligible #1
        CandidateVideo(
            id="v3", title="Vortex Shedding and Karman Streets Lecture", channel="AeroArchive",
            url="https://youtube.com/watch?v=v3", duration_seconds=1800,
            source_query="vortex shedding physics", source_query_type="archive_lecture"
        ),
        # Candidate 4: Eligible #2
        CandidateVideo(
            id="v4", title="Laminar Flow in Boundary Layers", channel="PhysicsFoundations",
            url="https://youtube.com/watch?v=v4", duration_seconds=1500,
            source_query="laminar boundary layers", source_query_type="first_principles"
        ),
        # Candidate 5: Duplicate of v3 -> should be deduplicated
        CandidateVideo(
            id="v3", title="Vortex Shedding Duplicate", channel="AeroArchive",
            url="https://youtube.com/watch?v=v3", duration_seconds=1800,
            source_query="vortex shedding physics", source_query_type="archive_lecture"
        ),
        # Candidate 6: Eligible #3
        CandidateVideo(
            id="v6", title="Taylor-Couette Flow Demonstration", channel="LabDemos",
            url="https://youtube.com/watch?v=v6", duration_seconds=950,
            source_query="taylor couette flow", source_query_type="practitioner_deep_dive"
        ),
    ]

    baseline = compute_search_order_baseline(candidates, prefs, filter_engine, length=5)

    assert len(baseline) == 3
    # Check that order strictly matches original retrieval sequence
    assert baseline[0]["video_id"] == "v3"
    assert baseline[0]["baseline_rank"] == 1
    assert baseline[0]["original_search_index"] == 3

    assert baseline[1]["video_id"] == "v4"
    assert baseline[1]["baseline_rank"] == 2
    assert baseline[1]["original_search_index"] == 4

    assert baseline[2]["video_id"] == "v6"
    assert baseline[2]["baseline_rank"] == 3
    assert baseline[2]["original_search_index"] == 6


def test_rating_sheet_leaves_scores_blank_and_includes_diagnostics():
    sample_eval_data = [
        {
            "profile": {
                "id": "p01_test",
                "title": "Test Profile",
                "category": "broad_interest",
                "description": "Test description",
                "preferences": {
                    "interests": "acoustics",
                    "familiar_topics": "music",
                    "explore_topics": "physics",
                    "avoid_topics": "pop",
                    "learning_goal": "learn",
                    "serendipity_level": 3,
                    "depth_level": "curious_explorer",
                    "min_duration_minutes": 6,
                }
            },
            "app_path": [
                {
                    "video_id": "v1", "title": "Acoustics 101", "channel": "MIT",
                    "duration_formatted": "25m 00s", "url": "https://youtube.com/watch?v=v1",
                    "path_step": "Start", "source_query_type": "first_principles",
                },
                {
                    "video_id": "v2", "title": "Harmonic Series", "channel": "Oxford",
                    "duration_formatted": "40m 00s", "url": "https://youtube.com/watch?v=v2",
                    "path_step": "Deepen", "source_query_type": "archive_lecture",
                }
            ],
            "baseline_path": [
                {
                    "video_id": "b1", "title": "Acoustics Intro", "channel": "OpenLearn",
                    "duration_formatted": "15m 00s", "url": "https://youtube.com/watch?v=b1",
                    "original_search_index": 0, "source_query_type": "first_principles",
                }
            ]
        }
    ]

    meta = {
        "run_id": "test_run_1",
        "timestamp": "2026-10-28T00:00:00Z",
        "source_mode": "fixture",
        "llm_mode": "test",
    }

    # Markdown test
    md = generate_markdown_rating_sheet(sample_eval_data, meta)
    assert "[ ]" in md, "Markdown rating sheet must contain empty [ ] checkboxes for human scoring"
    assert "Overall Path Coherence (1-5): `[ ]`" in md
    assert "Path Outcome Verdict: `[ ] SUCCESS`" in md
    assert "Why this path succeeded or failed" in md or "Failure / Success Reasons" in md

    # CSV test
    csv_text = generate_csv_rating_sheet(sample_eval_data)
    lines = csv_text.strip().splitlines()
    assert len(lines) == 4  # 1 header + 2 app rows + 1 baseline row

    header = lines[0].split(",")
    assert "interest_score_1_to_5" in header
    assert "fit_after_previous_1_to_5" in header
    assert "path_verdict_pass_fail_partial" in header

    # Ensure score columns in data rows are empty (not fabricated)
    app_row_1 = lines[1].split(",")
    # Find index of interest_score_1_to_5
    interest_idx = header.index("interest_score_1_to_5")
    assert app_row_1[interest_idx] == "", "Interest score must be left blank"


def test_sanitization_removes_secrets_and_paths():
    dirty_data = {
        "api_key": "AIzaSyFakeSecretKeyThatMustBeScrubbed123",
        "user_token": "secret_abc",
        "nested": {
            "auth_password": "mypassword",
            "safe_description": "Found at AIzaSyFakeSecretKeyThatMustBeScrubbed123 in /Users/johndoe/project/file.py",
        },
        "path_list": [
            "/home/developer/code/rabbithole",
            {"key_config": "should_be_deleted"}
        ]
    }

    clean = sanitize_data(dirty_data)

    assert "api_key" not in clean
    assert "user_token" not in clean
    assert "auth_password" not in clean["nested"]
    assert "key_config" not in clean["path_list"][1]
    assert "AIza" not in clean["nested"]["safe_description"]
    assert "[REDACTED_API_KEY]" in clean["nested"]["safe_description"]
    assert "/Users/johndoe/" not in clean["nested"]["safe_description"]
    assert "/home/developer/" not in clean["path_list"][0]


def test_eval_runner_fixture_run(repo_root, tmp_path, monkeypatch):
    fixtures_path = repo_root / "eval" / "fixtures" / "candidate_pools.json"
    assert fixtures_path.exists(), "Fixtures candidate pools must exist for offline reproducibility"

    # In unit tests, disable API key so evaluation uses fast local heuristics (sub-second execution)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    from rabbithole_app.service import RabbitholeService
    test_service = RabbitholeService(gemini_api_key="")
    test_service.filter_engine.api_key = None
    test_service.expander.api_key = None

    runner = EvaluationRunner(
        profiles_path=repo_root / "eval" / "profiles.json",
        fixtures_path=fixtures_path,
        output_dir=tmp_path / "results",
        service=test_service,
    )

    results = runner.run_evaluation(source_mode="fixture", path_length=5)

    assert results["metadata"]["source_mode"] == "fixture"
    assert results["metadata"]["total_profiles"] == 10
    assert len(results["profiles"]) == 10
    assert results["metadata"]["llm_mode"] == "no_key_heuristic"
    assert results["metadata"]["llm_execution_status"]["gemini_actually_succeeded"] is False
    assert results["metadata"]["fixture_provenance"]["dataset_role"] == "historical_evaluation_fixture"

    for p in results["profiles"]:
        assert len(p["candidate_pool"]) > 0
        assert len(p["app_path"]) > 0
        assert len(p["baseline_path"]) > 0
        assert "provenance_label" in p["retrieval_meta"]

    assert (tmp_path / "results" / "evaluation_run.json").exists()
    assert (tmp_path / "results" / "rating_sheet.md").exists()
    assert (tmp_path / "results" / "rating_sheet.csv").exists()


def test_eval_runner_reports_gemini_fallback(repo_root, tmp_path):
    fixtures_path = repo_root / "eval" / "fixtures" / "candidate_pools.json"
    from rabbithole_app.service import RabbitholeService

    test_service = RabbitholeService(gemini_api_key="fake_key_to_trigger_fallback")
    
    # Mock expander and filter_engine to raise exceptions during Gemini call, triggering fallback
    def failing_expand(*args, **kwargs):
        raise RuntimeError("Simulated Gemini API network timeout")

    def failing_eval(*args, **kwargs):
        raise RuntimeError("Simulated Gemini API rate limit error")

    test_service.expander._expand_with_gemini = failing_expand
    test_service.filter_engine._evaluate_with_gemini = failing_eval

    runner = EvaluationRunner(
        profiles_path=repo_root / "eval" / "profiles.json",
        fixtures_path=fixtures_path,
        output_dir=tmp_path / "results_fallback",
        service=test_service,
    )

    results = runner.run_evaluation(source_mode="fixture", path_length=3)

    assert results["metadata"]["llm_mode"] == "gemini_failed_fallback_to_heuristic"
    assert results["metadata"]["llm_execution_status"]["gemini_actually_succeeded"] is False
    assert results["metadata"]["llm_execution_status"]["fallback_occurred"] is True
    assert "Simulated Gemini" in str(results["metadata"]["llm_execution_status"]["filter_engine_error"])

