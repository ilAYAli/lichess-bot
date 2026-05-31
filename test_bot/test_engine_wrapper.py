"""Test engine wrapper stat formatting."""
import chess
import chess.engine

from lib.config import Configuration
from lib.engine_wrapper import EngineWrapper


def test_get_stats_formats_tablebase_status_with_dtz() -> None:
    engine = EngineWrapper({}, Configuration({}))
    engine.move_commentary.append({"string": "tbhit win dtz 13"})

    assert engine.get_stats(for_chat=True) == ["Source: Tablebase", "Evaluation: TB win DTZ 13"]


def test_get_stats_prefers_mate_score_over_tablebase_status() -> None:
    engine = EngineWrapper({}, Configuration({}))
    score = chess.engine.PovScore(chess.engine.Mate(7), chess.WHITE)
    engine.move_commentary.append({"string": "tbhit win dtz 13", "score": score})

    assert engine.get_stats(for_chat=True) == ["Source: Tablebase", "Evaluation: #7"]


def test_get_stats_uses_engine_source_without_tablebase_status() -> None:
    engine = EngineWrapper({}, Configuration({}))
    engine.move_commentary.append({"string": "ordinary engine text"})

    assert engine.get_stats(for_chat=True) == ["Source: Engine"]
