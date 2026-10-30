"""Offline tool demonstration with synthetic data and simulated adapter/model responses."""

import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from src import benchmark
from src.checkpoint import save
from src.content_diagnostics import ContentDiagnostics
from src.embedding_tokens import DEFAULT_TOKEN_BUDGET, token_policy
from src.evaluation import AnswerEvaluator, EvaluationConfig
from src.ingestion import message_parts
from src.models import Message

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("complete", "missing-evidence", "budget-exclusion", "answer-failure", "judge-failure")


class ByteEncoder:
    def encode(self, text, **kwargs):
        return text.encode("utf-8")


def run(output_dir, scenario):
    import tiktoken

    fixture = ROOT / "examples/content-diagnostics"
    args = SimpleNamespace(
        kind="locomo",
        dataset=fixture / "input.json",
        conversations=1,
        top_k=100,
        adapter_url="http://adapter.invalid",
        output=output_dir / "run.json",
        resume=False,
        mode="answer-eval",
        private_dir=output_dir / ".private",
        content_diagnostics=True,
        diagnostic_source=fixture / "source.json",
        diagnostic_max_questions=10,
        diagnostic_max_mib=8,
    )
    settings = {
        "ADAPTER_API_KEY": "offline-synthetic-adapter-credential",
        "BENCHMARK_ANSWER_MODEL": "offline-simulated-answer",
        "BENCHMARK_JUDGE_MODEL": "offline-simulated-judge",
        "BENCHMARK_ANSWER_BASE_URL": "http://answer.invalid/v1",
        "BENCHMARK_JUDGE_BASE_URL": "http://judge.invalid/v1",
        "BENCHMARK_ANSWER_API_KEY": "offline-synthetic-answer-credential",
        "BENCHMARK_JUDGE_API_KEY": "offline-synthetic-judge-credential",
        "BENCHMARK_INPUT_BUDGET": "30000",
        "BENCHMARK_OUTPUT_TOKENS": "1024",
        "BENCHMARK_TOKEN_ENCODING": "offline-byte-counter",
    }
    memories = []
    client = httpx.Client

    def respond(request):
        body = json.loads(request.content) if request.content else {}
        if request.url.host == "adapter.invalid":
            if request.url.path == "/diagnostics/render-policy":
                return httpx.Response(200, json={"policy": token_policy(DEFAULT_TOKEN_BUDGET)})
            if request.url.path == "/add":
                for message in body["messages"]:
                    for content, _ in message_parts(
                        Message.model_validate(message), token_budget=DEFAULT_TOKEN_BUDGET
                    ):
                        memories.append({"id": f"synthetic-{len(memories)}", "content": content})
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        **{k: body[k] for k in ("request_id", "user_id", "session_id")},
                    },
                )
            if request.url.path == "/search":
                return httpx.Response(
                    200, json={"data": memories[:1] if scenario == "missing-evidence" else memories}
                )
            return httpx.Response(200)
        if request.url.host == "answer.invalid":
            result = (
                " "
                if scenario == "answer-failure"
                else "Rust"
                if scenario in {"missing-evidence", "budget-exclusion"}
                else "Go"
            )
        else:
            result = (
                "invalid synthetic verdict"
                if scenario == "judge-failure"
                else json.dumps({"label": "CORRECT" if scenario == "complete" else "WRONG"})
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": result}, "finish_reason": "stop"}]}
        )

    with (
        patch.dict(os.environ, settings),
        patch.object(tiktoken, "get_encoding", return_value=ByteEncoder()),
        patch.object(
            httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(respond), **kw)
        ),
    ):
        if scenario == "budget-exclusion":
            sample = json.loads(args.dataset.read_text())[0]
            message = next(benchmark.batches(sample["conversation"]))[2][0]
            content = next(
                message_parts(Message.model_validate(message), token_budget=DEFAULT_TOKEN_BUDGET)
            )[0]
            evaluator = AnswerEvaluator(EvaluationConfig.from_env(), "locomo")
            _, _, tokens = evaluator.context(sample["qa"][0], [content])
            os.environ["BENCHMARK_INPUT_BUDGET"] = str(tokens)
        code = benchmark.run(args)
        report = json.loads(args.output.read_text())
        report["simulation"] = True
        save(args.output, report)
        root = args.private_dir / report["run_id"] / "content-diagnostics"
        state = json.loads((root / "manifest.json").read_text())
        state["simulation"] = True
        save(root / "manifest.json", state)
        diagnostics = ContentDiagnostics(root, state["policy"], report["contract"], selected=[])
        diagnostics.render(report["status"], report["summary"])
    return code, root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, default="complete")
    args = parser.parse_args()
    code, root = run(args.output_dir, args.scenario)
    print(
        json.dumps(
            {
                "simulation": True,
                "benchmark_exit_code": code,
                "local_report": str(root / "index.html"),
            }
        )
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
