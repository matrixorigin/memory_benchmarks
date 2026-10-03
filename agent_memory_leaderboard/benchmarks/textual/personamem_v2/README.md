# PersonaMem-v2

Official public source: https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2

The text benchmark split contains 5,000 questions across 200 personas. The default
is its complete 32k history variant. Histories are downloaded separately and
materialized in `data/personamem_v2_32k.json` by `python -m
benchmarks.textual.personamem_v2.prepare`; pass `--size 128k` for the alternative
context condition. The current prepared file covers the complete 32k text split,
not the 128k or multimodal variants.

First download `benchmark/text/benchmark.csv` from the source into
`data/benchmark.csv`. Preparation fetches the CSV's official history paths.
Run `bash benchmarks/textual/personamem_v2/run.sh --all`.

Only user and assistant history messages are added. Released system messages
contain expanded gold personas and are excluded. Question preferences, related
snippets, answer options, and correct answers never enter Add. Search receives
only the user query. This adapter does not infer evidence IDs from snippet labels;
evidence recall is undefined. Native MCQ accuracy or preference-judge scoring
requires answer generation and is not computed by this retrieval-only runner.
