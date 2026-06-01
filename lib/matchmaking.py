"""Challenge other bots."""
import random
import logging
import datetime
import contextlib
import json
import re
from lib import model
from lib.timer import Timer, days, seconds, minutes, years
from collections import defaultdict
from collections.abc import Sequence
from lib.lichess import Lichess, RateLimitedError
from lib.config import Configuration
from typing import cast, TypeAlias
from lib.blocklist import OnlineBlocklist
from lib.lichess_types import UserProfileType, PerfType, EventType, FilterType, ChallengeType
from pathlib import Path
MULTIPROCESSING_LIST_TYPE: TypeAlias = Sequence[model.Challenge]

logger = logging.getLogger(__name__)
STOCKFISH_BLOCK_LIST_PATH = Path.home() / ".config" / "enyo" / "stockfish_blocklist.jsonl"
STOCKFISH_PROFILE_PATTERNS = (
    re.compile(r"\bstockfish\b", re.IGNORECASE),
    re.compile(r"\bsf\s*[-_]?\s*1[0-9]\b", re.IGNORECASE),
)
STOCKFISH_PATTERN = re.compile(r"\bstockfish\b", re.IGNORECASE)
BLOCK_PATTERN = re.compile(r"\bblock(?:ing|s|ed)?\b", re.IGNORECASE)


def stockfish_profile_text(profile: UserProfileType) -> str:
    """Return public profile text worth scanning for engine identity."""
    return "\n".join(text for _, text in stockfish_profile_fields(profile))


def stockfish_profile_fields(profile: UserProfileType) -> list[tuple[str, str]]:
    """Return public profile fields worth scanning for engine identity."""
    profile_info = profile.get("profile", {})
    fields = [
        ("username", profile.get("username", "")),
        ("bio", profile_info.get("bio", "")),
        ("firstName", profile_info.get("firstName", "")),
        ("lastName", profile_info.get("lastName", "")),
        ("links", profile_info.get("links", "")),
    ]
    return [(field, str(text)) for field, text in fields if text]


def text_mentions_stockfish(text: str) -> bool:
    """Return whether free text appears to identify as Stockfish."""
    return bool(stockfish_text_match(text))


def stockfish_text_match(text: str) -> str:
    """Return the exact text that appears to identify as Stockfish."""
    lines = [line for line in text.splitlines()
             if not (STOCKFISH_PATTERN.search(line) and BLOCK_PATTERN.search(line))]
    for line in lines:
        if any(pattern.search(line) for pattern in STOCKFISH_PROFILE_PATTERNS):
            return line.strip()
    return ""


def profile_mentions_stockfish(profile: UserProfileType) -> bool:
    """Return whether a public profile appears to identify as Stockfish."""
    return bool(stockfish_profile_match(profile)[1])


def stockfish_profile_match(profile: UserProfileType) -> tuple[str, str]:
    """Return the public profile field and text that identify as Stockfish."""
    for field, text in stockfish_profile_fields(profile):
        match = stockfish_text_match(text)
        if match:
            return field, match
    return "", ""


def read_stockfish_block_list(path: Path = STOCKFISH_BLOCK_LIST_PATH) -> set[str]:
    """Read the persistent local Stockfish-bot blocklist."""
    blocked: set[str] = set()
    for block_list_path in stockfish_block_list_paths(path):
        blocked.update(read_stockfish_block_list_file(block_list_path))
    return blocked


def stockfish_block_list_paths(path: Path = STOCKFISH_BLOCK_LIST_PATH) -> list[Path]:
    """Return the JSONL blocklist path."""
    return [path]


def read_stockfish_block_list_file(path: Path) -> set[str]:
    """Read one Stockfish-bot blocklist file."""
    try:
        return {username for line in path.read_text(encoding="utf-8").splitlines()
                if (username := stockfish_block_list_username(line))}
    except FileNotFoundError:
        return set()


