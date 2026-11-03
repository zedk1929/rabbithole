import json
from unittest.mock import MagicMock, patch
from rabbithole_app.models import UserPreferences, CuriosityQueryPlan, FollowedCreator, LivingTasteProfile, TasteSignal
from rabbithole_app.query_expander import QueryExpander


def test_gemini_prompt_includes_refresh_planning_and_history():
    expander = QueryExpander(api_key="fake-test-key")

    mock_client = MagicMock()
    mock_interaction = MagicMock()
    mock_plan = CuriosityQueryPlan(
        queries=[
            {
                "query": "vintage chronometer escapement physics",
                "domain": "Horology & Mechanics",
                "rationale": "Explores mechanical physics from an archival perspective",
                "query_type": "first_principles",
            },
            {
                "query": "cryptographic mechanical enigma ciphers",
                "domain": "Cryptography & History",
                "rationale": "Explores game theory and mathematical mechanisms",
                "query_type": "cross_domain_analogy",
                "discovery_type": "adjacent",
                "discovery_rationale": "Adjacent mathematical exploration",
            },
        ]
    )
    mock_interaction.output_text = mock_plan.model_dump_json()
    mock_client.interactions.create.return_value = mock_interaction

    with patch("google.genai.Client", return_value=mock_client):
        prefs = UserPreferences(
            interests="watches and clocks",
            refresh_index=2,
            recent_queries=[
                "pendulum clock harmonic oscillation",
                "tourbillon balance wheel friction",
            ],
            recent_shown_subjects=[
                "Horology Foundations",
                "Harmonic Motion",
            ],
            recent_shown_creators=[
                "Clickspring",
                "Wristwatch Revival",
            ],
            watched_topics=["Precision Machining"],
            followed_creators=[FollowedCreator(name="AlphaPhoenix")],
            living_taste_profile=LivingTasteProfile(
                recent_signals=[
                    TasteSignal(
                        video_id="v1",
                        title="Restoring Antikythera",
                        domain="Ancient Computing",
                        channel="Clickspring",
                        action="completed",
                    )
                ]
            ),
        )

        queries = expander.expand_queries(prefs, count=2)

        assert mock_client.interactions.create.called
        call_input = mock_client.interactions.create.call_args[1]["input"]

        # 1. Assert refresh turn is explicitly guided
        assert "Turn #2" in call_input or "Feed Refresh Count: 2" in call_input

        # 2. Assert recent queries already explored are provided
        assert "pendulum clock harmonic oscillation" in call_input
        assert "tourbillon balance wheel friction" in call_input

        # 3. Assert recently shown subjects are included
        assert "Horology Foundations" in call_input or "Ancient Computing" in call_input

        # 4. Assert recently shown creators are included
        assert "Clickspring" in call_input
        assert "Wristwatch Revival" in call_input

        # 5. Assert clear instruction to explore different angles
        assert "MANDATORY REFRESH DIRECTIVE" in call_input or "Plan completely different specific angles" in call_input
        assert "not repeat" in call_input.lower()

        assert len(queries) == 2
