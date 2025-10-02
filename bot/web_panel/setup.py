import asyncio
import logging
import secrets
from pathlib import Path
from typing import Optional

import aiohttp_jinja2
import jinja2
from aiohttp import web
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

    aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader(str(template_root)))

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

    views.register_routes(app)
