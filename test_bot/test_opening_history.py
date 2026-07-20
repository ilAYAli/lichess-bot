"""Tests for SQLite opening-history recording."""
import logging
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import chess

from lib import lichess_bot, opening_history
from lib.config import Configuration


PGN = """[Event "rated blitz game"]
[Site "https://lichess.org/game1234"]
[Date "2026.07.20"]
[White "KingzBot"]
[Black "EnyoV2"]
[Result "0-1"]
[UTCDate "2026.07.20"]
[UTCTime "12:34:56"]
[WhiteElo "2908"]
[BlackElo "2861"]
[WhiteTitle "BOT"]
[BlackTitle "BOT"]
[Variant "Standard"]
[TimeControl "180+1"]
[ECO "D05"]
[Opening "Queen's Pawn Game: Colle System"]
[Termination "Normal"]

1. d4 Nf6 2. e3 e6 3. Bd3 d5 4. Nf3 b6 5. O-O Bd6 6. c4 O-O 0-1
"""


def test_record_game_stores_opening_and_opponent(tmp_path: Path) -> None:
    """Store Lichess opening tags and the actual opening line."""
    database = tmp_path / "opening_history.sqlite3"

    assert opening_history.record_game(database, "game1234", PGN, "EnyoV2", opening_ply_limit=8)

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM games WHERE game_id = ?", ("game1234",)).fetchone()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert row is not None
    assert row["opponent"] == "KingzBot"
    assert row["opponent_is_bot"] == 1
    assert row["bot_color"] == "black"
    assert row["bot_rating"] == 2861
    assert row["opponent_rating"] == 2908
    assert row["result"] == "0-1"
    assert row["bot_result"] == "win"
    assert row["bot_score"] == 1.0
    assert row["termination"] == "Normal"
    assert row["rated"] == 1
    assert row["eco"] == "D05"
    assert row["opening"] == "Queen's Pawn Game: Colle System"
    assert row["opening_san"] == "d4 Nf6 e3 e6 Bd3 d5 Nf3 b6"
    assert row["opening_uci"] == "d2d4 g8f6 e2e3 e7e6 f1d3 d7d5 g1f3 b7b6"
    assert row["opening_plies"] == 8
    assert row["total_plies"] == 12
    assert row["played_at"] == "2026-07-20T12:34:56+00:00"
    assert journal_mode == "wal"


def test_games_against_opponent_are_newest_first(tmp_path: Path) -> None:
    """List only the requested opponent and variant with display metadata."""
    database = tmp_path / "opening_history.sqlite3"
    older_pgn = PGN.replace('[UTCTime "12:34:56"]', '[UTCTime "11:00:00"]')
    newer_pgn = (PGN.replace('[UTCTime "12:34:56"]', '[UTCTime "13:00:00"]')
                    .replace('[Opening "Queen\'s Pawn Game: Colle System"]',
                             '[Opening "Indian Game"]'))
    other_opponent_pgn = PGN.replace('KingzBot', 'OtherBot')
    assert opening_history.record_game(database, "older", older_pgn, "EnyoV2")
    assert opening_history.record_game(database, "newer", newer_pgn, "EnyoV2")
    assert opening_history.record_game(database, "other", other_opponent_pgn, "EnyoV2")

    records = opening_history.games_against_opponent(database, "enyov2", "kingzbot", "standard")

    assert [record.game_id for record in records] == ["newer", "older"]
    assert records[0].opening == "Indian Game"
    assert records[0].bot_result == "win"
    assert records[0].bot_color == "black"
    assert records[0].bot_rating == 2861
    assert records[0].opponent_rating == 2908


