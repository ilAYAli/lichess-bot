"""Test chat reactions and greeting selection."""
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

from lib.conversation import ChatLine, Conversation


PLAYER_GREETING = "Hello, opponent!"
SPECTATOR_GREETING = "Hello, spectators!"
STOCKFISH_GREETING = "Greetings, Stockfish!"


def make_conversation(*, is_bot: bool = True) -> Conversation:
    """Create a conversation with a minimal game and mocked Lichess client."""
    game = SimpleNamespace(
        id="game-id",
        username="EnyoBot",
        opponent=SimpleNamespace(is_bot=is_bot, name="OtherBot"),
        url=Mock(return_value="https://lichess.org/game-id"),
    )
    return Conversation(game, Mock(), Mock(), "test-version", [])


@pytest.mark.parametrize("room", ["player", "spectator"])
def test_live_stockfish_name_reply_sends_stockfish_greeting(room: str) -> None:
    """Greet a first-time Stockfish bot immediately after its !name reply."""
    conversation = make_conversation()
    with patch("lib.conversation.matchmaking.stockfish_block_list_contains", return_value=False):
        conversation.start_greeting(PLAYER_GREETING, SPECTATOR_GREETING, STOCKFISH_GREETING)

    conversation.li.chat.assert_called_once_with("game-id", "player", "!name")
    reply = ChatLine({
        "room": room,
        "username": "OtherBot",
        "text": "OtherBot running Stockfish 18 (lichess-bot v2026.6.28.2)",
    })
    with patch("lib.conversation.matchmaking.block_stockfish_text", return_value=True):
        conversation.react(reply)

    assert conversation.li.chat.call_args_list == [
        call("game-id", "player", "!name"),
        call("game-id", "player", STOCKFISH_GREETING),
        call("game-id", "spectator", STOCKFISH_GREETING),
    ]


def test_non_stockfish_name_reply_sends_normal_greetings() -> None:
    """Keep normal greetings when an unknown bot identifies another engine."""
    conversation = make_conversation()
    with patch("lib.conversation.matchmaking.stockfish_block_list_contains", return_value=False):
        conversation.start_greeting(PLAYER_GREETING, SPECTATOR_GREETING, STOCKFISH_GREETING)

    reply = ChatLine({"room": "player", "username": "OtherBot", "text": "OtherBot running Enyo 1.0"})
    with patch("lib.conversation.matchmaking.block_stockfish_text", return_value=False):
        conversation.react(reply)

    assert conversation.li.chat.call_args_list == [
        call("game-id", "player", "!name"),
        call("game-id", "player", PLAYER_GREETING),
        call("game-id", "spectator", SPECTATOR_GREETING),
    ]


def test_undetected_bot_falls_back_to_normal_greetings() -> None:
    """Do not call an unresponsive, undetected bot Stockfish."""
    conversation = make_conversation()
    with patch("lib.conversation.matchmaking.stockfish_block_list_contains", return_value=False):
        conversation.start_greeting(PLAYER_GREETING, SPECTATOR_GREETING, STOCKFISH_GREETING)

    conversation.finish_pending_greeting()

    assert conversation.li.chat.call_args_list == [
        call("game-id", "player", "!name"),
        call("game-id", "player", PLAYER_GREETING),
        call("game-id", "spectator", SPECTATOR_GREETING),
    ]


def test_known_stockfish_bot_gets_stockfish_greeting_once() -> None:
    """Use the Stockfish greeting immediately for a previously detected bot."""
    conversation = make_conversation()
    with patch("lib.conversation.matchmaking.stockfish_block_list_contains", return_value=True):
        conversation.start_greeting(PLAYER_GREETING, SPECTATOR_GREETING, STOCKFISH_GREETING)

    reply = ChatLine({"room": "player", "username": "OtherBot", "text": "OtherBot running Stockfish 18"})
    with patch("lib.conversation.matchmaking.block_stockfish_text", return_value=True):
        conversation.react(reply)

    assert conversation.li.chat.call_args_list == [
        call("game-id", "player", STOCKFISH_GREETING),
        call("game-id", "spectator", STOCKFISH_GREETING),
        call("game-id", "player", "!name"),
    ]
