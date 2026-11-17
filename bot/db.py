"""Слой данных: SQLite (MVP). Интерфейс функций пригоден для переноса на PostgreSQL.

Все даты хранятся как ISO-строки с часовым поясом (aware datetime -> isoformat()).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL DEFAULT '',
  role       TEXT NOT NULL DEFAULT 'client',      -- client | artist | dispatcher
  state      TEXT NOT NULL DEFAULT 'idle',
  data       TEXT NOT NULL DEFAULT '{}',          -- JSON-контекст FSM
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artists (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL UNIQUE,
  stage_name    TEXT NOT NULL,
  districts     TEXT NOT NULL DEFAULT '',          -- районы через запятую
  price         INTEGER NOT NULL DEFAULT 0,        -- руб. за выезд
  description   TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
  active        INTEGER NOT NULL DEFAULT 1,
  rating        REAL NOT NULL DEFAULT 5.0,
  ratings_count INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id        INTEGER NOT NULL,
  child_name       TEXT NOT NULL DEFAULT '',
  child_age        INTEGER,
  district         TEXT NOT NULL DEFAULT '',
  address          TEXT NOT NULL DEFAULT '',
  event_at         TEXT NOT NULL,
  characters       TEXT NOT NULL DEFAULT 'both',   -- santa | snegurochka | both
  budget           INTEGER NOT NULL DEFAULT 0,
  wishes           TEXT NOT NULL DEFAULT '',
  phone            TEXT NOT NULL DEFAULT '',
  status           TEXT NOT NULL DEFAULT 'draft',
     -- draft | offered | pending_artist | assigned | paid | in_progress
     -- | completed | cancelled | force_majeure
  artist_id        INTEGER,
  backup_artist_id INTEGER,
  payment_url      TEXT,
  candidates       TEXT NOT NULL DEFAULT '[]',     -- JSON: id артистов, показанных клиенту
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS offers (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id   INTEGER NOT NULL,
  artist_id  INTEGER NOT NULL,
  kind       TEXT NOT NULL DEFAULT 'primary',      -- primary | backup | rescue
  status     TEXT NOT NULL DEFAULT 'pending',      -- pending | accepted | declined | expired
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ratings (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id   INTEGER NOT NULL,
  artist_id  INTEGER NOT NULL,
  stars      INTEGER NOT NULL,
  comment    TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id   INTEGER,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL,
  kind     TEXT NOT NULL,                          -- day_before | checkin | rate_request
  fire_at  TEXT NOT NULL,
  sent     INTEGER NOT NULL DEFAULT 0
);
"""