def test_moves_to_avoid_continuing_a_loss_against_same_opponent(tmp_path: Path) -> None:
    """Block only the next move of a matching losing line."""
    database = tmp_path / "opening_history.sqlite3"
    loss_pgn = PGN.replace('[Result "0-1"]', '[Result "1-0"]').replace("6. c4 O-O 0-1", "6. c4 O-O 1-0")
    assert opening_history.record_game(database, "loss1234", loss_pgn, "EnyoV2", opening_ply_limit=8)

    board = chess.Board()
    board.push_uci("d2d4")
    matches = opening_history.moves_to_avoid(database,
                                             "enyov2",
                                             "kingzbot",
                                             "black",
                                             "standard",
                                             board,
                                             avoid_loss_openings=True,
                                             avoid_draw_openings_vs_lower_rated_bots=False)

    assert matches == {chess.Move.from_uci("g8f6"): ["loss1234"]}
    assert not opening_history.moves_to_avoid(database,
                                              "EnyoV2",
                                              "KingzBot",
                                              "black",
                                              "Standard",
                                              board,
                                              avoid_loss_openings=False,
                                              avoid_draw_openings_vs_lower_rated_bots=False)

    board.push_uci("d7d5")
    assert not opening_history.moves_to_avoid(database,
                                              "EnyoV2",
                                              "KingzBot",
                                              "black",
                                              "Standard",
                                              board,
                                              avoid_loss_openings=True,
                                              avoid_draw_openings_vs_lower_rated_bots=False)


def test_moves_to_avoid_draw_against_lower_rated_bot(tmp_path: Path) -> None:
    """Avoid draws only when the recorded opponent was a lower-rated bot."""
    database = tmp_path / "opening_history.sqlite3"
    lower_rated_draw = (PGN.replace('[Result "0-1"]', '[Result "1/2-1/2"]')
                           .replace('[WhiteElo "2908"]', '[WhiteElo "2800"]')
                           .replace("6. c4 O-O 0-1", "6. c4 O-O 1/2-1/2"))
    higher_rated_draw = lower_rated_draw.replace('[WhiteElo "2800"]', '[WhiteElo "2908"]')
    lower_rated_human_draw = lower_rated_draw.replace('[WhiteTitle "BOT"]\n', "")
    assert opening_history.record_game(database, "lowerbot", lower_rated_draw, "EnyoV2", opening_ply_limit=8)
    assert opening_history.record_game(database, "higherbot", higher_rated_draw, "EnyoV2", opening_ply_limit=8)
    assert opening_history.record_game(database, "lowerhuman", lower_rated_human_draw,
                                       "EnyoV2", opening_ply_limit=8)

    board = chess.Board()
    board.push_uci("d2d4")
    matches = opening_history.moves_to_avoid(database,
                                             "EnyoV2",
                                             "KingzBot",
                                             "black",
                                             "Standard",
                                             board,
                                             avoid_loss_openings=False,
                                             avoid_draw_openings_vs_lower_rated_bots=True)

    assert matches == {chess.Move.from_uci("g8f6"): ["lowerbot"]}
    assert not opening_history.moves_to_avoid(database,
                                              "EnyoV2",
                                              "KingzBot",
                                              "black",
                                              "Standard",
                                              board,
                                              avoid_loss_openings=False,
                                              avoid_draw_openings_vs_lower_rated_bots=False)


def test_record_game_is_idempotent(tmp_path: Path) -> None:
    """Update an existing game instead of creating a duplicate after a reconnect."""
    database = tmp_path / "opening_history.sqlite3"
    assert opening_history.record_game(database, "game1234", PGN, "EnyoV2")
    updated_pgn = PGN.replace("Colle System", "Updated opening label")
    assert opening_history.record_game(database, "game1234", updated_pgn, "EnyoV2")

    with sqlite3.connect(database) as connection:
        count, opening = connection.execute("SELECT COUNT(*), opening FROM games").fetchone()

    assert count == 1
    assert opening == "Queen's Pawn Game: Updated opening label"


def test_record_game_rejects_empty_or_unrelated_pgn(tmp_path: Path) -> None:
    """Do not create rows without a parseable game containing the configured bot."""
    database = tmp_path / "opening_history.sqlite3"

    assert not opening_history.record_game(database, "empty", "", "EnyoV2")
    assert not opening_history.record_game(database, "other", PGN, "DifferentBot")
    assert not database.exists()


