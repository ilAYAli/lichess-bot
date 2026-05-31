"""Test lichess-bot."""
import yaml
import chess
import chess.engine
import threading
import os
import sys
import datetime
import time
import logging
import tempfile
from multiprocessing import Manager
from queue import Queue
from unittest.mock import Mock
from requests import Response
from requests.exceptions import HTTPError
import test_bot.lichess
from lib import config
from lib.timer import Timer, seconds
from lib.engine_wrapper import test_suffix
from lib.lichess_types import CONFIG_DICT_TYPE
if "pytest" not in sys.modules:
    sys.exit(f"The script {os.path.basename(__file__)} should only be run by pytest.")
from lib import lichess_bot
from test_bot.test_games import scholars_mate


logging_level = logging.DEBUG
testing_log_file_name = None
lichess_bot.logging_configurer(logging_level, testing_log_file_name, True)
logger = logging.getLogger(__name__)


def http_error(status_code: int) -> HTTPError:
    """Create an HTTPError with a status code."""
    response = Response()
    response.status_code = status_code
    return HTTPError(response=response)


def test_game_stream_rate_limit_is_not_final() -> None:
    """Test that game stream 429s are retried by the play-game backoff."""
    assert not lichess_bot.is_play_game_final(http_error(429))


def test_game_stream_client_error_is_final() -> None:
    """Test that ordinary client errors still stop play-game retries."""
    assert lichess_bot.is_play_game_final(http_error(404))


def test_start_game_thread_ignores_duplicate_running_game() -> None:
    """Test that duplicate gameStart events do not spawn duplicate workers."""
    active_games = {"gameid"}
    running_games = {"gameid"}
    play_game_args = lichess_bot.PlayGameArgsType()
    pool = SimpleNamespace(apply_async=Mock())

    lichess_bot.start_game_thread(active_games, running_games, "gameid", play_game_args, pool)

    pool.apply_async.assert_not_called()
    assert active_games == {"gameid"}
    assert running_games == {"gameid"}
    assert "game_id" not in play_game_args


def test_start_game_thread_allows_queued_active_game() -> None:
    """Test that accepted queued games still start once."""
    active_games = {"gameid"}
    running_games: set[str] = set()
    play_game_args = lichess_bot.PlayGameArgsType()
    pool = SimpleNamespace(apply_async=Mock())

    lichess_bot.start_game_thread(active_games, running_games, "gameid", play_game_args, pool)

    pool.apply_async.assert_called_once()
    assert active_games == {"gameid"}
    assert running_games == {"gameid"}
    assert play_game_args["game_id"] == "gameid"


def lichess_org_simulator(move_queue: Queue[chess.Move | None],
                          board_queue: Queue[chess.Board],
                          clock_queue: Queue[tuple[datetime.timedelta, datetime.timedelta, datetime.timedelta]],
                          results: Queue[bool]) -> None:
    """
    Run a mocked version of the lichess.org server to provide an opponent for a test. This opponent always plays white.

    :param opponent_path: The path to the executable of the opponent. Usually Stockfish.
    :param move_queue: An interprocess queue that supplies the moves chosen by the bot being tested.
    :param board_queue: An interprocess queue where this function sends the updated board after choosing a move.
    :param clock_queue: An interprocess queue where this function sends the updated game clock after choosing a move.
    :param results: An interprocess queue where this function sends the result of the game to the testing function.
    """
    start_time = seconds(10)
    increment = seconds(0.1)

    board = chess.Board()
    wtime = start_time
    btime = start_time

    while not board.is_game_over():
        move_timer = Timer()
        if board.turn == chess.WHITE:
            move_count = len(board.move_stack)
            engine_move = board.parse_uci(scholars_mate[move_count])
            board.push(engine_move)
            board_queue.put(board)
            clock_queue.put((wtime, btime, increment))
            if len(board.move_stack) > 1:
                wtime -= move_timer.time_since_reset()
                wtime += increment
        else:
            move_timer = Timer()
            while (bot_move := move_queue.get()) is None:
                board_queue.put(board)
                clock_queue.put((wtime, btime, increment))
                move_queue.task_done()
            board.push(bot_move)
            move_queue.task_done()
            if len(board.move_stack) > 2:
                btime -= move_timer.time_since_reset()
                btime += increment

    board_queue.put(board)
    clock_queue.put((wtime, btime, increment))
    outcome = board.outcome()
    results.put(outcome is not None and outcome.winner == chess.BLACK)


