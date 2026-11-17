#!/usr/bin/env python3
"""Консольное демо бота «Диспетчерская Деда Мороза» — без токена MAX и сети.

Запуск:
    python demo.py            # интерактивный режим
    python demo.py --script   # автоматический прогон полного сценария

В интерактивном режиме вы управляете пользователями:
    /users                    — список пользователей
    /user <id> [имя]          — переключиться на пользователя (создаётся)
    /tick <минуты>            — «прокрутить время» для планировщика
    /quit                     — выход
    <текст>                   — отправить сообщение боту от текущего пользователя
    <число>                   — нажать кнопку с этим номером из последнего сообщения

Предзаведённые пользователи: 1 — Анна (клиент), 2 — Иван (артист),
3 — Ольга (артист), 9 — Диспетчер (DISPATCHER_IDS=9).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.app import Bot                      # noqa: E402
from bot.config import Config                # noqa: E402
from bot.db import DB                        # noqa: E402
from bot.maxapi import FakeMaxApiClient      # noqa: E402
from bot import services                     # noqa: E402

NAMES = {1: "Анна", 2: "Иван", 3: "Ольга", 4: "Пётр", 9: "Диспетчер"}


class DemoBot(Bot):
    """Bot с управляемыми часами для демонстрации напоминаний/таймаутов."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.time_shift = timedelta(0)

    def now(self) -> datetime:
        return super().now() + self.time_shift


def make_demo_bot() -> DemoBot:
    cfg = Config(
        token="demo",
        database_path=os.path.join(tempfile.gettempdir(), "dedmoroz_demo.db"),
        dispatcher_ids={9},
        dispatcher_code="dedmoroz",
        offer_response_minutes=60,
        backup_response_minutes=15,
    )
    if os.path.exists(cfg.database_path):
        os.remove(cfg.database_path)
    return DemoBot(cfg, DB(cfg.database_path), FakeMaxApiClient())


def render(bot: DemoBot) -> None:
    """Напечатать все исходящие сообщения бота."""
    for msg in bot.api.pop_all():
        target = NAMES.get(msg.user_id, f"user {msg.user_id}") if msg.user_id else "?"
        print(f"\n  🤖 → {target}:")
        for line in msg.text.splitlines():
            print(f"     {line}")
        for i, btn in enumerate(msg.buttons, 1):
            print(f"     [{i}] {btn['text']}")


def press_button(bot: DemoBot, user_id: int, idx: int) -> bool:
    """Нажать idx-ю кнопку последнего сообщения пользователю."""
    last = bot.api.last_to(user_id)
    if not last or not (1 <= idx <= len(last.buttons)):
        print(f"  ⚠️ у {NAMES.get(user_id, user_id)} нет кнопки №{idx} "
              f"(кнопок: {len(last.buttons) if last else 0})")
        return False
    btn = last.buttons[idx - 1]
    if btn["type"] != "callback":
        print(f"  ⚠️ кнопка «{btn['text']}» не callback (тип {btn['type']})")
        return False
    print(f"\n👆 {NAMES.get(user_id, user_id)} нажимает «{btn['text']}»")
    bot.handle_update(bot.api.make_callback(user_id, NAMES.get(user_id, ""), btn["payload"]))
    render(bot)
    return True


def say(bot: DemoBot, user_id: int, text: str) -> None:
    print(f"\n💬 {NAMES.get(user_id, user_id)}: {text}")
    bot.handle_update(bot.api.make_message(user_id, NAMES.get(user_id, ""), text))
    render(bot)


def start(bot: DemoBot, user_id: int) -> None:
    bot.handle_update(bot.api.make_bot_started(user_id, NAMES.get(user_id, "")))
    render(bot)


