"""Конфигурация бота из переменных окружения (.env поддерживается вручную)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _load_dotenv(path: str = ".env") -> None:
    """Минимальный парсер .env без внешних зависимостей."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    token: str
    api_base: str = "https://platform-api.max.ru"
    database_path: str = "./dedmoroz.db"
    dispatcher_ids: set[int] = field(default_factory=set)
    dispatcher_code: str = "dedmoroz"
    payment_base_url: str = "https://pay.example.ru/dedmoroz"
    backup_response_minutes: int = 15
    offer_response_minutes: int = 60
    business_tz: str = "Europe/Moscow"
    polling_timeout: int = 30

    # --- Реквизиты оператора и документы (152-ФЗ / оферта) ---
    legal_entity: str = "«НАЗВАНИЕ ОРГАНИЗАЦИИ / ИП»"
    legal_inn: str = "«ИНН»"
    legal_ogrn: str = "«ОГРН / ОГРНИП»"
    legal_address: str = "«ЮРИДИЧЕСКИЙ АДРЕС»"
    legal_email: str = "«EMAIL ДЛЯ ОБРАЩЕНИЙ ПО ПДн»"
    legal_phone: str = "«ТЕЛЕФОН ПОДДЕРЖКИ»"
    service_city: str = "«ГОРОД»"
    privacy_url: str = ""   # если документы на сайте — кнопки-ссылки, иначе текст
    offer_url: str = ""
    refund_url: str = ""
    cancel_free_hours: int = 24      # бесплатная отмена до N часов
    cancel_fee_percent: int = 30     # удержание при поздней отмене
    refund_days: int = 10            # срок возврата денег

    @classmethod
    def from_env(cls, require_token: bool = True) -> "Config":
        _load_dotenv()
        token = os.environ.get("MAX_BOT_TOKEN", "").strip()
        if require_token and not token:
            raise SystemExit(
                "Не задан MAX_BOT_TOKEN. Создайте .env по образцу .env.example "
                "или передайте переменную окружения.\n"
                "Для локальной проверки без токена запустите: python demo.py"
            )
        dispatcher_ids = {
            int(x.strip())
            for x in os.environ.get("DISPATCHER_IDS", "").split(",")
            if x.strip().isdigit()
        }
        return cls(
            token=token,
            api_base=os.environ.get("MAX_API_BASE", "https://platform-api.max.ru").rstrip("/"),
            database_path=os.environ.get("DATABASE_PATH", "./dedmoroz.db"),
            dispatcher_ids=dispatcher_ids,
            dispatcher_code=os.environ.get("DISPATCHER_CODE", "dedmoroz"),
            payment_base_url=os.environ.get(
                "PAYMENT_BASE_URL", "https://pay.example.ru/dedmoroz"
            ).rstrip("/"),
            backup_response_minutes=int(os.environ.get("BACKUP_RESPONSE_MINUTES", "15")),
            offer_response_minutes=int(os.environ.get("OFFER_RESPONSE_MINUTES", "60")),
            business_tz=os.environ.get("BUSINESS_TZ", "Europe/Moscow"),
            polling_timeout=int(os.environ.get("POLLING_TIMEOUT", "30")),
        )