def run_bot(raw_config: CONFIG_DICT_TYPE, logging_level: int) -> bool:
    """
    Start lichess-bot test with a mocked version of the lichess.org site.

    :param raw_config: A dictionary of values to specify the engine to test. This engine will play as white.
    :param logging_level: The level of logging to use during the test. Usually logging.DEBUG.
    :param opponent_path: The path to the executable that will play the opponent. The opponent plays as black.
    """
    config.insert_default_values(raw_config)
    CONFIG = config.Configuration(raw_config)
    logger.info(lichess_bot.intro())
    manager = Manager()
    board_queue: Queue[chess.Board] = manager.Queue()
    clock_queue: Queue[tuple[datetime.timedelta, datetime.timedelta, datetime.timedelta]] = manager.Queue()
    move_queue: Queue[chess.Move | None] = manager.Queue()
    li = test_bot.lichess.Lichess(move_queue, board_queue, clock_queue)

    user_profile = li.get_profile()
    username = user_profile["username"]
    if user_profile.get("title") != "BOT":
        return False
    logger.info(f"Welcome {username}!")
    lichess_bot.disable_restart()

    results: Queue[bool] = manager.Queue()
    thr = threading.Thread(target=lichess_org_simulator, args=[move_queue, board_queue, clock_queue, results])
    thr.start()
    lichess_bot.start(li, user_profile, CONFIG, logging_level, testing_log_file_name, True, one_game=True)

    result = results.get()
    results.task_done()

    results.join()
    board_queue.join()
    clock_queue.join()
    move_queue.join()

    thr.join()

    return result


def test_uci() -> None:
    """Test lichess-bot with Stockfish (UCI)."""
    with open("./config.yml.default") as file:
        CONFIG = yaml.safe_load(file)

    with tempfile.TemporaryDirectory() as temp:
        CONFIG["token"] = ""
        CONFIG["engine"]["dir"] = "test_bot"
        CONFIG["engine"]["name"] = "uci_engine.py"
        CONFIG["engine"]["interpreter"] = sys.executable
        CONFIG["pgn_directory"] = os.path.join(temp, "uci_game_record")
        CONFIG["engine"]["uci_options"] = {}
        win = run_bot(CONFIG, logging_level)
        logger.info("Finished Testing UCI")
        assert win
        time.sleep(0.1)  # Wait for file to be written.
        assert os.path.isfile(os.path.join(CONFIG["pgn_directory"],
                                           "bo_vs_b_zzzzzzzz.pgn"))


def test_xboard() -> None:
    """Test lichess-bot with an XBoard engine."""
    with open("./config.yml.default") as file:
        CONFIG = yaml.safe_load(file)

    with tempfile.TemporaryDirectory() as temp:
        CONFIG["token"] = ""
        CONFIG["engine"]["dir"] = "test_bot"
        CONFIG["engine"]["name"] = "xboard_engine.py"
        CONFIG["engine"]["protocol"] = "xboard"
        CONFIG["engine"]["interpreter"] = sys.executable
        CONFIG["pgn_directory"] = os.path.join(temp, "lc0_game_record")
        win = run_bot(CONFIG, logging_level)
        logger.info("Finished Testing XBoard")
        assert win
        time.sleep(0.1)  # Wait for file to be written.
        assert os.path.isfile(os.path.join(CONFIG["pgn_directory"],
                                           "bo_vs_b_zzzzzzzz.pgn"))


def test_homemade() -> None:
    """Test lichess-bot with a homemade engine."""
    with open("./config.yml.default") as file:
        CONFIG = yaml.safe_load(file)

    with tempfile.TemporaryDirectory() as temp:
        CONFIG["token"] = ""
        CONFIG["engine"]["name"] = f"ScholarsMate{test_suffix}"
        CONFIG["engine"]["protocol"] = "homemade"
        CONFIG["pgn_directory"] = os.path.join(temp, "homemade_game_record")
        win = run_bot(CONFIG, logging_level)
        logger.info("Finished Testing Homemade")
        assert win
        time.sleep(0.1)  # Wait for file to be written.
        assert os.path.isfile(os.path.join(CONFIG["pgn_directory"],
                                           "bo_vs_b_zzzzzzzz.pgn"))


def test_buggy_engine() -> None:
    """Test lichess-bot with an engine that causes a timeout error within python-chess."""
    with open("./config.yml.default") as file:
        CONFIG = yaml.safe_load(file)

    with tempfile.TemporaryDirectory() as temp:
        CONFIG["token"] = ""
        CONFIG["engine"]["dir"] = "test_bot"
        CONFIG["engine"]["name"] = "buggy_engine.py"
        CONFIG["engine"]["interpreter"] = sys.executable
        CONFIG["engine"]["uci_options"] = {"go_commands": {"movetime": 100}}
        CONFIG["pgn_directory"] = os.path.join(temp, "bug_game_record")
        win = run_bot(CONFIG, logging_level)
        logger.info("Finished Testing Buggy Engine")
        assert win
        time.sleep(0.1)  # Wait for file to be written.
        assert os.path.isfile(os.path.join(CONFIG["pgn_directory"],
                                           "bo_vs_b_zzzzzzzz.pgn"))
