# Five-profile live discovery pilot

Run on October 28, 2026 with the current no-key heuristic mode. YouTube search returned candidates for all five broad profiles. The app produced five-video paths and search-order baselines for each. No videos were watched or rated for interest, depth, surprise, or transition quality.

| Broad input | Retrieved | Eligible | App path | Baseline |
| --- | ---: | ---: | ---: | ---: |
| Baseball and strategy | 19 | 12 | 5 | 5 |
| Movies and storytelling | 25 | 21 | 5 | 5 |
| Gardening and cooking | 21 | 15 | 5 | 5 |
| Music and sound | 19 | 8 | 5 | 5 |
| Birdwatching and photography | 20 | 15 | 5 | 5 |

## What the metadata shows

- The search pipeline can retrieve enough candidates for these five inputs. This does not establish video relevance or quality.
- The baseball path's **Deepen** pick is “Colleen Fitzpatrick - CSI meets Roots,” retrieved from a baseball lecture query. Its title suggests a poor match. The **Challenge** pick concerns a boycott and emails, also suggesting a poor match to baseball strategy.
- The birdwatching path starts with a video about atomic models and ends with forensic photography. Both titles suggest the search query or path selection drifted away from the user's interests.
- The music path has no `first_principles` candidate in its selected path. Its first three picks center on Pythagorean tuning, suggesting repetition rather than progression.
- Several selected videos have identical heuristic composite scores. Metadata-only scoring is too coarse to distinguish their actual educational value.

These are **title-level review flags**, not human judgments about the videos. Review the links and watch enough of each video to rate them before changing the algorithm.

## Human review

Use [rating_sheet.csv](rating_sheet.csv) or [rating_sheet.md](rating_sheet.md). Rate the app path and baseline for interest, depth, surprise, relevance, and fit after the previous video. Record why a sequence works or fails. The rating fields are blank.

## Interpretation limit

This was live retrieval, but the run did not verify each video's availability or content. The results are a snapshot of YouTube search and may change. The algorithm was not tuned from this pilot.
