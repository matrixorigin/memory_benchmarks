# ScriptMem

Local Add/Search evaluation is blocked by missing original source conversations.
The [official release](https://github.com/memorax-ai/ScriptMem) contains 457
questions across four scripted works, options, gold answers, metadata, and
evaluation code. It explicitly omits original scripts for copyright reasons;
the conversation fields contain omission notices and short synthetic examples.
These placeholders cannot reproduce the benchmark and are not ingested.

The source-access request remains visible at
https://github.com/memorax-ai/ScriptMem/issues/1.
To unblock, supply authorized source conversations in the benchmark's original
schema; question-only retrieval or substituting a different script is not a run.
