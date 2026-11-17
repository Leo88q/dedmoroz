"""Состояния FSM диалога с пользователем."""
from __future__ import annotations


class S:
    """Имена состояний (хранятся в users.state)."""

    IDLE = "idle"

    # Анкета заказа (клиент)
    ORDER_DATE = "order:date"            # ждём дату и время
    ORDER_DISTRICT = "order:district"    # ждём район/адрес
    ORDER_CHILD = "order:child"          # ждём имя и возраст ребёнка
    ORDER_CHARACTERS = "order:characters"  # ждём выбор персонажей кнопкой
    ORDER_BUDGET = "order:budget"        # ждём бюджет
    ORDER_PHONE = "order:phone"          # ждём телефон
    ORDER_WISHES = "order:wishes"        # ждём пожелания
    ORDER_CONFIRM = "order:confirm"      # подтверждение анкеты

    # Регистрация артиста
    ARTIST_NAME = "artist:name"
    ARTIST_DISTRICTS = "artist:districts"
    ARTIST_PRICE = "artist:price"
    ARTIST_ABOUT = "artist:about"

    # Вопрос диспетчеру от клиента
    CLIENT_QUESTION = "client:question"

    # Ответ диспетчера клиенту (в data храним user_id клиента)
    DISPATCHER_REPLY = "dispatcher:reply"

    # Комментарий к оценке (в data — order_id и stars)
    RATE_COMMENT = "rate:comment"
