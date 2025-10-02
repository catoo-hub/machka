import logging
from typing import Optional, List, Tuple
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, delete, asc, desc
from sqlalchemy.orm import selectinload

from db.models import SupportTicket, SupportMessage


async def create_support_ticket(
    session: AsyncSession,
    *,
    user_id: Optional[int],
    subject: Optional[str],
    priority: str = "normal",
) -> SupportTicket:
    ticket = SupportTicket(
        user_id=user_id,
        subject=subject,
        priority=priority,
        status="open",
        created_at=datetime.now(timezone.utc),
        last_user_message_at=datetime.now(timezone.utc) if user_id else None,
    )
    session.add(ticket)
    await session.flush()
    await session.refresh(ticket)
    logging.info("Support ticket %s created", ticket.ticket_id)
    return ticket


async def add_support_message(
    session: AsyncSession,
    *,
    ticket_id: int,
    sender_type: str,
    sender_id: Optional[int],
    content: str,
    sender_username: Optional[str] = None,
    attachments: Optional[str] = None,
) -> SupportMessage:
    message = SupportMessage(
        ticket_id=ticket_id,
        sender_type=sender_type,
        sender_id=sender_id,
        sender_username=sender_username,
        content=content,
        attachments=attachments,
    )
    session.add(message)

    now = datetime.now(timezone.utc)
    fields = {"updated_at": now}
    if sender_type == "user":
        fields["last_user_message_at"] = now
    else:
        fields["last_admin_message_at"] = now

    await session.execute(
        update(SupportTicket)
        .where(SupportTicket.ticket_id == ticket_id)
        .values(**fields)
    )

    await session.flush()
    await session.refresh(message)
    return message


async def get_ticket_by_id(
    session: AsyncSession, ticket_id: int
) -> Optional[SupportTicket]:
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.ticket_id == ticket_id)
        .options(selectinload(SupportTicket.messages))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_tickets(
    session: AsyncSession, user_id: int
) -> List[SupportTicket]:
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.user_id == user_id)
        .order_by(desc(SupportTicket.created_at))
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_tickets_paginated(
    session: AsyncSession,
    *,
    page: int = 1,
    per_page: int = 20,
    status: Optional[str] = None,
    is_starred: Optional[bool] = None,
    search_query: Optional[str] = None,
) -> Tuple[List[SupportTicket], int]:
    stmt = select(SupportTicket)
    count_stmt = select(func.count(SupportTicket.ticket_id))

    filters = []
    if status:
        filters.append(SupportTicket.status == status)
    if is_starred is not None:
        filters.append(SupportTicket.is_starred == is_starred)
    if search_query:
        like = f"%{search_query.strip()}%"
        filters.append(SupportTicket.subject.ilike(like))

    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    stmt = stmt.order_by(
        asc(SupportTicket.status),
        desc(SupportTicket.is_starred),
        desc(SupportTicket.updated_at.nullslast()),
        desc(SupportTicket.created_at),
    )
    stmt = stmt.offset(max(page - 1, 0) * per_page).limit(per_page)

    tickets_result = await session.execute(stmt)
    count_result = await session.execute(count_stmt)

    tickets = tickets_result.scalars().all()
    total = count_result.scalar_one()
    return tickets, total


async def update_ticket_status(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_status: str,
    admin_id: Optional[int] = None,
) -> Optional[SupportTicket]:
    now = datetime.now(timezone.utc)
    values = {
        "status": new_status,
        "updated_at": now,
    }
    if new_status == "closed":
        values["closed_at"] = now
    else:
        values["closed_at"] = None
    if admin_id is not None:
        values["assigned_admin_id"] = admin_id

    await session.execute(
        update(SupportTicket)
        .where(SupportTicket.ticket_id == ticket_id)
        .values(**values)
    )
    await session.flush()
    return await get_ticket_by_id(session, ticket_id)


async def toggle_ticket_star(
    session: AsyncSession,
    *,
    ticket_id: int,
    desired_state: Optional[bool] = None,
) -> Optional[SupportTicket]:
    ticket = await get_ticket_by_id(session, ticket_id)
    if not ticket:
        return None
    new_state = desired_state if desired_state is not None else not ticket.is_starred
    ticket.is_starred = new_state
    await session.flush()
    await session.refresh(ticket)
    return ticket


async def assign_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    admin_id: Optional[int],
) -> Optional[SupportTicket]:
    await session.execute(
        update(SupportTicket)
        .where(SupportTicket.ticket_id == ticket_id)
        .values(assigned_admin_id=admin_id, updated_at=datetime.now(timezone.utc))
    )
    await session.flush()
    return await get_ticket_by_id(session, ticket_id)


async def delete_ticket(session: AsyncSession, ticket_id: int) -> int:
    result = await session.execute(
        delete(SupportTicket).where(SupportTicket.ticket_id == ticket_id)
    )
    await session.flush()
    return result.rowcount or 0


async def get_open_tickets_count(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count(SupportTicket.ticket_id)).where(SupportTicket.status == "open")
    )
    return result.scalar() or 0


async def get_closed_tickets_count(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count(SupportTicket.ticket_id)).where(SupportTicket.status == "closed")
    )
    return result.scalar() or 0


async def get_all_tickets_count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(SupportTicket.ticket_id)))
    return result.scalar() or 0