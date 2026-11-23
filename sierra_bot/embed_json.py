import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

import discord

MAX_JSON_SIZE = 2_000_000
EMBED_KEYS = {"title", "description", "color", "url", "author", "footer", "image", "thumbnail", "fields", "timestamp"}


@dataclass(frozen=True, slots=True)
class MessageDraft:
    content: str | None = None
    embeds: tuple[discord.Embed, ...] = ()
    ignored_options: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = {"embeds": [embed.to_dict() for embed in self.embeds]}
        if self.content:
            data["content"] = self.content
        return data


def load_message_json(raw: str) -> tuple[dict | list, list[tuple[str, dict]]]:
    if not raw.strip() or len(raw) > MAX_JSON_SIZE:
        raise ValueError("Paste or open a JSON draft under 2 MB.")
    try:
        document = json.loads(raw, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON on line {error.lineno}, column {error.colno}.") from error
    except (RecursionError, ValueError) as error:
        raise ValueError("The JSON is too deeply nested or contains an invalid number.") from error
    return document, collect_messages(document)


def reject_constant(_value: str):
    raise ValueError("Use finite JSON numbers.")


def collect_messages(node, label: str = "", depth: int = 0) -> list[tuple[str, dict]]:
    if depth > 6:
        raise ValueError("The backup contains too many nested wrappers.")
    if isinstance(node, list):
        if node and all(isinstance(value, dict) and value.keys() & EMBED_KEYS for value in node):
            return [(label or "Message", {"embeds": node})]
        raise ValueError("Use a message object or an array of embed objects.")
    if not isinstance(node, dict):
        raise ValueError("Use a Discord message, an embed object, or Discohook message data.")
    if "messages" in node:
        messages = node["messages"]
        if not isinstance(messages, list) or not messages:
            raise ValueError("This backup has no messages.")
        result = []
        for number, item in enumerate(messages, 1):
            name = item.get("name") if isinstance(item, dict) else None
            title = name.strip()[:60] if isinstance(name, str) and name.strip() else f"Message {number}"
            result.extend(collect_messages(item, f"{label} / {title}" if label else title, depth + 1))
        return result
    if "backups" in node:
        backups = node["backups"]
        if not isinstance(backups, (dict, list)) or not backups:
            raise ValueError("This file contains no backups.")
        entries = backups.items() if isinstance(backups, dict) else enumerate(backups, 1)
        result = []
        for key, backup in entries:
            name = backup.get("name") if isinstance(backup, dict) else None
            name = name[:60] if isinstance(name, str) and name.strip() else str(key)[:60]
            result.extend(collect_messages(backup, name, depth + 1))
        return result
    if "data" in node and not node.keys() & {"content", "embeds", "embed"}:
        return collect_messages(node["data"], label, depth + 1)
    if node.keys() & {"content", "embeds", "embed"}:
        return [(label or "Message", node)]
    if node.keys() & {"components", "attachments", "poll"}:
        return [(label or "Message", node)]
    if node.keys() & EMBED_KEYS:
        return [(label or "Message", {"embeds": [node]})]
    raise ValueError("This JSON does not contain Discord message data.")


def optional_text(data: dict, key: str, limit: int, label: str) -> str | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError(f"{label} contains invalid Unicode.")
    if len(value) > limit:
        raise ValueError(f"{label} may contain up to {limit:,} characters.")
    return value or None


def optional_url(data: dict, key: str, label: str) -> str | None:
    value = optional_text(data, key, 2048, label)
    if value is None:
        return None
    try:
        url = urlsplit(value)
        valid = url.scheme in {"http", "https"} and url.hostname and url.username is None
        url.port
    except ValueError:
        valid = False
    if not valid or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError(f"{label} must be a public HTTP or HTTPS URL.")
    return value


def object_section(data: dict, key: str) -> dict:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key.capitalize()} must be an object.")
    return value


def read_embed(data) -> discord.Embed:
    if not isinstance(data, dict):
        raise ValueError("Each embed must be an object.")
    color = data.get("color")
    if color is not None and (type(color) is not int or not 0 <= color <= 0xFFFFFF):
        raise ValueError("Color must be a decimal integer from 0 to 16,777,215.")
    timestamp = optional_text(data, "timestamp", 64, "Timestamp")
    if timestamp:
        try:
            when = datetime.fromisoformat(timestamp)
            if when.tzinfo is None:
                raise ValueError
        except ValueError as error:
            raise ValueError("Use an ISO 8601 timestamp with a timezone, such as 2026-11-19T15:00:00Z.") from error
    else:
        when = None
    embed = discord.Embed(
        title=optional_text(data, "title", 256, "Title"),
        description=optional_text(data, "description", 4096, "Description"),
        url=optional_url(data, "url", "Title URL"), color=color, timestamp=when,
    )
    author = object_section(data, "author")
    if author:
        name = optional_text(author, "name", 256, "Author name")
        if not name:
            raise ValueError("Add an author name when using author details.")
        embed.set_author(
            name=name,
            url=optional_url(author, "url", "Author URL"),
            icon_url=optional_url(author, "icon_url", "Author icon URL"),
        )
    footer = object_section(data, "footer")
    if footer:
        text = optional_text(footer, "text", 2048, "Footer text")
        if not text:
            raise ValueError("Add footer text when using footer details.")
        embed.set_footer(text=text, icon_url=optional_url(footer, "icon_url", "Footer icon URL"))
    image = optional_url(object_section(data, "image"), "url", "Image URL")
    thumbnail = optional_url(object_section(data, "thumbnail"), "url", "Thumbnail URL")
    if image:
        embed.set_image(url=image)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    fields = data.get("fields")
    if fields is not None:
        if not isinstance(fields, list) or len(fields) > 25:
            raise ValueError("Fields must be an array of up to 25 items.")
        for number, field in enumerate(fields, 1):
            if not isinstance(field, dict):
                raise ValueError(f"Field {number} must be an object.")
            name = optional_text(field, "name", 256, f"Field {number} name")
            value = optional_text(field, "value", 1024, f"Field {number} value")
            inline = field.get("inline", False)
            if not name or not value or not isinstance(inline, bool):
                raise ValueError(f"Field {number} needs a name, a value, and a boolean inline option if supplied.")
            embed.add_field(name=name, value=value, inline=inline)
    if not (len(embed) or image or thumbnail):
        raise ValueError("Add text, fields, an image, or a thumbnail to the embed.")
    return embed


def read_message(data: dict) -> MessageDraft:
    content = optional_text(data, "content", 2000, "Message content")
    raw_embeds = data.get("embeds")
    if raw_embeds is None:
        raw_embeds = [data["embed"]] if data.get("embed") is not None else []
    if not isinstance(raw_embeds, list) or len(raw_embeds) > 10:
        raise ValueError("Embeds must be an array of up to 10 items.")
    embeds = []
    for number, raw_embed in enumerate(raw_embeds, 1):
        try:
            embeds.append(read_embed(raw_embed))
        except ValueError as error:
            raise ValueError(f"Embed {number} — {error}") from error
    if sum(map(len, embeds)) > 6000:
        raise ValueError("All embeds in one message must use at most 6,000 characters combined.")
    if not (content or embeds):
        raise ValueError("Add message content or an embed.")
    ignored = tuple(
        key for key in (
            "attachments", "components", "poll", "username", "avatar_url", "webhook_id",
            "allowed_mentions", "flags", "tts", "thread_name",
        )
        if data.get(key)
    )
    return MessageDraft(content, tuple(embeds), ignored)
