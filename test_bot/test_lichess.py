"""Tests for the lichess communication."""

from collections import defaultdict
from unittest.mock import Mock, patch

import chess
from lib import lichess
from lib.timer import Timer
import logging
import os
import pytest
import requests
from requests.exceptions import ReadTimeout


def mock_lichess() -> lichess.Lichess:
    """Create a Lichess client without making the token-test request."""
    li = object.__new__(lichess.Lichess)
    li.baseUrl = "https://lichess.org/"
    li.logging_level = logging.DEBUG
    li.rate_limit_timers = defaultdict(Timer)
    li.session = requests.Session()
    return li


def test_move_submission_does_not_retry_ambiguous_timeout(caplog: pytest.LogCaptureFixture) -> None:
    """A move timeout must return control to game-state recovery immediately."""
    li = mock_lichess()
    move = chess.engine.PlayResult(chess.Move.from_uci("e2e4"), None)

    with (patch.object(li.session, "post", side_effect=ReadTimeout("move response timed out")) as post,
          caplog.at_level(logging.WARNING),
          pytest.raises(ReadTimeout)):
        li.make_move("gameid", move)

    post.assert_called_once()
    assert "Move e2e4 for game gameid was not acknowledged (ReadTimeout)" in caplog.text


def test_non_move_post_keeps_transient_retry() -> None:
    """Removing move retries must not change retries for other API actions."""
    li = mock_lichess()
    response = Mock(status_code=200)
    response.json.return_value = {}

    with patch.object(li.session, "post", side_effect=[ReadTimeout("chat response timed out"), response]) as post:
        assert li.api_post("chat", "gameid", data={"room": "player", "text": "hello"}) == {}

    assert post.call_count == 2


def test_lichess() -> None:
    """Test the lichess communication."""
    token = os.environ.get("LICHESS_BOT_TEST_TOKEN")
    if not token:
        pytest.skip("Lichess-bot test token must be set.")
    li = lichess.Lichess(token, "https://lichess.org/", "0.0.0", logging.DEBUG, 3)
    assert len(li.get_online_bots()) > 20
    profile = li.get_profile()
    profile["seenAt"] = 1700000000000
    assert profile == {"blocking": False,
                       "count": {"all": 12, "bookmark": 0, "draw": 1, "import": 0,
                                 "loss": 8, "me": 0, "playing": 0, "rated": 0, "win": 3},
                       "createdAt": 1627834995597, "followable": True, "following": False, "id": "badsunfish",
                       "perfs": {"blitz": {"games": 0, "prog": 0, "prov": True, "rating": 1500, "rd": 500},
                                 "bullet": {"games": 0, "prog": 0, "prov": True, "rating": 1500, "rd": 500},
                                 "classical": {"games": 0, "prog": 0, "prov": True, "rating": 1500, "rd": 500},
                                 "correspondence": {"games": 0, "prog": 0, "prov": True, "rating": 1500, "rd": 500},
                                 "rapid": {"games": 0, "prog": 0, "prov": True, "rating": 1500, "rd": 500}},
                       "playTime": {"human": 1595, "total": 1873, "tv": 0}, "seenAt": 1700000000000, "title": "BOT",
                       "url": "https://lichess.org/@/BadSunfish", "username": "BadSunfish"}
    assert li.get_ongoing_games() == []
    assert li.is_online("NNWithSF") is False
    public_data = li.get_public_data("lichapibot")
    for key in public_data["perfs"]:
        public_data["perfs"][key]["rd"] = 0
    assert public_data == {"blocking": False, "count": {"all": 15774, "bookmark": 0, "draw": 3009,
                                                        "import": 0, "loss": 6423,
                                                        "me": 0, "playing": 0, "rated": 15121, "win": 6342},
                           "createdAt": 1524037267522, "followable": True, "following": False, "id": "lichapibot",
                           "perfs": {"blitz": {"games": 2430, "prog": 3, "prov": True, "rating": 2388, "rd": 0},
                                     "bullet": {"games": 7293, "prog": 9, "prov": True, "rating": 2298, "rd": 0},
                                     "classical": {"games": 0, "prog": 0, "prov": True, "rating": 1500, "rd": 0},
                                     "correspondence": {"games": 0, "prog": 0, "prov": True, "rating": 1500, "rd": 0},
                                     "rapid": {"games": 993, "prog": -80, "prov": True, "rating": 2363, "rd": 0}},
                           "playTime": {"total": 4111502, "tv": 1582068, "human": 534785}, "profile": {},
                           "seenAt": 1669272254317, "title": "BOT", "tosViolation": True,
                           "url": "https://lichess.org/@/lichapibot", "username": "lichapibot"}
