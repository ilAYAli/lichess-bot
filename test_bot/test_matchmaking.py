"""Test functions for matchmaking module."""
from unittest.mock import Mock, patch
from lib.matchmaking import (block_stockfish_profile, block_stockfish_text, game_category, Matchmaking,
                             profile_mentions_stockfish, stockfish_block_list_contains,
                             stockfish_block_list_entry, text_mentions_stockfish)
from lib.config import Configuration
from lib.lichess_types import UserProfileType
from pathlib import Path
import json
import tempfile


def test_game_category_standard_bullet() -> None:
    """Test bullet time control with config values."""
    # challenge_initial_time: 60 (1 min), challenge_increment: 1
    # 60 + 1*40 = 100 seconds < 179 = bullet
    assert game_category("standard", 60, 1, 0) == "bullet"

    # challenge_initial_time: 60, challenge_increment: 2
    # 60 + 2*40 = 140 seconds < 179 = bullet
    assert game_category("standard", 60, 2, 0) == "bullet"


def test_profile_mentions_stockfish() -> None:
    """Test that Stockfish-identifying public profiles are detected."""
    profile: UserProfileType = {
        "username": "somebot",
        "profile": {"bio": "Running Stockfish 17 via lichess-bot."},
    }

    assert profile_mentions_stockfish(profile)


def test_profile_blocks_stockfish_bots_is_not_stockfish() -> None:
    """Test that anti-Stockfish wording does not identify the bot as Stockfish."""
    profile: UserProfileType = {
        "username": "PZChessBot",
        "profile": {"bio": "BLOCKING ALL STOCKFISH BOTS. If you don't want to get blocked, don't challenge!"},
    }

    assert not profile_mentions_stockfish(profile)


def test_profile_block_and_stockfish_on_same_line_is_not_stockfish() -> None:
    """Test that any line mentioning both block and Stockfish is ignored."""
    profile: UserProfileType = {
        "username": "somebot",
        "profile": {"bio": "I block engines using stockfish and other external assistance."},
    }

    assert not profile_mentions_stockfish(profile)


def test_profile_blocks_stockfish_bots_but_also_runs_stockfish() -> None:
    """Test that rejection wording does not hide a separate Stockfish identity claim."""
    profile: UserProfileType = {
        "username": "somebot",
        "profile": {"bio": "Blocking all Stockfish bots.\nI run Stockfish 17."},
    }

    assert profile_mentions_stockfish(profile)


def test_chat_mentions_stockfish_engine_name() -> None:
    """Test that Stockfish-identifying chat replies are detected."""
    text = "Hey, I'm running Stockfish dev-20260213-77d46ff6. Type !help for a list of commands."

    assert text_mentions_stockfish(text)


def test_chat_block_and_stockfish_on_same_line_is_not_stockfish() -> None:
    """Test that anti-Stockfish chat wording does not identify the bot as Stockfish."""
    text = "BLOCKING ALL STOCKFISH BOTS. If you don't want to get blocked, don't challenge!"

    assert not text_mentions_stockfish(text)


def test_stockfish_chat_block_list_is_persistent() -> None:
    """Test that detected Stockfish chat replies are written to the persistent blocklist."""
    text = "Running Stockfish 17 via lichess-bot."
    with tempfile.TemporaryDirectory() as temp:
        block_list_path = Path(temp) / "stockfish_blocklist.jsonl"

        assert block_stockfish_text("SomeBot", text, "chat message", block_list_path)
        assert stockfish_block_list_contains("somebot", block_list_path)
        line = block_list_path.read_text(encoding="utf-8")
        assert line.startswith('{"username":')
        entry = json.loads(line)
        assert entry["username"] == "SomeBot"
        assert entry["reason"] == "mentions Stockfish"
        assert entry["source"] == "chat message"
        assert entry["field"] == "chat message"
        assert entry["matched_text"] == text


