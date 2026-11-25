"""Test rating-based engine options and opening book settings."""
from unittest.mock import Mock

from pathlib import Path

import chess
import chess.polyglot

from lib.config import Configuration
from lib.engine_wrapper import get_book_move, polyglot_config_for_game, rating_based_options


def game_with(me: int | None, opponent: int | None) -> Mock:
    """Create a game with the given ratings."""
    game = Mock()
    game.id = "test"
    game.me.rating = me
    game.opponent.rating = opponent
    return game


CONTEMPT = Configuration({"root_repetition_contempt": [{"min_diff": -10000, "value": 5},
                                                       {"min_diff": 0, "value": 24},
                                                       {"min_diff": 150, "value": 60}]})


def test_rating_based_options_pick_highest_matching_level() -> None:
    """The highest min_diff at or below the rating difference wins."""
    assert rating_based_options(CONTEMPT, game_with(3000, 3100)) == {"root_repetition_contempt": 5}
    assert rating_based_options(CONTEMPT, game_with(3000, 3000)) == {"root_repetition_contempt": 24}
    assert rating_based_options(CONTEMPT, game_with(3000, 2851)) == {"root_repetition_contempt": 24}
    assert rating_based_options(CONTEMPT, game_with(3000, 2850)) == {"root_repetition_contempt": 60}


def test_rating_based_options_skip_unknown_ratings_and_missing_config() -> None:
    """No options are set without both ratings or without config."""
    assert rating_based_options(CONTEMPT, game_with(3000, None)) == {}
    assert rating_based_options(CONTEMPT, None) == {}
    assert rating_based_options(None, game_with(3000, 2700)) == {}


POLYGLOT = Configuration({"enabled": True, "max_depth": 8, "selection": "best_move", "min_weight": 1,
                          "normalization": "none",
                          "lower_rated": {"min_diff": 150, "max_depth": 5, "selection": "weighted_random"}})


def test_polyglot_lower_rated_overrides_apply_only_below_threshold() -> None:
    """Book overrides apply only against opponents rated min_diff or more below."""
    weaker = polyglot_config_for_game(POLYGLOT, game_with(3000, 2800))
    assert (weaker.max_depth, weaker.selection, weaker.min_weight) == (5, "weighted_random", 1)

    for game in (game_with(3000, 2900), game_with(3000, 3200), game_with(None, 2800), None):
        cfg = polyglot_config_for_game(POLYGLOT, game)
        assert (cfg.max_depth, cfg.selection) == (8, "best_move")


def test_weighted_random_respects_min_weight(tmp_path: Path) -> None:
    """Weighted random selection never plays a book move below min_weight."""
    board = chess.Board()
    heavy, light = chess.Move.from_uci("e2e4"), chess.Move.from_uci("a2a3")
    book = tmp_path / "book.bin"
    key = chess.polyglot.zobrist_hash(board)
    with open(book, "wb") as f:
        for move, weight in sorted([(heavy, 100), (light, 5)], key=lambda e: e[0].to_square):
            raw = move.to_square | (move.from_square << 6)
            f.write(key.to_bytes(8, "big") + raw.to_bytes(2, "big") + weight.to_bytes(2, "big") + bytes(4))
    cfg = Configuration({"enabled": True, "max_depth": 8, "selection": "weighted_random", "min_weight": 50,
                         "normalization": "none", "book": {"standard": [str(book)]}})

    moves = {get_book_move(board, game_with(None, None), cfg).move for _ in range(50)}

    assert moves == {heavy}
