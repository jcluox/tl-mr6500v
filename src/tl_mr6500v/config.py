from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_PASSWORDS = {"", "change_me", "your_password_here"}


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    tplink_password: str
    admin_username: str = "admin"
    admin_password: str  # Dashboard login password
    session_days: float = 30  # Days a Dashboard login stays valid (each use renews it)
    # Source networks treated as this machine. With Docker the source can be the bridge gateway instead of 127.0.0.1; add it here
    local_client_cidrs: str = "127.0.0.0/8,::1/128"
    router_host: str = "http://192.168.1.1"
    poll_interval_seconds: float = 30
    import_limit: int = Field(20, ge=0)  # Most recent messages to import; 0 = all

    enabled_providers: str = ""
    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""
    line_channel_access_token: str = ""
    line_user_ids: str = ""

    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")

    @field_validator("admin_password")
    @classmethod
    def _real_admin_password(cls, value: str) -> str:
        if value.strip().lower() in PLACEHOLDER_PASSWORDS:
            raise ValueError("ADMIN_PASSWORD is empty or still the placeholder from .env.example; set a real password")
        return value

    @property
    def local_networks(self) -> list[IPv4Network | IPv6Network]:
        return [ip_network(c, strict=False) for c in _split(self.local_client_cidrs)]

    @property
    def provider_names(self) -> list[str]:
        return [name.lower() for name in _split(self.enabled_providers)]

    @property
    def telegram_chats(self) -> list[str]:
        return _split(self.telegram_chat_ids)

    @property
    def line_users(self) -> list[str]:
        return _split(self.line_user_ids)
