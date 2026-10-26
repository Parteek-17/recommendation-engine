"""Run the Telegram bot (development mode: long polling).

    python -m bot.main

Long polling = our program keeps asking Telegram "any new messages?". It works on any
laptop, no public URL needed. Only ONE copy of a bot may poll at a time (otherwise
Telegram answers "Conflict: terminated by other getUpdates request").
For deployment you would use a webhook instead — see bot/README.md.
"""

from __future__ import annotations

from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot import handlers
from common.config import ConfigError, settings
from common.db import init_db
from common.logging_utils import get_logger

log = get_logger("bot")

COMMANDS = [
    ("start", "Pick a demo shopper / switch offers on"),
    ("switch", "Play a different demo shopper"),
    ("recommend", "Top picks for you"),
    ("offers", "Your active offers"),
    ("settings", "Quiet hours, offers per week, categories"),
    ("gift", "Toggle gift mode"),
    ("stop", "Stop all offers"),
    ("help", "How to use"),
]


async def _post_init(app: Application) -> None:
    # Fills the "/" menu in Telegram, so users see the commands without BotFather setup.
    await app.bot.set_my_commands([BotCommand(name, text) for name, text in COMMANDS])
    log.info("Bot @%s is running. Press Ctrl+C to stop.", app.bot.username)


def build_application(token: str) -> Application:
    """Create the bot and connect each kind of update to its handler."""
    app = Application.builder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("stop", handlers.cmd_stop))
    app.add_handler(CommandHandler("help", handlers.cmd_help))
    app.add_handler(CommandHandler("switch", handlers.cmd_switch))
    app.add_handler(CommandHandler("gift", handlers.cmd_gift))
    app.add_handler(CommandHandler("recommend", handlers.cmd_recommend))
    app.add_handler(CommandHandler("offers", handlers.cmd_offers))
    app.add_handler(CommandHandler("settings", handlers.cmd_settings))
    app.add_handler(CallbackQueryHandler(handlers.on_button))
    # Any normal text that isn't a /command goes to the shopping assistant.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))
    app.add_error_handler(handlers.on_error)
    return app


def main() -> None:
    try:
        token = settings.require("TELEGRAM_BOT_TOKEN")
    except ConfigError as exc:
        log.error("%s", exc)
        raise SystemExit(1)
    init_db()
    build_application(token).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
