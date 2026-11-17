"""Роутинг обновлений MAX -> обработчики по ролям и состояниям FSM."""
from __future__ import annotations

import logging
import re

from . import handlers_admin, handlers_artist, handlers_client, handlers_legal
from .fsm import S

log = logging.getLogger(__name__)

COMMAND_MENU_WORDS = {"меню", "menu", "старт", "start", "привет", "начать"}


def _phone_from_attachments(attachments: list[dict]) -> str | None:
    """Извлечь телефон из вложения contact (кнопка request_contact)."""
    for att in attachments or []:
        if att.get("type") != "contact":
            continue
        payload = att.get("payload") or {}
        max_info = payload.get("max_info") or {}
        if max_info.get("phone"):
            return str(max_info["phone"])
        vcf = payload.get("vcf_info") or ""
        m = re.search(r"TEL[^:\n]*:([+0-9()\- ]+)", vcf)
        if m:
            return m.group(1).strip()
    return None


def dispatch(app, update: dict) -> None:
    """Разобрать одно обновление MAX и вызвать нужный обработчик."""
    utype = update.get("update_type")
    now = app.now().isoformat()

    if utype == "bot_started":
        user = update.get("user") or {}
        user_id = int(user.get("user_id") or update.get("chat_id"))
        app.db.upsert_user(user_id, user.get("first_name", ""), now)
        log.info("bot_started: user %s", user_id)
        handlers_client.send_main_menu(app, user_id)
        return

    if utype == "message_created":
        msg = update.get("message") or {}
        sender = msg.get("sender") or {}
        user_id = int(sender.get("user_id"))
        if sender.get("is_bot"):
            return
        app.db.upsert_user(user_id, sender.get("first_name", ""), now)
        body = msg.get("body") or {}
        text = (body.get("text") or "").strip()
        phone = _phone_from_attachments(body.get("attachments") or [])
        handle_message(app, user_id, text, phone)
        return

    if utype == "message_callback":
        cb = update.get("callback") or {}
        user = cb.get("user") or {}
        user_id = int(user.get("user_id"))
        app.db.upsert_user(user_id, user.get("first_name", ""), now)
        payload = cb.get("payload") or ""
        callback_id = cb.get("callback_id") or ""
        handle_callback(app, user_id, payload, callback_id)
        return


def handle_message(app, user_id: int, text: str, phone: str | None = None) -> None:
    user = app.db.get_user(user_id)
    state = user["state"] if user else S.IDLE

    # команды
    if text.startswith("/"):
        cmd = text.split()[0].lower()
        if cmd in ("/start", "/menu"):
            handlers_client.send_main_menu(app, user_id)
            return
        if cmd == "/id":
            app.api.send_message(f"Ваш MAX user_id: {user_id}", user_id=user_id)
            return
        if handlers_legal.handle_legal_command(app, user_id, text):
            return
        if handlers_admin.handle_admin_command(app, user_id, text):
            return

    low = text.lower()
    if low in COMMAND_MENU_WORDS:
        handlers_client.send_main_menu(app, user_id)
        return

    # активные состояния FSM (порядок важен)
    if handlers_admin.handle_reply_text(app, user_id, text):
        return
    if handlers_client.handle_rate_comment_text(app, user_id, text):
        return
    if handlers_client.handle_question_text(app, user_id, text):
        return
    if handlers_artist.handle_registration_text(app, user_id, text):
        return
    if handlers_client.handle_order_text(app, user_id, text, contact_phone=phone):
        return

    # контакт пришёл вне анкеты — просто поблагодарим
    if phone and state == S.IDLE:
        app.api.send_message("Контакт получен, спасибо! 🎄", user_id=user_id)
        handlers_client.send_main_menu(app, user_id)
        return

    # fallback
    app.api.send_message(
        "Не понял сообщение 🤔 Воспользуйтесь кнопками меню ниже.",
        user_id=user_id)
    handlers_client.send_main_menu(app, user_id)


def handle_callback(app, user_id: int, payload: str, callback_id: str) -> None:
    prefix = payload.split(":", 1)[0]
    handled = (
        handlers_client.handle_menu_callback(app, user_id, payload, callback_id)
        or handlers_client.handle_order_callback(app, user_id, payload, callback_id)
        or handlers_client.handle_fm_callback(app, user_id, payload, callback_id)
        or handlers_client.handle_rate_callback(app, user_id, payload, callback_id)
        or handlers_artist.handle_artist_callback(app, user_id, payload, callback_id)
        or handlers_admin.handle_admin_callback(app, user_id, payload, callback_id)
        or handlers_legal.handle_legal_callback(app, user_id, payload, callback_id)
    )
    if not handled:
        log.warning("unhandled callback payload=%r from user=%s", payload, user_id)
        app.api.answer_callback(callback_id, notification="Действие недоступно")
