"""Test engine wrapper stat formatting."""
import chess
import chess.engine

from lib.config import Configuration
from lib.engine_wrapper import EngineWrapper


def draw_or_resign_config(**overrides: object) -> Configuration:
    defaults = {
        "offer_draw_enabled": True,
        "offer_draw_for_egtb_zero": True,
        "offer_draw_moves": 5,
        "offer_draw_score": 25,
        "offer_draw_pieces": 12,
        "accept_draw_enabled": False,
        "resign_enabled": False,
        "resign_moves": 3,
        "resign_score": -1000,
    }
    return Configuration(defaults | overrides)


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


def test_offer_draw_or_resign_offers_on_engine_tablebase_draw() -> None:
    engine = EngineWrapper({}, draw_or_resign_config())
    board = chess.Board("8/8/8/8/8/2k5/8/KQ5r w - - 0 1")
    result = chess.engine.PlayResult(chess.Move.from_uci("b1g6"), None, {"string": "tbhit draw dtz 0"})

    engine.offer_draw_or_resign(result, board)

    assert result.draw_offered


def test_offer_draw_or_resign_respects_egtb_draw_offer_setting() -> None:
    engine = EngineWrapper({}, draw_or_resign_config(offer_draw_for_egtb_zero=False))
    board = chess.Board("8/8/8/8/8/2k5/8/KQ5r w - - 0 1")
    result = chess.engine.PlayResult(chess.Move.from_uci("b1g6"), None, {"string": "tbhit draw dtz 0"})

    engine.offer_draw_or_resign(result, board)

    assert not result.draw_offered
