# BEAM

Public source: https://github.com/mohammadtavakoli78/BEAM
Pinned revision: `b2da22eac88bb0874c64665f13457eb99835774a`.
Data license: CC BY-SA 4.0 (upstream code: MIT).

Download with `python -m benchmarks.textual.beam.download`; run with
`python -m benchmarks.textual.beam.benchmark --all` from the project root.
The downloader uses Python's standard library plus `curl` and downloads JSON
without executing upstream code or loading pickle files. Resume reuses downloaded
files; `data/manifest.json` records their SHA-256 hashes.

Coverage: all 100 published histories: 20 at 100K (called 128K in the paper),
35 at 500K, 35 at 1M, and 10 at 10M; approximately 800 MB raw chat JSON.
The 10M histories include their complete plan-wrapped messages, not the separate
plan-level copies. The default input is `data/manifest.json`; the adapter verifies
all file hashes and loads one history at a time. `data/beam.json` is also produced
as an optional combined export but is not used by the default runner.

Only original chat content, roles, message IDs, and time anchors enter Add.
Search receives only each probing question. Reference answers, rubrics, plans,
user profiles, and gold source IDs never enter Add or Search. Gold source IDs
support local message-evidence recall after retrieval, not official answer scores.
Abstention questions have no positive evidence target. Native BEAM scoring
requires answer generation and rubric judging, which this retrieval diagnostic
does not claim to reproduce.

Validation found 2,000 questions, 325,396 unique within-history message IDs,
and 1,785 questions with evidence annotations. One upstream evidence annotation
is the invalid literal `--` (`10M-6:event_ordering:0`); it is retained rather than
silently correcting the source and can never count as a retrieved evidence hit.
