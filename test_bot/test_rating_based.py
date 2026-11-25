"""Test opening book move selection."""
from unittest.mock import Mock

from pathlib import Path

import chess
import chess.polyglot

from lib.config import Configuration
from lib.engine_wrapper import get_book_move


def game_with(me: int | None, opponent: int | None) -> Mock:
    """Create a game with the given ratings."""
    game = Mock()
    game.id = "test"
    game.me.rating = me
    game.opponent.rating = opponent
    return game


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
