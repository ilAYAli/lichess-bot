"""Persist completed-game opening history in SQLite."""
from __future__ import annotations

import datetime
import io
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import chess
import chess.pgn


SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    site TEXT NOT NULL,
    bot_username TEXT NOT NULL COLLATE NOCASE,
    opponent TEXT NOT NULL COLLATE NOCASE,
    opponent_is_bot INTEGER NOT NULL,
    bot_color TEXT NOT NULL,
    bot_rating INTEGER,
    opponent_rating INTEGER,
    result TEXT NOT NULL,
    bot_result TEXT NOT NULL,
    bot_score REAL,
    termination TEXT NOT NULL,
    rated INTEGER NOT NULL,
    variant TEXT NOT NULL,
    time_control TEXT NOT NULL,
    eco TEXT NOT NULL,
    opening TEXT NOT NULL,
    variation TEXT NOT NULL,
    opening_san TEXT NOT NULL,
    opening_uci TEXT NOT NULL,
    opening_fen TEXT NOT NULL,
    opening_plies INTEGER NOT NULL,
    moves_uci TEXT NOT NULL,
    total_plies INTEGER NOT NULL,
    played_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS games_opponent_played_at
    ON games(opponent COLLATE NOCASE, played_at DESC);
CREATE INDEX IF NOT EXISTS games_opening
    ON games(eco, opening);
"""

UPSERT = """
INSERT INTO games (
    game_id, site, bot_username, opponent, opponent_is_bot, bot_color,
    bot_rating, opponent_rating, result, bot_result, bot_score, termination, rated, variant,
    time_control, eco, opening, variation, opening_san, opening_uci,
    opening_fen, opening_plies, moves_uci, total_plies, played_at, recorded_at
) VALUES (
    :game_id, :site, :bot_username, :opponent, :opponent_is_bot, :bot_color,
    :bot_rating, :opponent_rating, :result, :bot_result, :bot_score, :termination, :rated, :variant,
    :time_control, :eco, :opening, :variation, :opening_san, :opening_uci,
    :opening_fen, :opening_plies, :moves_uci, :total_plies, :played_at, :recorded_at
)
ON CONFLICT(game_id) DO UPDATE SET
    site = excluded.site,
    bot_username = excluded.bot_username,
    opponent = excluded.opponent,
    opponent_is_bot = excluded.opponent_is_bot,
    bot_color = excluded.bot_color,
    bot_rating = excluded.bot_rating,
    opponent_rating = excluded.opponent_rating,
    result = excluded.result,
    bot_result = excluded.bot_result,
    bot_score = excluded.bot_score,
    termination = excluded.termination,
    rated = excluded.rated,
    variant = excluded.variant,
    time_control = excluded.time_control,
    eco = excluded.eco,
    opening = excluded.opening,
    variation = excluded.variation,
    opening_san = excluded.opening_san,
    opening_uci = excluded.opening_uci,
    opening_fen = excluded.opening_fen,
    opening_plies = excluded.opening_plies,
    moves_uci = excluded.moves_uci,
    total_plies = excluded.total_plies,
    played_at = excluded.played_at,
    recorded_at = excluded.recorded_at;
"""


def record_game(database: str | Path,
                game_id: str,
                pgn: str,
                bot_username: str,
                opening_ply_limit: int = 20) -> bool:
    """Insert or update one completed game; return whether the PGN identified our bot."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        return False

    row = game_row(game_id, game, bot_username, opening_ply_limit)
    if row is None:
        return False

    path = Path(database).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as connection:
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.executescript(SCHEMA)
        connection.execute(UPSERT, row)
    return True


