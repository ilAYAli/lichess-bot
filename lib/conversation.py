"""Allows lichess-bot to send messages to the chat."""
import logging
import re
from concurrent.futures import Future
from threading import Thread
from requests.exceptions import RequestException
from lib import model
from lib.engine_wrapper import EngineWrapper
from lib.lichess import Lichess
from lib.lichess_types import GameEventType
from collections.abc import Sequence
from lib.timer import Timer, seconds
from typing import TypeAlias
MULTIPROCESSING_LIST_TYPE: TypeAlias = Sequence[model.Challenge]

logger = logging.getLogger(__name__)
STOCKFISH_BIO = re.compile(r"\bStockfish\b(?:\s+([0-9]{1,3}(?:\.[0-9]{1,3}){0,2})\b)?", re.IGNORECASE)


class ChatLine:
    """Information about the message."""

    def __init__(self, message_info: GameEventType) -> None:
        """Information about the message."""
        self.room = message_info["room"]
        """Whether the message was sent in the chat room or in the spectator room."""
        self.username = message_info["username"]
        """The username of the account that sent the message."""
        self.text = message_info["text"]
        """The message sent."""


class Conversation:
    """Enables the bot to communicate with its opponent and the spectators."""

    def __init__(self, game: model.Game, engine: EngineWrapper, li: Lichess, version: str,
                 challenge_queue: MULTIPROCESSING_LIST_TYPE) -> None:
        """
        Communication between lichess-bot and the game chats.

        :param game: The game that the bot will send messages to.
        :param engine: The engine playing the game.
        :param li: A class that is used for communication with lichess.
        :param version: The lichess-bot version.
        :param challenge_queue: The active challenges the bot has.
        """
        self.game = game
        self.engine = engine
        self.li = li
        self.version = version
        self.challengers = challenge_queue
        self.messages: list[ChatLine] = []
        self.auto_name_query_pending = False
        self.stockfish_greeting = ""
        self.stockfish_spectator_greeting = ""
        self.other_engine_greeting = ""
        self.no_engine_report_greeting = ""
        self.stockfish_bio_greeting = ""
        self.bio_wait: Timer | None = None
        self.bio_lookup: Future[str] | None = None
        self.opponent_engine_type = ""
        self.greeting_started = False
        self.engine_follow_up_sent = False

    command_prefix = "!"

    def react(self, line: ChatLine) -> None:
        """
        React to a received message.

        :param line: Information about the message.
        """
        self.messages.append(line)
        logger.info(f"*** {self.game.url()} [{line.room}] {line.username}: {line.text}")
        if self.is_opponent_name_reply(line):
            self.opponent_engine_type = self.opponent_engine_type_from_line(line)
            logger.info(f"Opponent {line.username} reports engine: {self.opponent_engine_type}")
            if "stockfish" in self.opponent_engine_type.casefold():
                self.send_stockfish_greeting(self.stockfish_engine_info())
            elif self.opponent_engine_type and self.other_engine_greeting:
                name, _, suffix = self.opponent_engine_type.rpartition(" ")
                engine_name = self.opponent_engine_type
                if name and suffix.casefold() in {"avx2", "avx512", "sse2", "ssse3", "neon"}:
                    engine_name = name.rstrip()
                self.send_engine_follow_up(self.other_engine_greeting, self.other_engine_greeting, engine_name)

        if (self.auto_name_query_pending and line.room == "player"
                and line.username.casefold() == self.game.username.casefold() and line.text == "!name"):
            self.auto_name_query_pending = False
            return

        if line.text and line.text[0] == self.command_prefix:
            self.command(line, line.text[1:].lower())

    def is_opponent_name_reply(self, line: ChatLine) -> bool:
        """Return whether a chat line is the opponent's conventional !name reply."""
        if not self.game.opponent.is_bot or line.username.casefold() != self.game.opponent.name.casefold():
            return False
        expected_prefix = f"{self.game.opponent.name} running ".casefold()
        return line.text.casefold().startswith(expected_prefix)

    def opponent_engine_type_from_line(self, line: ChatLine) -> str:
        """Return the engine type reported in the opponent's !name reply."""
        if not self.is_opponent_name_reply(line):
            return ""
        return line.text[len(f"{self.game.opponent.name} running "):].partition(" (")[0].strip()

    def stockfish_engine_info(self) -> str:
        """Return version/build details after Stockfish, if the report includes any."""
        engine_type = self.opponent_engine_type
        index = engine_type.casefold().find("stockfish")
        if index < 0:
            return ""
        details = engine_type[index + len("stockfish"):].strip(" ._-")
        return f"running {engine_type}" if details else ""

    def start_greeting(self, player: str, spectator: str,
                       stockfish: str = "", stockfish_spectator: str = "",
                       build_greeting: str = "", *, other_engine: str = "", stockfish_bio: str = "",
                       no_engine_report: str = "", genuine_engine: str = "", announce: bool = True) -> None:
        """Set up the follow-up and send the opening chat for a new game."""
        if self.greeting_started:
            return

        self.greeting_started = True
        self.stockfish_greeting = stockfish
        self.stockfish_spectator_greeting = stockfish_spectator or stockfish
        self.other_engine_greeting = other_engine
        self.no_engine_report_greeting = no_engine_report
        self.stockfish_bio_greeting = stockfish_bio

        if not announce:
            return
        if not self.game.opponent.is_bot:
            self.send_message("player", player)
            self.send_message("spectator", spectator)
            return

        if genuine_engine:
            # Whitelisted bot: skip the !name query and bio check, which it doesn't support.
            self.send_message("player", build_greeting)
            self.send_message("spectator", build_greeting)
            self.send_engine_follow_up(genuine_engine, genuine_engine, self.game.opponent.name)
            return

        self.ask_opponent_name()
        self.send_message("player", build_greeting)
        self.send_message("spectator", build_greeting)
        if self.stockfish_bio_greeting or self.no_engine_report_greeting:
            self.bio_wait = Timer(seconds(10))

    def check_bio_fallback(self) -> None:
        """After the reply window, check the public bio without blocking game updates."""
        if self.opponent_engine_type or self.engine_follow_up_sent:
            return
        if self.bio_wait is not None and self.bio_wait.is_expired():
            self.bio_wait = None
            if self.stockfish_bio_greeting:
                self.bio_lookup = Future()
                Thread(target=self.lookup_stockfish_bio, args=(self.bio_lookup,), daemon=True).start()
            elif self.no_engine_report_greeting:
                self.send_engine_follow_up(self.no_engine_report_greeting,
                                           self.no_engine_report_greeting, "")
        if self.bio_lookup is not None and self.bio_lookup.done():
            engine = self.bio_lookup.result()
            self.bio_lookup = None
            if engine:
                logger.info("Opponent %s lists %s in its bio", self.game.opponent.name, engine)
                self.send_engine_follow_up(self.stockfish_bio_greeting, self.stockfish_bio_greeting, engine)
            elif self.no_engine_report_greeting:
                self.send_engine_follow_up(self.no_engine_report_greeting,
                                           self.no_engine_report_greeting, "")

    def lookup_stockfish_bio(self, result: Future[str]) -> None:
        """Return "Stockfish" (with version, if given) when the public bio mentions Stockfish."""
        engine = ""
        try:
            profile = self.li.get_public_data(self.game.opponent.name).get("profile") or {}
            bio = profile.get("bio") or ""
            if match := STOCKFISH_BIO.search(bio):
                engine = f"Stockfish {match[1]}" if match[1] else "Stockfish"
        except (RequestException, OSError, ValueError) as exc:
            logger.debug("Could not read opponent bio for %s: %s", self.game.opponent.name, exc)
        finally:
            result.set_result(engine)

    def send_stockfish_greeting(self, engine_info: str = "") -> None:
        """Send the Stockfish taunt once after the opponent reports Stockfish in !name."""
        engine_info_suffix = f" ({engine_info})" if engine_info else ""
        self.send_engine_follow_up(self.stockfish_greeting, self.stockfish_spectator_greeting, engine_info_suffix)

    def send_engine_follow_up(self, player: str, spectator: str, engine_info: str) -> None:
        """Send the configured response to the opponent's engine report once."""
        if self.engine_follow_up_sent or not (player or spectator):
            return
        self.send_message("player", player.replace("{engine_info}", engine_info))
        self.send_message("spectator", spectator.replace("{engine_info}", engine_info))
        self.engine_follow_up_sent = True

    def ask_opponent_name(self) -> None:
        """Ask bot opponents to identify their engine."""
        if self.game.opponent.is_bot:
            self.auto_name_query_pending = True
            self.send_message("player", "!name")

    def command(self, line: ChatLine, cmd: str) -> None:
        """
        Reacts to the specific commands in the chat.

        :param line: Information about the message.
        :param cmd: The command to react to.
        """
        from_self = line.username == self.game.username
        is_eval = cmd.startswith("eval")
        if cmd in ("commands", "help"):
            self.send_reply(line,
                            "Supported commands: !wait (wait a minute for my first move), !name, "
                            "!eval (or any text starting with !eval), !queue")
        elif cmd == "wait" and self.game.is_abortable():
            self.game.ping(seconds(60), seconds(120), seconds(120))
            self.send_reply(line, "Waiting 60 seconds...")
        elif cmd == "name":
            name = self.game.me.name
            self.send_reply(line, f"{name} running {self.engine.name()} (lichess-bot v{self.version})")
        elif is_eval and (from_self or line.room == "spectator"):
            stats = self.engine.get_stats(for_chat=True)
            self.send_reply(line, ", ".join(stats))
        elif is_eval:
            self.send_reply(line, "I don't tell that to my opponent, sorry.")
        elif cmd == "queue":
            if self.challengers:
                challengers = ", ".join([f"@{challenger.challenger.name}" for challenger in reversed(self.challengers)])
                self.send_reply(line, f"Challenge queue: {challengers}")
            else:
                self.send_reply(line, "No challenges queued.")

    def send_reply(self, line: ChatLine, reply: str) -> None:
        """
        Send the reply to the chat.

        :param line: Information about the original message that we reply to.
        :param reply: The reply to send.
        """
        logger.info(f"*** {self.game.url()} [{line.room}] {self.game.username}: {reply}")
        self.li.chat(self.game.id, line.room, reply)

    def send_message(self, room: str, message: str) -> None:
        """Send the message to the chat."""
        if message:
            self.send_reply(ChatLine({"room": room, "username": "", "text": ""}), message)
