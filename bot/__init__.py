"""Telegram bot (Phase 5). Contract functions exported here so other modules can do:

    try:
        from bot import send_offer
    except ImportError:
        from common.stubs import send_offer
"""

from bot.linking import create_link_token
from bot.sender import send_offer, send_text

__all__ = ["create_link_token", "send_offer", "send_text"]
