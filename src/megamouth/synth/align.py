"""Alignment: deciding that two pieces of evidence talk about the same thing.

P0 uses lexical similarity plus order. Multilingual embeddings, graph position and
screen identity (ARCHITECTURE §5) plug in here later without changing callers.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

STOP = {"the", "a", "an", "to", "of", "and", "then", "it", "its", "on", "in", "into", "for", "is",
        "el", "la", "los", "las", "de", "y", "un", "una", "en", "para", "se"}
THRESHOLD = 0.55   # below this an alignment goes to the review queue
ACCEPT = 0.75      # at or above this an alignment is accepted without review


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def tokens(s: str) -> set[str]:
    return {t for t in norm(s).split() if t not in STOP and len(t) > 1}


def similarity(a: str, b: str) -> float:
    """0..1: max of token-set Jaccard-ish overlap and character sequence ratio."""
    ta, tb = tokens(a), tokens(b)
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb))) if ta and tb else 0.0
    seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(overlap * 0.9 + 0.1 * seq, seq), 3)


def align_sequences(left: list[str], right: list[str]) -> list[tuple[int, int, float]]:
    """Order-preserving alignment (Needleman–Wunsch on similarity). Returns (i, j, score)
    for pairs scoring >= THRESHOLD. Order is preserved, so a swap shows up as a gap."""
    n, m = len(left), len(right)
    sim = [[similarity(a, b) for b in right] for a in left]
    gap = 0.0
    S = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s = sim[i - 1][j - 1]
            S[i][j] = max(S[i - 1][j] + gap, S[i][j - 1] + gap,
                          S[i - 1][j - 1] + (s if s >= THRESHOLD else -1.0))
    out, i, j = [], n, m
    while i > 0 and j > 0:
        s = sim[i - 1][j - 1]
        if s >= THRESHOLD and S[i][j] == S[i - 1][j - 1] + s:
            out.append((i - 1, j - 1, s)); i -= 1; j -= 1
        elif S[i][j] == S[i - 1][j] + gap:
            i -= 1
        else:
            j -= 1
    return out[::-1]


def best_match(needle: str, hay: list[str]) -> tuple[int, float]:
    scores = [similarity(needle, h) for h in hay]
    if not scores:
        return -1, 0.0
    k = max(range(len(scores)), key=scores.__getitem__)
    return k, scores[k]
