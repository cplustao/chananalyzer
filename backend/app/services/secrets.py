from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.core.security import secret_cipher
from backend.app.db.models import SecretSetting

SUPPORTED_SECRET_KEYS = {
    "TUSHARE_TOKEN": "Tushare 行情数据",
    "DEEPSEEK_API_KEY": "DeepSeek AI 分析",
    "SILICONFLOW_API_KEY": "硅基流动 AI 分析",
}

SECRET_SETTING_FIELDS = {
    "TUSHARE_TOKEN": "tushare_token",
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "SILICONFLOW_API_KEY": "siliconflow_api_key",
}


class SecretService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.cipher = secret_cipher(settings)

    def status(self) -> list[dict[str, object]]:
        rows = {row.key for row in self.session.scalars(select(SecretSetting)).all()}
        configured_from_settings = {key for key in SUPPORTED_SECRET_KEYS if self._settings_value(key)}
        return [
            {
                "key": key,
                "configured": key in rows or bool(os.getenv(key)) or key in configured_from_settings,
                "source": "database" if key in rows else "environment" if os.getenv(key) or key in configured_from_settings else "missing",
                "description": description,
            }
            for key, description in SUPPORTED_SECRET_KEYS.items()
        ]

    def set(self, key: str, value: str, user_id: str) -> None:
        if key not in SUPPORTED_SECRET_KEYS:
            raise ValueError("Unsupported secret key")
        row = self.session.get(SecretSetting, key)
        encrypted = self.cipher.encrypt(value.encode("utf-8")).decode("ascii")
        if row is None:
            row = SecretSetting(key=key, encrypted_value=encrypted, updated_by=user_id)
            self.session.add(row)
        else:
            row.encrypted_value = encrypted
            row.updated_by = user_id
        self.session.commit()

    def _settings_value(self, key: str) -> str | None:
        value = getattr(self.settings, SECRET_SETTING_FIELDS[key], None)
        if value is None:
            return None
        return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    def get(self, key: str) -> str | None:
        row = self.session.get(SecretSetting, key)
        if row is not None:
            return self.cipher.decrypt(row.encrypted_value.encode("ascii")).decode("utf-8")
        return os.getenv(key) or self._settings_value(key)

    def apply_to_environment(self) -> None:
        for key in SUPPORTED_SECRET_KEYS:
            value = self.get(key)
            if value:
                os.environ[key] = value