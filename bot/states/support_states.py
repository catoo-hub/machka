from aiogram.fsm.state import State, StatesGroup


class SupportTicketStates(StatesGroup):
    waiting_for_subject = State()
    waiting_for_description = State()


class SupportTicketReplyStates(StatesGroup):
    waiting_for_reply = State()


class SupportAdminReplyStates(StatesGroup):
    waiting_for_admin_reply = State()