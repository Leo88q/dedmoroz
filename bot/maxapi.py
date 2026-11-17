"""Клиент MAX Bot API (https://dev.max.ru/docs-api).

Официальные факты о API:
  * базовый URL: https://platform-api.max.ru (для РФ — https://platform-api2.max.ru,
    нужен сертификат Минцифры);
  * авторизация: заголовок `Authorization: <access_token>`;
  * обновления: GET /updates?limit=&timeout=&marker=&types=  -> {updates, marker};
  * отправка:   POST /messages?user_id= | ?chat_id=  {text, attachments};
  * ответ на кнопку: POST /answers?callback_id= {message?, notification?};
  * inline-клавиатура: attachment {"type": "inline_keyboard",
    "payload": {"buttons": [[{type, text, payload|url, intent?}]]}}.

Типы обновлений, которые обрабатывает бот:
  bot_started, message_created, message_callback.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterable

import requests

log = logging.getLogger(__name__)

# Типы событий, которые мы запрашиваем у /updates
UPDATE_TYPES = ["bot_started", "message_created", "message_callback"]


class MaxApiError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"MAX API error {status}: {body[:300]}")
        self.status = status
        self.body = body


class MaxApiClient:
    """Тонкий синхронный клиент MAX Bot API."""

    def __init__(self, token: str, base_url: str = "https://platform-api.max.ru"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": token})

    # ------------------------------------------------------------------ HTTP
    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        resp = self.session.request(method, url, timeout=40, **kwargs)
        if resp.status_code >= 400:
            raise MaxApiError(resp.status_code, resp.text)
        if not resp.content:
            return {}
        return resp.json()

    # ---------------------------------------------------------------- методы
    def get_me(self) -> dict:
        return self._request("GET", "/me")

    def get_updates(
        self,
        marker: int | None = None,
        limit: int = 100,
        timeout: int = 30,
        types: Iterable[str] = UPDATE_TYPES,
    ) -> dict:
        params: dict[str, Any] = {"limit": limit, "timeout": timeout}
        if marker is not None:
            params["marker"] = marker
        if types:
            params["types"] = ",".join(types)
        return self._request("GET", "/updates", params=params)

    def send_message(
        self,
        text: str,
        user_id: int | None = None,
        chat_id: int | None = None,
        attachments: list[dict] | None = None,
    ) -> dict:
        params = {}
        if user_id is not None:
            params["user_id"] = user_id
        if chat_id is not None:
            params["chat_id"] = chat_id
        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        return self._request("POST", "/messages", params=params, json=body)

    def edit_message(
        self,
        message_id: str,
        text: str,
        attachments: list[dict] | None = None,
    ) -> dict:
        body: dict[str, Any] = {"text": text}
        if attachments is not None:
            body["attachments"] = attachments
        return self._request("PUT", "/messages", params={"message_id": message_id}, json=body)

    def answer_callback(
        self,
        callback_id: str,
        notification: str | None = None,
        message: dict | None = None,
    ) -> dict:
        body: dict[str, Any] = {}
        if notification:
            body["notification"] = notification
        if message:
            body["message"] = message
        return self._request(
            "POST", "/answers", params={"callback_id": callback_id}, json=body
        )


def keyboard(rows: list[list[dict]]) -> dict:
    """Собрать attachment inline_keyboard из рядов кнопок."""
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def cb(text: str, payload: str, intent: str | None = None) -> dict:
    """Кнопка-callback."""
    btn: dict[str, Any] = {"type": "callback", "text": text, "payload": payload}
    if intent:
        btn["intent"] = intent
    return btn


def link(text: str, url: str) -> dict:
    return {"type": "link", "text": text, "url": url}


def request_contact(text: str) -> dict:
    return {"type": "request_contact", "text": text}


# ---------------------------------------------------------------------------
# Fake-клиент для демо и тестов: имитирует MAX, не ходит в сеть.
# ---------------------------------------------------------------------------
class Outbox:
    """Одно «отправленное» сообщение бота."""

    def __init__(self, user_id: int | None, chat_id: int | None, text: str,
                 attachments: list[dict] | None):
        self.user_id = user_id
        self.chat_id = chat_id
        self.text = text
        self.attachments = attachments or []

    @property
    def buttons(self) -> list[dict]:
        btns: list[dict] = []
        for att in self.attachments:
            if att.get("type") == "inline_keyboard":
                for row in att["payload"]["buttons"]:
                    btns.extend(row)
        return btns

    def __repr__(self) -> str:  # для отладки в тестах
        return f"<Outbox →u{self.user_id}: {self.text[:60]!r}>"


class FakeMaxApiClient:
    """Заглушка MaxApiClient: складывает исходящие сообщения в outbox."""

    def __init__(self):
        self.outbox: list[Outbox] = []
        self.last_by_user: dict[int, Outbox] = {}
        self.notifications: list[tuple[int | None, str]] = []
        self._cb_seq = 0

    # --- API-сигнатуры, которые использует бот ---
    def get_me(self) -> dict:
        return {"user_id": 1, "first_name": "Диспетчерская Деда Мороза", "is_bot": True}

    def send_message(self, text, user_id=None, chat_id=None, attachments=None) -> dict:
        msg = Outbox(user_id, chat_id, text, attachments)
        self.outbox.append(msg)
        if user_id is not None:
            self.last_by_user[user_id] = msg
        return {"message": {"body": {"mid": f"m{len(self.outbox)}"}}}

    def edit_message(self, message_id, text, attachments=None) -> dict:
        self.outbox.append(Outbox(None, None, f"[edit {message_id}] {text}", attachments))
        return {}

    def answer_callback(self, callback_id, notification=None, message=None) -> dict:
        if notification:
            self.notifications.append((None, notification))
        if message:
            self.outbox.append(Outbox(None, None, message.get("text", ""),
                                      message.get("attachments")))
        return {}

    # --- помощники для демо/тестов ---
    def pop_all(self) -> list[Outbox]:
        msgs, self.outbox = self.outbox, []
        return msgs

    def last_to(self, user_id: int) -> Outbox | None:
        """Последнее сообщение пользователю (не очищается pop_all)."""
        return self.last_by_user.get(user_id)

    def make_callback(self, user_id: int, name: str, payload: str) -> dict:
        """Сконструировать update message_callback «как от MAX»."""
        self._cb_seq += 1
        return {
            "update_type": "message_callback",
            "timestamp": int(time.time() * 1000),
            "callback": {
                "callback_id": f"cb{self._cb_seq}",
                "payload": payload,
                "user": {"user_id": user_id, "first_name": name, "is_bot": False},
                "message": {"recipient": {"chat_id": user_id, "chat_type": "dialog"},
                            "body": {"mid": f"m{self._cb_seq}", "text": ""}},
            },
        }

    @staticmethod
    def make_message(user_id: int, name: str, text: str,
                     attachments: list[dict] | None = None) -> dict:
        """Сконструировать update message_created «как от MAX»."""
        return {
            "update_type": "message_created",
            "timestamp": int(time.time() * 1000),
            "message": {
                "sender": {"user_id": user_id, "first_name": name, "is_bot": False},
                "recipient": {"chat_id": user_id, "chat_type": "dialog"},
                "body": {"mid": f"u{int(time.time()*1000)}", "text": text,
                         "attachments": attachments or []},
            },
        }

    @staticmethod
    def make_bot_started(user_id: int, name: str) -> dict:
        return {
            "update_type": "bot_started",
            "timestamp": int(time.time() * 1000),
            "chat_id": user_id,
            "user": {"user_id": user_id, "first_name": name, "is_bot": False},
        }

    @staticmethod
    def make_contact_message(user_id: int, name: str, phone: str) -> dict:
        """Сообщение с контактом (кнопка request_contact)."""
        vcf = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nTEL;TYPE=CELL:{phone}\nEND:VCARD"
        att = {"type": "contact", "payload": {"vcf_info": vcf, "max_info": {"phone": phone}}}
        return FakeMaxApiClient.make_message(user_id, name, "", [att])


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)
