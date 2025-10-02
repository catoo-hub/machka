import math
from typing import Any, Dict, Optional

import aiohttp_jinja2
from aiohttp import web
from aiohttp_session import get_session
from passlib.context import CryptContext
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from config.settings import Settings
from bot.services.support_service import SupportService
from db.models import User, Payment, Subscription, SupportTicket
from db.dal import user_dal, payment_dal


def _panel_config(request: web.Request) -> Dict[str, Any]:
    return request.app.get("panel_settings", {})


def _password_context(request: web.Request) -> CryptContext:
    return request.app.get("panel_password_context")


def _settings(request: web.Request) -> Settings:
    cfg = _panel_config(request)
    return cfg.get("settings")


def _support_service(request: web.Request) -> SupportService:
    cfg = _panel_config(request)
    return cfg.get("support_service")


def _session_factory(request: web.Request):
    cfg = _panel_config(request)
    return cfg.get("async_session_factory")


def _password_hash(request: web.Request) -> Optional[str]:
    cfg = _panel_config(request)
    return cfg.get("password_hash")


def _require_panel_enabled(request: web.Request):
    settings = _settings(request)
    if not settings or not settings.WEB_PANEL_ENABLED:
        raise web.HTTPNotFound()


def _auth_route(path: str) -> str:
    return f"/panel{path}"


def register_routes(app: web.Application) -> None:
    app.router.add_get(_auth_route("/login"), login_form, name="panel-login")
    app.router.add_post(_auth_route("/login"), login_submit, name="panel-login-submit")
    app.router.add_post(_auth_route("/logout"), logout_handler, name="panel-logout")
    app.router.add_get(_auth_route("/"), dashboard, name="panel-home")
    app.router.add_get(_auth_route("/users"), users_list, name="panel-users")
    app.router.add_get(_auth_route("/users/{user_id}"), user_details, name="panel-user-detail")
    app.router.add_get(_auth_route("/tickets"), tickets_list, name="panel-tickets")
    app.router.add_get(_auth_route("/tickets/{ticket_id}"), ticket_details, name="panel-ticket-detail")
    app.router.add_post(_auth_route("/tickets/{ticket_id}/status"), ticket_update_status, name="panel-ticket-status")
    app.router.add_post(_auth_route("/tickets/{ticket_id}/reply"), ticket_reply, name="panel-ticket-reply")
    app.router.add_post(_auth_route("/tickets/{ticket_id}/delete"), ticket_delete, name="panel-ticket-delete")


async def login_form(request: web.Request) -> web.StreamResponse:
    _require_panel_enabled(request)
    session = await get_session(request)
    if session.get("panel_authenticated"):
        raise web.HTTPFound(_auth_route("/"))
    return aiohttp_jinja2.render_template("login.html", request, {})


async def login_submit(request: web.Request) -> web.StreamResponse:
    _require_panel_enabled(request)
    if request.method != "POST":
        raise web.HTTPMethodNotAllowed(method=request.method, allowed_methods=["POST"])

    data = await request.post()
    login = (data.get("login") or "").strip()
    password = data.get("password") or ""

    settings = _settings(request)
    expected_login = settings.WEB_PANEL_LOGIN if settings else "admin"
    password_hash = _password_hash(request)

    if login != expected_login or not password_hash:
        context = {"error": "Неверный логин или пароль"}
        return aiohttp_jinja2.render_template("login.html", request, context)

    pwd_context: CryptContext = request.app.setdefault("panel_password_context", CryptContext(schemes=["bcrypt"]))
    if not pwd_context.verify(password, password_hash):
        context = {"error": "Неверный логин или пароль"}
        return aiohttp_jinja2.render_template("login.html", request, context)

    session = await get_session(request)
    session["panel_authenticated"] = True
    raise web.HTTPFound(_auth_route("/"))


async def logout_handler(request: web.Request) -> web.StreamResponse:
    session = await get_session(request)
    session.invalidate()
    raise web.HTTPFound(_auth_route("/login"))


async def _ensure_authenticated(request: web.Request) -> None:
    _require_panel_enabled(request)
    session = await get_session(request)
    if not session.get("panel_authenticated"):
        raise web.HTTPFound(_auth_route("/login"))


