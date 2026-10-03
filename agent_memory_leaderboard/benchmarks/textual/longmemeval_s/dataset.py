"""LongMemEval-S parser; excludes gold and has_answer labels from history."""

DEFAULT_PATH = "benchmarks/textual/longmemeval_s/data/longmemeval_s_cleaned.json"
SOURCE = "https://github.com/xiaowu0162/LongMemEval"
EVIDENCE_UNIT = "session"


def normalize(dataset):
    result = []
    for item in dataset:
        conversation = {}
        for index, (sid, date, turns) in enumerate(zip(
            item["haystack_session_ids"], item["haystack_dates"],
            item["haystack_sessions"], strict=True,
        ), 1):
            name = f"session_{index}"
            conversation[name + "_date_time"] = date
            conversation[name] = [
                {"dia_id": sid, "role": turn["role"], "speaker": turn["role"],
                 "text": turn["content"]}
                for turn in turns
            ]
        qid = item["question_id"]
        result.append({
            "sample_id": qid, "conversation": conversation,
            "qa": [{"id": qid, "question": item["question"],
                    "category": "abstention" if qid.endswith("_abs") else item["question_type"],
                    "evidence": [] if qid.endswith("_abs") else item["answer_session_ids"]}],
        })
    return result
