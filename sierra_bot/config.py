import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

from .message_style import GOODBYE_STYLE, MESSAGE_DEFAULTS, MESSAGE_SETTINGS, TICKET_STYLE, WELCOME_STYLE, MessageStyle
from .ticket_config import (
    TICKET_BUTTON_STYLES, TICKET_CHANNEL_PREFIX, TICKET_FORM_QUESTION, TICKET_FORM_TITLE,
    TICKET_SELECT_PROMPT, TICKET_TYPE_SETTINGS_KEY, TicketType, parse_ticket_types,
)
from .ticket_json import EXTRA_KEY, panel_details

# A frozen build extracts its code to a temporary directory. Keep operator files by the EXE.
PROJECT_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)
if config_directory := os.environ.get("SIERRA_CONFIG_DIR"):
    PROJECT_DIR = Path(config_directory).expanduser().resolve()


class ConfigError(ValueError):
    """An invalid or missing deployment setting."""


def required_id(name: str, values: Mapping[str, str | None]) -> int:
    value = (values.get(name) or "").strip()
    if not value.isascii() or not value.isdecimal() or len(value) > 20:
        raise ConfigError(f"Set {name} to the numeric Discord ID in .env.")
    result = int(value)
    if not 0 < result < 2**64:
        raise ConfigError(f"Set {name} to a valid Discord ID in .env.")
    return result


def optional_id(name: str, values: Mapping[str, str | None]) -> int | None:
    return required_id(name, values) if (values.get(name) or "").strip() else None


def member_message_enabled(name: str, values: Mapping[str, str | None]) -> bool:
    value = (values.get(name) or "").strip().lower() or "true"
    if value not in {"true", "false"}:
        raise ConfigError(f"Set {name} to true or false.")
    return value == "true"


def list_values(raw: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in (raw or "").split(",") if part.strip()))


def message_setting(key: str, values: Mapping[str, str | None], limit: int) -> str:
    value = (values.get(key) or "").strip() or MESSAGE_DEFAULTS[key]
    if len(value) > limit:
        raise ConfigError(f"{MESSAGE_SETTINGS[key]} may contain up to {limit:,} characters.")
    return value


def message_style(prefix: str, values: Mapping[str, str | None]) -> MessageStyle:
    color = message_setting(f"{prefix}_COLOR", values, 7)
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", color)
    if not match:
        raise ConfigError(f"{MESSAGE_SETTINGS[f'{prefix}_COLOR']} must be six hex digits, such as #5865F2.")
    image = (values.get(f"{prefix}_IMAGE_URL") or "").strip() if prefix in {"WELCOME", "GOODBYE"} else ""
    if image and image != "off":
        try:
            url = urlsplit(image)
            valid = url.scheme == "https" and url.hostname and url.username is None and url.port in (None, 443)
        except ValueError:
            valid = False
        if not valid or len(image) > 2048 or any(char.isspace() for char in image):
            raise ConfigError(f"Use an HTTPS image URL, 'off', or a blank value for the {prefix.lower()} banner.")
    return MessageStyle(message_setting(f"{prefix}_TITLE", values, 256),
                        message_setting(f"{prefix}_DESCRIPTION", values, 4096 if prefix == "TICKET_PANEL" else 2000),
                        int(match[1], 16), image)