def scripted() -> None:
    """Полный прогон: регистрация артиста → модерация → заказ → оплата →
    резерв → форс-мажор → спасение → оценка."""
    bot = make_demo_bot()
    print("=" * 70)
    print("ДЕМО: «Диспетчерская Деда Мороза» — полный сценарий")
    print("=" * 70)

    print("\n─── 1. Артист Иван регистрируется ───")
    start(bot, 2)
    press_button(bot, 2, 5)          # «Стать артистом»
    say(bot, 2, "Дед Мороз Иван")
    say(bot, 2, "Центр, Северный")
    say(bot, 2, "4500")
    say(bot, 2, "45 минут: игры, хоровод, подарки. Опыт 7 лет.")

    print("\n─── 2. Артист Ольга регистрируется ───")
    start(bot, 3)
    press_button(bot, 3, 5)
    say(bot, 3, "Снегурочка Оля")
    say(bot, 3, "Центр")
    say(bot, 3, "4000")
    say(bot, 3, "30 минут сказки, конкурсы. Опыт 3 года.")

    print("\n─── 3. Диспетчер одобряет обоих ───")
    start(bot, 9)
    say(bot, 9, "/artists")
    press_button(bot, 9, 1)          # одобрить последнюю присланную заявку
    say(bot, 9, "/artists")          # перечитать список — осталась одна
    press_button(bot, 9, 1)          # одобрить вторую

    print("\n─── 4. Клиент Анна оформляет заказ ───")
    start(bot, 1)
    press_button(bot, 1, 1)          # «Заказать праздник»
    press_button(bot, 1, 1)          # «✅ Согласен» — согласие на ПДн (152-ФЗ)
    say(bot, 1, "25.12 18:00")
    say(bot, 1, "Центр, ул. Ленина 5, кв. 12")
    say(bot, 1, "Миша, 6")
    press_button(bot, 1, 3)          # «Оба»
    say(bot, 1, "6000")
    say(bot, 1, "+7 900 111-22-33")
    say(bot, 1, "Миша боится громкой музыки")
    press_button(bot, 1, 1)          # «Всё верно»

    print("\n─── 5. Анна выбирает артиста ───")
    press_button(bot, 1, 1)          # первый из карточек

    print("\n─── 6. Артист Иван подтверждает заказ ───")
    press_button(bot, 2, 1)          # «Беру заказ»

    print("\n─── 7. Анна оплачивает ───")
    press_button(bot, 1, 2)          # «Я оплатил»

    print("\n─── 8. Резерв: Ольга подтверждает подстраховку ───")
    press_button(bot, 3, 1)          # «Готов подстраховать»

    print("\n─── 9. День X, за час до — чек-ин (прокручиваем время) ───")
    order = bot.db.active_order_of_client(1)
    event_at = datetime.fromisoformat(order["event_at"])
    target = event_at - timedelta(minutes=30)
    bot.time_shift = target - datetime.now(bot.tz)
    services.scheduler_tick(bot)
    render(bot)

    print("\n─── 10. ФОРС-МАЖОР: артист не приехал ───")
    press_button(bot, 1, 3)          # «Не приехал!»

    print("\n─── 11. Резервная Ольга выручает ───")
    press_button(bot, 3, 1)          # «Выезжаю!»

    print("\n─── 12. Праздник состоялся, автозавершение и оценка ───")
    bot.time_shift += timedelta(hours=6)
    services.scheduler_tick(bot)
    render(bot)
    press_button(bot, 1, 5)          # 5 звёзд
    say(bot, 1, "Спасибо, Миша в восторге!")

    print("\n─── 13. Диспетчер смотрит панель и журнал ───")
    say(bot, 9, "/admin")
    say(bot, 9, "/orders")
    say(bot, 9, "/events")
    print("\n✅ Демо завершено. Интерактивный режим: python demo.py")


def interactive() -> None:
    bot = make_demo_bot()
    current = 1
    print("Интерактивное демо. Команды: /users, /user <id> [имя], "
          "/tick <мин>, /quit. Число — нажать кнопку.")
    start(bot, current)
    while True:
        try:
            line = input(f"\n[{current} {NAMES.get(current, '')}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line == "/quit":
            break
        if line == "/users":
            print("  " + ", ".join(f"{k}={v}" for k, v in NAMES.items()))
            continue
        if line.startswith("/user"):
            parts = line.split(maxsplit=2)
            if len(parts) >= 2 and parts[1].isdigit():
                current = int(parts[1])
                if len(parts) == 3:
                    NAMES[current] = parts[2]
                if not bot.db.get_user(current):
                    start(bot, current)
            continue
        if line.startswith("/tick"):
            minutes = int(line.split()[1]) if len(line.split()) > 1 else 60
            bot.time_shift += timedelta(minutes=minutes)
            services.scheduler_tick(bot)
            render(bot)
            continue
        if line.isdigit():
            if not press_button(bot, current, int(line)):
                print("  (нет такой кнопки — отправьте /user или сообщение)")
            continue
        say(bot, current, line)
    print("Пока! 🎄")


if __name__ == "__main__":
    if "--script" in sys.argv:
        scripted()
    else:
        interactive()
