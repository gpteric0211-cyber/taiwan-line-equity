from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from evaluation.expert_response_quality_corpus import (  # noqa: E402
    EXPERT_RESPONSE_QUALITY_CORPUS,
    RATING_DIMENSIONS,
)


def test_blinded_expert_corpus_has_frozen_100_case_mix() -> None:
    corpus = list(EXPERT_RESPONSE_QUALITY_CORPUS)
    assert len(corpus) == 100
    assert Counter(row["category"] for row in corpus) == {
        "focused": 30,
        "comprehensive": 30,
        "context": 20,
        "edge": 20,
    }
    assert len({row["case_id"] for row in corpus}) == 100
    assert all(row["blind_labels"] == ["answer_a", "answer_b"] for row in corpus)
    assert all(row["human_rating_required"] is True for row in corpus)
    assert all("winner" not in row and "expected_answer" not in row for row in corpus)
    assert all(row["rating_dimensions"] == list(RATING_DIMENSIONS) for row in corpus)
    prompts = "\n".join(row["prompt"] for row in corpus)
    for required in ("星宇呢", "2317 台積電", "它明天呢", "長榮航和長榮", "自拍", "6669"):
        assert required in prompts