class DB:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------------- базово
    def _q(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self._q(sql, params)
        return rows[0] if rows else None

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(sql, tuple(params))
            return cur.lastrowid or cur.rowcount

    # ---------------------------------------------------------------- users
    def upsert_user(self, user_id: int, name: str, now: str) -> None:
        self._exec(
            """INSERT INTO users (user_id, name, created_at) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET name=excluded.name""",
            (user_id, name, now),
        )

    def get_user(self, user_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM users WHERE user_id=?", (user_id,))

    def set_state(self, user_id: int, state: str, data: dict | None = None) -> None:
        if data is None:
            self._exec("UPDATE users SET state=? WHERE user_id=?", (state, user_id))
        else:
            self._exec(
                "UPDATE users SET state=?, data=? WHERE user_id=?",
                (state, json.dumps(data, ensure_ascii=False), user_id),
            )

    def get_data(self, user_id: int) -> dict:
        row = self.get_user(user_id)
        if not row:
            return {}
        try:
            return json.loads(row["data"] or "{}")
        except json.JSONDecodeError:
            return {}

    def set_role(self, user_id: int, role: str) -> None:
        self._exec("UPDATE users SET role=? WHERE user_id=?", (role, user_id))

    def dispatchers(self) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM users WHERE role='dispatcher'")

    # ---------------------------------------------------------------- artists
    def create_artist(self, user_id: int, stage_name: str, districts: str,
                      price: int, description: str, now: str) -> int:
        """Создать заявку артиста или переоткрыть существующую (повторная подача)."""
        existing = self._one("SELECT id FROM artists WHERE user_id=?", (user_id,))
        if existing:
            self._exec(
                """UPDATE artists SET stage_name=?, districts=?, price=?, description=?,
                                      status='pending' WHERE user_id=?""",
                (stage_name, districts, price, description, user_id),
            )
            return int(existing["id"])
        return self._exec(
            """INSERT INTO artists (user_id, stage_name, districts, price, description,
                                    status, created_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
            (user_id, stage_name, districts, price, description, now),
        )

    def get_artist(self, artist_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM artists WHERE id=?", (artist_id,))

    def get_artist_by_user(self, user_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM artists WHERE user_id=?", (user_id,))

    def set_artist_status(self, artist_id: int, status: str) -> None:
        self._exec("UPDATE artists SET status=? WHERE id=?", (status, artist_id))

    def set_artist_active(self, artist_id: int, active: bool) -> None:
        self._exec("UPDATE artists SET active=? WHERE id=?", (1 if active else 0, artist_id))

    def pending_artists(self) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM artists WHERE status='pending' ORDER BY id")

    def approved_artists(self) -> list[sqlite3.Row]:
        return self._q(
            "SELECT * FROM artists WHERE status='approved' AND active=1 ORDER BY rating DESC, id"
        )

    def add_rating(self, order_id: int, artist_id: int, stars: int,
                   comment: str, now: str) -> None:
        self._exec(
            "INSERT INTO ratings (order_id, artist_id, stars, comment, created_at) VALUES (?,?,?,?,?)",
            (order_id, artist_id, stars, comment, now),
        )
        row = self._one(
            "SELECT AVG(stars) AS avg_s, COUNT(*) AS cnt FROM ratings WHERE artist_id=?",
            (artist_id,),
        )
        if row:
            self._exec(
                "UPDATE artists SET rating=?, ratings_count=? WHERE id=?",
                (round(float(row["avg_s"]), 2), int(row["cnt"]), artist_id),
            )

    def get_rating(self, order_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM ratings WHERE order_id=?", (order_id,))

    # ---------------------------------------------------------------- orders
    def create_order(self, client_id: int, fields: dict, now: str) -> int:
        cols = ["client_id", *fields.keys(), "created_at", "updated_at"]
        placeholders = ",".join("?" for _ in cols)
        return self._exec(
            f"INSERT INTO orders ({','.join(cols)}) VALUES ({placeholders})",
            [client_id, *fields.values(), now, now],
        )

    def update_order(self, order_id: int, now: str, **fields) -> None:
        if not fields:
            return
        assignments = ",".join(f"{k}=?" for k in fields)
        self._exec(
            f"UPDATE orders SET {assignments}, updated_at=? WHERE id=?",
            [*fields.values(), now, order_id],
        )

    def get_order(self, order_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM orders WHERE id=?", (order_id,))

    def active_order_of_client(self, client_id: int) -> sqlite3.Row | None:
        return self._one(
            """SELECT * FROM orders WHERE client_id=? AND status NOT IN
               ('completed','cancelled') ORDER BY id DESC LIMIT 1""",
            (client_id,),
        )

    def orders_of_artist(self, artist_id: int, statuses: tuple[str, ...]) -> list[sqlite3.Row]:
        ph = ",".join("?" for _ in statuses)
        return self._q(
            f"""SELECT * FROM orders WHERE (artist_id=? OR backup_artist_id=?)
                AND status IN ({ph}) ORDER BY event_at""",
            (artist_id, artist_id, *statuses),
        )

    def busy_artist_ids(self, event_from: str, event_to: str) -> set[int]:
        """Артисты, занятые в окне [event_from, event_to]."""
        rows = self._q(
            """SELECT artist_id, backup_artist_id FROM orders
               WHERE status IN ('assigned','paid','in_progress')
                 AND event_at >= ? AND event_at <= ?""",
            (event_from, event_to),
        )
        busy: set[int] = set()
        for r in rows:
            if r["artist_id"]:
                busy.add(int(r["artist_id"]))
            if r["backup_artist_id"]:
                busy.add(int(r["backup_artist_id"]))
        return busy

    def orders_due(self, now_iso: str) -> list[sqlite3.Row]:
        """Активные заказы, у которых время события уже прошло (для автозавершения)."""
        return self._q(
            """SELECT * FROM orders WHERE status IN ('paid','in_progress')
               AND event_at <= ?""",
            (now_iso,),
        )

    def orders_in_status(self, statuses: tuple[str, ...]) -> list[sqlite3.Row]:
        ph = ",".join("?" for _ in statuses)
        return self._q(f"SELECT * FROM orders WHERE status IN ({ph}) ORDER BY id DESC", statuses)

    # ---------------------------------------------------------------- offers
    def create_offer(self, order_id: int, artist_id: int, kind: str,
                     now: str, expires_at: str) -> int:
        return self._exec(
            "INSERT INTO offers (order_id, artist_id, kind, status, created_at, expires_at)"
            " VALUES (?,?,?,'pending',?,?)",
            (order_id, artist_id, kind, now, expires_at),
        )

    def get_offer(self, offer_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM offers WHERE id=?", (offer_id,))

    def set_offer_status(self, offer_id: int, status: str) -> None:
        self._exec("UPDATE offers SET status=? WHERE id=?", (status, offer_id))

    def pending_offer(self, order_id: int, kind: str) -> sqlite3.Row | None:
        return self._one(
            "SELECT * FROM offers WHERE order_id=? AND kind=? AND status='pending'"
            " ORDER BY id DESC LIMIT 1",
            (order_id, kind),
        )

    def expired_offers(self, now_iso: str) -> list[sqlite3.Row]:
        return self._q(
            "SELECT * FROM offers WHERE status='pending' AND expires_at <= ?", (now_iso,)
        )

    # ---------------------------------------------------------------- events
    def add_event(self, order_id: int | None, kind: str, payload: str, now: str) -> None:
        self._exec(
            "INSERT INTO events (order_id, kind, payload, created_at) VALUES (?,?,?,?)",
            (order_id, kind, payload, now),
        )

    def last_events(self, limit: int = 10) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------------- reminders
    def add_reminder(self, order_id: int, kind: str, fire_at: str) -> None:
        self._exec(
            "INSERT INTO reminders (order_id, kind, fire_at) VALUES (?,?,?)",
            (order_id, kind, fire_at),
        )

    def due_reminders(self, now_iso: str) -> list[sqlite3.Row]:
        return self._q(
            "SELECT * FROM reminders WHERE sent=0 AND fire_at <= ? ORDER BY fire_at",
            (now_iso,),
        )

    def mark_reminder_sent(self, reminder_id: int) -> None:
        self._exec("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))