def test_stockfish_block_list_is_persistent() -> None:
    """Test that detected Stockfish bots are written to a persistent blocklist."""
    profile: UserProfileType = {
        "username": "somebot",
        "profile": {"bio": "Runs SF 17."},
    }
    with tempfile.TemporaryDirectory() as temp:
        block_list_path = Path(temp) / "stockfish_blocklist.jsonl"

        assert block_stockfish_profile("SomeBot", profile, block_list_path)
        assert stockfish_block_list_contains("somebot", block_list_path)
        entry = json.loads(block_list_path.read_text(encoding="utf-8"))
        assert entry["username"] == "SomeBot"
        assert entry["reason"] == "mentions Stockfish"
        assert entry["source"] == "public profile"
        assert entry["field"] == "bio"
        assert entry["matched_text"] == "Runs SF 17."
        assert stockfish_block_list_entry("somebot", block_list_path)["matched_text"] == "Runs SF 17."


def test_stockfish_block_list_reads_legacy_plain_text() -> None:
    """Test that the JSONL blocklist reader still reads the legacy text file."""
    with tempfile.TemporaryDirectory() as temp:
        block_list_path = Path(temp) / "stockfish_blocklist.jsonl"
        block_list_path.with_suffix(".txt").write_text("OldBot\nOtherBot # old comment\n", encoding="utf-8")

        assert stockfish_block_list_contains("oldbot", block_list_path)
        assert stockfish_block_list_contains("otherbot", block_list_path)


def test_matchmaking_uses_stockfish_block_list() -> None:
    """Test that matchmaking blocks Stockfish blocklist entries by default."""
    mock_li = Mock()
    mock_config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": False,
            "block_list": [],
            "online_block_list": [],
            "ignore_stockfish_blocklist": False,
        },
    })
    mock_user_profile: UserProfileType = {"username": "testbot", "perfs": {}}
    matchmaking = Matchmaking(mock_li, mock_config, mock_user_profile)
    matchmaking.should_accept_challenge = Mock(return_value=True)

    with patch("lib.matchmaking.stockfish_block_list_contains", return_value=True):
        assert matchmaking.in_block_list("SomeBot")


def test_matchmaking_can_ignore_stockfish_block_list() -> None:
    """Test that matchmaking can ignore Stockfish blocklist entries."""
    mock_li = Mock()
    mock_config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": False,
            "block_list": [],
            "online_block_list": [],
            "ignore_stockfish_blocklist": True,
        },
    })
    mock_user_profile: UserProfileType = {"username": "testbot", "perfs": {}}
    matchmaking = Matchmaking(mock_li, mock_config, mock_user_profile)
    matchmaking.should_accept_challenge = Mock(return_value=True)

    with patch("lib.matchmaking.stockfish_block_list_contains", return_value=True):
        assert not matchmaking.in_block_list("SomeBot")


def test_matchmaking_records_stockfish_profile_when_blocklist_is_ignored() -> None:
    """Test that ignored Stockfish filtering still records profile detections."""
    mock_li = Mock()
    mock_li.get_online_bots.return_value = [{
        "username": "SomeBot",
        "perfs": {"bullet": {"games": 1, "rating": 2000}},
    }]
    mock_li.get_public_data.return_value = {"username": "SomeBot", "profile": {"bio": "Running Stockfish 17."}}
    mock_config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": False,
            "block_list": [],
            "online_block_list": [],
            "ignore_stockfish_blocklist": True,
            "overrides": {},
            "challenge_initial_time": [60],
            "challenge_increment": [1],
            "challenge_days": [0],
            "challenge_variant": "standard",
            "challenge_mode": "rated",
            "rating_preference": "none",
            "opponent_min_rating": 0,
            "opponent_max_rating": 4000,
            "opponent_rating_difference": None,
        },
    })
    mock_user_profile: UserProfileType = {"username": "testbot", "perfs": {"bullet": {"rating": 2000}}}
    matchmaking = Matchmaking(mock_li, mock_config, mock_user_profile)

    with patch("lib.matchmaking.block_stockfish_profile", return_value=True) as block_stockfish_profile:
        assert matchmaking.choose_opponent()[0] == "SomeBot"

    block_stockfish_profile.assert_called_once()


