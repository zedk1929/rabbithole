"""
Rabbithole Viewing Path Evaluation Engine.
Evaluates the journey path algorithm against the search result order baseline
across 10 diverse curiosity profiles.
"""
import os
import re
import json
import logging
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from rabbithole_app.models import UserPreferences, CandidateVideo, FeedItem
from rabbithole_app.service import RabbitholeService
from rabbithole_app.journey import build_path
from rabbithole_app.filter_engine import FilterEngine
from eval.baseline import compute_search_order_baseline, is_candidate_eligible
from eval.rating_sheet import generate_markdown_rating_sheet, generate_csv_rating_sheet

logger = logging.getLogger("rabbithole_eval")


# Sanitization patterns to ensure no secrets or local user paths enter results
SECRET_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z-_]{35}")


def sanitize_data(obj: Any) -> Any:
    """Recursively sanitize data structures to strip credentials and personal paths."""
    if isinstance(obj, dict):
        sanitized = {}
        for k, v in obj.items():
            if any(secret_term in k.lower() for secret_term in ("key", "token", "secret", "password", "auth")):
                continue
            sanitized[k] = sanitize_data(v)
        return sanitized
    elif isinstance(obj, list):
        return [sanitize_data(item) for item in obj]
    elif isinstance(obj, str):
        # Redact any Google API key pattern
        cleaned = SECRET_KEY_PATTERN.sub("[REDACTED_API_KEY]", obj)
        # Redact absolute user home directory paths
        cleaned = re.sub(r"/(?:Users|home)/[^/]+/", "./", cleaned)
        return cleaned
    return obj