def test_result_for_bot() -> None:
    """Interpret standard PGN results from either color."""
    assert opening_history.result_for_bot("1-0", "white") == "win"
    assert opening_history.result_for_bot("1-0", "black") == "loss"
    assert opening_history.result_for_bot("0-1", "black") == "win"
    assert opening_history.result_for_bot("1/2-1/2", "white") == "draw"
    assert opening_history.result_for_bot("*", "white") == "incomplete"
    assert opening_history.score_for_bot("win") == 1.0
    assert opening_history.score_for_bot("draw") == 0.5
    assert opening_history.score_for_bot("loss") == 0.0
    assert opening_history.score_for_bot("incomplete") is None


def test_completed_game_fetches_pgn_when_file_saving_is_disabled() -> None:
    """Opening history obtains PGN independently of save_pgn."""
    li = Mock()
    li.get_game_pgn.return_value = PGN
    config = Configuration({"save_pgn": False, "opening_history": {"enabled": True}})
    game = SimpleNamespace(id="game1234", state={"status": "mate"})

    record = lichess_bot.game_record_for_outputs(li, config, game, chess.Board(), Mock())

    assert record == PGN
    li.get_game_pgn.assert_called_once_with("game1234")


def test_incomplete_game_does_not_fetch_opening_pgn() -> None:
    """Do not export a correspondence game merely because its worker disconnected."""
    li = Mock()
    config = Configuration({"save_pgn": False, "opening_history": {"enabled": True}})
    game = SimpleNamespace(id="game1234", state={"status": "started"})

    record = lichess_bot.game_record_for_outputs(li, config, game, chess.Board(), Mock())

    assert record == ""
    li.get_game_pgn.assert_not_called()


def test_completed_game_records_before_completion_signal(tmp_path: Path, caplog) -> None:
    """Record and confirm a completed game synchronously even when PGN files are disabled."""
    database = tmp_path / "opening_history.sqlite3"
    config = Configuration({
        "save_pgn": False,
        "pgn_directory": None,
        "opening_history": {
            "enabled": True,
            "database": str(database),
            "opening_plies": 20,
        },
    })
    with caplog.at_level(logging.INFO):
        lichess_bot.save_opening_history_game("game1234", PGN, True, config, "EnyoV2")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT opponent, opening FROM games").fetchone() == (
            "KingzBot",
            "Queen's Pawn Game: Colle System",
        )
    assert f"Recorded opening history for game1234 in {database}." in caplog.text


def test_game_start_logs_opening_history(tmp_path: Path, caplog) -> None:
    """Show prior opening results locally when a game starts."""
    database = tmp_path / "opening_history.sqlite3"
    assert opening_history.record_game(database, "game1234", PGN, "EnyoV2")
    config = Configuration({
        "opening_history": {
            "enabled": True,
            "database": str(database),
        },
    })
    game = SimpleNamespace(
        me=SimpleNamespace(name="EnyoV2"),
        opponent=SimpleNamespace(name="KingzBot"),
        variant_name="Standard",
    )

    with caplog.at_level(logging.INFO):
        lichess_bot.log_opening_history(config, game)

    assert "Opening history against KingzBot (1 prior Standard game, newest first):" in caplog.text
    assert ("WIN as Black: D05 Queen's Pawn Game: Colle System; "
            "ratings EnyoV2 2861, KingzBot 2908; "
            "https://lichess.org/game1234") in caplog.text


def test_completed_record_is_queued_before_game_done() -> None:
    """Do not let shutdown place its recorder sentinel ahead of the completed game."""
    calls: list[tuple[str, object]] = []

    class RecordingQueue:
        def __init__(self, name: str) -> None:
            self.name = name

        def put_nowait(self, item: object) -> None:
            calls.append((self.name, item))

    game = SimpleNamespace(
        id="game1234",
        state={"status": "draw"},
        url=lambda: "https://lichess.org/game1234",
    )
    lichess_bot.final_queue_entries(RecordingQueue("control"),
                                    Mock(),
                                    game,
                                    False,
                                    PGN,
                                    RecordingQueue("pgn"))

    assert [queue_name for queue_name, _item in calls] == ["pgn", "control"]
