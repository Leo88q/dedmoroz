"""Бизнес-логика: подбор артистов, офферы, резерв, форс-мажор, напоминания.

Все функции получают `app` — объект бота с атрибутами db, api, cfg и методом now().
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import keyboards, texts
from .maxapi import keyboard

log = logging.getLogger(__name__)

MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

BUSY_WINDOW_HOURS = 3          # артист занят ±3 часа от начала заказа
BUDGET_TOLERANCE = 1.15        # артист подходит, если его цена <= бюджет * 1.15
AUTOCOMPLETE_HOURS = 4         # автозавершение заказа спустя 4 ч после начала


# --------------------------------------------------------------------- время
def parse_datetime(text: str, tz: ZoneInfo, now: datetime) -> datetime | None:
    """'25.12 18:00' / '25.12.2026 18:00' / '25.12 18-00' -> aware datetime."""
    text = text.strip().replace("/", ".").replace("-", ":").replace("  ", " ")
    parts = text.split(" ")
    if len(parts) != 2:
        return None
    date_part, time_part = parts
    try:
        d = [int(x) for x in date_part.split(".")]
        t = [int(x) for x in time_part.split(":")]
        if len(d) == 2:
            day, month, year = d[0], d[1], now.year
        elif len(d) == 3:
            day, month, year = d[0], d[1], d[2]
        else:
            return None
        hour, minute = (t + [0])[:2]
        dt = datetime(year, month, day, hour, minute, tzinfo=tz)
        if len(d) == 2 and dt < now - timedelta(hours=1):
            dt = dt.replace(year=year + 1)  # «25.12» в ноябре -> ближайший декабрь
        return dt
    except (ValueError, TypeError):
        return None


def fmt_dt(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return f"{dt.day} {MONTHS_RU[dt.month]}, {dt:%H:%M}"


# ------------------------------------------------------------------ matching
def district_matches(artist_districts: str, order_district: str) -> bool:
    if not artist_districts.strip():
        return True
    order_low = order_district.lower()
    for d in artist_districts.split(","):
        d = d.strip().lower()
        if d and (d in order_low or order_low in d):
            return True
    return False


def match_artists(app, order, limit: int = 3, exclude: set[int] | None = None) -> list:
    """Подобрать артистов под заказ: район, бюджет, занятость, рейтинг."""
    exclude = exclude or set()
    event_dt = datetime.fromisoformat(order["event_at"])
    win = timedelta(hours=BUSY_WINDOW_HOURS)
    busy = app.db.busy_artist_ids(
        (event_dt - win).isoformat(), (event_dt + win).isoformat()
    )
    budget = int(order["budget"] or 0)
    result = []
    for artist in app.db.approved_artists():
        aid = int(artist["id"])
        if aid in exclude or aid in busy:
            continue
        if not district_matches(artist["districts"], order["district"]):
            continue
        if budget and artist["price"] > budget * BUDGET_TOLERANCE:
            continue
        result.append(artist)
        if len(result) >= limit:
            break
    return result


# ------------------------------------------------------------------- офферы
def send_order_offer(app, order, artist, kind: str) -> int:
    """Создать оффер и отправить артисту сообщение с кнопками."""
    now = app.now()
    minutes = (app.cfg.backup_response_minutes if kind == "rescue"
               else app.cfg.offer_response_minutes)
    expires = now + timedelta(minutes=minutes)
    offer_id = app.db.create_offer(order["id"], artist["id"], kind,
                                   now.isoformat(), expires.isoformat())
    common = dict(
        id=order["id"], event_at=fmt_dt(order["event_at"]),
        district=order["district"], budget=order["budget"],
        characters=texts.CHARACTER_NAMES.get(order["characters"], order["characters"]),
        child=f"{order['child_name']}, {order['child_age']} л.",
        wishes=order["wishes"] or "—", minutes=minutes,
    )
    if kind == "primary":
        text = texts.NEW_ORDER_OFFER.format(**common)
        kb = keyboards.artist_offer(offer_id)
    elif kind == "backup":
        text = texts.BACKUP_OFFER.format(**common)
        kb = keyboards.backup_offer(offer_id)
    else:
        text = texts.RESCUE_OFFER.format(**common)
        kb = keyboards.rescue_offer(offer_id)
    app.api.send_message(text, user_id=artist["user_id"], attachments=[kb])
    app.db.add_event(order["id"], f"offer_{kind}", f"artist #{artist['id']}",
                     now.isoformat())
    return offer_id


def offer_candidates(app, order, exclude: set[int] | None = None) -> bool:
    """Показать клиенту карточки 2–3 артистов. False — никого не нашли."""
    candidates = match_artists(app, order, limit=3, exclude=exclude)
    if not candidates:
        app.api.send_message(texts.NO_ARTISTS, user_id=order["client_id"])
        escalate(app, order, "нет свободных артистов на слот")
        return False
    app.db.update_order(order["id"], app.now().isoformat(),
                        status="offered",
                        candidates=json.dumps([a["id"] for a in candidates]))
    lines = [texts.OFFER_HEADER.format(n=len(candidates))]
    for i, a in enumerate(candidates, 1):
        lines.append(texts.ARTIST_CARD.format(
            num=i, name=a["stage_name"], rating=a["rating"],
            ratings_count=a["ratings_count"], price=a["price"],
            districts=a["districts"] or "любой",
            description=(a["description"] or "")[:200],
        ))
    kb = keyboards.artist_choice([(int(a["id"]), a["stage_name"]) for a in candidates])
    app.api.send_message("\n\n".join(lines), user_id=order["client_id"], attachments=[kb])
    return True


def pick_artist(app, order, artist_id: int) -> None:
    """Клиент выбрал артиста: шлём оффер, заказ -> pending_artist."""
    artist = app.db.get_artist(artist_id)
    if not artist:
        return
    app.db.update_order(order["id"], app.now().isoformat(),
                        status="pending_artist", artist_id=artist_id)
    send_order_offer(app, order, artist, "primary")
    app.api.send_message(
        texts.WAIT_ARTIST.format(name=artist["stage_name"],
                                 minutes=app.cfg.offer_response_minutes),
        user_id=order["client_id"],
    )


def artist_accepted(app, offer) -> None:
    """Артист подтвердил заказ: назначаем, выставляем оплату, подбираем резерв."""
    now = app.now().isoformat()
    order = app.db.get_order(offer["order_id"])
    artist = app.db.get_artist(offer["artist_id"])
    app.db.set_offer_status(offer["id"], "accepted")
    payment_url = f"{app.cfg.payment_base_url}/{order['id']}"
    app.db.update_order(order["id"], now, status="assigned", artist_id=artist["id"],
                        payment_url=payment_url)
    app.db.add_event(order["id"], "artist_accepted", f"artist #{artist['id']}", now)
    app.api.send_message(
        texts.PAYMENT.format(name=artist["stage_name"], id=order["id"]),
        user_id=order["client_id"],
        attachments=[keyboards.pay_keyboard(order["id"], payment_url)],
    )
    notify_dispatchers(
        app,
        f"✅ Заказ №{order['id']}: артист «{artist['stage_name']}» подтвердил, "
        f"ждём оплату клиента.",
    )


def artist_declined(app, offer) -> None:
    """Артист отказался: возвращаем клиента к выбору."""
    now = app.now().isoformat()
    app.db.set_offer_status(offer["id"], "declined")
    order = app.db.get_order(offer["order_id"])
    artist = app.db.get_artist(offer["artist_id"])
    app.db.add_event(order["id"], "artist_declined", f"artist #{artist['id']}", now)
    app.api.send_message(
        texts.ARTIST_DECLINED.format(name=artist["stage_name"]),
        user_id=order["client_id"],
    )
    tried = set(_candidates(order)) | {int(artist["id"])}
    if not offer_candidates(app, order, exclude=tried):
        app.db.update_order(order["id"], now, status="offered")


def _candidates(order) -> list[int]:
    try:
        return [int(x) for x in json.loads(order["candidates"] or "[]")]
    except (ValueError, TypeError):
        return []


# ------------------------------------------------------------------- резерв
def assign_backup(app, order) -> None:
    """Автоназначение резервного артиста — ядро «подстрахуй свой Новый год»."""
    exclude = {int(order["artist_id"])} if order["artist_id"] else set()
    backups = match_artists(app, order, limit=1, exclude=exclude)
    if not backups:
        app.db.add_event(order["id"], "backup_not_found", "", app.now().isoformat())
        notify_dispatchers(
            app,
            f"⚠️ Заказ №{order['id']}: не нашлось резервного артиста "
            f"({fmt_dt(order['event_at'])}, {order['district']}). Назначьте вручную!",
        )
        return
    backup = backups[0]
    app.db.update_order(order["id"], app.now().isoformat(),
                        backup_artist_id=backup["id"])
    send_order_offer(app, order, backup, "backup")


# --------------------------------------------------------------- форс-мажор
def start_force_majeure(app, order, reason: str) -> None:
    """Клиент сообщил «не приехал» (или артист отменил): запускаем резерв."""
    now = app.now().isoformat()
    app.db.update_order(order["id"], now, status="force_majeure")
    app.db.add_event(order["id"], "force_majeure", reason, now)
    app.api.send_message(texts.FM_STARTED, user_id=order["client_id"])

    backup_id = order["backup_artist_id"]
    backup = app.db.get_artist(backup_id) if backup_id else None
    if backup:
        send_order_offer(app, order, backup, "rescue")  # у резервного N минут на ответ
        notify_dispatchers(
            app,
            f"🚨 Заказ №{order['id']}: форс-мажор ({reason}). "
            f"Резервному «{backup['stage_name']}» отправлен SOS, "
            f"ждём {app.cfg.backup_response_minutes} мин.",
        )
    else:
        escalate(app, order, reason)


def rescue_accepted(app, offer) -> None:
    """Резервный артист выехал вместо основного."""
    now = app.now().isoformat()
    order = app.db.get_order(offer["order_id"])
    backup = app.db.get_artist(offer["artist_id"])
    app.db.set_offer_status(offer["id"], "accepted")
    # заказ уже оплачен клиентом — сразу возвращаем в paid
    new_status = "paid" if order["status"] == "force_majeure" else order["status"]
    app.db.update_order(order["id"], now, artist_id=backup["id"], status=new_status)
    app.db.add_event(order["id"], "rescued", f"backup artist #{backup['id']}", now)
    app.api.send_message(
        texts.FM_RESCUED.format(name=backup["stage_name"]),
        user_id=order["client_id"],
    )
    app.api.send_message(
        f"🎄 Вы — герой дня! Заказ №{order['id']} ваш: "
        f"{fmt_dt(order['event_at'])}, {order['district']}, "
        f"телефон клиента {order['phone']}. Диспетчер на связи.",
        user_id=backup["user_id"],
    )
    notify_dispatchers(
        app,
        f"🛟 Заказ №{order['id']}: резервный «{backup['stage_name']}» выехал к клиенту.",
    )


def escalate(app, order, reason: str) -> None:
    """Эскалация диспетчерам с кнопками ручного назначения."""
    now = app.now().isoformat()
    app.db.add_event(order["id"], "escalation", reason, now)
    client = app.db.get_user(order["client_id"])
    backup = (app.db.get_artist(order["backup_artist_id"])
              if order["backup_artist_id"] else None)
    backup_line = (f"Резервный артист ({backup['stage_name']}) не ответил."
                   if backup else "Резервного артиста нет.")
    free = [(int(a["id"]), a["stage_name"])
            for a in match_artists(app, order, limit=5)]
    kb = keyboards.admin_escalation(order["id"], free)
    text = texts.ESCALATION.format(
        id=order["id"], event_at=fmt_dt(order["event_at"]),
        district=order["district"], reason=reason,
        client=client["name"] if client else order["client_id"],
        phone=order["phone"] or "—", backup_line=backup_line,
    )
    for d in app.all_dispatchers():
        app.api.send_message(text, user_id=d, attachments=[kb])
    app.api.send_message(
        texts.FM_ESCALATED.format(minutes=app.cfg.backup_response_minutes,
                                  phone=order["phone"] or "—"),
        user_id=order["client_id"],
    )


# -------------------------------------------------------------- напоминания
def schedule_reminders(app, order) -> None:
    """После оплаты: за день — напоминание, за час до — чек-ин."""
    event_dt = datetime.fromisoformat(order["event_at"])
    day_before = event_dt - timedelta(hours=24)
    checkin = event_dt - timedelta(hours=1)
    if day_before > app.now():
        app.db.add_reminder(order["id"], "day_before", day_before.isoformat())
    app.db.add_reminder(order["id"], "checkin", checkin.isoformat())


def scheduler_tick(app) -> None:
    """Периодическая задача: напоминания, истёкшие офферы, автозавершение."""
    now = app.now()
    now_iso = now.isoformat()

    # истёкшие офферы
    for offer in app.db.expired_offers(now_iso):
        app.db.set_offer_status(offer["id"], "expired")
        order = app.db.get_order(offer["order_id"])
        if not order:
            continue
        artist = app.db.get_artist(offer["artist_id"])
        app.db.add_event(order["id"], f"offer_{offer['kind']}_expired",
                         f"artist #{offer['artist_id']}", now_iso)
        if offer["kind"] == "primary" and order["status"] == "pending_artist":
            app.api.send_message(
                texts.ARTIST_DECLINED.format(name=artist["stage_name"] if artist else "—"),
                user_id=order["client_id"],
            )
            offer_candidates(app, order, exclude=set(_candidates(order)))
        elif offer["kind"] in ("backup", "rescue"):
            if offer["kind"] == "rescue" and order["status"] == "force_majeure":
                escalate(app, order, "резервный артист не ответил вовремя")
            else:
                app.db.update_order(order["id"], now_iso, backup_artist_id=None)
                assign_backup(app, order)

    # автозавершение: событие прошло AUTOCOMPLETE_HOURS назад
    horizon = (now - timedelta(hours=AUTOCOMPLETE_HOURS)).isoformat()
    for order in app.db.orders_due(horizon):
        app.db.update_order(order["id"], now_iso, status="completed")
        app.db.add_event(order["id"], "autocompleted", "", now_iso)
        app.db.add_reminder(order["id"], "rate_request", now_iso)

    _send_due_reminders(app, now_iso)


def _send_due_reminders(app, now_iso: str) -> None:
    """Отправка напоминаний, чек-инов и запросов оценки (включая созданные
    автозавершением в этом же тикe)."""
    for rem in app.db.due_reminders(now_iso):
        order = app.db.get_order(rem["order_id"])
        # отменённые — пропускаем; завершённым шлём только запрос оценки
        if not order or order["status"] == "cancelled" or (
            order["status"] == "completed" and rem["kind"] != "rate_request"
        ):
            app.db.mark_reminder_sent(rem["id"])
            continue
        artist = app.db.get_artist(order["artist_id"]) if order["artist_id"] else None
        name = artist["stage_name"] if artist else "артист"
        if rem["kind"] == "day_before":
            app.api.send_message(
                f"🎄 Напоминание: завтра, {fmt_dt(order['event_at'])}, к вам приедет "
                f"«{name}» (заказ №{order['id']}). Адрес: {order['district']}. "
                f"Если планы изменились — напишите диспетчеру.",
                user_id=order["client_id"],
            )
        elif rem["kind"] == "checkin":
            app.api.send_message(
                texts.CHECKIN.format(id=order["id"], name=name,
                                     time=fmt_dt(order["event_at"]).split(", ")[-1]),
                user_id=order["client_id"],
                attachments=[keyboards.checkin(order["id"])],
            )
        elif rem["kind"] == "rate_request":
            # это информационное сообщение — уважаем отписку (152-ФЗ/реклама)
            user = app.db.get_user(order["client_id"])
            if user and not user["notif_opt_in"]:
                app.db.mark_reminder_sent(rem["id"])
                continue
            app.api.send_message(
                texts.RATE_ASK.format(name=name),
                user_id=order["client_id"],
                attachments=[keyboards.stars(order["id"])],
            )
        app.db.mark_reminder_sent(rem["id"])


# ------------------------------------------------------------------ прочее
def notify_dispatchers(app, text: str, attachments: list | None = None) -> None:
    for user_id in app.all_dispatchers():
        app.api.send_message(text, user_id=user_id, attachments=attachments)


def order_status_line(app, order) -> str:
    artist = app.db.get_artist(order["artist_id"]) if order["artist_id"] else None
    return texts.MY_ORDER.format(
        id=order["id"],
        status=texts.STATUS_NAMES.get(order["status"], order["status"]),
        event_at=fmt_dt(order["event_at"]),
        district=order["district"],
        artist=artist["stage_name"] if artist else "ещё не назначен",
    )
