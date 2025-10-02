import logging
import secrets
from pathlib import Path
from typing import Optional

import aiohttp_jinja2
import jinja2
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web
from aiohttp_jinja2 import request_processor
from aiohttp_session import setup as setup_sessions
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from passlib.context import CryptContext

from config.settings import Settings
from bot.services.support_service import SupportService
from db.dal import user_dal, payment_dal

from . import views

PASSWORD_CONTEXT = CryptContext(schemes=["bcrypt"], deprecated="auto")


def setup_web_panel(
    app: web.Application,
    settings: Settings,
    async_session_factory,
    support_service: SupportService,
) -> None:
    if not settings.WEB_PANEL_ENABLED:
        logging.info("Web panel disabled via configuration")
        return

    template_root = Path(__file__).parent / "templates"
    static_root = Path(__file__).parent / "static"

    env = aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(template_root)),
        context_processors=[request_processor],
    )
    env.globals.setdefault("str", str)

    session_secret = settings.WEB_PANEL_SESSION_SECRET
    if session_secret:
        secret_bytes = session_secret.encode("utf-8")
    else:
        secret_bytes = secrets.token_bytes(32)
        logging.warning("WEB_PANEL_SESSION_SECRET not set; generated ephemeral secret.")
    secret_bytes = (secret_bytes[:32]).ljust(32, b"0")
    setup_sessions(app, EncryptedCookieStorage(secret_bytes))

    if settings.WEB_PANEL_PASSWORD_HASH:
        password_hash = settings.WEB_PANEL_PASSWORD_HASH
    elif settings.WEB_PANEL_PASSWORD_PLAIN:
        password_hash = PASSWORD_CONTEXT.hash(settings.WEB_PANEL_PASSWORD_PLAIN)
    else:
        logging.error("Web panel password not configured. Panel login will be locked.")
        password_hash = None

    app["panel_settings"] = {
        "settings": settings,
        "password_hash": password_hash,
        "support_service": support_service,
        "async_session_factory": async_session_factory,
    }
    app["panel_password_context"] = PASSWORD_CONTEXT

    app.router.add_static("/panel/static", path=str(static_root), name="panel-static")

    support_bot: Optional[Bot] = None
    support_bot_token = settings.SUPPORT_BOT_TOKEN or settings.BOT_TOKEN
    if support_bot_token:
        if settings.SUPPORT_BOT_TOKEN:
            support_bot = Bot(
                token=support_bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )

            async def _close_support_bot(application: web.Application) -> None:
                try:
                    await support_bot.session.close()
                except Exception:
                    logging.exception("Failed to close support panel bot session")

            app.on_cleanup.append(_close_support_bot)
        else:
            # Reuse main bot if dedicated support bot is not configured
            support_bot = app.get("bot")
    else:
        logging.warning("Support notifications disabled: no bot token available.")

    async def _notify_user(user_id: Optional[int], text: str) -> None:
        if not user_id or not text or support_bot is None:
            return
        try:
            await support_bot.send_message(chat_id=user_id, text=text)
        except Exception as exc:
            logging.warning("Failed to deliver support reply to %s: %s", user_id, exc)

    app["panel_support_bot"] = support_bot
    app["panel_notify_user"] = _notify_user

    views.register_routes(app)
