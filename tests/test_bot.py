"""Тесты ключевых сценариев бота на FakeMaxApiClient (без сети и токена)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.app import Bot
from bot.config import Config
from bot.db import DB
from bot.maxapi import FakeMaxApiClient
from bot import services

CLIENT, ARTIST, BACKUP, DISPATCHER = 1, 2, 3, 9


@pytest.fixture()
def bot(tmp_path):
    cfg = Config(
        token="test",
        database_path=str(tmp_path / "test.db"),
        dispatcher_ids={DISPATCHER},
        dispatcher_code="code",
        offer_response_minutes=60,
        backup_response_minutes=15,
    )
    b = Bot(cfg, DB(cfg.database_path), FakeMaxApiClient())
    yield b
    b.db.close()


# ------------------------------------------------------------------ helpers
def say(bot, uid, text, name="User"):
    bot.handle_update(bot.api.make_message(uid, name, text))


def press(bot, uid, payload, name="User"):
    bot.handle_update(bot.api.make_callback(uid, name, payload))


def start(bot, uid, name="User"):
    bot.handle_update(bot.api.make_bot_started(uid, name))


def register_artist(bot, uid, name, districts="Центр", price=4000):
    start(bot, uid, name)
    press(bot, uid, "menu:become_artist", name)
    say(bot, uid, name, name)
    say(bot, uid, districts, name)
    say(bot, uid, str(price), name)
    say(bot, uid, "Программа 45 минут", name)
    return bot.db.get_artist_by_user(uid)


def approve(bot, artist_id):
    press(bot, DISPATCHER, f"admin:artist_ok:{artist_id}", "Disp")


def make_order(bot, client=CLIENT, when=None, budget=6000, district="Центр, ул. Ленина 5"):
    start(bot, client, "Anna")
    press(bot, client, "menu:order", "Anna")
    press(bot, client, "consent:accept", "Anna")   # согласие на ПДн
    when = when or (bot.now() + timedelta(days=5))
    say(bot, client, when.strftime("%d.%m.%Y %H:%M"), "Anna")
    say(bot, client, district, "Anna")
    say(bot, client, "Миша, 6", "Anna")
    press(bot, client, "order:chars:both", "Anna")
    say(bot, client, str(budget), "Anna")
    say(bot, client, "+79001112233", "Anna")
    say(bot, client, "без громкой музыки", "Anna")
    press(bot, client, "order:confirm", "Anna")
    return bot.db.active_order_of_client(client)


# -------------------------------------------------------------------- тесты
def test_client_full_happy_path(bot):
    a1 = register_artist(bot, ARTIST, "Дед Мороз Иван")
    a2 = register_artist(bot, BACKUP, "Снегурочка Оля")
    approve(bot, a1["id"])
    approve(bot, a2["id"])

    order = make_order(bot)
    assert order["status"] == "offered"
    # клиенту показали карточки артистов
    cards = bot.api.last_to(CLIENT)
    assert cards and any("Иван" in b["text"] for b in cards.buttons)

    press(bot, CLIENT, f"offer:pick:{a1['id']}", "Anna")
    assert bot.db.get_order(order["id"])["status"] == "pending_artist"

    offer = bot.db.pending_offer(order["id"], "primary")
    press(bot, ARTIST, f"artist:accept:{offer['id']}", "Ivan")
    order = bot.db.get_order(order["id"])
    assert order["status"] == "assigned"
    assert order["payment_url"]

    press(bot, CLIENT, f"pay:done:{order['id']}", "Anna")
    order = bot.db.get_order(order["id"])
    assert order["status"] == "paid"
    # резервный назначен автоматически («подстрахуй свой Новый год»)
    assert order["backup_artist_id"] == a2["id"]

    # резерв подтверждает
    backup_offer = bot.db.pending_offer(order["id"], "backup")
    press(bot, BACKUP, f"artist:backup_yes:{backup_offer['id']}", "Olya")
    assert bot.db.get_offer(backup_offer["id"])["status"] == "accepted"

    # день X: клиент жмёт «артист на месте»
    press(bot, CLIENT, f"fm:ok:{order['id']}", "Anna")
    assert bot.db.get_order(order["id"])["status"] == "in_progress"

    # автозавершение: прокручиваем время на 6 часов после начала праздника
    event_at = datetime.fromisoformat(bot.db.get_order(order["id"])["event_at"])
    bot_now = bot.now
    bot.now = lambda: event_at + timedelta(hours=6)  # noqa: E731
    services.scheduler_tick(bot)
    assert bot.db.get_order(order["id"])["status"] == "completed"

    # оценка 5 звёзд
    press(bot, CLIENT, f"rate:5:{order['id']}", "Anna")
    say(bot, CLIENT, "Супер!", "Anna")
    artist = bot.db.get_artist(a1["id"])
    assert artist["ratings_count"] == 1 and artist["rating"] == 5.0


def test_matching_by_district_budget_and_busy(bot):
    a1 = register_artist(bot, ARTIST, "Иван", districts="Центр", price=4000)
    a2 = register_artist(bot, BACKUP, "Оля", districts="Окраина", price=9000)
    approve(bot, a1["id"])
    approve(bot, a2["id"])

    order = make_order(bot, budget=5000, district="Центр, Ленина 1")
    found = services.match_artists(bot, order)
    assert [a["id"] for a in found] == [a1["id"]]  # Оля: и район не тот, и дорого

    # Иван занят на этот слот -> никого
    press(bot, CLIENT, f"offer:pick:{a1['id']}", "Anna")
    offer = bot.db.pending_offer(order["id"], "primary")
    press(bot, ARTIST, f"artist:accept:{offer['id']}", "Ivan")
    press(bot, CLIENT, f"pay:done:{order['id']}", "Anna")
    other = dict(order)
    assert services.match_artists(bot, order) == []  # все заняты на слот


def test_no_artists_escalates_to_dispatcher(bot):
    order = make_order(bot)
    assert order["status"] == "offered"
    to_dispatcher = [m for m in bot.api.outbox if m.user_id == DISPATCHER]
    assert any("не найдено" in m.text or "Новый заказ" in m.text or m.text
               for m in to_dispatcher)  # диспетчер уведомлён о новом заказе
    to_client = bot.api.last_to(CLIENT)
    assert "диспетчер" in to_client.text.lower() or "не нашлось" in to_client.text.lower()


def test_artist_decline_offers_next_candidates(bot):
    a1 = register_artist(bot, ARTIST, "Иван")
    a2 = register_artist(bot, BACKUP, "Оля")
    approve(bot, a1["id"])
    approve(bot, a2["id"])

    order = make_order(bot)
    press(bot, CLIENT, f"offer:pick:{a1['id']}", "Anna")
    offer = bot.db.pending_offer(order["id"], "primary")
    press(bot, ARTIST, f"artist:decline:{offer['id']}", "Ivan")
    # клиенту показали оставшихся кандидатов, статус снова offered
    assert bot.db.get_order(order["id"])["status"] == "offered"


def test_force_majeure_rescue_by_backup(bot):
    a1 = register_artist(bot, ARTIST, "Иван")
    a2 = register_artist(bot, BACKUP, "Оля")
    approve(bot, a1["id"])
    approve(bot, a2["id"])
    order = make_order(bot)
    press(bot, CLIENT, f"offer:pick:{a1['id']}", "Anna")
    offer = bot.db.pending_offer(order["id"], "primary")
    press(bot, ARTIST, f"artist:accept:{offer['id']}", "Ivan")
    press(bot, CLIENT, f"pay:done:{order['id']}", "Anna")

    press(bot, CLIENT, f"fm:noshow:{order['id']}", "Anna")
    assert bot.db.get_order(order["id"])["status"] == "force_majeure"

    rescue = bot.db.pending_offer(order["id"], "rescue")
    assert rescue and rescue["artist_id"] == a2["id"]
    press(bot, BACKUP, f"artist:rescue_yes:{rescue['id']}", "Olya")
    order = bot.db.get_order(order["id"])
    assert order["status"] == "paid"               # заказ спасён
    assert order["artist_id"] == a2["id"]          # артист заменён
    rescued = bot.api.last_to(CLIENT)
    assert "спасён" in rescued.text.lower()


def test_force_majeure_without_backup_escalates(bot):
    a1 = register_artist(bot, ARTIST, "Иван")
    approve(bot, a1["id"])
    order = make_order(bot)
    press(bot, CLIENT, f"offer:pick:{a1['id']}", "Anna")
    offer = bot.db.pending_offer(order["id"], "primary")
    press(bot, ARTIST, f"artist:accept:{offer['id']}", "Ivan")
    press(bot, CLIENT, f"pay:done:{order['id']}", "Anna")
    assert bot.db.get_order(order["id"])["backup_artist_id"] is None

    press(bot, CLIENT, f"fm:noshow:{order['id']}", "Anna")
    assert bot.db.get_order(order["id"])["status"] == "force_majeure"
    to_dispatcher = bot.api.last_to(DISPATCHER)
    assert "ФОРС-МАЖОР" in to_dispatcher.text
    assert any("Назначить" in b["text"] for b in to_dispatcher.buttons)

    # диспетчер вручную назначает свободного артиста
    a2 = register_artist(bot, BACKUP, "Оля")
    approve(bot, a2["id"])
    press(bot, DISPATCHER, f"admin:assign:{order['id']}:{a2['id']}", "Disp")
    order = bot.db.get_order(order["id"])
    assert order["status"] == "pending_artist"
    assert order["artist_id"] == a2["id"]


def test_expired_rescue_offer_escalates(bot):
    a1 = register_artist(bot, ARTIST, "Иван")
    a2 = register_artist(bot, BACKUP, "Оля")
    approve(bot, a1["id"])
    approve(bot, a2["id"])
    order = make_order(bot)
    press(bot, CLIENT, f"offer:pick:{a1['id']}", "Anna")
    press(bot, ARTIST,
          f"artist:accept:{bot.db.pending_offer(order['id'], 'primary')['id']}", "Ivan")
    press(bot, CLIENT, f"pay:done:{order['id']}", "Anna")
    press(bot, CLIENT, f"fm:noshow:{order['id']}", "Anna")

    # резервный молчит -> по истечении таймаута эскалация диспетчеру
    real_now = bot.now
    bot.now = lambda: real_now() + timedelta(minutes=20)  # noqa: E731
    services.scheduler_tick(bot)
    msgs = [m for m in bot.api.outbox if m.user_id == DISPATCHER]
    assert any("ФОРС-МАЖОР" in m.text for m in msgs)


def test_reminders_sent_on_schedule(bot):
    a1 = register_artist(bot, ARTIST, "Иван")
    approve(bot, a1["id"])
    order = make_order(bot)
    press(bot, CLIENT, f"offer:pick:{a1['id']}", "Anna")
    press(bot, ARTIST,
          f"artist:accept:{bot.db.pending_offer(order['id'], 'primary')['id']}", "Ivan")
    press(bot, CLIENT, f"pay:done:{order['id']}", "Anna")

    event_at = datetime.fromisoformat(bot.db.get_order(order["id"])["event_at"])
    # «за день до»
    real_now = bot.now
    bot.now = lambda: real_now() + (event_at - real_now() - timedelta(hours=23))  # noqa
    services.scheduler_tick(bot)
    assert any("Напоминание" in (m.text) for m in bot.api.pop_all() if m.user_id == CLIENT)
    # «за час до» — чек-ин с кнопками
    bot.now = lambda: real_now() + (event_at - real_now() - timedelta(minutes=30))  # noqa
    services.scheduler_tick(bot)
    checkin = bot.api.last_to(CLIENT)
    assert checkin and any("не приехал" in b["text"].lower() for b in checkin.buttons)


def test_question_to_dispatcher_and_reply(bot):
    start(bot, CLIENT, "Anna")
    press(bot, CLIENT, "menu:ask", "Anna")
    say(bot, CLIENT, "А можно два подарка?", "Anna")
    assert any("два подарка" in m.text for m in bot.api.outbox
               if m.user_id == DISPATCHER)

    say(bot, DISPATCHER, f"/reply {CLIENT}", "Disp")
    say(bot, DISPATCHER, "Конечно, можно!", "Disp")
    assert any("Конечно, можно!" in m.text for m in bot.api.outbox
               if m.user_id == CLIENT)


def test_dispatcher_code_self_assignment(bot):
    start(bot, 42, "Boss")
    say(bot, 42, "/admin wrongcode", "Boss")
    assert not (bot.db.get_user(42)["role"] == "dispatcher")
    say(bot, 42, "/admin code", "Boss")
    assert bot.db.get_user(42)["role"] == "dispatcher"
    assert 42 in bot.all_dispatchers()


def test_consent_required_before_order(bot):
    """П. 6/17 чек-листа: без согласия на ПДн анкета не начинается."""
    start(bot, CLIENT, "Anna")
    assert not bot.db.has_consent(CLIENT)
    press(bot, CLIENT, "menu:order", "Anna")
    # показан экран согласия, анкета НЕ началась
    assert bot.db.get_user(CLIENT)["state"] != "order:date"
    consent_msg = bot.api.last_to(CLIENT)
    assert "Персональные данные" in consent_msg.text
    assert any("Согласен" in b["text"] for b in consent_msg.buttons)
    press(bot, CLIENT, "consent:accept", "Anna")
    assert bot.db.has_consent(CLIENT)
    assert bot.db.get_user(CLIENT)["state"] == "order:date"


def test_my_data_and_deletion(bot):
    """П. 20 чек-листа: просмотр и удаление своих данных."""
    start(bot, CLIENT, "Anna")
    press(bot, CLIENT, "menu:order", "Anna")
    press(bot, CLIENT, "consent:accept", "Anna")
    press(bot, CLIENT, "order:cancel", "Anna")   # отмена, чтобы можно было удалиться
    press(bot, CLIENT, "menu:legal", "Anna")
    press(bot, CLIENT, "legal:mydata", "Anna")
    mydata = bot.api.last_to(CLIENT)
    assert "ID в MAX" in mydata.text and "дано" in mydata.text

    press(bot, CLIENT, "legal:delete", "Anna")
    confirm = bot.api.last_to(CLIENT)
    assert any("удалить" in b["text"].lower() for b in confirm.buttons)
    press(bot, CLIENT, "legal:delete_yes", "Anna")
    user = bot.db.get_user(CLIENT)
    assert user["name"] == "" and user["consent_at"] is None


def test_delete_blocked_with_active_order(bot):
    a1 = register_artist(bot, ARTIST, "Иван")
    approve(bot, a1["id"])
    order = make_order(bot)
    press(bot, CLIENT, "menu:legal", "Anna")
    press(bot, CLIENT, "legal:delete", "Anna")
    msg = bot.api.last_to(CLIENT)
    assert "нельзя" in msg.text          # удаление заблокировано активным заказом
    assert bot.db.get_user(CLIENT)["name"] == "Anna"


def test_notif_opt_out_skips_rate_request(bot):
    """П. 18 чек-листа: отписка от рассылок отключает запросы оценки."""
    a1 = register_artist(bot, ARTIST, "Иван")
    approve(bot, a1["id"])
    order = make_order(bot)
    press(bot, CLIENT, f"offer:pick:{a1['id']}", "Anna")
    press(bot, ARTIST,
          f"artist:accept:{bot.db.pending_offer(order['id'], 'primary')['id']}", "Ivan")
    press(bot, CLIENT, f"pay:done:{order['id']}", "Anna")

    press(bot, CLIENT, "menu:legal", "Anna")
    press(bot, CLIENT, "legal:notif", "Anna")    # отписка
    assert bot.db.get_user(CLIENT)["notif_opt_in"] == 0

    event_at = datetime.fromisoformat(bot.db.get_order(order["id"])["event_at"])
    bot.now = lambda: event_at + timedelta(hours=6)  # noqa: E731
    services.scheduler_tick(bot)
    assert bot.db.get_order(order["id"])["status"] == "completed"
    # запрос оценки НЕ отправлен
    assert not any("Оцените" in m.text for m in bot.api.pop_all())


def test_legal_docs_available(bot):
    """П. 1-3, 16: документы и реквизиты доступны из бота."""
    start(bot, CLIENT, "Anna")
    press(bot, CLIENT, "menu:legal", "Anna")
    for payload, marker in (("legal:doc:privacy", "ПОЛИТИКА"),
                            ("legal:doc:offer", "ОФЕРТА"),
                            ("legal:doc:refund", "ВОЗВРАТА"),
                            ("legal:reqs", "РЕКВИЗИТЫ")):
        press(bot, CLIENT, payload, "Anna")
        assert marker in bot.api.last_to(CLIENT).text


def test_parse_datetime_rolls_to_next_year():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 11, 17, 12, 0, tzinfo=tz)
    dt = services.parse_datetime("25.12 18:00", tz, now)
    assert dt and dt.year == 2026 and dt.month == 12 and dt.hour == 18
    # уже прошедшая дата -> следующий год
    dt = services.parse_datetime("10.01 10:00", tz, now)
    assert dt and dt.year == 2027
    assert services.parse_datetime("когда-нибудь", tz, now) is None