def test_game_category_standard_blitz() -> None:
    """Test blitz time control with config values."""
    # challenge_initial_time: 180 (3 min), challenge_increment: 1
    # 180 + 1*40 = 220 seconds, 179 <= 220 < 479 = blitz
    assert game_category("standard", 180, 1, 0) == "blitz"

    # challenge_initial_time: 180, challenge_increment: 2
    # 180 + 2*40 = 260 seconds, 179 <= 260 < 479 = blitz
    assert game_category("standard", 180, 2, 0) == "blitz"


def test_game_category_standard_rapid() -> None:
    """Test rapid time control."""
    # 10 minutes + 5 seconds increment
    # 600 + 5*40 = 800 seconds, 479 <= 800 < 1499 = rapid
    assert game_category("standard", 600, 5, 0) == "rapid"

    # 15 minutes no increment
    # 900 + 0*40 = 900 seconds, 479 <= 900 < 1499 = rapid
    assert game_category("standard", 900, 0, 0) == "rapid"


def test_game_category_standard_classical() -> None:
    """Test classical time control with max config values."""
    # max_base: 1800 (30 min), max_increment: 20
    # 1800 + 20*40 = 2600 seconds >= 1499 = classical
    assert game_category("standard", 1800, 20, 0) == "classical"

    # 25 minutes no increment
    # 1500 + 0*40 = 1500 seconds >= 1499 = classical
    assert game_category("standard", 1500, 0, 0) == "classical"


def test_game_category_correspondence() -> None:
    """Test correspondence games with config values."""
    # min_days: 1
    assert game_category("standard", 0, 0, 1) == "correspondence"

    # challenge_days: 2
    assert game_category("standard", 0, 0, 2) == "correspondence"

    # max_days: 14
    assert game_category("standard", 0, 0, 14) == "correspondence"


def test_game_category_variants() -> None:
    """Test chess variants from config."""
    assert game_category("atomic", 60, 1, 0) == "atomic"
    assert game_category("chess960", 180, 2, 0) == "chess960"
    assert game_category("crazyhouse", 600, 5, 0) == "crazyhouse"
    assert game_category("horde", 60, 0, 0) == "horde"
    assert game_category("kingOfTheHill", 180, 1, 0) == "kingOfTheHill"
    assert game_category("racingKings", 600, 0, 0) == "racingKings"
    assert game_category("threeCheck", 60, 1, 0) == "threeCheck"
    assert game_category("antichess", 180, 2, 0) == "antichess"


def test_game_category_time_boundaries() -> None:
    """Test edge cases at time control boundaries."""
    # Exactly at bullet/blitz boundary
    # 179 seconds should be blitz (179 < 179 is False)
    assert game_category("standard", 179, 0, 0) == "blitz"

    # Just below boundary
    assert game_category("standard", 178, 0, 0) == "bullet"

    # Exactly at blitz/rapid boundary
    assert game_category("standard", 479, 0, 0) == "rapid"

    # Just below
    assert game_category("standard", 478, 0, 0) == "blitz"

    # Exactly at rapid/classical boundary
    assert game_category("standard", 1499, 0, 0) == "classical"

    # Just below
    assert game_category("standard", 1498, 0, 0) == "rapid"


def test_game_category_min_config_values() -> None:
    """Test minimum config values."""
    # min_base: 0, min_increment: 0
    # This is an edge case: 0 + 0*40 = 0 < 179 = bullet
    assert game_category("standard", 0, 0, 0) == "bullet"

    # min_base: 0, min_increment: 0, min_days: 1
    assert game_category("standard", 0, 0, 1) == "correspondence"