async def dashboard(request: web.Request) -> web.StreamResponse:
    await _ensure_authenticated(request)
    settings = _settings(request)

    async_session_factory = _session_factory(request)
    async with async_session_factory() as session:
        user_stats = await user_dal.get_enhanced_user_statistics(session)
        payments_count = await payment_dal.get_payments_count(session)
        latest_payments_stmt = (
            select(Payment)
            .options(selectinload(Payment.user))
            .order_by(Payment.created_at.desc())
            .limit(10)
        )
        latest_payments = (await session.execute(latest_payments_stmt)).scalars().all()

    support_service = _support_service(request)
    open_count, closed_count, all_count = await support_service.get_counts()

    context = {
        "settings": settings,
        "user_stats": user_stats,
        "payments_count": payments_count,
        "latest_payments": latest_payments,
        "support_counts": {
            "open": open_count,
            "closed": closed_count,
            "all": all_count,
        },
    }
    return aiohttp_jinja2.render_template("dashboard.html", request, context)


async def users_list(request: web.Request) -> web.StreamResponse:
    await _ensure_authenticated(request)
    async_session_factory = _session_factory(request)
    page = int(request.query.get("page", 1))
    per_page = 25

    async with async_session_factory() as session:
        stmt = (
            select(User)
            .order_by(User.registration_date.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        users = (await session.execute(stmt)).scalars().all()
        total = (await session.execute(select(func.count(User.user_id)))).scalar() or 0

    total_pages = max(1, math.ceil(total / per_page))
    context = {
        "users": users,
        "page": page,
        "total_pages": total_pages,
        "total": total,
    }
    return aiohttp_jinja2.render_template("users.html", request, context)


async def user_details(request: web.Request) -> web.StreamResponse:
    await _ensure_authenticated(request)
    user_id = int(request.match_info["user_id"])
    async_session_factory = _session_factory(request)

    async with async_session_factory() as session:
        user = await user_dal.get_user_by_id(session, user_id)
        if not user:
            raise web.HTTPNotFound()
        subscriptions_stmt = (
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.end_date.desc())
        )
        subscriptions = (await session.execute(subscriptions_stmt)).scalars().all()
        payments_stmt = (
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
        )
        payments = (await session.execute(payments_stmt)).scalars().all()

    context = {
        "user": user,
        "subscriptions": subscriptions,
        "payments": payments,
    }
    return aiohttp_jinja2.render_template("user_detail.html", request, context)


async def tickets_list(request: web.Request) -> web.StreamResponse:
    await _ensure_authenticated(request)
    support_service = _support_service(request)
    status = request.query.get("status") or None
    page = int(request.query.get("page", 1))

    tickets, total = await support_service.get_paginated(page=page, per_page=20, status=status)
    total_pages = max(1, math.ceil(total / 20))

    context = {
        "tickets": tickets,
        "status": status,
        "page": page,
        "total": total,
        "total_pages": total_pages,
    }
    return aiohttp_jinja2.render_template("tickets.html", request, context)


async def ticket_details(request: web.Request) -> web.StreamResponse:
    await _ensure_authenticated(request)
    support_service = _support_service(request)
    ticket_id = int(request.match_info["ticket_id"])
    ticket = await support_service.get_ticket(ticket_id)
    if not ticket:
        raise web.HTTPNotFound()
    context = {
        "ticket": ticket,
    }
    return aiohttp_jinja2.render_template("ticket_detail.html", request, context)


async def ticket_update_status(request: web.Request) -> web.StreamResponse:
    await _ensure_authenticated(request)
    support_service = _support_service(request)
    ticket_id = int(request.match_info["ticket_id"])
    data = await request.post()
    new_status = data.get("status")
    if new_status not in {"open", "closed"}:
        raise web.HTTPBadRequest()
    ticket = await support_service.update_status(ticket_id=ticket_id, new_status=new_status)
    if not ticket:
        raise web.HTTPNotFound()
    raise web.HTTPFound(_auth_route(f"/tickets/{ticket_id}"))


async def ticket_reply(request: web.Request) -> web.StreamResponse:
    await _ensure_authenticated(request)
    support_service = _support_service(request)
    ticket_id = int(request.match_info["ticket_id"])
    data = await request.post()
    message = (data.get("message") or "").strip()
    if not message:
        raise web.HTTPFound(_auth_route(f"/tickets/{ticket_id}"))
    await support_service.add_admin_message(ticket_id=ticket_id, admin_id=None, message=message)
    raise web.HTTPFound(_auth_route(f"/tickets/{ticket_id}"))


async def ticket_delete(request: web.Request) -> web.StreamResponse:
    await _ensure_authenticated(request)
    support_service = _support_service(request)
    ticket_id = int(request.match_info["ticket_id"])
    await support_service.delete_ticket(ticket_id)
    raise web.HTTPFound(_auth_route("/tickets"))
