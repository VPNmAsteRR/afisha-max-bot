"""Конфигурация приложения (читается из переменных окружения / .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Минимальный загрузчик .env без внешних зависимостей."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "да", "on"}


def _get_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(slots=True)
class Settings:
    # --- Платформа MAX ---
    bot_token: str = os.getenv("BOT_TOKEN", "")
    api_base: str = os.getenv("API_BASE", "https://platform-api2.max.ru")
    api_version: str = os.getenv("API_VERSION", "0.1.5")
    poll_timeout: int = int(os.getenv("POLL_TIMEOUT", "30"))
    poll_limit: int = int(os.getenv("POLL_LIMIT", "100"))

    # --- Вебхук / сервер ---
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8080"))
    webhook_url: str = os.getenv("WEBHOOK_URL", "").rstrip("/")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    webhook_path: str = os.getenv("WEBHOOK_PATH", "/webhook")

    # --- Данные ---
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).expanduser()
    )
    google_credentials: str = os.getenv("GOOGLE_CREDENTIALS", "")
    google_sheet_id: str = os.getenv("GOOGLE_SHEET_ID", "")
    google_sheet_name: str = os.getenv("GOOGLE_SHEET_NAME", "Афиша")
    sheet_refresh_seconds: int = int(os.getenv("SHEET_REFRESH_SECONDS", "60"))
    backups_dir: str = os.getenv("BACKUPS_DIR", "")

    # --- Администрирование ---
    admin_ids: list[str] = field(default_factory=lambda: _get_list("ADMIN_IDS"))
    admin_chat_id: str = os.getenv("ADMIN_CHAT_ID", "")
    max_edit_steps: int = int(os.getenv("MAX_EDIT_STEPS", "15"))

    # --- Поведение ---
    timezone: str = os.getenv("TIMEZONE", "Asia/Krasnoyarsk")
    daily_digest_hour: int = int(os.getenv("DAILY_DIGEST_HOUR", "9"))
    backup_hour: int = int(os.getenv("BACKUP_HOUR", "3"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    per_chat_rate: int = int(os.getenv("PER_CHAT_RATE", "2"))
    global_rate: int = int(os.getenv("GLOBAL_RATE", "30"))

    # --- Дополнительно ---
    channel_id: str = os.getenv("CHANNEL_ID", "")
    demo_mode: bool = _get_bool("DEMO_MODE", False)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "afisha.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def bak_dir(self) -> Path:
        return Path(self.backups_dir).expanduser() if self.backups_dir else self.data_dir / "backups"

    @property
    def webhook_enabled(self) -> bool:
        return bool(self.webhook_url)

    @property
    def sheet_enabled(self) -> bool:
        return bool(self.google_sheet_id and self.google_credentials)

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.log_dir, self.bak_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
