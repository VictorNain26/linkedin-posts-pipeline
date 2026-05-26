"""Tests pour _load_learnings_block (agents/system.py) — injection learnings hebdo."""

import json
from datetime import datetime, timedelta


def test_returns_none_if_file_absent(tmp_data_dir):
    from agents.system import _load_learnings_block

    assert _load_learnings_block() is None


def test_returns_none_if_malformed_json(tmp_data_dir):
    from config import LEARNINGS_PATH

    LEARNINGS_PATH.parent.mkdir(exist_ok=True)
    LEARNINGS_PATH.write_text("not a json {{{", encoding="utf-8")

    from agents.system import _load_learnings_block

    assert _load_learnings_block() is None


def test_returns_none_if_no_biases(tmp_data_dir):
    """Si biases=[] (Claude n'a rien d'actionnable), on n'injecte rien."""
    from config import LEARNINGS_PATH

    LEARNINGS_PATH.parent.mkdir(exist_ok=True)
    LEARNINGS_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(),
                "summary": "test",
                "biases": [],
                "recommendations": ["something"],
            }
        ),
        encoding="utf-8",
    )

    from agents.system import _load_learnings_block

    assert _load_learnings_block() is None


def test_returns_none_if_stale(tmp_data_dir):
    """Si le learnings.json > 14j, on l'ignore (anti-drift)."""
    from config import LEARNINGS_PATH

    LEARNINGS_PATH.parent.mkdir(exist_ok=True)
    old_date = (datetime.now() - timedelta(days=20)).isoformat()
    LEARNINGS_PATH.write_text(
        json.dumps(
            {
                "generated_at": old_date,
                "summary": "stale",
                "biases": [
                    {"id": "x", "type": "formula_weight", "key": "y", "instruction": "z", "evidence": "w"}
                ],
            }
        ),
        encoding="utf-8",
    )

    from agents.system import _load_learnings_block

    assert _load_learnings_block() is None


def test_returns_block_when_valid(tmp_data_dir):
    """Cas nominal : retourne un dict {type, text, cache_control}."""
    from config import LEARNINGS_PATH

    LEARNINGS_PATH.parent.mkdir(exist_ok=True)
    LEARNINGS_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(),
                "based_on_posts": 12,
                "summary": "Performance solide cette semaine",
                "biases": [
                    {
                        "id": "b1",
                        "type": "formula_weight",
                        "key": "prospect_question",
                        "instruction": "privilégier +50%",
                        "evidence": "47 vs 22 impr",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    from agents.system import _load_learnings_block

    block = _load_learnings_block()

    assert block is not None
    assert block["type"] == "text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert "<past_learnings>" in block["text"]
    assert "prospect_question" in block["text"]
    assert "privilégier +50%" in block["text"]
    assert "Performance solide cette semaine" in block["text"]
    # Note : `evidence` n'est pas injecté dans le block (gardé pour le dashboard / email user)


def test_caps_at_5_biases(tmp_data_dir):
    """MAX 5 biases injectés même si le fichier en contient plus."""
    from config import LEARNINGS_PATH

    LEARNINGS_PATH.parent.mkdir(exist_ok=True)
    LEARNINGS_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(),
                "summary": "test cap",
                "biases": [
                    {
                        "id": f"b{i}",
                        "type": "formula_weight",
                        "key": f"k{i}",
                        "instruction": f"inst{i}",
                        "evidence": f"ev{i}",
                    }
                    for i in range(10)  # 10 biases
                ],
            }
        ),
        encoding="utf-8",
    )

    from agents.system import _load_learnings_block

    block = _load_learnings_block()
    # Compte les occurrences de '<bias type=' dans le block — devrait être ≤ 5
    assert block["text"].count("<bias type=") == 5
