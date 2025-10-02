import logging
from typing import Optional, Tuple, List

from sqlalchemy.orm import sessionmaker

from config.settings import Settings
from db.dal import support_ticket_dal
from db.models import SupportTicket, SupportMessage


class SupportService:
    def __init__(self, settings: Settings, session_factory: sessionmaker):
        self._settings = settings
        self._session_factory = session_factory

    @property
    def settings(self) -> Settings:
        return self._settings

    async def create_ticket_with_message(
        self,
        *,
        user_id: Optional[int],
        subject: Optional[str],
        message: str,
        username: Optional[str] = None,
    ) -> SupportTicket:
        async with self._session_factory() as session:
            ticket = await support_ticket_dal.create_support_ticket(
                session,
                user_id=user_id,
                subject=subject,
            )
            await support_ticket_dal.add_support_message(
                session,
                ticket_id=ticket.ticket_id,
                sender_type="user" if user_id else "system",
                sender_id=user_id,
                sender_username=username,
                content=message,
            )
            await session.commit()
            await session.refresh(ticket)
            return ticket

    async def add_user_message(
        self,
        *,
        ticket_id: int,
        user_id: Optional[int],
        message: str,
        username: Optional[str] = None,
    ) -> Optional[SupportMessage]:
        async with self._session_factory() as session:
            ticket = await support_ticket_dal.get_ticket_by_id(session, ticket_id)
            if not ticket:
                logging.warning("Support ticket %s not found for user message", ticket_id)
                return None
            msg = await support_ticket_dal.add_support_message(
                session,
                ticket_id=ticket_id,
                sender_type="user",
                sender_id=user_id,
                sender_username=username,
                content=message,
            )
            if ticket.status == "closed":
                await support_ticket_dal.update_ticket_status(
                    session,
                    ticket_id=ticket_id,
                    new_status="open",
                )
            await session.commit()
            return msg

    async def add_admin_message(
        self,
        *,
        ticket_id: int,
        admin_id: Optional[int],
        message: str,
        username: Optional[str] = None,
    ) -> Optional[SupportMessage]:
        async with self._session_factory() as session:
            ticket = await support_ticket_dal.get_ticket_by_id(session, ticket_id)
            if not ticket:
                return None
            msg = await support_ticket_dal.add_support_message(
                session,
                ticket_id=ticket_id,
                sender_type="admin",
                sender_id=admin_id,
                sender_username=username,
                content=message,
            )
            await session.commit()
            return msg

    async def get_ticket(self, ticket_id: int) -> Optional[SupportTicket]:
        async with self._session_factory() as session:
            return await support_ticket_dal.get_ticket_by_id(session, ticket_id)

    async def get_user_tickets(self, user_id: int) -> List[SupportTicket]:
        async with self._session_factory() as session:
            return await support_ticket_dal.get_user_tickets(session, user_id)

    async def get_paginated(
        self,
        *,
        page: int,
        per_page: int,
        status: Optional[str] = None,
        is_starred: Optional[bool] = None,
        search_query: Optional[str] = None,
    ) -> Tuple[List[SupportTicket], int]:
        async with self._session_factory() as session:
            return await support_ticket_dal.get_tickets_paginated(
                session,
                page=page,
                per_page=per_page,
                status=status,
                is_starred=is_starred,
                search_query=search_query,
            )

    async def update_status(
        self,
        *,
        ticket_id: int,
        new_status: str,
        admin_id: Optional[int] = None,
    ) -> Optional[SupportTicket]:
        async with self._session_factory() as session:
            ticket = await support_ticket_dal.update_ticket_status(
                session,
                ticket_id=ticket_id,
                new_status=new_status,
                admin_id=admin_id,
            )
            await session.commit()
            return ticket

    async def toggle_star(self, ticket_id: int, desired_state: Optional[bool] = None) -> Optional[SupportTicket]:
        async with self._session_factory() as session:
            ticket = await support_ticket_dal.toggle_ticket_star(
                session,
                ticket_id=ticket_id,
                desired_state=desired_state,
            )
            await session.commit()
            return ticket

    async def assign(self, ticket_id: int, admin_id: Optional[int]) -> Optional[SupportTicket]:
        async with self._session_factory() as session:
            ticket = await support_ticket_dal.assign_ticket(
                session,
                ticket_id=ticket_id,
                admin_id=admin_id,
            )
            await session.commit()
            return ticket

    async def delete_ticket(self, ticket_id: int) -> bool:
        async with self._session_factory() as session:
            deleted = await support_ticket_dal.delete_ticket(session, ticket_id)
            await session.commit()
            return deleted > 0

    async def get_counts(self) -> Tuple[int, int, int]:
        async with self._session_factory() as session:
            open_count = await support_ticket_dal.get_open_tickets_count(session)
            closed_count = await support_ticket_dal.get_closed_tickets_count(session)
            all_count = await support_ticket_dal.get_all_tickets_count(session)
            return open_count, closed_count, all_count