def test_game_category_correspondence_overrides_time() -> None:
    """Test that correspondence takes precedence over time controls."""
    # If both days and time controls are set, days takes precedence
    assert game_category("standard", 1800, 20, 1) == "correspondence"
    assert game_category("standard", 60, 1, 2) == "correspondence"


def test_game_category_variant_overrides_time() -> None:
    """Test that variants override time control categorization."""
    # Variants are returned regardless of time control
    # Even if time would be "classical", variant name is returned
    assert game_category("atomic", 1800, 20, 0) == "atomic"
    assert game_category("horde", 60, 1, 0) == "horde"

    # Variants override correspondence too
    assert game_category("chess960", 0, 0, 14) == "chess960"


def test_game_category_negative_values() -> None:
    """Test edge case with negative values (should not happen in practice)."""
    # Negative base time
    assert game_category("standard", -100, 5, 0) == "bullet"

    # Negative increment results in negative duration
    result = game_category("standard", 100, -10, 0)
    # 100 + (-10)*40 = -300, which is < 179, so bullet
    assert result == "bullet"


def test_game_category_realistic_scenarios() -> None:
    """Test realistic game scenarios from actual lichess games."""
    # 1+0 bullet
    assert game_category("standard", 60, 0, 0) == "bullet"

    # 2+1 bullet
    assert game_category("standard", 120, 1, 0) == "bullet"

    # 3+0 blitz
    assert game_category("standard", 180, 0, 0) == "blitz"

    # 3+2 blitz
    assert game_category("standard", 180, 2, 0) == "blitz"

    # 5+0 blitz
    assert game_category("standard", 300, 0, 0) == "blitz"

    # 5+3 blitz
    assert game_category("standard", 300, 3, 0) == "blitz"

    # 10+0 rapid
    assert game_category("standard", 600, 0, 0) == "rapid"

    # 15+5 rapid
    assert game_category("standard", 900, 5, 0) == "rapid"

    # 15+10 rapid
    assert game_category("standard", 900, 10, 0) == "rapid"

    # 30+0 classical
    assert game_category("standard", 1800, 0, 0) == "classical"

    # 30+20 classical
    assert game_category("standard", 1800, 20, 0) == "classical"


def test_get_random_config_value__returns_specific_value() -> None:
    """Test that get_random_config_value returns the config value when it's not 'random'."""
    # Create mock objects
    mock_li = Mock()
    mock_config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": False,
            "block_list": [],
            "online_block_list": [],
            "challenge_timeout": 30
        }
    })
    mock_user_profile: UserProfileType = {"username": "testbot", "perfs": {}}

    # Create matchmaking instance
    matchmaking = Matchmaking(mock_li, mock_config, mock_user_profile)

    # Create config with a specific value
    test_config = Configuration({"challenge_variant": "atomic"})

    # Test that it returns the specific value, not a random choice
    choices = ["standard", "chess960", "atomic", "horde"]
    result = matchmaking.get_random_config_value(test_config, "challenge_variant", choices)

    assert result == "atomic", f"Expected 'atomic' but got '{result}'"


def test_get_random_config_value__returns_from_choices_when_random() -> None:
    """Test that get_random_config_value returns a value from choices when config value is 'random'."""
    # Create mock objects
    mock_li = Mock()
    mock_config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": False,
            "block_list": [],
            "online_block_list": [],
            "challenge_timeout": 30
        }
    })
    mock_user_profile: UserProfileType = {"username": "testbot", "perfs": {}}

    # Create matchmaking instance
    matchmaking = Matchmaking(mock_li, mock_config, mock_user_profile)

    # Create config with "random" value
    test_config = Configuration({"challenge_mode": "random"})

    # Test that it returns one of the choices
    choices = ["casual", "rated"]
    result = matchmaking.get_random_config_value(test_config, "challenge_mode", choices)

    assert result in choices, f"Expected result to be in {choices} but got '{result}'"
