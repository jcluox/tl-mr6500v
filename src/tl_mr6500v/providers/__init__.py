from ..config import Settings
from .base import Provider
from .line import LineProvider
from .telegram import TelegramProvider


def build_providers(settings: Settings) -> list[Provider]:
    """Build the providers listed in ENABLED_PROVIDERS. To add a provider, register a factory here."""
    factories = {
        "telegram": lambda: TelegramProvider(settings.telegram_bot_token, settings.telegram_chats),
        "line": lambda: LineProvider(settings.line_channel_access_token, settings.line_users),
    }
    providers = []
    for name in settings.provider_names:
        if name not in factories:
            raise ValueError(f"Unknown provider: {name} (available: {', '.join(factories)})")
        providers.append(factories[name]())
    return providers
