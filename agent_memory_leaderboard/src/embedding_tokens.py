"""Pinned BGE-M3 tokenizer. Download at build/setup time, never during requests."""

import hashlib
import os
import tempfile
import urllib.request
from functools import cache
from pathlib import Path

from tokenizers import Tokenizer

TOKENIZER_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
TOKENIZER_SHA256 = "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08"
TOKENIZER_URL = f"https://huggingface.co/BAAI/bge-m3/resolve/{TOKENIZER_REVISION}/tokenizer.json"
DEFAULT_TOKEN_BUDGET = 7680


def tokenizer_path() -> Path:
    return Path(
        os.getenv("ADAPTER_TOKENIZER_PATH", Path(__file__).parent / "assets/tokenizer.json")
    )


@cache
def load_tokenizer() -> Tokenizer:
    try:
        data = tokenizer_path().read_bytes()
    except OSError:
        raise RuntimeError("Tokenizer asset missing; run python -m src.embedding_tokens") from None
    if hashlib.sha256(data).hexdigest() != TOKENIZER_SHA256:
        raise RuntimeError("Tokenizer asset checksum mismatch")
    tokenizer = Tokenizer.from_str(data.decode("utf-8"))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


def token_count(text: str) -> int:
    # Count the exact transmitted text, including BGE-M3's special tokens.
    return len(load_tokenizer().encode(text, add_special_tokens=True).ids)


def download_tokenizer() -> None:
    target = tokenizer_path()
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == TOKENIZER_SHA256:
        return
    with urllib.request.urlopen(TOKENIZER_URL, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != TOKENIZER_SHA256:
        raise RuntimeError("Downloaded tokenizer checksum mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        temporary.chmod(0o644)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    download_tokenizer()
    load_tokenizer()
    print("Pinned BGE-M3 tokenizer ready (checksum verified)")
