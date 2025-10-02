import logging
from typing import Optional, List

from aiogram import Router, types, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

from config.settings import Settings
from bot.states.support_states import (
    SupportTicketStates,
    SupportTicketReplyStates,
    SupportAdminReplyStates,
)
from bot.services.support_service import SupportService
from db.models import SupportTicket


USER_MENU_NEW = "✍️ Новое обращение"
USER_MENU_LIST = "📨 Мои обращения"
ADMIN_MENU_OPEN = "📋 Открытые"
ADMIN_MENU_STARRED = "⭐ Избранные"


def is_support_admin(user_id: int, settings: Settings) -> bool:
    if user_id in settings.support_admin_ids:
        return True
    if user_id in settings.ADMIN_IDS:
        return True
    if settings.PRIMARY_ADMIN_ID and user_id == settings.PRIMARY_ADMIN_ID:
        return True
    return False


async def notify_admins(bot: Bot, settings: Settings, text: str) -> None:
    admin_ids: List[int] = settings.support_admin_ids or []
    if not admin_ids:
        admin_ids = settings.ADMIN_IDS
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.debug("Failed to notify admin %s: %s", admin_id, e)


async def notify_user(bot: Bot, user_id: Optional[int], text: str) -> None:
    if not user_id:
        return
    try:
        await bot.send_message(user_id, text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.debug("Failed to notify support user %s: %s", user_id, e)


def build_user_menu() -> types.ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=USER_MENU_NEW)
    builder.button(text=USER_MENU_LIST)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def build_admin_menu() -> types.ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=ADMIN_MENU_OPEN)
    builder.button(text=ADMIN_MENU_STARRED)
    builder.button(text=USER_MENU_LIST)
    builder.button(text=USER_MENU_NEW)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def render_ticket_preview(ticket: SupportTicket) -> str:
    subject = ticket.subject or "Без темы"
    status = "🟢 открыто" if ticket.status == "open" else "🔒 закрыто"
    star = "⭐ " if ticket.is_starred else ""
    return f"{star}#{ticket.ticket_id} — {subject} ({status})"


def render_ticket_details(ticket: SupportTicket) -> str:
    lines = [
        f"<b>Тикет #{ticket.ticket_id}</b>",
        f"Статус: {'🟢 Открыт' if ticket.status == 'open' else '🔒 Закрыт'}",
    ]
    if ticket.subject:
        lines.append(f"Тема: {ticket.subject}")
    if ticket.user_id:
        lines.append(f"Пользователь: <code>{ticket.user_id}</code>")
    if ticket.is_starred:
        lines.append("⭐ Отмечен как важный")
    lines.append("")
    for message in ticket.messages[-10:]:
        author = "Пользователь" if message.sender_type == "user" else ("Админ" if message.sender_type == "admin" else "Система")
        lines.append(f"<b>{author}</b>: {message.content}")
    return "\n".join(lines)


def ticket_user_keyboard(ticket: SupportTicket) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if ticket.status == "open":
        builder.button(text="💬 Ответить", callback_data=f"support:reply:{ticket.ticket_id}")
        builder.button(text="✅ Закрыть", callback_data=f"support:close:{ticket.ticket_id}")
    else:
        builder.button(text="🔓 Открыть", callback_data=f"support:open:{ticket.ticket_id}")
    builder.button(text="🔙 Назад", callback_data="support:back")
    builder.adjust(1)
    return builder.as_markup()


def ticket_admin_keyboard(ticket: SupportTicket) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if ticket.status == "open":
        builder.button(text="✉️ Ответить", callback_data=f"support:admin_reply:{ticket.ticket_id}")
        builder.button(text="✅ Закрыть", callback_data=f"support:close:{ticket.ticket_id}")
    else:
        builder.button(text="🔓 Открыть", callback_data=f"support:open:{ticket.ticket_id}")
    star_text = "⭐ Убрать важность" if ticket.is_starred else "⭐ Сделать важным"
    builder.button(text=star_text, callback_data=f"support:star:{ticket.ticket_id}")
    builder.button(text="🗑 Удалить", callback_data=f"support:delete:{ticket.ticket_id}")
    builder.button(text="🔙 Назад", callback_data="support:back")
    builder.adjust(1)
    return builder.as_markup()


