"""Allows lichess-bot to send messages to the chat."""
import logging
from lib import matchmaking, model
from lib.engine_wrapper import EngineWrapper
from lib.lichess import Lichess
from lib.lichess_types import GameEventType
from collections.abc import Sequence
from lib.timer import seconds
from typing import TypeAlias
MULTIPROCESSING_LIST_TYPE: TypeAlias = Sequence[model.Challenge]

logger = logging.getLogger(__name__)


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
        self.player_greeting = ""
        self.spectator_greeting = ""
        self.stockfish_greeting = ""
        self.greeting_started = False
        self.greeting_pending = False
        self.greeting_sent = False
        self.stockfish_detected = False
        self.stockfish_greeting_sent = False

    command_prefix = "!"

    def react(self, line: ChatLine) -> None:
        """
        React to a received message.

        :param line: Information about the message.
        """
        self.messages.append(line)
        logger.info(f"*** {self.game.url()} [{line.room}] {line.username}: {line.text}")
        stockfish_detected = self.block_stockfish_chat(line)

        if stockfish_detected:
            self.send_stockfish_greeting()
        elif self.greeting_pending and self.is_opponent_name_reply(line):
            self.send_normal_greeting()

        if self.auto_name_query_pending and line.username == self.game.username and line.text == "!name":
            self.auto_name_query_pending = False
            return

        if line.text and line.text[0] == self.command_prefix:
            self.command(line, line.text[1:].lower())

    def block_stockfish_chat(self, line: ChatLine) -> bool:
        """Record whether the opponent identifies as Stockfish in either chat room."""
        from_opponent = line.username.casefold() == self.game.opponent.name.casefold()
        supported_room = line.room == "player" or self.is_opponent_name_reply(line)
        if from_opponent and supported_room:
            return matchmaking.block_stockfish_text(line.username, line.text, "chat message")
        return False

    def is_opponent_name_reply(self, line: ChatLine) -> bool:
        """Return whether a chat line is the opponent's conventional !name reply."""
        if line.username.casefold() != self.game.opponent.name.casefold():
            return False
        expected_prefix = f"{self.game.opponent.name} running ".casefold()
        return line.text.casefold().startswith(expected_prefix)

    def start_greeting(self, player: str, spectator: str, stockfish: str) -> None:
        """Start greeting, querying an unknown bot's engine before choosing the text."""
        if self.greeting_started:
            return

        self.greeting_started = True
        self.player_greeting = player
        self.spectator_greeting = spectator
        self.stockfish_greeting = stockfish

        if not self.game.opponent.is_bot:
            self.send_normal_greeting()
            return

        if self.stockfish_detected or matchmaking.stockfish_block_list_contains(self.game.opponent.name):
            self.send_stockfish_greeting()
        else:
            self.greeting_pending = True
        self.ask_opponent_name()

    def finish_pending_greeting(self) -> None:
        """Use the normal greeting when a bot does not answer !name promptly."""
        if self.greeting_pending:
            self.send_normal_greeting()

    def send_normal_greeting(self) -> None:
        """Send the configured normal greetings once."""
        if self.greeting_sent:
            return
        self.greeting_pending = False
        self.send_message("player", self.player_greeting)
        self.send_message("spectator", self.spectator_greeting)
        self.greeting_sent = True

    def send_stockfish_greeting(self) -> None:
        """Send the Stockfish greeting once after a positive detection."""
        self.stockfish_detected = True
        if not self.greeting_started or self.stockfish_greeting_sent:
            return
        self.greeting_pending = False
        self.send_message("player", self.stockfish_greeting)
        self.send_message("spectator", self.stockfish_greeting)
        self.greeting_sent = True
        self.stockfish_greeting_sent = True

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
