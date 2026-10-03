"""PersonaMem-v2 text histories; exclude profile, preference, and answer labels."""

import ast

DEFAULT_PATH = "benchmarks/textual/personamem_v2/data/personamem_v2_32k.json"
SOURCE = "https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2"
EVIDENCE_UNIT = None


def normalize(dataset):
    result = []
    for item in dataset:
        ident = str(item["persona_id"])
        turns = []
        for index, message in enumerate(item["history"]):
            # The released system message contains the expanded gold persona.
            if message["role"] not in {"user", "assistant"}:
                continue
            if not isinstance(message["content"], str):
                raise ValueError("Only the text split is supported")
            turns.append(
                {
                    "dia_id": f"{ident}:m{index}",
                    "role": message["role"],
                    "speaker": message["role"],
                    "text": message["content"],
                }
            )
        questions = []
        for index, row in enumerate(item["questions"]):
            query = row["user_query"]
            if isinstance(query, str):
                query = ast.literal_eval(query)
            questions.append(
                {
                    "id": f"{ident}:q{index}",
                    "question": query["content"],
                    "category": row["topic_query"],
                    "evidence": [],
                }
            )
        result.append(
            {
                "sample_id": ident,
                "conversation": {"session_1_date_time": "unspecified", "session_1": turns},
                "qa": questions,
            }
        )
    return result