def build_support_router(settings: Settings, support_service: SupportService) -> Router:
    router = Router(name="support-bot")

    @router.message(CommandStart())
    async def start_handler(message: types.Message) -> None:
        is_admin = is_support_admin(message.from_user.id, settings)
        keyboard = build_admin_menu() if is_admin else build_user_menu()
        await message.answer(
            "👋 Добро пожаловать в поддержку! Выберите действие в меню.",
            reply_markup=keyboard,
        )

    @router.message(F.text == USER_MENU_NEW)
    async def new_ticket_prompt(message: types.Message, state: FSMContext) -> None:
        await state.set_state(SupportTicketStates.waiting_for_subject)
        await message.answer("📝 Укажите тему обращения.")

    @router.message(SupportTicketStates.waiting_for_subject)
    async def subject_received(message: types.Message, state: FSMContext) -> None:
        subject = (message.text or "").strip()
        await state.update_data(subject=subject)
        await state.set_state(SupportTicketStates.waiting_for_description)
        await message.answer("✉️ Опишите проблему." )

    @router.message(SupportTicketStates.waiting_for_description)
    async def description_received(message: types.Message, state: FSMContext, bot: Bot) -> None:
        data = await state.get_data()
        subject = data.get("subject")
        description = (message.text or message.caption or "").strip()
        ticket = await support_service.create_ticket_with_message(
            user_id=message.from_user.id,
            subject=subject,
            message=description,
            username=message.from_user.username,
        )
        await state.clear()
        await message.answer(
            f"✅ Обращение #{ticket.ticket_id} создано. Мы ответим как можно быстрее.",
            reply_markup=build_user_menu(),
        )
        await notify_admins(
            bot,
            settings,
            f"📬 Новое обращение #{ticket.ticket_id} от <code>{message.from_user.id}</code>\nТема: {subject or 'без темы'}",
        )

    @router.message(F.text == USER_MENU_LIST)
    async def list_user_tickets(message: types.Message) -> None:
        tickets = await support_service.get_user_tickets(message.from_user.id)
        if not tickets:
            await message.answer("📭 У вас пока нет обращений.")
            return
        builder = InlineKeyboardBuilder()
        for ticket in tickets[:15]:
            builder.button(
                text=render_ticket_preview(ticket),
                callback_data=f"support:view:{ticket.ticket_id}"
            )
        builder.adjust(1)
        await message.answer(
            "📨 Ваши обращения:",
            reply_markup=builder.as_markup(),
        )

    @router.message(F.text == ADMIN_MENU_OPEN)
    async def list_open_tickets_for_admin(message: types.Message) -> None:
        if not is_support_admin(message.from_user.id, settings):
            return
        tickets, total = await support_service.get_paginated(page=1, per_page=20, status="open")
        if not tickets:
            await message.answer("✅ Нет открытых обращений.")
            return
        builder = InlineKeyboardBuilder()
        for ticket in tickets:
            builder.button(text=render_ticket_preview(ticket), callback_data=f"support:view:{ticket.ticket_id}")
        builder.adjust(1)
        await message.answer(f"Открытых обращений: {total}", reply_markup=builder.as_markup())

    @router.message(F.text == ADMIN_MENU_STARRED)
    async def list_starred_tickets(message: types.Message) -> None:
        if not is_support_admin(message.from_user.id, settings):
            return
        tickets, total = await support_service.get_paginated(page=1, per_page=50, is_starred=True)
        if not tickets:
            await message.answer("⭐ Нет отмеченных обращений.")
            return
        builder = InlineKeyboardBuilder()
        for ticket in tickets:
            builder.button(text=render_ticket_preview(ticket), callback_data=f"support:view:{ticket.ticket_id}")
        builder.adjust(1)
        await message.answer(f"⭐ Важные обращения: {total}", reply_markup=builder.as_markup())

    @router.callback_query(F.data.startswith("support:view:"))
    async def view_ticket(callback: types.CallbackQuery) -> None:
        ticket_id = int(callback.data.split(":")[-1])
        ticket = await support_service.get_ticket(ticket_id)
        if not ticket:
            await callback.answer("Тикет не найден", show_alert=True)
            return
        text = render_ticket_details(ticket)
        keyboard = ticket_admin_keyboard(ticket) if is_support_admin(callback.from_user.id, settings) else ticket_user_keyboard(ticket)
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()

    @router.callback_query(F.data == "support:back")
    async def back_to_menu(callback: types.CallbackQuery) -> None:
        is_admin = is_support_admin(callback.from_user.id, settings)
        keyboard = build_admin_menu() if is_admin else build_user_menu()
        await callback.message.edit_text(
            "🔙 Возврат в меню.",
        )
        await callback.message.answer("Выберите действие:", reply_markup=keyboard)
        await callback.answer()

    @router.callback_query(F.data.startswith("support:reply:"))
    async def user_reply(callback: types.CallbackQuery, state: FSMContext) -> None:
        ticket_id = int(callback.data.split(":")[-1])
        await state.update_data(reply_ticket_id=ticket_id)
        await state.set_state(SupportTicketReplyStates.waiting_for_reply)
        await callback.message.answer("✍️ Напишите ответ одним сообщением.")
        await callback.answer()

    @router.message(SupportTicketReplyStates.waiting_for_reply)
    async def user_reply_received(message: types.Message, state: FSMContext, bot: Bot) -> None:
        data = await state.get_data()
        ticket_id = data.get("reply_ticket_id")
        await state.clear()
        reply_text = (message.text or message.caption or "").strip()
        if not ticket_id or not reply_text:
            await message.answer("Сообщение отправлено.")
            return
        msg = await support_service.add_user_message(
            ticket_id=ticket_id,
            user_id=message.from_user.id,
            message=reply_text,
            username=message.from_user.username,
        )
        if not msg:
            await message.answer("❌ Не удалось отправить сообщение.")
            return
        await message.answer("✅ Сообщение отправлено.")
        await notify_admins(
            bot,
            settings,
            f"📥 Ответ по тикету #{ticket_id} от <code>{message.from_user.id}</code>:\n{reply_text}",
        )

    @router.callback_query(F.data.startswith("support:close:"))
    async def close_ticket(callback: types.CallbackQuery, bot: Bot) -> None:
        ticket_id = int(callback.data.split(":")[-1])
        ticket = await support_service.update_status(ticket_id=ticket_id, new_status="closed", admin_id=callback.from_user.id if is_support_admin(callback.from_user.id, settings) else None)
        if not ticket:
            await callback.answer("Тикет не найден", show_alert=True)
            return
        await callback.message.edit_text(render_ticket_details(ticket), reply_markup=ticket_admin_keyboard(ticket) if is_support_admin(callback.from_user.id, settings) else ticket_user_keyboard(ticket))
        await callback.answer("Тикет закрыт")
        await notify_user(bot, ticket.user_id, f"🔒 Ваш тикет #{ticket.ticket_id} закрыт.")

    @router.callback_query(F.data.startswith("support:open:"))
    async def open_ticket(callback: types.CallbackQuery, bot: Bot) -> None:
        ticket_id = int(callback.data.split(":")[-1])
        ticket = await support_service.update_status(ticket_id=ticket_id, new_status="open", admin_id=callback.from_user.id if is_support_admin(callback.from_user.id, settings) else None)
        if not ticket:
            await callback.answer("Тикет не найден", show_alert=True)
            return
        await callback.message.edit_text(render_ticket_details(ticket), reply_markup=ticket_admin_keyboard(ticket) if is_support_admin(callback.from_user.id, settings) else ticket_user_keyboard(ticket))
        await callback.answer("Тикет открыт")
        await notify_user(bot, ticket.user_id, f"🔓 Ваш тикет #{ticket.ticket_id} снова открыт.")

    @router.callback_query(F.data.startswith("support:star:"))
    async def toggle_star(callback: types.CallbackQuery) -> None:
        if not is_support_admin(callback.from_user.id, settings):
            await callback.answer()
            return
        ticket_id = int(callback.data.split(":")[-1])
        ticket = await support_service.toggle_star(ticket_id)
        if not ticket:
            await callback.answer("Тикет не найден", show_alert=True)
            return
        await callback.message.edit_text(render_ticket_details(ticket), reply_markup=ticket_admin_keyboard(ticket))
        await callback.answer("Статус важности обновлён")

    @router.callback_query(F.data.startswith("support:delete:"))
    async def delete_ticket(callback: types.CallbackQuery) -> None:
        if not is_support_admin(callback.from_user.id, settings):
            await callback.answer()
            return
        ticket_id = int(callback.data.split(":")[-1])
        deleted = await support_service.delete_ticket(ticket_id)
        if deleted:
            await callback.message.edit_text("🗑 Тикет удалён.")
            await callback.answer("Удалено")
        else:
            await callback.answer("Тикет не найден", show_alert=True)

    @router.callback_query(F.data.startswith("support:admin_reply:"))
    async def admin_reply(callback: types.CallbackQuery, state: FSMContext) -> None:
        if not is_support_admin(callback.from_user.id, settings):
            await callback.answer()
            return
        ticket_id = int(callback.data.split(":")[-1])
        await state.update_data(admin_reply_ticket_id=ticket_id)
        await state.set_state(SupportAdminReplyStates.waiting_for_admin_reply)
        await callback.message.answer("✍️ Отправьте ответ для пользователя.")
        await callback.answer()

    @router.message(SupportAdminReplyStates.waiting_for_admin_reply)
    async def admin_reply_received(message: types.Message, state: FSMContext, bot: Bot) -> None:
        if not is_support_admin(message.from_user.id, settings):
            await state.clear()
            return
        data = await state.get_data()
        ticket_id = data.get("admin_reply_ticket_id")
        await state.clear()
        reply_text = (message.text or message.caption or "").strip()
        if not ticket_id or not reply_text:
            await message.answer("Сообщение пустое.")
            return
        msg = await support_service.add_admin_message(
            ticket_id=ticket_id,
            admin_id=message.from_user.id,
            message=reply_text,
            username=message.from_user.username,
        )
        if not msg:
            await message.answer("❌ Не удалось отправить ответ.")
            return
        ticket = await support_service.get_ticket(ticket_id)
        await message.answer("✅ Ответ отправлен пользователю.")
        await notify_user(bot, ticket.user_id if ticket else None, f"📨 Ответ по тикету #{ticket_id}:\n{reply_text}")

    return router
