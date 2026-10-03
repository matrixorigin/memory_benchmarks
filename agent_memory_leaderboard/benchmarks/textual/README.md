# Textual Datasets

- [LoCoMo-Refined](locomo_refined/README.md)
- [ScriptMem](scriptmem/README.md): blocked by omitted original conversations.
- [LongMemEval-Refined](longmemeval_refined/README.md): exact public release unverified.
- [LongMemEval-S](longmemeval_s/README.md)
- [CLBench](clbench/README.md)
- [PersonaMem-v2](personamem_v2/README.md): public 32k text split.
- [BEAM](beam/README.md)

Available datasets run through the same Memoria-backed Add/Search endpoints.
Evidence recall units differ, so scores stay dataset-specific. CLBench and
PersonaMem-v2 have no retrieval evidence labels; these runs check endpoint
completion and empty results. Native answer generation and judging are separate.
