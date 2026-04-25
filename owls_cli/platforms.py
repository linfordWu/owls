"""
Shared platform registry for OWLS.

Single source of truth for platform metadata consumed by both
skills_config (label display) and tools_config (default toolset
resolution).  Import ``PLATFORMS`` from here instead of maintaining
duplicate dicts in each module.
"""

from collections import OrderedDict
from typing import NamedTuple


class PlatformInfo(NamedTuple):
    """Metadata for a single platform entry."""
    label: str
    default_toolset: str


# Ordered so that TUI menus are deterministic.
PLATFORMS: OrderedDict[str, PlatformInfo] = OrderedDict([
    ("cli",            PlatformInfo(label="🖥️  CLI",            default_toolset="owls-cli")),
    ("telegram",       PlatformInfo(label="📱 Telegram",        default_toolset="owls-telegram")),
    ("discord",        PlatformInfo(label="💬 Discord",         default_toolset="owls-discord")),
    ("slack",          PlatformInfo(label="💼 Slack",           default_toolset="owls-slack")),
    ("whatsapp",       PlatformInfo(label="📱 WhatsApp",        default_toolset="owls-whatsapp")),
    ("signal",         PlatformInfo(label="📡 Signal",          default_toolset="owls-signal")),
    ("bluebubbles",    PlatformInfo(label="💙 BlueBubbles",     default_toolset="owls-bluebubbles")),
    ("email",          PlatformInfo(label="📧 Email",           default_toolset="owls-email")),
    ("homeassistant",  PlatformInfo(label="🏠 Home Assistant",  default_toolset="owls-homeassistant")),
    ("mattermost",     PlatformInfo(label="💬 Mattermost",      default_toolset="owls-mattermost")),
    ("matrix",         PlatformInfo(label="💬 Matrix",          default_toolset="owls-matrix")),
    ("dingtalk",       PlatformInfo(label="💬 DingTalk",        default_toolset="owls-dingtalk")),
    ("feishu",         PlatformInfo(label="🪽 Feishu",          default_toolset="owls-feishu")),
    ("wecom",          PlatformInfo(label="💬 WeCom",           default_toolset="owls-wecom")),
    ("wecom_callback", PlatformInfo(label="💬 WeCom Callback",  default_toolset="owls-wecom-callback")),
    ("weixin",         PlatformInfo(label="💬 Weixin",          default_toolset="owls-weixin")),
    ("qqbot",          PlatformInfo(label="💬 QQBot",           default_toolset="owls-qqbot")),
    ("webhook",        PlatformInfo(label="🔗 Webhook",         default_toolset="owls-webhook")),
    ("api_server",     PlatformInfo(label="🌐 API Server",      default_toolset="owls-api-server")),
])


def platform_label(key: str, default: str = "") -> str:
    """Return the display label for a platform key, or *default*."""
    info = PLATFORMS.get(key)
    return info.label if info is not None else default