def ticket_text(key: str, values: Mapping[str, str | None], limit: int) -> str:
    value = (values.get(key) or "").strip() or TICKET_DEFAULTS.get(key, "")
    if len(value) > limit or (key != "TICKET_STARTER_MESSAGE" and not value.isprintable()):
        raise ConfigError(f"{TICKET_SETTINGS[key]} needs valid text up to {limit:,} characters.")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    client_id: int
    guild_id: int
    welcome_channel_id: int
    mod_log_channel_id: int
    member_role_id: int
    database_path: Path
    ticket_category_id: int | None = None
    ticket_staff_role_id: int | None = None
    self_roles: tuple[int, ...] = ()
    trap_channel_id: int | None = None
    trap_timeout_minutes: int = 0
    trap_mode: str = "all"
    alert_channel_id: int | None = None
    youtube_channels: tuple[str, ...] = ()
    welcome_style: MessageStyle = WELCOME_STYLE
    ticket_panel_style: MessageStyle = TICKET_STYLE
    ticket_panel_details: Mapping = field(default_factory=dict)
    ticket_button_label: str = MESSAGE_DEFAULTS["TICKET_BUTTON_LABEL"]
    ticket_button_style: str = "primary"
    ticket_form_title: str = TICKET_FORM_TITLE
    ticket_form_question: str = TICKET_FORM_QUESTION
    ticket_select_prompt: str = TICKET_SELECT_PROMPT
    ticket_starter_message: str = ""
    ticket_channel_prefix: str = TICKET_CHANNEL_PREFIX
    ticket_allow_owner_close: bool = True
    ticket_types: tuple[TicketType, ...] = ()
    welcome_enabled: bool = True
    goodbye_enabled: bool = True
    goodbye_channel_id: int | None = None
    goodbye_style: MessageStyle = GOODBYE_STYLE
    flight_news_channel_id: int | None = None
    special_offers_channel_id: int | None = None
    steam_store_country: str = "gb"

    @property
    def goodbye_destination_id(self) -> int:
        return self.goodbye_channel_id or self.welcome_channel_id

    @property
    def ticket_staff_role_ids(self) -> set[int]:
        role_ids = {self.ticket_staff_role_id, *(kind.staff_role_id for kind in self.ticket_types)}
        return {role_id for role_id in role_ids if role_id is not None}

    @classmethod
    def from_env(cls) -> tuple["Settings", str]:
        # A restart in the desktop app must read the file again, including updated credentials.
        values = {**read_local_settings(), **os.environ}
        return cls.from_values(values)

    @classmethod
    def from_values(cls, values: Mapping[str, str | None]) -> tuple["Settings", str]:
        token = (values.get("DISCORD_TOKEN") or "").strip()
        if not token:
            raise ConfigError("Set DISCORD_TOKEN to the bot token in .env.")

        database_path = (values.get("DATABASE_PATH") or "").strip() or "data/warnings.sqlite3"
        roles = list_values(values.get("SELF_ROLE_IDS"))
        if len(roles) > 25:
            raise ConfigError("Use up to 25 self-assignable roles.")
        youtube = list_values(values.get("YOUTUBE_CHANNEL_IDS"))
        if len(youtube) > 10 or any(not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", key) for key in youtube):
            raise ConfigError("Enter up to ten YouTube channel IDs beginning with UC, separated by commas.")
        alerts = optional_id("CREATOR_ALERT_CHANNEL_ID", values)
        if youtube and alerts is None:
            raise ConfigError("Set a YouTube alert channel when adding a YouTube feed.")
        timeout = (values.get("SPAM_TRAP_TIMEOUT_MINUTES") or "").strip()
        if timeout and (not re.fullmatch(r"[0-9]{1,2}", timeout) or int(timeout) > 60):
            raise ConfigError("Use a spam-trap timeout from 0 to 60 minutes.")
        trap_mode = (values.get("SPAM_TRAP_MODE") or "").strip().lower() or "all"
        if trap_mode not in {"all", "activity"}:
            raise ConfigError("Choose all or activity for the spam-trap trigger mode.")
        try:
            ticket_types = parse_ticket_types(values.get(TICKET_TYPE_SETTINGS_KEY) or "")
            panel_style = message_style("TICKET_PANEL", values)
            details = panel_details(values.get(EXTRA_KEY) or "", panel_style)
        except ValueError as error:
            raise ConfigError(str(error)) from error
        button_style = (values.get("TICKET_BUTTON_STYLE") or "").strip().lower() or "primary"
        if button_style not in TICKET_BUTTON_STYLES:
            raise ConfigError("Choose a valid ticket button style.")
        prefix = (values.get("TICKET_CHANNEL_PREFIX") or "").strip().lower() or TICKET_CHANNEL_PREFIX
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", prefix) or len(prefix) > 32:
            raise ConfigError("Ticket channel prefix must use up to 32 letters, numbers, and single hyphens.")
        owner_close = (values.get("TICKET_ALLOW_OWNER_CLOSE") or "").strip().lower() or "true"
        if owner_close not in {"true", "false"}:
            raise ConfigError("TICKET_ALLOW_OWNER_CLOSE must be true or false.")
        steam_country = (values.get("STEAM_STORE_COUNTRY") or "").strip().lower() or "gb"
        if not re.fullmatch(r"[a-z]{2}", steam_country):
            raise ConfigError("Use a two-letter Steam store country code such as GB.")
        settings = cls(
            client_id=required_id("CLIENT_ID", values),
            guild_id=required_id("GUILD_ID", values),
            welcome_channel_id=required_id("WELCOME_CHANNEL_ID", values),
            mod_log_channel_id=required_id("MOD_LOG_CHANNEL_ID", values),
            member_role_id=required_id("MEMBER_ROLE_ID", values),
            database_path=PROJECT_DIR / Path(database_path).expanduser(),
            ticket_category_id=optional_id("TICKET_CATEGORY_ID", values),
            ticket_staff_role_id=optional_id("TICKET_STAFF_ROLE_ID", values),
            self_roles=tuple(required_id("SELF_ROLE_IDS", {"SELF_ROLE_IDS": role}) for role in roles),
            trap_channel_id=optional_id("SPAM_TRAP_CHANNEL_ID", values),
            trap_timeout_minutes=int(timeout or "0"),
            trap_mode=trap_mode,
            alert_channel_id=alerts,
            youtube_channels=youtube,
            welcome_style=message_style("WELCOME", values),
            ticket_panel_style=panel_style,
            ticket_panel_details=details,
            ticket_button_label=message_setting("TICKET_BUTTON_LABEL", values, 80),
            ticket_button_style=button_style,
            ticket_form_title=ticket_text("TICKET_FORM_TITLE", values, 45),
            ticket_form_question=ticket_text("TICKET_FORM_QUESTION", values, 45),
            ticket_select_prompt=ticket_text("TICKET_SELECT_PROMPT", values, 150),
            ticket_starter_message=ticket_text("TICKET_STARTER_MESSAGE", values, 1500),
            ticket_channel_prefix=prefix,
            ticket_allow_owner_close=owner_close == "true",
            ticket_types=ticket_types,
            welcome_enabled=member_message_enabled("WELCOME_ENABLED", values),
            goodbye_enabled=member_message_enabled("GOODBYE_ENABLED", values),
            goodbye_channel_id=optional_id("GOODBYE_CHANNEL_ID", values),
            goodbye_style=message_style("GOODBYE", values),
            flight_news_channel_id=optional_id("FLIGHT_NEWS_CHANNEL_ID", values),
            special_offers_channel_id=optional_id("SPECIAL_OFFERS_CHANNEL_ID", values),
            steam_store_country=steam_country,
        )
        if settings.welcome_channel_id == settings.mod_log_channel_id:
            raise ConfigError("Use separate channels for welcomes and private moderation logs.")
        if settings.trap_channel_id is not None and settings.trap_channel_id in {
            settings.welcome_channel_id, settings.mod_log_channel_id, settings.alert_channel_id,
        }:
            raise ConfigError("Use a dedicated channel for the spam trap.")
        if settings.ticket_staff_role_ids & {settings.guild_id, settings.member_role_id, *settings.self_roles}:
            raise ConfigError("Ticket staff roles must be separate from default and self-assignable roles.")
        return settings, token


EDITABLE_SETTINGS = {
    "CLIENT_ID": "Application ID",
    "GUILD_ID": "Server ID",
    "WELCOME_CHANNEL_ID": "Welcome channel ID",
    "MOD_LOG_CHANNEL_ID": "Private mod-log channel ID",
    "MEMBER_ROLE_ID": "Default member role ID",
    "DATABASE_PATH": "Warning database path",
}
AUTOMATION_SETTINGS = {
    "SELF_ROLE_IDS": "Self-assignable role IDs (comma-separated)",
    "SPAM_TRAP_CHANNEL_ID": "Spam-trap channel ID",
    "SPAM_TRAP_MODE": "Spam trigger (all or activity)",
    "SPAM_TRAP_TIMEOUT_MINUTES": "Trap timeout minutes (0 for alerts only)",
    "CREATOR_ALERT_CHANNEL_ID": "YouTube alert channel ID",
    "YOUTUBE_CHANNEL_IDS": "YouTube channel IDs (comma-separated)",
    "FLIGHT_NEWS_CHANNEL_ID": "Flight sim news channel ID",
    "SPECIAL_OFFERS_CHANNEL_ID": "Special offers channel ID",
    "STEAM_STORE_COUNTRY": "Steam store country (e.g. GB)",
}
MEMBER_MESSAGE_SETTINGS = {
    "WELCOME_ENABLED": "Automatic welcome",
    "GOODBYE_ENABLED": "Automatic goodbye",
    "GOODBYE_CHANNEL_ID": "Goodbye channel ID (blank uses welcome)",
}
TICKET_SETTINGS = {
    **{key: label for key, label in MESSAGE_SETTINGS.items() if key.startswith("TICKET_")},
    "TICKET_BUTTON_STYLE": "Ticket button colour",
    "TICKET_SELECT_PROMPT": "Ticket type menu prompt",
    "TICKET_FORM_TITLE": "Ticket form title",
    "TICKET_FORM_QUESTION": "Default form question",
    "TICKET_STARTER_MESSAGE": "New ticket greeting",
    "TICKET_CHANNEL_PREFIX": "Ticket channel prefix",
    "TICKET_ALLOW_OWNER_CLOSE": "Members can close their own tickets",
    "TICKET_CATEGORY_ID": "Default Discord category",
    "TICKET_STAFF_ROLE_ID": "Default ticket staff role",
    TICKET_TYPE_SETTINGS_KEY: "Ticket types",
    EXTRA_KEY: "Ticket panel embed details",
}
TICKET_DEFAULTS = {
    **{key: text for key, text in MESSAGE_DEFAULTS.items() if key.startswith("TICKET_")},
    "TICKET_BUTTON_STYLE": "primary",
    "TICKET_SELECT_PROMPT": TICKET_SELECT_PROMPT,
    "TICKET_FORM_TITLE": TICKET_FORM_TITLE,
    "TICKET_FORM_QUESTION": TICKET_FORM_QUESTION,
    "TICKET_CHANNEL_PREFIX": TICKET_CHANNEL_PREFIX,
    "TICKET_ALLOW_OWNER_CLOSE": "true",
}
SETTINGS_FIELDS = EDITABLE_SETTINGS | AUTOMATION_SETTINGS | MESSAGE_SETTINGS | MEMBER_MESSAGE_SETTINGS | TICKET_SETTINGS
MULTILINE_SETTINGS = {"WELCOME_DESCRIPTION", "GOODBYE_DESCRIPTION", "TICKET_PANEL_DESCRIPTION", "TICKET_STARTER_MESSAGE"}


def read_local_settings() -> dict[str, str | None]:
    return dict(dotenv_values(PROJECT_DIR / ".env", interpolate=False, encoding="utf-8-sig"))


def save_local_settings(fields: Mapping[str, str], new_token: str) -> None:
    path = PROJECT_DIR / ".env"
    before = path.read_bytes() if path.exists() else b""
    current = read_local_settings()
    changes = {key: fields[key].strip() for key in SETTINGS_FIELDS if key in fields}
    if new_token.strip():
        changes["DISCORD_TOKEN"] = new_token.strip()
    Settings.from_values({**current, **changes, **os.environ})

    source = before.decode("utf-8-sig")
    for key, value in changes.items():
        multiline = key in MULTILINE_SETTINGS
        if "\0" in value or (not multiline and ("\n" in value or "\r" in value)):
            raise ConfigError(f"{key} must fit on one line.")
        if multiline:
            assignment = f"{key}={json.dumps(value, ensure_ascii=False)}"
        else:
            quoted = value.replace("\\", "\\\\").replace("'", "\\'")
            assignment = f"{key}='{quoted}'"
        pattern = rf"(?m)^[ \t]*(?:export[ \t]+)?{key}[ \t]*(?:=[^\r\n]*)?(?=\r?$)"
        source, count = re.subn(pattern, lambda _: assignment, source)
        if not count:
            source = source.rstrip("\r\n") + ("\n" if source else "") + assignment + "\n"

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        staged = Path(temporary.name)
        try:
            temporary.write(source.replace("\r\n", "\n"))
            temporary.close()
            if (path.read_bytes() if path.exists() else b"") != before:
                raise ConfigError("The settings file changed. Reload it before saving.")
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)
