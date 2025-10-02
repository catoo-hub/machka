import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import Settings
from bot.services.support_service import SupportService
from .router import build_support_router


class SupportBotRunner:
    def __init__(self, settings: Settings, support_service: SupportService):
        self._settings = settings
        self._support_service = support_service
        self._bot: Optional[Bot] = None
        self._dispatcher: Optional[Dispatcher] = None
        self._task: Optional[asyncio.Task] = None

    @property
    def bot(self) -> Optional[Bot]:
        return self._bot

    @property
    def dispatcher(self) -> Optional[Dispatcher]:
        return self._dispatcher

    def is_enabled(self) -> bool:
        return bool(self._settings.SUPPORT_BOT_TOKEN)

    async def start(self) -> None:
        if not self.is_enabled():
            logging.info("Support bot is disabled (SUPPORT_BOT_TOKEN missing).")
            return

        if self._bot or self._dispatcher:
            logging.warning("Support bot already initialized.")
            return

        logging.info("Starting support bot polling...")
        self._bot = Bot(
            token=self._settings.SUPPORT_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self._dispatcher = Dispatcher(storage=MemoryStorage())
        router = build_support_router(self._settings, self._support_service)
        self._dispatcher.include_router(router)

        try:
            await self._dispatcher.start_polling(self._bot)
        except asyncio.CancelledError:
            logging.info("Support bot polling cancelled")
            raise
        except Exception as exc:
            logging.exception("Support bot polling error: %s", exc)
            raise

    async def shutdown(self) -> None:
        if self._dispatcher:
            try:
                await self._dispatcher.emit_shutdown()
            except Exception:
                logging.exception("Failed to emit shutdown for support dispatcher")
            try:
                await self._dispatcher.storage.close()
            except Exception:
                pass
        if self._bot:
            try:
                await self._bot.session.close()
            except Exception:
                pass
        self._dispatcher = None
        self._bot = None


def create_support_bot(settings: Settings, support_service: SupportService) -> Optional[SupportBotRunner]:
    runner = SupportBotRunner(settings, support_service)
    if not runner.is_enabled():
        return None
    return runner
