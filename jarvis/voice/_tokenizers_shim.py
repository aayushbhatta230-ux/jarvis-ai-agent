"""Pure-Python stand-in for the HuggingFace ``tokenizers`` package.

Windows Smart App Control on this machine blocks the native ``tokenizers``
DLL, and faster-whisper imports it unconditionally. Whisper only needs a
GPT-2 style byte-level BPE tokenizer (token_to_id / encode / decode), which is
small enough to implement here. ``install()`` registers this module as
``tokenizers`` only when the real package cannot be imported.
"""

from __future__ import annotations

import json
import re
import sys
import types
from dataclasses import dataclass
from functools import lru_cache

# Approximation of the GPT-2 pre-tokenizer pattern using the stdlib ``re``
# (the original relies on \p{L} / \p{N} from the third-party ``regex`` module).
_PRETOKENIZE = re.compile(
    r"""'s|'t|'re|'ve|'m|'ll|'d| ?[^\W\d_]+| ?\d+| ?[^\s\w]+|\s+(?!\S)|\s+""",
    re.UNICODE,
)


@lru_cache(maxsize=1)
def _bytes_to_unicode() -> dict[int, str]:
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(
        range(ord("®"), ord("ÿ") + 1)
    )
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, (chr(c) for c in cs), strict=True))


@dataclass
class Encoding:
    ids: list[int]
    tokens: list[str]


class Tokenizer:
    """Byte-level BPE tokenizer loaded from a ``tokenizer.json`` file."""

    def __init__(self, vocab: dict[str, int], merges: list[tuple[str, str]], added: dict[str, int]):
        self._vocab = dict(vocab)
        self._vocab.update(added)
        self._inverse = {i: t for t, i in self._vocab.items()}
        self._added = added
        self._ranks = {pair: i for i, pair in enumerate(merges)}
        self._byte_encoder = _bytes_to_unicode()
        self._byte_decoder = {v: k for k, v in self._byte_encoder.items()}
        self._cache: dict[str, list[str]] = {}
        special = sorted(added, key=len, reverse=True)
        self._special_re = re.compile("(" + "|".join(re.escape(s) for s in special) + ")") if special else None

    @classmethod
    def from_file(cls, path: str) -> Tokenizer:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        model = data["model"]
        merges: list[tuple[str, str]] = []
        for m in model.get("merges", []):
            if isinstance(m, str):
                a, b = m.split(" ", 1)
            else:
                a, b = m
            merges.append((a, b))
        added = {t["content"]: t["id"] for t in data.get("added_tokens", [])}
        return cls(model["vocab"], merges, added)

    @classmethod
    def from_str(cls, text: str) -> Tokenizer:  # pragma: no cover - API parity
        data = json.loads(text)
        model = data["model"]
        merges = [tuple(m.split(" ", 1)) if isinstance(m, str) else tuple(m) for m in model.get("merges", [])]
        added = {t["content"]: t["id"] for t in data.get("added_tokens", [])}
        return cls(model["vocab"], merges, added)  # type: ignore[arg-type]

    def token_to_id(self, token: str) -> int | None:
        return self._vocab.get(token)

    def id_to_token(self, idx: int) -> str | None:
        return self._inverse.get(idx)

    def get_vocab_size(self, with_added_tokens: bool = True) -> int:
        return len(self._vocab)

    def _bpe(self, word: str) -> list[str]:
        if word in self._cache:
            return self._cache[word]
        parts = list(word)
        while len(parts) > 1:
            best = None
            best_rank = None
            for i in range(len(parts) - 1):
                rank = self._ranks.get((parts[i], parts[i + 1]))
                if rank is not None and (best_rank is None or rank < best_rank):
                    best, best_rank = i, rank
            if best is None:
                break
            parts = parts[:best] + [parts[best] + parts[best + 1]] + parts[best + 2 :]
        self._cache[word] = parts
        return parts

    def encode(self, text: str, add_special_tokens: bool = True) -> Encoding:
        ids: list[int] = []
        tokens: list[str] = []
        chunks = self._special_re.split(text) if self._special_re else [text]
        for chunk in chunks:
            if not chunk:
                continue
            if chunk in self._added:
                ids.append(self._added[chunk])
                tokens.append(chunk)
                continue
            for piece in _PRETOKENIZE.findall(chunk):
                mapped = "".join(self._byte_encoder[b] for b in piece.encode("utf-8"))
                for tok in self._bpe(mapped):
                    idx = self._vocab.get(tok)
                    if idx is None:  # unknown merge result: fall back to single bytes
                        for ch in tok:
                            if ch in self._vocab:
                                ids.append(self._vocab[ch])
                                tokens.append(ch)
                        continue
                    ids.append(idx)
                    tokens.append(tok)
        return Encoding(ids=ids, tokens=tokens)

    def decode(self, ids: list[int], skip_special_tokens: bool = True) -> str:
        out: list[str] = []
        buf: list[str] = []

        def flush() -> None:
            if buf:
                raw = bytes(self._byte_decoder.get(c, ord("?")) for c in "".join(buf))
                out.append(raw.decode("utf-8", errors="replace"))
                buf.clear()

        for idx in ids:
            tok = self._inverse.get(int(idx))
            if tok is None:
                continue
            if tok in self._added:
                if not skip_special_tokens:
                    flush()
                    out.append(tok)
                continue
            buf.append(tok)
        flush()
        return "".join(out)


def install() -> bool:
    """Register the shim as ``tokenizers`` if the native package is unusable.

    Returns True when the shim was installed.
    """
    try:
        import tokenizers  # noqa: F401

        return False
    except Exception:
        sys.modules.pop("tokenizers", None)
    mod = types.ModuleType("tokenizers")
    mod.Tokenizer = Tokenizer  # type: ignore[attr-defined]
    mod.Encoding = Encoding  # type: ignore[attr-defined]
    sys.modules["tokenizers"] = mod
    return True


def install_av_stub() -> None:
    """faster-whisper imports PyAV for file decoding; JARVIS only passes arrays."""
    try:
        import av  # noqa: F401
    except Exception:
        sys.modules.pop("av", None)
        stub = types.ModuleType("av")
        sys.modules["av"] = stub
