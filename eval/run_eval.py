#!/usr/bin/env python3
"""
CLI Runner for the Rabbithole Viewing Path Evaluation Workflow.

Usage:
    uv run python eval/run_eval.py [--source {auto,live,fixture}] [--save-fixtures] [--length 5]

Examples:
    # Run using offline fixtures (fast, deterministic, zero network calls):
    uv run python eval/run_eval.py --source fixture

    # Run with live retrieval and update the fixtures with fresh candidates:
    uv run python eval/run_eval.py --source live --save-fixtures

    # Run in auto mode (tries live, falls back to fixtures if unavailable):
    uv run python eval/run_eval.py --source auto
"""
import sys
import argparse
import logging
from pathlib import Path

# Add src to sys.path so rabbithole_app is importable
repo_root = Path(__file__).resolve().parent.parent
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from eval.evaluate import EvaluationRunner
from rabbithole_app.service import RabbitholeService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("rabbithole_eval")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run reproducible evaluation workflow for Rabbithole viewing paths."
    )
    parser.add_argument(
        "--source",
        choices=["auto", "live", "fixture"],
        default="auto",
        help="Source of candidate videos: 'auto' (live with fixture fallback), 'live', or 'fixture'."
    )
    parser.add_argument(
        "--save-fixtures",
        action="store_true",
        help="Save retrieved live candidate pools into eval/fixtures/candidate_pools.json for future offline runs."
    )
    parser.add_argument(
        "--length",
        type=int,
        default=5,
        help="Number of steps in each viewing path (default: 5)."
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Custom output directory for evaluation artifacts."
    )
    parser.add_argument(
        "--profiles",
        type=Path,
        default=None,
        help="JSON file containing the profiles to evaluate (default: eval/profiles.json)."
    )
    parser.add_argument(
        "--no-gemini",
        action="store_true",
        help="Use heuristic query expansion and scoring even if a Gemini key is configured."
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else None

    service = RabbitholeService() if args.no_gemini else None
    if service is not None:
        service.expander.api_key = None
        service.filter_engine.api_key = None
    runner = EvaluationRunner(output_dir=output_dir, profiles_path=args.profiles, service=service)
    print("=" * 78)
    print("🐇 RABBITHOLE VIEWING PATH EVALUATION WORKFLOW")
    print("=" * 78)
    print(f"Candidate Source Mode: {args.source}")
    print(f"Target Path Length:    {args.length} steps")
    print(f"Profiles:              {runner.profiles_path}")
    print(f"Output Directory:      {runner.output_dir}")
    print("-" * 78)

    try:
        results = runner.run_evaluation(
            source_mode=args.source,
            path_length=args.length,
            save_new_fixtures=args.save_fixtures
        )
    except Exception as e:
        logger.exception(f"Evaluation run failed: {e}")
        sys.exit(1)

    meta = results["metadata"]
    profiles = results["profiles"]

    print("\n" + "=" * 78)
    print("EVALUATION RUN SUMMARY")
    print("=" * 78)
    print(f"Run ID:            {meta['run_id']}")
    print(f"Timestamp:         {meta['timestamp']}")
    print(f"Total Profiles:    {meta['total_profiles']}")
    print(f"Live Verified:     {meta['live_verified_profiles']}/{meta['total_profiles']}")
    print(f"Fixture Backed:    {meta['fixture_profiles']}/{meta['total_profiles']}")
    print(f"LLM Mode:          {meta['llm_mode']}")
    print(f"Algorithm Tuned:   {meta['verification_status']['algorithm_tuning_applied']} (frozen)")
    print("-" * 78)
    print(f"{'#':<3} {'Profile ID':<26} {'Category':<18} {'Pool':<6} {'Eligible':<9} {'App Path':<9} {'Baseline':<9}")
    print("-" * 78)

    for idx, p in enumerate(profiles, 1):
        prof = p["profile"]
        rmeta = p["retrieval_meta"]
        print(
            f"{idx:<3} {prof['id']:<26} {prof.get('category', ''):<18} "
            f"{rmeta['total_candidates']:<6} {rmeta['eligible_candidates']:<9} "
            f"{len(p['app_path']):<9} {len(p['baseline_path']):<9}"
        )

    print("=" * 78)
    print("GENERATED ARTIFACTS:")
    print(f"1. Machine-Readable Data:  {runner.output_dir / 'evaluation_run.json'}")
    print(f"2. Human Rating Sheet (MD): {runner.output_dir / 'rating_sheet.md'}")
    print(f"3. Human Rating Sheet (CSV):{runner.output_dir / 'rating_sheet.csv'}")
    print("=" * 78)
    print("Note: Rating sheets are generated with blank scores ready for human evaluation.\n")


if __name__ == "__main__":
    main()
