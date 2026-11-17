"""Обработчики юридического блока: согласие на ПДн, документы,
«мои данные», отписка от рассылок, удаление данных (152-ФЗ)."""
from __future__ import annotations

import logging

from . import keyboards, legal_docs, texts
from .fsm import S

log = logging.getLogger(__name__)

DOCS = {
    "privacy": ("privacy_url", legal_docs.privacy_text),
    "offer": ("offer_url", legal_docs.offer_text),
    "refund": ("refund_url", legal_docs.refund_text),
}

DOC_TITLES = {"privacy": "Политика конфиденциальности",
              "offer": "Оферта", "refund": "Условия возврата"}


def show_consent(app, user_id: int) -> None:
    app.api.send_message(
        texts.CONSENT, user_id=user_id,
        attachments=[keyboards.consent_keyboard(app.cfg.privacy_url,
                                                app.cfg.offer_url)],
    )


def show_legal_menu(app, user_id: int) -> None:
    user = app.db.get_user(user_id)
    opt_in = bool(user["notif_opt_in"]) if user else True
    app.api.send_message(texts.LEGAL_MENU, user_id=user_id,
                         attachments=[keyboards.legal_menu(opt_in)])


def send_doc(app, user_id: int, kind: str) -> None:
    """Документ ссылкой (если задан URL) или текстом."""
    url_attr, renderer = DOCS[kind]
    url = getattr(app.cfg, url_attr)
    if url:
        from .maxapi import keyboard, link
        app.api.send_message(f"{DOC_TITLES[kind]} — по ссылке ниже:",
                             user_id=user_id,
                             attachments=[keyboard([[link("Открыть документ", url)]])])
    else:
        app.api.send_message(renderer(app.cfg), user_id=user_id)


def show_my_data(app, user_id: int) -> None:
    user = app.db.get_user(user_id)
    consent = (f"дано {user['consent_at'][:10]}" if user and user["consent_at"]
               else "не дано")
    notif = ("🔔 подписаны" if user and user["notif_opt_in"] else "🔕 отписаны")
    app.api.send_message(
        texts.MY_DATA.format(
            user_id=user_id,
            name=(user["name"] if user else "") or "—",
            consent=consent,
            orders=app.db.orders_count(user_id),
            notif=notif,
            email=app.cfg.legal_email,
        ),
        user_id=user_id,
    )


def handle_legal_callback(app, user_id: int, payload: str, callback_id: str) -> bool:
    parts = payload.split(":")
    if parts[0] not in ("legal", "consent"):
        return False

    if parts[0] == "consent" and len(parts) == 2 and parts[1] == "accept":
        app.db.set_consent(user_id, app.now().isoformat())
        app.api.answer_callback(callback_id, notification=texts.CONSENT_OK)
        from . import handlers_client
        handlers_client.start_order(app, user_id)
        return True

    cmd = parts[1] if len(parts) > 1 else "menu"

    if cmd == "doc" and len(parts) == 3 and parts[2] in DOCS:
        app.api.answer_callback(callback_id)
        send_doc(app, user_id, parts[2])
        return True

    if cmd == "reqs":
        app.api.answer_callback(callback_id)
        app.api.send_message(legal_docs.requisites_text(app.cfg), user_id=user_id)
        return True

    if cmd == "mydata":
        app.api.answer_callback(callback_id)
        show_my_data(app, user_id)
        return True

    if cmd == "notif":
        user = app.db.get_user(user_id)
        new_value = not (user and user["notif_opt_in"])
        app.db.set_notif_opt_in(user_id, new_value)
        app.api.answer_callback(
            callback_id,
            notification="Готово ✅" if new_value else "Вы отписаны 🔕")
        app.api.send_message(
            texts.NOTIF_ON if new_value else texts.NOTIF_OFF, user_id=user_id)
        show_legal_menu(app, user_id)
        return True

    if cmd == "delete":
        order = app.db.active_order_of_client(user_id)
        if order:
            app.api.answer_callback(callback_id)
            app.api.send_message(texts.DELETE_BLOCKED.format(id=order["id"]),
                                 user_id=user_id)
            return True
        app.api.answer_callback(callback_id)
        app.api.send_message(texts.DELETE_CONFIRM, user_id=user_id,
                             attachments=[keyboards.delete_confirm()])
        return True

    if cmd == "delete_yes":
        order = app.db.active_order_of_client(user_id)
        if order:
            app.api.answer_callback(callback_id)
            app.api.send_message(texts.DELETE_BLOCKED.format(id=order["id"]),
                                 user_id=user_id)
            return True
        app.db.anonymize_user(user_id, app.now().isoformat())
        app.api.answer_callback(callback_id, notification="Данные удалены")
        app.api.send_message(texts.DELETE_DONE, user_id=user_id)
        return True

    if cmd == "menu":
        app.api.answer_callback(callback_id)
        show_legal_menu(app, user_id)
        return True

    return False


def handle_legal_command(app, user_id: int, text: str) -> bool:
    """Команды /privacy /offer /refund /requisites /delete. True — поглощено."""
    cmd = text.split()[0].lower()
    if cmd in ("/privacy", "/offer", "/refund"):
        send_doc(app, user_id, cmd[1:])
        return True
    if cmd == "/requisites":
        app.api.send_message(legal_docs.requisites_text(app.cfg), user_id=user_id)
        return True
    if cmd == "/delete":
        order = app.db.active_order_of_client(user_id)
        if order:
            app.api.send_message(texts.DELETE_BLOCKED.format(id=order["id"]),
                                 user_id=user_id)
        else:
            app.api.send_message(texts.DELETE_CONFIRM, user_id=user_id,
                                 attachments=[keyboards.delete_confirm()])
        return True
    return False
