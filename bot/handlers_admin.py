"""Обработчики диспетчера: панель, модерация артистов, ручное назначение,
ответы клиентам, журнал событий."""
from __future__ import annotations

import logging

from . import keyboards, services, texts
from .fsm import S

log = logging.getLogger(__name__)

ACTIVE_STATUSES = ("offered", "pending_artist", "assigned", "paid", "in_progress")


def is_dispatcher(app, user_id: int) -> bool:
    return user_id in app.cfg.dispatcher_ids or (
        (u := app.db.get_user(user_id)) is not None and u["role"] == "dispatcher"
    )


def handle_admin_command(app, user_id: int, text: str) -> bool:
    """Команды /admin, /artists, /orders, /events, /reply. True — поглощено."""
    if not text.startswith("/"):
        return False
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower()

    if cmd == "/admin":
        # /admin <код> — самоназначение диспетчером
        if not is_dispatcher(app, user_id):
            if arg.strip() and arg.strip() == app.cfg.dispatcher_code:
                app.db.set_role(user_id, "dispatcher")
                app.api.send_message("🧑‍💼 Вы назначены диспетчером.", user_id=user_id)
            else:
                app.api.send_message("Неверный код диспетчера.", user_id=user_id)
                return True
        pending = len(app.db.pending_artists())
        active = len(app.db.orders_in_status(ACTIVE_STATUSES))
        fm = len(app.db.orders_in_status(("force_majeure",)))
        app.api.send_message(
            texts.ADMIN_PANEL.format(pending_artists=pending, active_orders=active,
                                     force_majeure=fm),
            user_id=user_id)
        return True

    if not is_dispatcher(app, user_id):
        return False

    if cmd == "/artists":
        pending = app.db.pending_artists()
        if not pending:
            app.api.send_message("Заявок на модерации нет ✅", user_id=user_id)
            return True
        for a in pending:
            card = texts.NEW_ARTIST_CARD.format(
                id=a["id"], name=a["stage_name"], districts=a["districts"],
                price=a["price"], description=a["description"])
            app.api.send_message(card, user_id=user_id,
                                 attachments=[keyboards.admin_artist_moderation(a["id"])])
        return True

    if cmd == "/orders":
        orders = app.db.orders_in_status(ACTIVE_STATUSES + ("force_majeure",))
        if not orders:
            app.api.send_message("Активных заказов нет 🎄", user_id=user_id)
            return True
        lines = []
        for o in orders[:15]:
            artist = app.db.get_artist(o["artist_id"]) if o["artist_id"] else None
            lines.append(
                f"№{o['id']} • {texts.STATUS_NAMES.get(o['status'], o['status'])} • "
                f"{services.fmt_dt(o['event_at'])} • {o['district']} • "
                f"{o['budget']} ₽ • артист: "
                f"{artist['stage_name'] if artist else '—'}"
            )
        app.api.send_message("📦 Заказы:\n\n" + "\n".join(lines), user_id=user_id)
        return True

    if cmd == "/events":
        events = app.db.last_events(12)
        if not events:
            app.api.send_message("Журнал пуст.", user_id=user_id)
            return True
        lines = [
            f"#{e['id']} [{e['kind']}] заказ {e['order_id'] or '—'}: {e['payload']}"
            for e in events
        ]
        app.api.send_message("🗒 Журнал (свежие сверху):\n\n" + "\n".join(lines),
                             user_id=user_id)
        return True

    if cmd == "/reply":
        # /reply <user_id> — следующий текст уйдёт клиенту
        if not arg.strip().isdigit():
            app.api.send_message("Формат: /reply <user_id клиента>", user_id=user_id)
            return True
        app.db.set_state(user_id, S.DISPATCHER_REPLY, {"client_id": int(arg.strip())})
        app.api.send_message(
            f"Напишите ответ — он уйдёт клиенту #{arg.strip()}.", user_id=user_id)
        return True

    return False


def handle_reply_text(app, user_id: int, text: str) -> bool:
    """Диспетчер отвечает клиенту (состояние DISPATCHER_REPLY)."""
    user = app.db.get_user(user_id)
    if user["state"] != S.DISPATCHER_REPLY:
        return False
    data = app.db.get_data(user_id)
    client_id = int(data.get("client_id", 0))
    if client_id:
        app.api.send_message(f"🧑‍💼 Диспетчер: {text}", user_id=client_id)
        app.api.send_message("✅ Ответ отправлен клиенту.", user_id=user_id)
    app.db.set_state(user_id, S.IDLE, {})
    return True


def handle_admin_callback(app, user_id: int, payload: str, callback_id: str) -> bool:
    parts = payload.split(":")
    if parts[0] != "admin" or not is_dispatcher(app, user_id):
        return False
    cmd = parts[1]
    now = app.now().isoformat()

    if cmd in ("artist_ok", "artist_no") and len(parts) == 3:
        artist_id = int(parts[2])
        artist = app.db.get_artist(artist_id)
        if not artist:
            app.api.answer_callback(callback_id, notification="Артист не найден")
            return True
        if cmd == "artist_ok":
            app.db.set_artist_status(artist_id, "approved")
            app.api.answer_callback(callback_id, notification="Артист одобрен ✅")
            app.api.send_message(texts.ARTIST_APPROVED, user_id=artist["user_id"])
            app.db.add_event(None, "artist_approved", f"artist #{artist_id}", now)
        else:
            app.db.set_artist_status(artist_id, "rejected")
            app.api.answer_callback(callback_id, notification="Артист отклонён")
            app.api.send_message(texts.ARTIST_REJECTED, user_id=artist["user_id"])
            app.db.add_event(None, "artist_rejected", f"artist #{artist_id}", now)
        return True

    if cmd == "assign" and len(parts) == 4:
        # Ручное назначение артиста при эскалации
        order_id, artist_id = int(parts[2]), int(parts[3])
        order = app.db.get_order(order_id)
        artist = app.db.get_artist(artist_id)
        if not order or not artist:
            app.api.answer_callback(callback_id, notification="Заказ/артист не найден")
            return True
        app.api.answer_callback(callback_id,
                                notification=f"Отправили оффер «{artist['stage_name']}»")
        app.db.update_order(order_id, now, artist_id=artist_id,
                            status="pending_artist")
        services.send_order_offer(app, order, artist, "primary")
        app.db.add_event(order_id, "manual_assign", f"artist #{artist_id}", now)
        return True

    if cmd == "reply_client" and len(parts) == 3:
        order_id = int(parts[2])
        order = app.db.get_order(order_id)
        if not order:
            app.api.answer_callback(callback_id, notification="Заказ не найден")
            return True
        app.api.answer_callback(callback_id)
        app.db.set_state(user_id, S.DISPATCHER_REPLY,
                         {"client_id": int(order["client_id"])})
        app.api.send_message(
            f"Напишите ответ — он уйдёт клиенту заказа №{order_id} "
            f"(#{order['client_id']}).", user_id=user_id)
        return True

    if cmd == "cancel" and len(parts) == 3:
        order_id = int(parts[2])
        order = app.db.get_order(order_id)
        if not order:
            app.api.answer_callback(callback_id, notification="Заказ не найден")
            return True
        app.db.update_order(order_id, now, status="cancelled")
        app.db.add_event(order_id, "cancelled_by_dispatcher", "", now)
        app.api.answer_callback(callback_id, notification="Заказ отменён")
        app.api.send_message(
            f"Заказ №{order_id} отменён. Если была предоплата — диспетчер "
            f"оформит возврат. Приносим извинения 🙏", user_id=order["client_id"])
        return True

    return False
