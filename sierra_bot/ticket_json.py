import json
from collections.abc import Mapping

import discord

from .embed_json import load_message_json, read_message, reject_constant
from .message_style import MessageStyle, render_description
from .ticket_config import parse_ticket_id, parse_ticket_types, ticket_types_json

MAX_TICKET_JSON_BYTES = 100_000
EXTRA_KEY = "TICKET_PANEL_EMBED_JSON"
PANEL_FIELDS = {
    "button_label": "TICKET_BUTTON_LABEL",
    "button_style": "TICKET_BUTTON_STYLE",
    "select_prompt": "TICKET_SELECT_PROMPT",
}
WORKFLOW_FIELDS = {
    "form_title": "TICKET_FORM_TITLE",
    "form_question": "TICKET_FORM_QUESTION",
    "channel_prefix": "TICKET_CHANNEL_PREFIX",
    "category_id": "TICKET_CATEGORY_ID",
    "staff_role_id": "TICKET_STAFF_ROLE_ID",
    "allow_owner_close": "TICKET_ALLOW_OWNER_CLOSE",
    "greeting": "TICKET_STARTER_MESSAGE",
}
VISUAL_EMBED_KEYS = {"type", "title", "description", "color"}


def decode_json(raw: str):
    if not raw.strip() or len(raw.encode("utf-8")) > MAX_TICKET_JSON_BYTES:
        raise ValueError("Provide a ticket JSON file under 100 KB.")
    try:
        return json.loads(raw, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON on line {error.lineno}, column {error.colno}.") from error
    except (RecursionError, ValueError) as error:
        raise ValueError("Ticket JSON contains too many nested values or an invalid number.") from error


def panel_details(raw: str, style: MessageStyle) -> dict:
    if not raw.strip():
        return {}
    data = decode_json(raw)
    if not isinstance(data, dict):
        raise ValueError("Ticket panel embed details must be a JSON object.")
    full = {**data, "title": style.title, "description": style.description, "color": style.color}
    embed = read_message({"embeds": [full]}).embeds[0]
    return {key: value for key, value in embed.to_dict().items() if key not in VISUAL_EMBED_KEYS}


def panel_updates(embed: discord.Embed) -> dict[str, str]:
    extras = {key: value for key, value in embed.to_dict().items() if key not in VISUAL_EMBED_KEYS}
    return {
        "TICKET_PANEL_TITLE": embed.title or "",
        "TICKET_PANEL_DESCRIPTION": embed.description or "",
        "TICKET_PANEL_COLOR": f"#{embed.color.value:06X}" if embed.color is not None else "",
        EXTRA_KEY: json.dumps(extras, ensure_ascii=False, separators=(",", ":")) if extras else "",
    }


def section(value, allowed: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object.")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"Unrecognised {label} setting: {sorted(unknown)[0]}.")
    return value


def parse_ticket_json(raw: str) -> dict[str, str]:
    data = decode_json(raw)
    if not isinstance(data, dict) or not data.keys() & {"panel", "workflow", "types"}:
        _, messages = load_message_json(raw)
        if len(messages) != 1:
            raise ValueError("Import one message with one embed for the ticket panel.")
        draft = read_message(messages[0][1])
        if len(draft.embeds) != 1:
            raise ValueError("Import one embed for the ticket panel.")
        return panel_updates(draft.embeds[0])
    section(data, {"version", "panel", "workflow", "types"}, "ticket profile")
    if type(data.get("version", 1)) is not int or data.get("version", 1) != 1:
        raise ValueError("This app supports ticket profile version 1.")
    updates = {}
    if "panel" in data:
        panel = section(data["panel"], {*PANEL_FIELDS, "embed"}, "panel")
        if "embed" in panel:
            embed = read_message({"embeds": [panel["embed"]]}).embeds[0]
            updates.update(panel_updates(embed))
        for key, setting in PANEL_FIELDS.items():
            if key in panel:
                if not isinstance(panel[key], str):
                    raise ValueError(f"Panel {key} must be text.")
                updates[setting] = panel[key]
    if "workflow" in data:
        workflow = section(data["workflow"], set(WORKFLOW_FIELDS), "workflow")
        for key, value in workflow.items():
            if key in {"category_id", "staff_role_id"}:
                updates[WORKFLOW_FIELDS[key]] = str(parse_ticket_id(value, key) or "")
            elif key == "allow_owner_close":
                if type(value) is not bool:
                    raise ValueError("allow_owner_close must be true or false.")
                updates[WORKFLOW_FIELDS[key]] = "true" if value else "false"
            else:
                if not isinstance(value, str):
                    raise ValueError(f"Workflow {key} must be text.")
                updates[WORKFLOW_FIELDS[key]] = value
    if "types" in data:
        types = parse_ticket_types(json.dumps(data["types"], ensure_ascii=False))
        updates["TICKET_TYPES_JSON"] = ticket_types_json(types)
    return updates


def export_ticket_json(values: Mapping[str, str], style: MessageStyle) -> str:
    details = panel_details(values.get(EXTRA_KEY, ""), style)
    workflow = {key: values.get(setting, "") for key, setting in WORKFLOW_FIELDS.items()}
    for key in ("category_id", "staff_role_id"):
        workflow[key] = str(parse_ticket_id(workflow[key], key) or "") or None
    workflow["allow_owner_close"] = workflow["allow_owner_close"].strip().lower() != "false"
    types = json.loads(ticket_types_json(parse_ticket_types(values.get("TICKET_TYPES_JSON", ""))) or "[]")
    for kind in types:
        for key in ("category_id", "staff_role_id"):
            kind[key] = str(kind[key]) if kind[key] is not None else None
    profile = {
        "version": 1,
        "panel": {
            "embed": {"title": style.title, "description": style.description, "color": style.color, **details},
            **{key: values.get(setting, "") for key, setting in PANEL_FIELDS.items()},
        },
        "workflow": workflow,
        "types": types,
    }
    return json.dumps(profile, ensure_ascii=False, indent=2) + "\n"


def styled_panel(style: MessageStyle, details: Mapping, server_name: str, button_label: str) -> discord.Embed:
    panel = discord.Embed.from_dict({"type": "rich", "title": style.title, "color": style.color, **details})
    description = render_description(
        style.description,
        button=discord.utils.escape_markdown(button_label, ignore_links=False),
        server=discord.utils.escape_markdown(server_name, ignore_links=False),
    )
    panel.description = description[:max(0, 6000 - len(panel))] or None
    return panel
