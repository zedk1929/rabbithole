# Rabbithole Viewing Path Evaluation Workflow

This directory contains a reproducible, human-in-the-loop evaluation benchmark for Rabbithole's viewing path algorithm against a simple search result order baseline.

---

## 1. Objectives

1. **Benchmark Algorithm Quality**: Evaluate whether the 5-step journey path (`Start` → `Deepen` → `Bridge` → `Challenge` → `Continue`) provides better learning progression, depth, and serendipity than raw search retrieval order.
2. **Stress-Test on Broad-Interest Inputs**: Test on 10 realistic profiles where the user supplies broad interests or tastes without naming target discovery fields.
3. **Structured Human Evaluation**: Provide clear rating sheets (Markdown and CSV) leaving all scores blank so a human judge can inspect videos, rate them, and record diagnostic reasons for path success or failure.
4. **Reproducibility & Offline Independence**: Provide offline candidate fixtures (`eval/fixtures/candidate_pools.json`) clearly labeled with historical provenance so the entire benchmark can run deterministically without internet access or API quotas, while also supporting live retrieval.
5. **Data Sanitization**: Ensure zero API keys, secrets, or personal local file paths appear in saved results.

---

## 2. The 10 Preference Profiles

The benchmark includes 10 profiles defined in [`eval/profiles.json`](file:///Users/lcooney/Downloads/Rabbithole%20app/eval/profiles.json). In line with the discovery redesign, **none** of the profiles specify target exploration fields (`explore_topics: ""`):

- **6 Broad-Interest Minimal Profiles** (`p01`, `p03`, `p04`, `p06`, `p08`, `p10`): Provide **only** broad interests and a serendipity setting, leaving background knowledge, session goals, and avoided topics completely empty.
- **4 Unfamiliar-Field & Constraint Profiles** (`p02`, `p05`, `p07`, `p09`): Test optional advanced settings (specifically avoided topics such as sensationalist tropes or commercial gimmicks) to verify that the pipeline strictly honors avoidance constraints.

| Profile ID | Title | Category | Input Configuration | Description / Stated Curiosity |
| :--- | :--- | :--- | :--- | :--- |
| `p01_infrastructure` | Everyday Municipal Infrastructure | Broad Interest | Broad Interest + Serendipity (3/5) only | Water distribution networks, wastewater treatment, and electric power grids. |
| `p02_slime_molds` | Slime Mold Optimization & Biological Computing | Unfamiliar Field | Broad Interest + Serendipity (4/5) + Avoided Topics | *Physarum polycephalum* plasmodial routing and non-neural cellular computation; avoids foraging/supplements. |
| `p03_instrument_physics` | Acoustics of Musical Instruments | Broad Interest | Broad Interest + Serendipity (2/5) only | Physics of resonance chambers, standing waves, and material timbre in acoustic instruments. |
| `p04_fluid_dynamics` | Intuitive Fluid Dynamics & Turbulence | Broad Interest | Broad Interest + Serendipity (3/5) only | Visual grasp of vortex dynamics, atmospheric turbulence, and boundary layer transition. |
| `p05_ancient_metallurgy` | Ancient Metallurgy & Bronze Casting | Unfamiliar Field | Broad Interest + Serendipity (4/5) + Avoided Topics | Bloomery iron smelting, lost-wax casting, and archaeometallurgy; avoids fantasy weapons and gimmicks. |
| `p06_spatial_memory` | Cognitive Maps, Wayfinding & Memory | Broad Interest | Broad Interest + Serendipity (4/5) only | Polynesian non-instrument navigation linked to hippocampal place/grid cells and memory architecture. |
| `p07_deep_sea_vents` | Abyssal Hydrothermal Vents | Unfamiliar Field | Broad Interest + Serendipity (5/5) + Avoided Topics | Deep-sea chemosynthetic ecosystems thriving independent of sunlight; avoids sensationalism. |
| `p08_islamic_geometry` | Aperiodic Tilings & Quasicrystals | Broad Interest | Broad Interest + Serendipity (3/5) only | Medieval girih strapwork, Penrose tilings, and crystallographic symmetry. |
| `p09_ancient_scripts` | Linguistic Decipherment of Ancient Scripts | Unfamiliar Field | Broad Interest + Serendipity (3/5) + Avoided Topics | Cryptanalytic methods and paleography used to decipher Linear B; avoids pseudo-archaeology. |
| `p10_wild_fermentation` | Microbial Ecology of Traditional Fermentation | Broad Interest | Broad Interest + Serendipity (3/5) only | Solid-state *Aspergillus oryzae* (koji) biochemistry, wild yeasts, and enzymatic proteolysis. |

---

## 3. The Two Systems Evaluated

For each profile, the workflow compares:

1. **Path A — Rabbithole Ordered Journey**:
   - Built by the journey planner (`rabbithole_app/journey.py`).
   - Uses role-based query expansion (`first_principles`, `archive_lecture`, `cross_domain_analogy`, `niche_history`, `practitioner_deep_dive`).
   - Gated by Tier 1 heuristics (minimum duration, anti-clickbait) and ranked by Tier 2 substance and novelty scoring.
   - Sequenced for progressive learning and continuity while penalizing channel repetition.
2. **Path B — Search Result Order Baseline**:
   - Orders eligible candidates strictly by their **raw search retrieval order**.
   - Filters out ineligible items (duration limits, clickbait threshold, explicit avoided topics), but applies no semantic role sequencing or substance-to-novelty optimization.

---

## 4. How to Run the Evaluation Workflow

### Five-profile live pilot

The smaller pilot uses broad inputs and forces the no-key mode, even if a Gemini key is configured. It saves a fresh candidate pool, both paths, and blank human rating sheets:

```bash
uv run python eval/run_eval.py --source live --no-gemini --profiles eval/pilot_profiles.json --output-dir eval/pilot_results
```

Review `eval/pilot_results/pilot_findings.md` for the first run's metadata inspection, then rate the actual videos in `eval/pilot_results/rating_sheet.csv` or `rating_sheet.md`. The pilot report is a snapshot; rerunning the command refreshes the result files, but does not regenerate the written findings.

From the repository root (`/Users/lcooney/Downloads/Rabbithole app`):

### Option A: Offline / Fixture Mode (Deterministic & Instant)
Uses pre-saved candidate pools in `eval/fixtures/candidate_pools.json`. Requires no network calls:
```bash
uv run python eval/run_eval.py --source fixture
```

### Option B: Live Mode (Fresh Search Retrieval)
Queries YouTube/search and Gemini live for all 10 profiles:
```bash
uv run python eval/run_eval.py --source live
```

### Option C: Auto Mode (Default)
Tries live retrieval, and automatically falls back to fixtures if network or search is throttled:
```bash
uv run python eval/run_eval.py --source auto
```

### Option D: Update Fixtures from a Live Run
```bash
uv run python eval/run_eval.py --source live --save-fixtures
```

---

## 5. Generated Artifacts & Provenance Tracking

Each evaluation run writes artifacts to [`eval/results/`](file:///Users/lcooney/Downloads/Rabbithole%20app/eval/results/) (or a custom `--output-dir`):

1. **[`evaluation_run.json`](file:///Users/lcooney/Downloads/Rabbithole%20app/eval/results/evaluation_run.json)**:
   - Full machine-readable record including:
     - Run metadata with audit tracking (`llm_mode`: `gemini`, `gemini_partial`, `gemini_failed_fallback_to_heuristic`, or `no_key_heuristic`).
     - Component-level execution state for both query expansion and tier 2 evaluation.
     - Fixture provenance labels identifying historical pre-redesign candidate pools vs. live search pools.
     - Full candidate pool for each profile with eligibility flags and rejection reasons.
     - Serialized Rabbithole Path and Baseline Path.
2. **[`rating_sheet.md`](file:///Users/lcooney/Downloads/Rabbithole%20app/eval/results/rating_sheet.md)**:
   - Human rating sheet in GitHub Flavored Markdown.
   - Contains video links, durations, roles, and empty `[ ]` scoring fields.
   - Includes path-level diagnostic checkboxes and notes sections.
3. **[`rating_sheet.csv`](file:///Users/lcooney/Downloads/Rabbithole%20app/eval/results/rating_sheet.csv)**:
   - Tabular version ready for spreadsheet import (Google Sheets, Excel).
   - All score columns left blank for human scoring.

---

## 6. Human Scoring Rubric

Human judges evaluate each video on a **1 to 5 scale**:

- **Interest (1–5)**: Is the video captivating and engaging, or dry and monotonous?
- **Depth (1–5)**: Does it offer rigorous first-principles explanation, or is it a superficial recap?
- **Surprise (1–5)**: Is it a genuine discovery outside mainstream search results, or an obvious mainstream video?
- **Relevance (1–5)**: Does it directly address the profile's curiosity and learning goal?
- **Fit After Previous (1–5)**: How natural and cohesive is the conceptual transition from the preceding video? *(Marked N/A for Video 1)*.

### Path-Level Failure & Success Diagnosis
Judges check diagnostic reason codes:
- `[ ] Natural, progressive narrative bridge across concepts`
- `[ ] Surfaced authentic high-signal archival gems`
- `[ ] Successfully avoided forbidden/superficial tropes`
- `[ ] Abrupt or jarring topic leap`
- `[ ] Repetitive content (redundant videos in same hole)`
- `[ ] Overly shallow or overly academic jargon`
- `[ ] Missed core learning goal`

---

## 7. Limitations & Caveats

- **Metadata-Only Representation**: The current algorithm scores videos based on titles, descriptions, duration, and search provenance. It does not analyze full video or audio frames.
- **Candidate Fixture Provenance**: Offline candidate pools in `eval/fixtures/candidate_pools.json` represent historical pre-redesign query formulation snapshots. They provide deterministic offline reproducibility but do not reflect the new dynamic query expansion.
- **Frozen Algorithm Rule**: Ranking scores have **not** been tuned against this evaluation set. It reflects the pure baseline performance of the algorithm prior to human judgment scoring.
