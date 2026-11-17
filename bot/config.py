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
    legal_entity: str = "ИП «Фамилия Имя Отчество»"   # TODO: прислать ФИО
    legal_inn: str = "231521684300"
    legal_ogrn: str = "321344300084915"
    legal_okpo: str = "2010823753"
    legal_address: str = "«АДРЕС»"                     # TODO: прислать адрес
    legal_email: str = "«EMAIL ДЛЯ ОБРАЩЕНИЙ ПО ПДн»"
    legal_phone: str = "«ТЕЛЕФОН ПОДДЕРЖКИ»"
    service_city: str = "Волгоград"
    privacy_url: str = ""   # если документы на сайте — кнопки-ссылки, иначе текст
    offer_url: str = ""
    refund_url: str = ""
    cancel_free_hours: int = 24      # бесплатная отмена до N часов
    cancel_fee_percent: int = 30     # удержание при поздней отмене
    refund_days: int = 10            # срок возврата денег

    # --- Эквайринг (заполняется после модерации ЮKassa) ---
    yookassa_shop_id: str = ""
    yookassa_secret: str = ""

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
            legal_entity=os.environ.get("LEGAL_ENTITY", cls.legal_entity),
            legal_inn=os.environ.get("LEGAL_INN", cls.legal_inn),
            legal_ogrn=os.environ.get("LEGAL_OGRN", cls.legal_ogrn),
            legal_okpo=os.environ.get("LEGAL_OKPO", cls.legal_okpo),
            legal_address=os.environ.get("LEGAL_ADDRESS", cls.legal_address),
            legal_email=os.environ.get("LEGAL_EMAIL", cls.legal_email),
            legal_phone=os.environ.get("LEGAL_PHONE", cls.legal_phone),
            service_city=os.environ.get("SERVICE_CITY", cls.service_city),
            privacy_url=os.environ.get("PRIVACY_URL", ""),
            offer_url=os.environ.get("OFFER_URL", ""),
            refund_url=os.environ.get("REFUND_URL", ""),
            cancel_free_hours=int(os.environ.get("CANCEL_FREE_HOURS", "24")),
            cancel_fee_percent=int(os.environ.get("CANCEL_FEE_PERCENT", "30")),
            refund_days=int(os.environ.get("REFUND_DAYS", "10")),
            yookassa_shop_id=os.environ.get("YOOKASSA_SHOP_ID", ""),
            yookassa_secret=os.environ.get("YOOKASSA_SECRET", ""),
        )