class EvaluationRunner:
    def __init__(
        self,
        profiles_path: Optional[Path] = None,
        fixtures_path: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        service: Optional[RabbitholeService] = None,
    ):
        base_dir = Path(__file__).resolve().parent
        self.profiles_path = profiles_path or (base_dir / "profiles.json")
        self.fixtures_path = fixtures_path or (base_dir / "fixtures" / "candidate_pools.json")
        self.output_dir = output_dir or (base_dir / "results")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.service = service or RabbitholeService()
        self.filter_engine = self.service.filter_engine

    def load_profiles(self) -> List[Dict[str, Any]]:
        """Loads and validates the 10 evaluation preference profiles."""
        if not self.profiles_path.exists():
            raise FileNotFoundError(f"Profiles file not found at {self.profiles_path}")

        with open(self.profiles_path, "r", encoding="utf-8") as f:
            profiles = json.load(f)

        validated = []
        for p in profiles:
            # Validate against UserPreferences schema
            prefs = UserPreferences(**p["preferences"])
            validated.append({
                "id": p["id"],
                "title": p["title"],
                "category": p.get("category", "unspecified"),
                "description": p.get("description", ""),
                "preferences": prefs.model_dump(),
                "_prefs_obj": prefs,
            })
        return validated

    def load_fixtures(self) -> Dict[str, List[Dict[str, Any]]]:
        """Loads saved candidate pools from fixtures if present."""
        if not self.fixtures_path.exists():
            return {}
        try:
            with open(self.fixtures_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load fixtures from {self.fixtures_path}: {e}")
            return {}

    def save_fixtures(self, fixture_data: Dict[str, List[Dict[str, Any]]]) -> None:
        """Saves candidate pools to fixtures file for offline reproducibility."""
        self.fixtures_path.parent.mkdir(parents=True, exist_ok=True)
        sanitized = sanitize_data(fixture_data)
        with open(self.fixtures_path, "w", encoding="utf-8") as f:
            json.dump(sanitized, f, indent=2)
        logger.info(f"Saved candidate pool fixtures to {self.fixtures_path}")

    def get_candidate_pool(
        self,
        profile: Dict[str, Any],
        source_mode: str = "auto",
        fixtures: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[CandidateVideo], str, bool]:
        """
        Retrieves the candidate pool for a profile.
        Returns: (candidate_list, actual_source_used, is_verified_live)
        """
        p_id = profile["id"]
        prefs: UserPreferences = profile["_prefs_obj"]

        # If fixture mode explicitly requested
        if source_mode == "fixture":
            if fixtures and p_id in fixtures:
                candidates = [CandidateVideo(**c) for c in fixtures[p_id]]
                return candidates, "fixture", False
            raise ValueError(f"No fixture candidates found for profile '{p_id}'")

        # If live or auto requested, attempt live search
        if source_mode in ("live", "auto"):
            try:
                queries = self.service.expander.expand_queries(prefs, count=5)
                candidates = self.service.retriever.retrieve_candidates(queries, max_per_query=5)
                if candidates:
                    return candidates, "live", True
                if source_mode == "live":
                    raise RuntimeError(f"Live retrieval returned 0 candidates for profile '{p_id}'")
            except Exception as e:
                logger.warning(f"Live retrieval failed for '{p_id}': {e}")
                if source_mode == "live":
                    raise

        # Auto fallback to fixture if available
        if fixtures and p_id in fixtures:
            logger.info(f"Using fixture fallback for profile '{p_id}'")
            candidates = [CandidateVideo(**c) for c in fixtures[p_id]]
            return candidates, "fixture_fallback", False

        raise RuntimeError(f"Could not retrieve candidates for profile '{p_id}' via live or fixture.")

    def run_evaluation(
        self,
        source_mode: str = "auto",
        path_length: int = 5,
        save_new_fixtures: bool = False
    ) -> Dict[str, Any]:
        """
        Runs the full evaluation workflow across all 10 profiles:
        1. Retrieves candidate pool (live or fixture).
        2. Computes the Rabbithole App Viewing Path.
        3. Computes the Search Result Order Baseline.
        4. Saves candidate pool, app path, and baseline for each profile.
        5. Generates human rating sheets (Markdown and CSV).
        """
        profiles = self.load_profiles()
        fixtures = self.load_fixtures()
        new_fixtures_accumulator = {}

        run_id = f"eval_run_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        evaluation_results = []
        live_verified_count = 0


        for profile in profiles:
            p_id = profile["id"]
            prefs: UserPreferences = profile["_prefs_obj"]

            logger.info(f"Evaluating profile '{profile['title']}' ({p_id})...")

            # 1. Candidate Pool
            candidates, actual_source, is_live = self.get_candidate_pool(profile, source_mode, fixtures)
            if is_live:
                live_verified_count += 1

            if save_new_fixtures and is_live:
                new_fixtures_accumulator[p_id] = [c.model_dump() for c in candidates]

            # Annotate candidate pool with eligibility
            annotated_candidates = []
            eligible_count = 0
            for idx, cand in enumerate(candidates):
                eligible, rejection_reason = is_candidate_eligible(cand, prefs, self.filter_engine)
                if eligible:
                    eligible_count += 1
                annotated_candidates.append({
                    "search_rank": idx + 1,
                    "video_id": cand.id,
                    "title": cand.title,
                    "channel": cand.channel,
                    "duration_seconds": cand.duration_seconds,
                    "duration_formatted": cand.duration_formatted,
                    "url": cand.url,
                    "source_query": cand.source_query,
                    "source_query_type": cand.source_query_type,
                    "description_preview": (cand.description or "")[:200],
                    "is_eligible": eligible,
                    "eligibility_rejection_reason": rejection_reason if not eligible else None,
                })

            # 2. Compute Rabbithole App Viewing Path
            # Disable live network enrichment for fixture pools so offline evaluation is sub-second and deterministic
            is_fixture_eval = actual_source in ("fixture", "fixture_fallback")
            feed_items, filtered_count = self.filter_engine.filter_and_rank(
                candidates, prefs, top_k=25, enable_enrichment=not is_fixture_eval
            )
            app_path_items = build_path(feed_items, prefs, length=path_length)

            serialized_app_path = []
            for item in app_path_items:
                v = item.video
                a = item.assessment
                serialized_app_path.append({
                    "video_id": v.id,
                    "title": v.title,
                    "channel": v.channel,
                    "duration_seconds": v.duration_seconds,
                    "duration_formatted": v.duration_formatted,
                    "url": v.url,
                    "source_query": item.source_query or v.source_query,
                    "source_query_type": v.source_query_type,
                    "path_step": item.path_step,
                    "path_reason": item.path_reason,
                    "composite_score": a.composite_score,
                    "substance_score": a.substance_score,
                    "novelty_score": a.novelty_score,
                    "clickbait_score": a.clickbait_score,
                    "connection_note": a.connection_note,
                    "key_takeaways": a.key_takeaways,
                    "branch_suggestions": a.branch_suggestions,
                })

            # 3. Compute Search Result Order Baseline
            baseline_path = compute_search_order_baseline(
                candidates,
                prefs,
                self.filter_engine,
                length=path_length
            )

            # Store per-profile result
            evaluation_results.append({
                "profile": {
                    "id": p_id,
                    "title": profile["title"],
                    "category": profile.get("category"),
                    "description": profile.get("description"),
                    "preferences": profile["preferences"],
                },
                "retrieval_meta": {
                    "source_used": actual_source,
                    "is_live_verified": is_live,
                    "provenance_label": (
                        "historical_pre_redesign_fixture"
                        if not is_live else "live_retrieval_redesigned_discovery"
                    ),
                    "provenance_note": (
                        "Historical candidate pool collected prior to discovery redesign; preserved for offline reproducibility."
                        if not is_live else "Live candidates retrieved via current discovery pipeline."
                    ),
                    "total_candidates": len(candidates),
                    "eligible_candidates": eligible_count,
                    "filtered_out_candidates": len(candidates) - eligible_count,
                },
                "candidate_pool": annotated_candidates,
                "app_path": serialized_app_path,
                "baseline_path": baseline_path,
            })

        # Save fixtures if requested and live candidates were retrieved
        if save_new_fixtures and new_fixtures_accumulator:
            self.save_fixtures(new_fixtures_accumulator)

        # Verification & Audit Metadata
        expander_mode = getattr(self.service.expander, "last_mode", "uninitialized")
        filter_mode = getattr(self.filter_engine, "last_mode", "uninitialized")

        if expander_mode == "gemini" and filter_mode == "gemini":
            llm_mode = "gemini"
            gemini_actually_succeeded = True
            fallback_occurred = False
        elif expander_mode == "gemini" or filter_mode == "gemini":
            llm_mode = "gemini_partial"
            gemini_actually_succeeded = True
            fallback_occurred = ("fallback" in expander_mode or "fallback" in filter_mode)
        elif "fallback" in expander_mode or "fallback" in filter_mode:
            llm_mode = "gemini_failed_fallback_to_heuristic"
            gemini_actually_succeeded = False
            fallback_occurred = True
        else:
            llm_mode = "no_key_heuristic"
            gemini_actually_succeeded = False
            fallback_occurred = False

        metadata = {
            "run_id": run_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source_mode": source_mode,
            "llm_mode": llm_mode,
            "llm_execution_status": {
                "query_expander_mode": expander_mode,
                "filter_engine_mode": filter_mode,
                "gemini_actually_succeeded": gemini_actually_succeeded,
                "fallback_occurred": fallback_occurred,
                "query_expander_error": getattr(self.service.expander, "last_error", None),
                "filter_engine_error": getattr(self.filter_engine, "last_error", None),
            },
            "fixture_provenance": fixtures.get("_provenance", {
                "dataset_role": "historical_evaluation_fixture",
                "description": "Pre-redesign candidate video pools preserved as static benchmark fixtures."
            }) if source_mode in ("fixture", "auto") else None,
            "total_profiles": len(profiles),
            "live_verified_profiles": live_verified_count,
            "fixture_profiles": len(profiles) - live_verified_count,
            "verification_status": {
                "live_retrieval_verified": (live_verified_count == len(profiles)),
                "partial_live_retrieval": (0 < live_verified_count < len(profiles)),
                "fixture_backed": (live_verified_count < len(profiles)),
                "gemini_execution_verified": gemini_actually_succeeded,
                "human_ratings_completed": False,
                "human_ratings_note": "Rating sheets left blank; waiting for human judge evaluation.",
                "algorithm_tuning_applied": False,
            }
        }

        complete_run = {
            "metadata": metadata,
            "profiles": evaluation_results,
        }

        # Sanitize everything before writing
        clean_run = sanitize_data(complete_run)

        # 4. Save JSON results
        json_output_path = self.output_dir / "evaluation_run.json"
        with open(json_output_path, "w", encoding="utf-8") as f:
            json.dump(clean_run, f, indent=2)
        logger.info(f"Saved evaluation run data to {json_output_path}")

        # 5. Generate and Save Markdown Rating Sheet
        md_rating_sheet = generate_markdown_rating_sheet(clean_run["profiles"], clean_run["metadata"])
        md_output_path = self.output_dir / "rating_sheet.md"
        with open(md_output_path, "w", encoding="utf-8") as f:
            f.write(md_rating_sheet)
        logger.info(f"Saved Markdown rating sheet to {md_output_path}")

        # 6. Generate and Save CSV Rating Sheet
        csv_rating_sheet = generate_csv_rating_sheet(clean_run["profiles"])
        csv_output_path = self.output_dir / "rating_sheet.csv"
        with open(csv_output_path, "w", encoding="utf-8") as f:
            f.write(csv_rating_sheet)
        logger.info(f"Saved CSV rating sheet to {csv_output_path}")

        return clean_run
