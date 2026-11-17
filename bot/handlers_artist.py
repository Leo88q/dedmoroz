"""Обработчики сценария артиста: регистрация, модерация, ответы на заказы."""
from __future__ import annotations

import logging
import re

from . import services, texts
from .fsm import S

log = logging.getLogger(__name__)


def start_registration(app, user_id: int) -> None:
    existing = app.db.get_artist_by_user(user_id)
    if existing and existing["status"] == "approved":
        app.api.send_message(
            "Вы уже одобренный артист 🎭 Новые заказы будут приходить сюда автоматически.",
            user_id=user_id)
        return
    app.db.set_state(user_id, S.ARTIST_NAME, {})
    app.api.send_message(texts.ARTIST_WELCOME, user_id=user_id)


def handle_registration_text(app, user_id: int, text: str) -> bool:
    """Шаги анкеты артиста. True — сообщение поглощено."""
    user = app.db.get_user(user_id)
    state = user["state"]
    data = app.db.get_data(user_id)

    if state == S.ARTIST_NAME:
        if len(text.strip()) < 2:
            app.api.send_message("Напишите сценическое имя (минимум 2 символа).",
                                 user_id=user_id)
            return True
        data["stage_name"] = text.strip()
        app.db.set_state(user_id, S.ARTIST_DISTRICTS, data)
        app.api.send_message(texts.ARTIST_DISTRICTS, user_id=user_id)
        return True

    if state == S.ARTIST_DISTRICTS:
        data["districts"] = text.strip()
        app.db.set_state(user_id, S.ARTIST_PRICE, data)
        app.api.send_message(texts.ARTIST_PRICE, user_id=user_id)
        return True

    if state == S.ARTIST_PRICE:
        digits = re.sub(r"\D", "", text)
        if not digits:
            app.api.send_message("Напишите стоимость числом, например: 4000",
                                 user_id=user_id)
            return True
        data["price"] = int(digits)
        app.db.set_state(user_id, S.ARTIST_ABOUT, data)
        app.api.send_message(texts.ARTIST_ABOUT, user_id=user_id)
        return True

    if state == S.ARTIST_ABOUT:
        now = app.now().isoformat()
        artist_id = app.db.create_artist(
            user_id, data.get("stage_name", "Артист"),
            data.get("districts", ""), data.get("price", 0), text.strip(), now)
        app.db.set_state(user_id, S.IDLE, {})
        app.db.set_role(user_id, "artist")
        app.db.add_event(None, "artist_application", f"artist #{artist_id}", now)
        app.api.send_message(texts.ARTIST_PENDING, user_id=user_id)
        artist = app.db.get_artist(artist_id)
        card = texts.NEW_ARTIST_CARD.format(
            id=artist_id, name=artist["stage_name"], districts=artist["districts"],
            price=artist["price"], description=artist["description"])
        from . import keyboards
        services.notify_dispatchers(
            app, card, attachments=[keyboards.admin_artist_moderation(artist_id)])
        return True

    return False


def handle_artist_callback(app, user_id: int, payload: str, callback_id: str) -> bool:
    """Ответы артиста на офферы: primary/backup/rescue × accept/decline."""
    parts = payload.split(":")
    if parts[0] != "artist" or len(parts) != 3:
        return False
    cmd, offer_id = parts[1], int(parts[2])
    offer = app.db.get_offer(offer_id)
    if not offer or offer["status"] != "pending":
        app.api.answer_callback(callback_id, notification="Предложение уже неактуально")
        return True
    artist = app.db.get_artist(offer["artist_id"])
    if not artist or artist["user_id"] != user_id:
        app.api.answer_callback(callback_id, notification="Это предложение не вам")
        return True
    order = app.db.get_order(offer["order_id"])

    if cmd == "accept":  # основной заказ
        if order["status"] != "pending_artist" or order["artist_id"] != artist["id"]:
            app.api.answer_callback(callback_id, notification="Заказ уже занят")
            app.db.set_offer_status(offer_id, "expired")
            return True
        app.api.answer_callback(callback_id, notification="Заказ ваш! 🎄")
        app.api.send_message(
            f"✅ Вы приняли заказ №{order['id']}: {services.fmt_dt(order['event_at'])}, "
            f"{order['district']}. Контакт клиента будет доступен после оплаты. "
            f"Не подведите — и рейтинг вырастет!",
            user_id=user_id)
        services.artist_accepted(app, offer)
        return True

    if cmd == "decline":
        app.api.answer_callback(callback_id, notification="Отказ принят")
        app.api.send_message("Хорошо, отклонили заказ. Будут новые! 🎭",
                             user_id=user_id)
        services.artist_declined(app, offer)
        return True

    if cmd == "backup_yes":
        app.api.answer_callback(callback_id, notification="Вы в резерве 🛟")
        app.db.set_offer_status(offer_id, "accepted")
        app.db.add_event(order["id"], "backup_confirmed", f"artist #{artist['id']}",
                         app.now().isoformat())
        app.api.send_message(
            f"🛟 Вы подтверждены резервным по заказу №{order['id']} "
            f"({services.fmt_dt(order['event_at'])}). Держите слот свободным — "
            f"если основной артист не выйдет, праздник за вами.",
            user_id=user_id)
        services.notify_dispatchers(
            app, f"🛟 Заказ №{order['id']}: резервный «{artist['stage_name']}» подтвердил.")
        return True

    if cmd == "backup_no":
        app.api.answer_callback(callback_id, notification="Принято")
        app.db.set_offer_status(offer_id, "declined")
        app.api.send_message("Поняли, спасибо за ответ!", user_id=user_id)
        order = app.db.get_order(order["id"])
        app.db.update_order(order["id"], app.now().isoformat(), backup_artist_id=None)
        services.assign_backup(app, order)  # ищем другого резервного
        return True

    if cmd == "rescue_yes":
        if order["status"] != "force_majeure":
            app.api.answer_callback(callback_id, notification="Ситуация уже решена")
            return True
        app.api.answer_callback(callback_id, notification="Спасибо! Вы спасаете праздник! 🚀")
        services.rescue_accepted(app, offer)
        return True

    if cmd == "rescue_no":
        app.api.answer_callback(callback_id, notification="Принято")
        app.db.set_offer_status(offer_id, "declined")
        app.api.send_message("Поняли. Диспетчер подключится.", user_id=user_id)
        services.escalate(app, order, "резервный артист отказался")
        return True

    return False
