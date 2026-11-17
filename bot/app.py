"""Сборка приложения: long polling + фоновой планировщик."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import router, services
from .config import Config
from .db import DB
from .maxapi import MaxApiClient, MaxApiError

log = logging.getLogger(__name__)


class Bot:
    """Корневой объект приложения: cfg + db + api + маршрутизация."""

    def __init__(self, cfg: Config, db: DB, api):
        self.cfg = cfg
        self.db = db
        self.api = api
        self.tz = ZoneInfo(cfg.business_tz)
        self._stop = threading.Event()

    # ------------------------------------------------------------- helpers
    def now(self) -> datetime:
        return datetime.now(self.tz)

    def all_dispatchers(self) -> list[int]:
        ids = set(self.cfg.dispatcher_ids)
        ids.update(int(u["user_id"]) for u in self.db.dispatchers())
        return sorted(ids)

    def handle_update(self, update: dict) -> None:
        try:
            router.dispatch(self, update)
        except Exception:
            log.exception("ошибка обработки update: %s", update)

    # --------------------------------------------------------------- циклы
    def run_forever(self) -> None:
        me = self.api.get_me()
        log.info("Бот запущен: %s (id=%s)", me.get("first_name"), me.get("user_id"))
        self._start_scheduler()
        marker: int | None = None
        while not self._stop.is_set():
            try:
                resp = self.api.get_updates(marker=marker,
                                            timeout=self.cfg.polling_timeout)
                for update in resp.get("updates", []):
                    self.handle_update(update)
                marker = resp.get("marker", marker)
            except MaxApiError as exc:
                log.error("MAX API: %s", exc)
                time.sleep(5)
            except Exception:
                log.exception("сбой цикла polling")
                time.sleep(5)

    def _start_scheduler(self) -> None:
        def loop() -> None:
            while not self._stop.is_set():
                try:
                    services.scheduler_tick(self)
                except Exception:
                    log.exception("сбой планировщика")
                self._stop.wait(30)

        thread = threading.Thread(target=loop, name="scheduler", daemon=True)
        thread.start()
        log.info("Планировщик запущен (интервал 30 c)")

    def stop(self) -> None:
        self._stop.set()


def build_from_env() -> Bot:
    cfg = Config.from_env()
    db = DB(cfg.database_path)
    api = MaxApiClient(cfg.token, cfg.api_base)
    return Bot(cfg, db, api)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    app = build_from_env()
    try:
        app.run_forever()
    except KeyboardInterrupt:
        log.info("Остановка…")
        app.stop()
