"""Обработчики сценария клиента: анкета заказа, выбор артиста, оплата,
чек-ин в день праздника, форс-мажор, оценка."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from . import keyboards, services, texts
from .fsm import S

log = logging.getLogger(__name__)


def _data(app, user_id: int) -> dict:
    return app.db.get_data(user_id)


# ------------------------------------------------------------------- старт
def send_main_menu(app, user_id: int) -> None:
    artist = app.db.get_artist_by_user(user_id)
    app.db.set_state(user_id, S.IDLE, {})
    app.api.send_message(texts.WELCOME, user_id=user_id,
                         attachments=[keyboards.main_menu(is_artist=bool(artist))])


def start_order(app, user_id: int) -> None:
    app.db.set_state(user_id, S.ORDER_DATE, {})
    app.api.send_message(texts.ASK_DATE, user_id=user_id)


# ------------------------------------------------------------ шаги анкеты
def handle_order_text(app, user_id: int, text: str,
                      contact_phone: str | None = None) -> bool:
    """Обработать текстовый ввод внутри анкеты. True — сообщение поглощено."""
    user = app.db.get_user(user_id)
    state = user["state"]
    data = _data(app, user_id)

    if state == S.ORDER_DATE:
        dt = services.parse_datetime(text, app.tz, app.now())
        if not dt:
            app.api.send_message(
                "Не понял дату 😔 Напишите так: 25.12 18:00", user_id=user_id)
            return True
        if dt < app.now() + timedelta(hours=2):
            app.api.send_message(
                "Дата слишком близко или уже прошла — нам нужно минимум 2 часа "
                "на подбор. Укажите более позднее время.", user_id=user_id)
            return True
        data["event_at"] = dt.isoformat()
        app.db.set_state(user_id, S.ORDER_DISTRICT, data)
        app.api.send_message(texts.ASK_DISTRICT, user_id=user_id)
        return True

    if state == S.ORDER_DISTRICT:
        if len(text) < 4:
            app.api.send_message("Уточните, пожалуйста, район и адрес.",
                                 user_id=user_id)
            return True
        data["district"] = text
        app.db.set_state(user_id, S.ORDER_CHILD, data)
        app.api.send_message(texts.ASK_CHILD, user_id=user_id)
        return True

    if state == S.ORDER_CHILD:
        match = re.match(r"^(.+?)[,\s]+(\d{1,2})\s*(год|года|лет|л\.)?$", text.strip())
        if not match:
            app.api.send_message("Напишите имя и возраст, например: «Миша, 6»",
                                 user_id=user_id)
            return True
        data["child_name"] = match.group(1).strip(" ,")
        data["child_age"] = int(match.group(2))
        app.db.set_state(user_id, S.ORDER_CHARACTERS, data)
        app.api.send_message(texts.ASK_CHARACTERS, user_id=user_id,
                             attachments=[keyboards.characters()])
        return True

    if state == S.ORDER_CHARACTERS:
        app.api.send_message("Выберите кнопкой, кого позвать 👆", user_id=user_id,
                             attachments=[keyboards.characters()])
        return True

    if state == S.ORDER_BUDGET:
        digits = re.sub(r"\D", "", text)
        if not digits:
            app.api.send_message("Напишите сумму числом, например: 5000",
                                 user_id=user_id)
            return True
        data["budget"] = int(digits)
        app.db.set_state(user_id, S.ORDER_PHONE, data)
        app.api.send_message(texts.ASK_PHONE, user_id=user_id,
                             attachments=[keyboards.phone_request()])
        return True

    if state == S.ORDER_PHONE:
        phone = contact_phone or text.strip()
        if contact_phone is None and not re.match(r"^[+()\- 0-9]{6,18}$", phone):
            app.api.send_message(
                "Нажмите кнопку «Отправить контакт» или напишите номер цифрами.",
                user_id=user_id, attachments=[keyboards.phone_request()])
            return True
        data["phone"] = phone
        app.db.set_state(user_id, S.ORDER_WISHES, data)
        app.api.send_message(texts.ASK_WISHES, user_id=user_id)
        return True

    if state == S.ORDER_WISHES:
        data["wishes"] = "" if text.strip() == "-" else text.strip()
        _show_summary(app, user_id, data)
        return True

    return False


def _show_summary(app, user_id: int, data: dict) -> None:
    app.db.set_state(user_id, S.ORDER_CONFIRM, data)
    child = f"{data.get('child_name', '—')}, {data.get('child_age', '?')} л."
    app.api.send_message(
        texts.ORDER_SUMMARY.format(
            id="(новая)", event_at=services.fmt_dt(data["event_at"]),
            district=data["district"], child=child,
            characters=texts.CHARACTER_NAMES.get(data.get("characters", "both")),
            budget=data.get("budget", 0), phone=data.get("phone", "—"),
            wishes=data.get("wishes") or "—",
        ),
        user_id=user_id, attachments=[keyboards.confirm_order()],
    )


# ------------------------------------------------------------- колбэки меню
def handle_menu_callback(app, user_id: int, payload: str, callback_id: str) -> bool:
    action = payload.split(":")
    if action[0] != "menu":
        return False
    app.api.answer_callback(callback_id)
    cmd = action[1] if len(action) > 1 else "home"

    if cmd in ("home",):
        send_main_menu(app, user_id)
    elif cmd == "order":
        if app.db.active_order_of_client(user_id):
            app.api.send_message(
                "У вас уже есть активный заказ. Сначала завершите или отмените его "
                "(«📋 Мой заказ»).", user_id=user_id)
            send_main_menu(app, user_id)
        else:
            start_order(app, user_id)
    elif cmd == "my":
        order = app.db.active_order_of_client(user_id)
        if not order:
            app.api.send_message("У вас пока нет активных заказов. 🎄",
                                 user_id=user_id)
            send_main_menu(app, user_id)
        else:
            app.api.send_message(
                services.order_status_line(app, order), user_id=user_id,
                attachments=[keyboards.my_order_actions(order["id"], order["status"])])
    elif cmd == "fm":
        order = app.db.active_order_of_client(user_id)
        if not order or not order["artist_id"]:
            app.api.send_message(
                "По активному заказу артист ещё не назначен — форс-мажор запускать "
                "не нужно. Если тревожно — напишите диспетчеру.", user_id=user_id)
        else:
            app.api.send_message(
                "Что случилось?", user_id=user_id,
                attachments=[keyboards.checkin(order["id"])])
    elif cmd == "ask":
        app.db.set_state(user_id, S.CLIENT_QUESTION, {})
        app.api.send_message("Напишите вопрос — диспетчер ответит вам здесь же.",
                             user_id=user_id)
    elif cmd == "become_artist":
        from . import handlers_artist
        handlers_artist.start_registration(app, user_id)
    return True


# -------------------------------------------------------- колбэки заказа
def handle_order_callback(app, user_id: int, payload: str, callback_id: str) -> bool:
    parts = payload.split(":")
    if parts[0] == "order" and len(parts) >= 2:
        cmd = parts[1]
        if cmd == "chars" and len(parts) == 3:
            app.api.answer_callback(callback_id)
            data = _data(app, user_id)
            data["characters"] = parts[2]
            app.db.set_state(user_id, S.ORDER_BUDGET, data)
            app.api.send_message(texts.ASK_BUDGET, user_id=user_id)
            return True
        if cmd == "confirm":
            app.api.answer_callback(callback_id, notification="Заявка принята! 🎄")
            data = _data(app, user_id)
            now = app.now().isoformat()
            order_id = app.db.create_order(user_id, {
                "child_name": data.get("child_name", ""),
                "child_age": data.get("child_age"),
                "district": data.get("district", ""),
                "event_at": data["event_at"],
                "characters": data.get("characters", "both"),
                "budget": data.get("budget", 0),
                "wishes": data.get("wishes", ""),
                "phone": data.get("phone", ""),
                "status": "offered",
            }, now)
            app.db.set_state(user_id, S.IDLE, {})
            app.db.add_event(order_id, "order_created", "", now)
            services.notify_dispatchers(
                app, f"🆕 Новый заказ №{order_id} от {user_id}: "
                     f"{services.fmt_dt(data['event_at'])}, {data.get('district')}, "
                     f"бюджет {data.get('budget')} ₽.")
            order = app.db.get_order(order_id)
            services.offer_candidates(app, order)
            return True
        if cmd == "restart":
            app.api.answer_callback(callback_id)
            start_order(app, user_id)
            return True
        if cmd == "cancel":
            app.api.answer_callback(callback_id, notification="Заказ отменён")
            data = _data(app, user_id)
            order_id = int(parts[2]) if len(parts) > 2 else None
            order = (app.db.get_order(order_id) if order_id
                     else app.db.active_order_of_client(user_id))
            if order:
                app.db.update_order(order["id"], app.now().isoformat(),
                                    status="cancelled")
                app.db.add_event(order["id"], "cancelled_by_client", "",
                                 app.now().isoformat())
                services.notify_dispatchers(
                    app, f"❌ Клиент отменил заказ №{order['id']}.")
                artist = (app.db.get_artist(order["artist_id"])
                          if order["artist_id"] else None)
                if artist:
                    app.api.send_message(
                        f"❌ Заказ №{order['id']} отменён клиентом.",
                        user_id=artist["user_id"])
            app.db.set_state(user_id, S.IDLE, {})
            app.api.send_message("Заказ отменён. Будем рады видеть вас снова! 🎄",
                                 user_id=user_id)
            send_main_menu(app, user_id)
            return True

    if parts[0] == "offer" and len(parts) == 3:
        cmd, arg = parts[1], parts[2]
        if cmd == "pick":
            app.api.answer_callback(callback_id)
            order = app.db.active_order_of_client(user_id)
            if not order:
                app.api.send_message("Заказ не найден 😔", user_id=user_id)
                return True
            services.pick_artist(app, order, int(arg))
            return True
        if cmd == "none":
            app.api.answer_callback(callback_id)
            order = app.db.active_order_of_client(user_id)
            services.notify_dispatchers(
                app, f"🔄 Клиент {user_id} не выбрал никого по заказу "
                     f"№{order['id'] if order else '?'} — нужен ручной подбор.")
            app.api.send_message(
                "Передали диспетчеру — он подберёт артиста вручную и напишет вам.",
                user_id=user_id)
            return True

    if parts[0] == "pay" and len(parts) == 3 and parts[1] == "done":
        order_id = int(parts[2])
        order = app.db.get_order(order_id)
        # В проде статус выставляет webhook эквайринга; здесь — подтверждение клиента.
        if order and order["client_id"] == user_id and order["status"] == "assigned":
            now = app.now().isoformat()
            app.db.update_order(order_id, now, status="paid")
            app.db.add_event(order_id, "paid", "", now)
            app.api.answer_callback(callback_id, notification="Оплата принята! ✅")
            app.api.send_message(texts.PAID.format(id=order_id), user_id=user_id)
            order = app.db.get_order(order_id)
            services.schedule_reminders(app, order)
            services.assign_backup(app, order)   # «подстрахуй свой Новый год»
            send_main_menu(app, user_id)
        else:
            app.api.answer_callback(
                callback_id, notification="Оплата для этого заказа не ожидается")
        return True

    return False


# -------------------------------------------- чек-ин, форс-мажор, рейтинг
def handle_fm_callback(app, user_id: int, payload: str, callback_id: str) -> bool:
    parts = payload.split(":")
    if parts[0] != "fm" or len(parts) != 3:
        return False
    cmd, order_id = parts[1], int(parts[2])
    order = app.db.get_order(order_id)
    if not order or order["client_id"] != user_id:
        app.api.answer_callback(callback_id, notification="Заказ не найден")
        return True
    artist = app.db.get_artist(order["artist_id"]) if order["artist_id"] else None
    now = app.now().isoformat()

    if cmd == "ok":
        app.api.answer_callback(callback_id, notification="Отлично! 🎄")
        app.db.update_order(order_id, now, status="in_progress")
        app.db.add_event(order_id, "artist_arrived", "", now)
        app.api.send_message(
            "🎄 Чудесно! Волшебство началось. После праздника попросим оценить артиста.",
            user_id=user_id)
        services.notify_dispatchers(
            app, f"✅ Заказ №{order_id}: артист на месте, праздник идёт.")
        return True

    if cmd == "late":
        app.api.answer_callback(callback_id)
        app.db.add_event(order_id, "artist_late", "", now)
        app.api.send_message(texts.FM_LATE_OK, user_id=user_id)
        if artist:
            app.api.send_message(
                f"⏰ Клиент по заказу №{order_id} сообщил, что вы опаздываете. "
                f"Пожалуйста, свяжитесь с клиентом: {order['phone']}. "
                f"Если не успеваете — сообщите, запустим резерв!",
                user_id=artist["user_id"])
        services.notify_dispatchers(
            app, f"⏰ Заказ №{order_id}: клиент сообщил, что артист опаздывает.")
        return True

    if cmd == "noshow":
        app.api.answer_callback(callback_id)
        if order["status"] == "force_majeure":
            app.api.send_message(
                "Мы уже решаем проблему — диспетчер на связи.", user_id=user_id)
            return True
        services.start_force_majeure(app, order, "клиент сообщил «не приехал»")
        return True
    return False


def handle_rate_callback(app, user_id: int, payload: str, callback_id: str) -> bool:
    parts = payload.split(":")
    if parts[0] != "rate":
        return False
    if len(parts) == 2 and parts[1] == "skip":
        app.api.answer_callback(callback_id)
        app.db.set_state(user_id, S.IDLE, {})
        app.api.send_message(texts.RATE_THANKS, user_id=user_id)
        send_main_menu(app, user_id)
        return True
    if len(parts) == 3:
        stars, order_id = int(parts[1]), int(parts[2])
        order = app.db.get_order(order_id)
        if not order or order["client_id"] != user_id or not order["artist_id"]:
            app.api.answer_callback(callback_id, notification="Заказ не найден")
            return True
        if app.db.get_rating(order_id):
            app.api.answer_callback(callback_id, notification="Оценка уже сохранена")
            return True
        app.api.answer_callback(callback_id, notification="Спасибо! ⭐")
        app.db.set_state(user_id, S.RATE_COMMENT,
                         {"order_id": order_id, "stars": stars})
        app.api.send_message(texts.RATE_COMMENT, user_id=user_id,
                             attachments=[keyboards.skip_comment()])
        return True
    return False


def handle_rate_comment_text(app, user_id: int, text: str) -> bool:
    user = app.db.get_user(user_id)
    if user["state"] != S.RATE_COMMENT:
        return False
    data = _data(app, user_id)
    order = app.db.get_order(int(data["order_id"]))
    comment = "" if text.strip() == "-" else text.strip()
    if order and order["artist_id"]:
        app.db.add_rating(order["id"], order["artist_id"], int(data["stars"]),
                          comment, app.now().isoformat())
    app.db.set_state(user_id, S.IDLE, {})
    app.api.send_message(texts.RATE_THANKS, user_id=user_id)
    send_main_menu(app, user_id)
    return True


def handle_question_text(app, user_id: int, text: str) -> bool:
    user = app.db.get_user(user_id)
    if user["state"] != S.CLIENT_QUESTION:
        return False
    order = app.db.active_order_of_client(user_id)
    note = texts.CLIENT_QUESTION_NOTE.format(
        user_id=user_id, name=user["name"],
        order=f"№{order['id']}" if order else "(нет активного)", text=text)
    services.notify_dispatchers(app, note)
    app.db.set_state(user_id, S.IDLE, {})
    app.api.send_message("Вопрос передан диспетчеру — ответим вам здесь же. 🎅",
                         user_id=user_id)
    send_main_menu(app, user_id)
    return True