def moves_to_avoid(database: str | Path,
                   bot_username: str,
                   opponent: str,
                   bot_color: str,
                   variant: str,
                   board: chess.Board,
                   avoid_results: Iterable[str],
                   avoid_draws_against_lower_rated_bots: bool = False) -> dict[chess.Move, list[str]]:
    """Return next moves that would continue a matching historical opening line."""
    path = Path(database).expanduser()
    results = tuple(dict.fromkeys(avoid_results))
    criteria: list[str] = []
    if results:
        placeholders = ", ".join("?" for _ in results)
        criteria.append(f"bot_result IN ({placeholders})")
    if avoid_draws_against_lower_rated_bots:
        criteria.append("""(bot_result = 'draw'
                         AND opponent_is_bot = 1
                         AND bot_rating IS NOT NULL
                         AND opponent_rating IS NOT NULL
                         AND opponent_rating < bot_rating)""")
    if not path.is_file() or not criteria:
        return {}

    query = f"""
        SELECT game_id, opening_uci
        FROM games
        WHERE bot_username = ? COLLATE NOCASE
          AND opponent = ? COLLATE NOCASE
          AND bot_color = ?
          AND variant = ? COLLATE NOCASE
          AND ({" OR ".join(criteria)})
    """
    parameters = (bot_username, opponent, bot_color, variant, *results)
    try:
        with sqlite3.connect(path, timeout=1) as connection:
            rows = connection.execute(query, parameters).fetchall()
    except sqlite3.Error:
        return {}

    played_moves = [move.uci() for move in board.move_stack]
    legal_moves = set(board.legal_moves)
    avoided: dict[chess.Move, list[str]] = {}
    for game_id, opening_uci in rows:
        historical_moves = opening_uci.split()
        if (len(historical_moves) <= len(played_moves)
                or historical_moves[:len(played_moves)] != played_moves):
            continue
        try:
            next_move = chess.Move.from_uci(historical_moves[len(played_moves)])
        except ValueError:
            continue
        if next_move in legal_moves:
            avoided.setdefault(next_move, []).append(game_id)

    return avoided


def game_row(game_id: str,
             game: chess.pgn.Game,
             bot_username: str,
             opening_ply_limit: int) -> dict[str, str | int | float | None] | None:
    """Build a database row from a parsed PGN game."""
    headers = game.headers
    white = headers.get("White", "")
    black = headers.get("Black", "")
    if white.casefold() == bot_username.casefold():
        bot_color = "white"
        opponent = black
        bot_prefix = "White"
        opponent_prefix = "Black"
    elif black.casefold() == bot_username.casefold():
        bot_color = "black"
        opponent = white
        bot_prefix = "Black"
        opponent_prefix = "White"
    else:
        return None

    moves = list(game.mainline_moves())
    opening_moves = moves[:opening_ply_limit]
    board = game.board()
    opening_san: list[str] = []
    for move in opening_moves:
        opening_san.append(board.san(move))
        board.push(move)

    result = headers.get("Result", "*")
    bot_result = result_for_bot(result, bot_color)
    return {
        "game_id": game_id,
        "site": headers.get("Site", ""),
        "bot_username": bot_username,
        "opponent": opponent,
        "opponent_is_bot": int(headers.get(f"{opponent_prefix}Title", "").upper() == "BOT"),
        "bot_color": bot_color,
        "bot_rating": integer_header(headers.get(f"{bot_prefix}Elo")),
        "opponent_rating": integer_header(headers.get(f"{opponent_prefix}Elo")),
        "result": result,
        "bot_result": bot_result,
        "bot_score": score_for_bot(bot_result),
        "termination": headers.get("Termination", ""),
        "rated": int(headers.get("Event", "").casefold().startswith("rated ")),
        "variant": headers.get("Variant", "Standard"),
        "time_control": headers.get("TimeControl", ""),
        "eco": headers.get("ECO", ""),
        "opening": headers.get("Opening", ""),
        "variation": headers.get("Variation", ""),
        "opening_san": " ".join(opening_san),
        "opening_uci": " ".join(move.uci() for move in opening_moves),
        "opening_fen": board.fen(),
        "opening_plies": len(opening_moves),
        "moves_uci": " ".join(move.uci() for move in moves),
        "total_plies": len(moves),
        "played_at": pgn_played_at(headers),
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def integer_header(value: str | None) -> int | None:
    """Parse an optional integer PGN header."""
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def result_for_bot(result: str, bot_color: str) -> str:
    """Return the result from the configured bot's perspective."""
    if result == "1/2-1/2":
        return "draw"
    if result == "1-0":
        return "win" if bot_color == "white" else "loss"
    if result == "0-1":
        return "win" if bot_color == "black" else "loss"
    return "incomplete"


def score_for_bot(bot_result: str) -> float | None:
    """Return a numeric score suitable for aggregating opening results."""
    return {"win": 1.0, "draw": 0.5, "loss": 0.0}.get(bot_result)


def pgn_played_at(headers: chess.pgn.Headers) -> str:
    """Return the PGN UTC timestamp in sortable ISO-8601 form."""
    date = headers.get("UTCDate") or headers.get("Date", "")
    time = headers.get("UTCTime", "00:00:00")
    try:
        timestamp = datetime.datetime.strptime(f"{date} {time} +0000", "%Y.%m.%d %H:%M:%S %z")
    except ValueError:
        return ""
    return timestamp.isoformat()
