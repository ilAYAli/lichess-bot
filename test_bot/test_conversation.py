"""Test chat reactions and greeting selection."""
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from lib.conversation import ChatLine, Conversation


PLAYER_GREETING = "Greetings, OtherBot! I'm Enyo, created and trained by Petter Wahlman."
SPECTATOR_GREETING = "Greetings, Earthling! I'm Enyo, created and trained by Petter Wahlman."
STOCKFISH_GREETING = "yet-an-other rebranded Stockfish!{engine_info}"
STOCKFISH_SPECTATOR_GREETING = "yet-an-other rebranded Stockfish!{engine_info}"


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
def test_stockfish_greeting_requires_opponent_name_reply(room: str) -> None:
    """Only identify an engine when the opponent's !name reply reports it."""
    conversation = make_conversation()
    conversation.start_greeting(PLAYER_GREETING,
                                SPECTATOR_GREETING,
                                STOCKFISH_GREETING,
                                STOCKFISH_SPECTATOR_GREETING,
                                "I am Enyo")

    assert conversation.li.chat.call_args_list == [
        call("game-id", "player", "!name"),
        call("game-id", "player", "I am Enyo"),
        call("game-id", "spectator", "I am Enyo"),
    ]
    reply = ChatLine({
        "room": room,
        "username": "OtherBot",
        "text": "OtherBot running Stockfish 18 (lichess-bot v2026.6.28.2)",
    })
    conversation.react(reply)
    assert conversation.opponent_engine_type == "Stockfish 18"

    assert conversation.li.chat.call_args_list == [
        call("game-id", "player", "!name"),
        call("game-id", "player", "I am Enyo"),
        call("game-id", "spectator", "I am Enyo"),
        call("game-id", "player", "yet-an-other rebranded Stockfish! (running Stockfish 18)"),
        call("game-id", "spectator", "yet-an-other rebranded Stockfish! (running Stockfish 18)"),
    ]


def test_non_stockfish_name_reply_uses_configured_follow_up() -> None:
    """Use the reported engine name for the optional non-Stockfish follow-up."""
    conversation = make_conversation()
    conversation.start_greeting(PLAYER_GREETING, SPECTATOR_GREETING,
                                other_engine="Not Stockfish this time—but apparently my opponent is running {engine_info}.")

    reply = ChatLine({"room": "player", "username": "OtherBot",
                      "text": "OtherBot running Black Marlin 7.0 (lichess-bot v2026.6.28.2)"})
    conversation.react(reply)
    conversation.react(reply)
    assert conversation.opponent_engine_type == "Black Marlin 7.0"

    expected = "Not Stockfish this time—but apparently my opponent is running Black Marlin 7.0."
    assert conversation.li.chat.call_args_list == [
        call("game-id", "player", "!name"),
        call("game-id", "player", expected),
        call("game-id", "spectator", expected),
    ]


def test_unresponsive_bot_gets_no_engine_specific_follow_up() -> None:
    """Do not send an engine-specific follow-up when a bot does not answer !name."""
    conversation = make_conversation()
    conversation.start_greeting(PLAYER_GREETING, SPECTATOR_GREETING)

    assert conversation.li.chat.call_args_list == [
        call("game-id", "player", "!name"),
    ]




def test_genuine_engine_skips_name_query() -> None:
    """Whitelisted bots get the genuine-engine follow-up without a !name query or bio check."""
    conversation = make_conversation()
    genuine = "Not Stockfish this time. Apparently my opponent is running a genuine engine."
    conversation.start_greeting(PLAYER_GREETING, SPECTATOR_GREETING, build_greeting="I am Enyo",
                                no_engine_report="no report", genuine_engine=genuine)

    assert conversation.li.chat.call_args_list == [
        call("game-id", "player", "I am Enyo"),
        call("game-id", "spectator", "I am Enyo"),
        call("game-id", "player", genuine),
        call("game-id", "spectator", genuine),
    ]
    assert conversation.bio_wait is None


@pytest.mark.parametrize(("bio", "expected"), [
    ("Architect: @Emirus_A 🌠 (DM For Bot Membership 😎)\nCore Engine: Stockfish ✨", "Stockfish"),
    ("I run Stockfish 17.1 NNUE", "Stockfish 17.1"),
    ("Homemade engine written in Rust", ""),
])
def test_bio_lookup_detects_stockfish_anywhere(bio: str, expected: str) -> None:
    """Any mention of Stockfish in the bio counts; the version is optional."""
    conversation = make_conversation()
    conversation.li.get_public_data.return_value = {"profile": {"bio": bio}}
    result: Future[str] = Future()
    conversation.lookup_stockfish_bio(result)
    assert result.result() == expected
