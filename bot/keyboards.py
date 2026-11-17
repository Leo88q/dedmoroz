"""Клавиатуры бота (inline_keyboard attachments)."""
from __future__ import annotations

from .maxapi import cb, keyboard, link, request_contact


def main_menu(is_artist: bool = False) -> dict:
    rows = [
        [cb("🎄 Заказать праздник", "menu:order", "positive")],
        [cb("📋 Мой заказ", "menu:my")],
        [cb("🚨 Форс-мажор / замена", "menu:fm", "negative")],
        [cb("❓ Вопрос диспетчеру", "menu:ask")],
    ]
    if not is_artist:
        rows.append([cb("🎭 Стать артистом", "menu:become_artist")])
    return keyboard(rows)


def characters() -> dict:
    return keyboard([
        [cb("🎅 Дед Мороз", "order:chars:santa"),
         cb("❄️ Снегурочка", "order:chars:snegurochka"),
         cb("🎅❄️ Оба", "order:chars:both")],
    ])


def phone_request() -> dict:
    return keyboard([[request_contact("📞 Отправить контакт")]])


def confirm_order() -> dict:
    return keyboard([
        [cb("✅ Всё верно", "order:confirm", "positive"),
         cb("✏️ Заполнить заново", "order:restart")],
        [cb("❌ Отменить", "order:cancel", "negative")],
    ])


def artist_choice(candidates: list[tuple[int, str]]) -> dict:
    """candidates: [(artist_id, stage_name), ...]"""
    rows = [
        [cb(f"{i + 1}. {name}", f"offer:pick:{artist_id}", "positive")]
        for i, (artist_id, name) in enumerate(candidates)
    ]
    rows.append([cb("🔄 Другие варианты / диспетчер", "offer:none")])
    return keyboard(rows)


def pay_keyboard(order_id: int, payment_url: str) -> dict:
    return keyboard([
        [link("💳 Оплатить предоплату", payment_url)],
        [cb("✅ Я оплатил", f"pay:done:{order_id}", "positive")],
        [cb("❌ Отменить заказ", f"order:cancel:{order_id}", "negative")],
    ])


def checkin(order_id: int) -> dict:
    return keyboard([
        [cb("✅ Артист на месте", f"fm:ok:{order_id}", "positive")],
        [cb("⏰ Опаздывает", f"fm:late:{order_id}")],
        [cb("🚨 Не приехал!", f"fm:noshow:{order_id}", "negative")],
    ])


def stars(order_id: int) -> dict:
    return keyboard([
        [cb("⭐️" * n, f"rate:{n}:{order_id}") for n in range(1, 4)],
        [cb("⭐️" * n, f"rate:{n}:{order_id}") for n in range(4, 6)],
    ])


def artist_offer(offer_id: int) -> dict:
    return keyboard([
        [cb("✅ Беру заказ", f"artist:accept:{offer_id}", "positive"),
         cb("❌ Откажусь", f"artist:decline:{offer_id}", "negative")],
    ])


def backup_offer(offer_id: int) -> dict:
    return keyboard([
        [cb("🛟 Готов подстраховать", f"artist:backup_yes:{offer_id}", "positive"),
         cb("❌ Не могу", f"artist:backup_no:{offer_id}", "negative")],
    ])


def rescue_offer(offer_id: int) -> dict:
    return keyboard([
        [cb("🚀 Выезжаю!", f"artist:rescue_yes:{offer_id}", "positive"),
         cb("❌ Не успею", f"artist:rescue_no:{offer_id}", "negative")],
    ])


def admin_artist_moderation(artist_id: int) -> dict:
    return keyboard([
        [cb("✅ Одобрить", f"admin:artist_ok:{artist_id}", "positive"),
         cb("❌ Отклонить", f"admin:artist_no:{artist_id}", "negative")],
    ])


def admin_escalation(order_id: int, free_artists: list[tuple[int, str]]) -> dict:
    rows = [
        [cb(f"➡️ Назначить: {name}", f"admin:assign:{order_id}:{artist_id}", "positive")]
        for artist_id, name in free_artists[:5]
    ]
    rows.append([cb("💬 Ответить клиенту", f"admin:reply_client:{order_id}")])
    rows.append([cb("❌ Отменить заказ (возврат)", f"admin:cancel:{order_id}", "negative")])
    return keyboard(rows)


def skip_comment() -> dict:
    return keyboard([[cb("Пропустить", "rate:skip", "default")]])


def my_order_actions(order_id: int, status: str) -> dict:
    rows = []
    if status in ("assigned", "paid", "in_progress"):
        rows.append([cb("🚨 Сообщить о форс-мажоре", f"fm:noshow:{order_id}", "negative")])
    if status in ("draft", "offered", "pending_artist", "assigned"):
        rows.append([cb("❌ Отменить заказ", f"order:cancel:{order_id}", "negative")])
    return keyboard(rows) if rows else keyboard([[cb("🏠 В меню", "menu:home")]])
