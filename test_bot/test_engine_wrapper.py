"""Test engine wrapper stat formatting."""
from unittest.mock import Mock, patch

import chess
import chess.engine

from lib.config import Configuration
from lib.engine_wrapper import EngineWrapper, get_egtb_move, get_online_move


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
        "draw_score_threshold": -50,
        "draw_score_lower_rated_threshold": -250,
        "draw_score_lower_rated_min_diff": 200,
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
    engine = EngineWrapper({}, draw_or_resign_config(offer_draw_enabled=False))
    board = chess.Board("8/8/8/8/8/2k5/8/KQ5r w - - 0 1")
    result = chess.engine.PlayResult(chess.Move.from_uci("b1g6"), None, {"string": "tbhit draw dtz 0"})

    engine.offer_draw_or_resign(result, board)

    assert result.draw_offered


def test_online_tablebase_draw_does_not_require_heuristic_draw_offers() -> None:
    board = chess.Board()
    online_moves = Configuration({"online_egtb": {}})
    draw_or_resign = draw_or_resign_config(offer_draw_enabled=False)

    with patch("lib.engine_wrapper.get_online_egtb_move", return_value=("e2e4", 0, {})):
        result = get_online_move(Mock(), board, Mock(), online_moves, draw_or_resign)

    assert isinstance(result, chess.engine.PlayResult)
    assert result.draw_offered


def test_local_tablebase_draw_does_not_require_heuristic_draw_offers() -> None:
    board = chess.Board()
    tablebases = Configuration({"syzygy": {}, "gaviota": {}})
    draw_or_resign = draw_or_resign_config(offer_draw_enabled=False)

    with patch("lib.engine_wrapper.get_syzygy", return_value=(chess.Move.from_uci("e2e4"), 0)):
        result = get_egtb_move(board, Mock(), tablebases, draw_or_resign)

    assert isinstance(result, chess.engine.PlayResult)
    assert result.draw_offered


def test_offer_draw_or_resign_respects_egtb_draw_offer_setting() -> None:
    engine = EngineWrapper({}, draw_or_resign_config(offer_draw_for_egtb_zero=False))
    board = chess.Board("8/8/8/8/8/2k5/8/KQ5r w - - 0 1")
    result = chess.engine.PlayResult(chess.Move.from_uci("b1g6"), None, {"string": "tbhit draw dtz 0"})

    engine.offer_draw_or_resign(result, board)

    assert not result.draw_offered


def test_offer_draw_or_resign_offers_on_unfavorable_wdl() -> None:
    engine = EngineWrapper({}, draw_or_resign_config(offer_draw_pieces=32))
    board = chess.Board()
    result = chess.engine.PlayResult(chess.Move.from_uci("e2e4"), None, {
        "wdl": chess.engine.PovWdl(chess.engine.Wdl(wins=10, draws=900, losses=90), chess.WHITE)
    })

    engine.offer_draw_or_resign(result, board)

    assert result.draw_offered


def test_offer_draw_or_resign_does_not_offer_on_favorable_wdl() -> None:
    engine = EngineWrapper({}, draw_or_resign_config(offer_draw_pieces=32))
    board = chess.Board()
    result = chess.engine.PlayResult(chess.Move.from_uci("e2e4"), None, {
        "wdl": chess.engine.PovWdl(chess.engine.Wdl(wins=90, draws=900, losses=10), chess.WHITE)
    })

    engine.offer_draw_or_resign(result, board)

    assert not result.draw_offered


def test_offer_draw_or_resign_offers_when_move_claims_repetition() -> None:
    engine = EngineWrapper({}, draw_or_resign_config())
    board = chess.Board()
    for move in ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1"]:
        board.push_uci(move)
    result = chess.engine.PlayResult(chess.Move.from_uci("f6g8"), None, {
        "wdl": chess.engine.PovWdl(chess.engine.Wdl(wins=90, draws=900, losses=10), chess.BLACK)
    })

    engine.offer_draw_or_resign(result, board)

    assert result.draw_offered


def test_disabled_heuristic_draw_offers_do_not_claim_repetition() -> None:
    engine = EngineWrapper({}, draw_or_resign_config(offer_draw_enabled=False))
    board = chess.Board()
    for move in ["g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1"]:
        board.push_uci(move)
    result = chess.engine.PlayResult(chess.Move.from_uci("f6g8"), None, {
        "wdl": chess.engine.PovWdl(chess.engine.Wdl(wins=90, draws=900, losses=10), chess.BLACK)
    })

    engine.offer_draw_or_resign(result, board)

    assert not result.draw_offered


def accept_draw_engine(scores_cp: list[int], my_rating: int, opponent_rating: int) -> EngineWrapper:
    engine = EngineWrapper({}, draw_or_resign_config(offer_draw_enabled=False, accept_draw_enabled=True,
                                                     accept_draw_score=10, accept_draw_moves=5,
                                                     accept_draw_pieces=32, accept_draw_min_rating_diff=100))
    engine.scores = [chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE) for cp in scores_cp]
    engine.game = Mock(me=Mock(rating=my_rating), opponent=Mock(rating=opponent_rating))
    return engine


def test_accepts_offered_draw_in_dead_equal_position_against_equal_opponent() -> None:
    engine = accept_draw_engine([9, -3, 0, 5, 9], my_rating=2962, opponent_rating=2966)
    result = chess.engine.PlayResult(chess.Move.from_uci("e2e4"), None, {})
    with patch("lib.engine_wrapper.check_for_draw_offer", return_value=True):
        engine.offer_draw_or_resign(result, chess.Board())
    assert result.draw_offered


def test_declines_offered_draw_when_slightly_better_against_equal_opponent() -> None:
    engine = accept_draw_engine([9, 12, 15, 20, 30], my_rating=2962, opponent_rating=2966)
    result = chess.engine.PlayResult(chess.Move.from_uci("e2e4"), None, {})
    with patch("lib.engine_wrapper.check_for_draw_offer", return_value=True):
        engine.offer_draw_or_resign(result, chess.Board())
    assert not result.draw_offered
