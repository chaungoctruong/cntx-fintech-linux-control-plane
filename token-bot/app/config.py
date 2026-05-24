from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "token-bot"
    host: str = "0.0.0.0"
    port: int = 8090

    master_key_b64: str
    jwt_secret: str
    admin_api_key: str

    source_bot_dir: Path = Path("../bot-trading")
    encrypted_bot_dir: Path = Path("./var/encrypted")
    database_url: str
    auto_encrypt_on_startup: bool = True

    token_default_ttl_sec: int = 86400
    enable_debug_decrypt: bool = False
    enable_legacy_jwt_tokens: bool = False

    telegram_bot_token: Optional[str] = None
    tg_admin_user_ids: str = ""
    telegram_force_ipv4: bool = True
    telegram_connect_timeout_sec: float = 15.0
    telegram_read_timeout_sec: float = 25.0
    telegram_write_timeout_sec: float = 25.0
    telegram_pool_timeout_sec: float = 10.0
    telegram_get_updates_read_timeout_sec: float = 35.0
    telegram_connection_pool_size: int = 32

    redis_url: Optional[str] = None
    redis_state_grace_sec: int = 7 * 86400

    # Để lock/revoke tự dừng bot trên Windows runner, token-bot gọi internal
    # endpoint của backend chính qua HTTP. Để trống = chỉ mark state, không
    # chủ động stop bot (Mini App khách / TradingView signal vẫn chạy).
    backend_url: Optional[str] = None
    backend_internal_key: Optional[str] = None

    partner_weekly_billing_enabled: bool = True
    partner_billing_timezone: str = "Asia/Ho_Chi_Minh"
    partner_billing_cycle_days: int = 30
    partner_billing_notice_weekday: int = 6  # Sunday, Python weekday()
    partner_billing_notice_hour: int = 18
    partner_user_fee_usd: int = 15
    partner_support_block_size: int = 15
    partner_support_fee_usd: int = 150
    partner_infra_fee_usd: int = 100

    def admin_id_set(self) -> set[int]:
        return {int(x.strip()) for x in self.tg_admin_user_ids.split(",") if x.strip()}