def stockfish_block_list_entry_from_line(line: str) -> dict:
    """Return a JSONL blocklist entry."""
    text = line.strip()
    if not text or text.startswith("#"):
        return {}
    try:
        entry = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return entry if isinstance(entry, dict) else {}


def stockfish_block_list_entry(username: str, path: Path = STOCKFISH_BLOCK_LIST_PATH) -> dict:
    """Return the persistent blocklist entry for a username."""
    target = username.casefold()
    for block_list_path in stockfish_block_list_paths(path):
        try:
            for line in block_list_path.read_text(encoding="utf-8").splitlines():
                entry = stockfish_block_list_entry_from_line(line)
                if str(entry.get("username", "")).casefold() == target:
                    return entry
        except FileNotFoundError:
            continue
    return {}


def stockfish_block_list_username(line: str) -> str:
    """Return a normalized username from a JSONL blocklist line."""
    entry = stockfish_block_list_entry_from_line(line)
    return str(entry.get("username", "")).strip().casefold()


def stockfish_block_list_contains(username: str, path: Path = STOCKFISH_BLOCK_LIST_PATH) -> bool:
    """Return whether the username is in the persistent local Stockfish-bot blocklist."""
    return username.casefold() in read_stockfish_block_list(path)


def add_stockfish_block_list(username: str,
                             source: str,
                             field: str,
                             matched_text: str,
                             path: Path = STOCKFISH_BLOCK_LIST_PATH) -> None:
    """Persistently block a Stockfish-identifying bot."""
    blocked = read_stockfish_block_list(path)
    if username.casefold() in blocked:
        return

    entry = {
        "username": username,
        "reason": "mentions Stockfish",
        "source": source,
        "field": field,
        "matched_text": matched_text,
        "blocked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as block_list:
        block_list.write(f"{json.dumps(entry)}\n")


def block_stockfish_profile(username: str, profile: UserProfileType, path: Path = STOCKFISH_BLOCK_LIST_PATH) -> bool:
    """Persistently block a bot if its public profile identifies it as Stockfish."""
    field, matched_text = stockfish_profile_match(profile)
    if not matched_text:
        return False

    logger.warning(f"Detected Stockfish-derived bot {username}: public profile {field} mentions Stockfish.")
    add_stockfish_block_list(username, "public profile", field, matched_text, path)
    return True


def block_stockfish_text(username: str, text: str, source: str, path: Path = STOCKFISH_BLOCK_LIST_PATH) -> bool:
    """Persistently block a bot if text identifies it as Stockfish."""
    matched_text = stockfish_text_match(text)
    if not matched_text:
        return False

    logger.warning(f"Detected Stockfish-derived bot {username}: {source} mentions Stockfish.")
    add_stockfish_block_list(username, source, source, matched_text, path)
    return True


class Matchmaking:
    """Challenge other bots."""

    def __init__(self, li: Lichess, config: Configuration, user_profile: UserProfileType) -> None:
        """Initialize values needed for matchmaking."""
        self.li = li
        self.variants = list(filter(lambda variant: variant != "fromPosition", config.challenge.variants))
        self.matchmaking_cfg = config.matchmaking
        self.user_profile = user_profile
        self.last_challenge_created_delay = Timer(seconds(25))  # Challenges expire after 20 seconds.
        # Startup should be allowed to create the first challenge immediately.
        # The post-game cooldown begins when `game_done()` resets this timer.
        self.last_game_ended_delay = Timer()
        self.last_user_profile_update_time = Timer(minutes(5))
        self.min_wait_time = seconds(60)  # Wait before new challenge to avoid api rate limits.
        self.rate_limit_timer = Timer()

        # Maximum time between challenges, even if there are active games
        self.max_wait_time = minutes(10) if self.matchmaking_cfg.allow_during_games else years(10)
        self.challenge_id = ""

        # (opponent name, game aspect) --> other bot is likely to accept challenge
        # game aspect is the one the challenged bot objects to and is one of:
        #   - game speed (bullet, blitz, etc.)
        #   - variant (standard, horde, etc.)
        #   - casual/rated
        #   - empty string (if no other reason is given or self.filter_type is COARSE)
        self.challenge_type_acceptable: defaultdict[tuple[str, str], Timer] = defaultdict(Timer)
        self.challenge_filter = self.matchmaking_cfg.challenge_filter

        for name in self.matchmaking_cfg.block_list:
            self.add_to_block_list(name)

        self.online_block_list = OnlineBlocklist(self.matchmaking_cfg.online_block_list)

    def should_create_challenge(self) -> bool:
        """Whether we should create a challenge."""
        matchmaking_enabled = self.matchmaking_cfg.allow_matchmaking
        rate_limit_ok = self.rate_limit_timer.is_expired()
        time_has_passed = self.last_game_ended_delay.is_expired()
        challenge_expired = self.last_challenge_created_delay.is_expired() and self.challenge_id
        min_wait_time_passed = self.last_challenge_created_delay.time_since_reset() > self.min_wait_time
        if challenge_expired:
            self.li.cancel(self.challenge_id)
            logger.info(f"Challenge id {self.challenge_id} cancelled.")
            self.discard_challenge(self.challenge_id)
            self.show_earliest_challenge_time()
        return bool(matchmaking_enabled and rate_limit_ok and (time_has_passed or challenge_expired) and min_wait_time_passed)

    def create_challenge(self, username: str, base_time: int, increment: int, days: int, variant: str,
                         mode: str) -> str:
        """Create a challenge."""
        params: dict[str, str | int | bool] = {"rated": mode == "rated", "variant": variant}

        if days:
            params["days"] = days
        elif base_time or increment:
            params["clock.limit"] = base_time
            params["clock.increment"] = increment
        else:
            logger.error("At least one of challenge_days, challenge_initial_time, or challenge_increment "
                         "must be greater than zero in the matchmaking section of your config file.")
            return ""

        try:
            self.last_challenge_created_delay.reset()
            response = self.li.challenge(username, params)
            challenge_id = response.get("id", "")
            if not challenge_id:
                self.handle_challenge_error_response(response, username)
            return challenge_id
        except RateLimitedError as e:
            logger.warning(e)
            self.rate_limit_timer = Timer(e.timeout)
        except Exception as e:
            logger.debug(e, exc_info=e)

        logger.warning("Could not create challenge")
        self.show_earliest_challenge_time()
        return ""

    def handle_challenge_error_response(self, response: ChallengeType, username: str) -> None:
        """If a challenge fails, print the error and adjust the challenge requirements in response."""
        logger.error(response)
        if response.get("bot_is_rate_limited"):
            timeout = cast(datetime.timedelta, response.get("rate_limit_timeout"))
            self.rate_limit_timer = Timer(timeout)
        elif response.get("opponent_is_rate_limited"):
            self.add_challenge_filter(username, "", response.get("rate_limit_timeout"))
        else:
            self.add_challenge_filter(username, "")
        self.show_earliest_challenge_time()

    def perf(self) -> dict[str, PerfType]:
        """Get the bot's rating in every variant. Bullet, blitz, rapid etc. are considered different variants."""
        user_perf: dict[str, PerfType] = self.user_profile["perfs"]
        return user_perf

    def username(self) -> str:
        """Our username."""
        username: str = self.user_profile["username"]
        return username

    def update_user_profile(self) -> None:
        """Update our user profile data, to get our latest rating."""
        if self.last_user_profile_update_time.is_expired():
            self.last_user_profile_update_time.reset()
            with contextlib.suppress(Exception):
                self.user_profile = self.li.get_profile()

    def get_weights(self, online_bots: list[UserProfileType], rating_preference: str, min_rating: int, max_rating: int,
                    game_type: str) -> list[int]:
        """Get the weight for each bot. A higher weights means the bot is more likely to get challenged."""
        def rating(bot: UserProfileType) -> int:
            perfs: dict[str, PerfType] = bot.get("perfs", {})
            perf: PerfType = perfs.get(game_type, {})
            return perf.get("rating", 0)

        if rating_preference == "high":
            # A bot with max_rating rating will be twice as likely to get picked than a bot with min_rating rating.
            reduce_ratings_by = min(min_rating - (max_rating - min_rating), min_rating - 1)
            weights = [rating(bot) - reduce_ratings_by for bot in online_bots]
        elif rating_preference == "low":
            # A bot with min_rating rating will be twice as likely to get picked than a bot with max_rating rating.
            reduce_ratings_by = max(max_rating - (min_rating - max_rating), max_rating + 1)
            weights = [reduce_ratings_by - rating(bot) for bot in online_bots]
        else:
            weights = [1] * len(online_bots)
        return weights

    def choose_challenge_details(self, match_config: Configuration) -> tuple[int, int, int, str, str]:
        """Choose variant, clock, and mode for a challenge."""
        variant = self.get_random_config_value(match_config, "challenge_variant", self.variants)
        mode = self.get_random_config_value(match_config, "challenge_mode", ["casual", "rated"])

        base_time = random.choice(match_config.challenge_initial_time)
        increment = random.choice(match_config.challenge_increment)
        num_days = random.choice(match_config.challenge_days)

        play_correspondence = [bool(num_days), not bool(base_time or increment)]
        if random.choice(play_correspondence):
            base_time = 0
            increment = 0
        else:
            num_days = 0

        return base_time, increment, num_days, variant, mode

    def choose_opponent(self) -> tuple[str | None, int, int, int, str, str]:
        """Choose an opponent."""
        override_choice = random.choice(self.matchmaking_cfg.overrides.keys() + [None])
        logger.info(f"Using the {override_choice or 'default'} matchmaking configuration.")
        override = {} if override_choice is None else self.matchmaking_cfg.overrides.lookup(override_choice)
        match_config = self.matchmaking_cfg | override

        base_time, increment, num_days, variant, mode = self.choose_challenge_details(match_config)
        rating_preference = match_config.rating_preference
        game_type = game_category(variant, base_time, increment, num_days)

        min_rating = match_config.opponent_min_rating
        max_rating = match_config.opponent_max_rating
        rating_diff = match_config.opponent_rating_difference
        bot_rating = self.perf().get(game_type, {}).get("rating", 0)
        if rating_diff is not None and bot_rating > 0:
            min_rating = bot_rating - rating_diff
            max_rating = bot_rating + rating_diff
        logger.info(f"Seeking {game_type} game with opponent rating in [{min_rating}, {max_rating}] ...")

        def is_suitable_opponent(bot: UserProfileType) -> bool:
            perf = bot.get("perfs", {}).get(game_type, {})
            return (bot["username"] != self.username()
                    and not self.in_block_list(bot["username"])
                    and perf.get("games", 0) > 0
                    and min_rating <= perf.get("rating", 0) <= max_rating)

        self.online_block_list.refresh()
        online_bots = self.li.get_online_bots()
        logger.info(f"Found {len(online_bots)} online bots")
        online_bots = list(filter(is_suitable_opponent, online_bots))
        logger.info(f"Choosing from {len(online_bots)} suitable opponents")

        def ready_for_challenge(bot: UserProfileType) -> bool:
            aspects = [variant, game_type, mode] if self.challenge_filter == FilterType.FINE else []
            return all(self.should_accept_challenge(bot["username"], aspect) for aspect in aspects)

        ready_bots = list(filter(ready_for_challenge, online_bots))
        online_bots = ready_bots or online_bots
        bot_username = None
        weights = self.get_weights(online_bots, rating_preference, min_rating, max_rating, game_type)

        try:
            while online_bots:
                bot = random.choices(online_bots, weights=weights)[0]
                bot_profile = self.li.get_public_data(bot["username"])
                stockfish_profile_detected = block_stockfish_profile(bot["username"], bot_profile)
                if bot_profile.get("blocking"):
                    self.add_to_block_list(bot["username"])
                elif self.matchmaking_cfg.ignore_stockfish_blocklist or not stockfish_profile_detected:
                    bot_username = bot["username"]
                    break

                online_bots.remove(bot)
                weights = self.get_weights(online_bots, rating_preference, min_rating, max_rating, game_type)

            if not bot_username:
                logger.error("No suitable bots found to challenge.")
        except Exception:
            if online_bots:
                logger.exception("Error:")
            else:
                logger.error("No suitable bots found to challenge.")

        return bot_username, base_time, increment, num_days, variant, mode

    def challenge_username(self, username: str) -> str:
        """Create one explicit challenge to a named opponent."""
        base_time, increment, days, variant, mode = self.choose_challenge_details(self.matchmaking_cfg)
        logger.info(f"Will challenge {username} for a {variant} {mode} game.")
        challenge_id = self.create_challenge(username, base_time, increment, days, variant, mode)
        logger.info(f"Challenge id is {challenge_id or 'None'}.")
        self.challenge_id = challenge_id
        return challenge_id

    def get_random_config_value(self, config: Configuration, parameter: str, choices: list[str]) -> str:
        """Choose a random value from `choices` if the parameter value in the config is `random`."""
        value: str = config.lookup(parameter)
        return value if value != "random" else random.choice(choices)

    def challenge(self, active_games: set[str], challenge_queue: MULTIPROCESSING_LIST_TYPE, max_games: int) -> None:
        """
        Challenge an opponent.

        :param active_games: The games that the bot is playing.
        :param challenge_queue: The queue containing the challenges.
        :param max_games: The maximum allowed number of simultaneous games.
        """
        max_games_for_matchmaking = max_games if self.matchmaking_cfg.allow_during_games else min(1, max_games)
        game_count = len(active_games) + len(challenge_queue)
        if (game_count >= max_games_for_matchmaking
                or (game_count > 0 and self.last_challenge_created_delay.time_since_reset() < self.max_wait_time)
                or not self.should_create_challenge()):
            return

        logger.info("Challenging a random bot")
        self.update_user_profile()
        bot_username, base_time, increment, days, variant, mode = self.choose_opponent()
        if not bot_username:
            logger.info("No challenge will be created.")
            self.challenge_id = ""
            self.rate_limit_timer = Timer(seconds(60))
            return

        logger.info(f"Will challenge {bot_username} for a {variant} game.")
        challenge_id = self.create_challenge(bot_username, base_time, increment, days, variant, mode)
        logger.info(f"Challenge id is {challenge_id or 'None'}.")
        self.challenge_id = challenge_id

    def discard_challenge(self, challenge_id: str) -> None:
        """
        Clear the ID of the most recent challenge if it is no longer needed.

        :param challenge_id: The ID of the challenge that is expired, accepted, or declined.
        """
        if self.challenge_id == challenge_id:
            self.challenge_id = ""

    def game_done(self) -> None:
        """Reset the timer for when the last game ended, and prints the earliest that the next challenge will be created."""
        self.last_game_ended_delay.reset()
        self.show_earliest_challenge_time()

    def show_earliest_challenge_time(self) -> None:
        """Show the earliest that the next challenge will be created."""
        if self.matchmaking_cfg.allow_matchmaking:
            postgame_timeout = self.last_game_ended_delay.time_until_expiration()
            time_to_next_challenge = self.min_wait_time - self.last_challenge_created_delay.time_since_reset()
            rate_limit_delay = self.rate_limit_timer.time_until_expiration()
            time_left = max(postgame_timeout, time_to_next_challenge, rate_limit_delay)
            earliest_challenge_time = datetime.datetime.now().astimezone() + time_left
            logger.info(f"Next challenge attempt after {earliest_challenge_time.strftime('%c %Z')}")

    def add_to_block_list(self, username: str) -> None:
        """Add a bot to the blocklist."""
        self.add_challenge_filter(username, "", years(10))

    def in_block_list(self, username: str) -> bool:
        """Check if an opponent is in the block list to prevent future challenges."""
        stockfish_blocked = (not self.matchmaking_cfg.ignore_stockfish_blocklist
                             and stockfish_block_list_contains(username))
        return ((not self.should_accept_challenge(username, ""))
                or username in self.online_block_list
                or stockfish_blocked)

    def add_challenge_filter(self, username: str, game_aspect: str, timeout: datetime.timedelta | None = None) -> None:
        """
        Prevent creating another challenge for a timeout when an opponent has declined a challenge.

        :param username: The name of the opponent.
        :param game_aspect: The aspect of a game (time control, chess variant, etc.) that caused the opponent to decline a
        challenge. If the parameter is empty, that is equivalent to adding the opponent to the block list.
        :param timeout: The amount of time to not challenge an opponent. If None, the default is a day.
        """
        self.challenge_type_acceptable[(username, game_aspect)] = Timer(timeout or days(1))

    def should_accept_challenge(self, username: str, game_aspect: str) -> bool:
        """
        Whether a bot is likely to accept a challenge to a game.

        :param username: The name of the opponent.
        :param game_aspect: A category of the challenge type (time control, chess variant, etc.) to test for acceptance.
        If game_aspect is empty, this is equivalent to checking if the opponent is in the block list.
        """
        return self.challenge_type_acceptable[(username, game_aspect)].is_expired()

    def accepted_challenge(self, event: EventType) -> None:
        """
        Set the challenge id to an empty string, if the challenge was accepted.

        Otherwise, we would attempt to cancel the challenge later.
        """
        self.discard_challenge(event["game"]["id"])

    def declined_challenge(self, event: EventType) -> None:
        """
        Handle a challenge that was declined by the opponent.

        Depends on whether `FilterType` is `NONE`, `COARSE`, or `FINE`.
        """
        challenge = model.Challenge(event["challenge"], self.user_profile)
        opponent = challenge.challenge_target
        reason = event["challenge"]["declineReason"]
        logger.info(f"{opponent} declined {challenge}: {reason}")
        self.discard_challenge(challenge.id)
        if not challenge.from_self or self.challenge_filter == FilterType.NONE:
            return

        mode = "rated" if challenge.rated else "casual"
        decline_details: dict[str, str] = {"generic": "",
                                           "later": "",
                                           "nobot": "",
                                           "toofast": challenge.speed,
                                           "tooslow": challenge.speed,
                                           "timecontrol": challenge.speed,
                                           "rated": mode,
                                           "casual": mode,
                                           "standard": challenge.variant,
                                           "variant": challenge.variant}

        reason_key = event["challenge"]["declineReasonKey"].lower()
        if reason_key not in decline_details:
            logger.warning(f"Unknown decline reason received: {reason_key}")
        game_problem = decline_details.get(reason_key, "") if self.challenge_filter == FilterType.FINE else ""
        self.add_challenge_filter(opponent.name, game_problem)
        logger.info(f"Will not challenge {opponent} to another {game_problem}".strip() + " game today.")

        self.show_earliest_challenge_time()


def game_category(variant: str, base_time: int, increment: int, num_days: int) -> str:
    """
    Get the game type (e.g. bullet, atomic, classical). Lichess has one rating for every variant regardless of time control.

    :param variant: The game's variant.
    :param base_time: The base time in seconds.
    :param increment: The increment in seconds.
    :param num_days: If the game is correspondence, we have some days to play the move.
    :return: The game category.
    """
    game_duration = base_time + increment * 40
    if variant != "standard":
        return variant
    if num_days:
        return "correspondence"
    if game_duration < 179:
        return "bullet"
    if game_duration < 479:
        return "blitz"
    if game_duration < 1499:
        return "rapid"
    return "classical"
