import json
import re
from dataclasses import asdict, dataclass

MAX_TICKET_TYPES = 10
TICKET_FORM_TITLE = "Open a support ticket"
TICKET_FORM_QUESTION = "How can staff help?"
TICKET_SELECT_PROMPT = "Choose a ticket type"
TICKET_CHANNEL_PREFIX = "ticket"
TICKET_BUTTON_STYLES = {
    "primary": "Blue",
    "secondary": "Grey",
    "success": "Green",
    "danger": "Red",
}
TICKET_TYPE_SETTINGS_KEY = "TICKET_TYPES_JSON"


@dataclass(frozen=True, slots=True)
class TicketType:
    key: str
    name: str
    description: str = ""
    question: str = ""
    category_id: int | None = None
    staff_role_id: int | None = None


@dataclass(frozen=True, slots=True)
class TicketRoute:
    name: str
    category_id: int | None
    staff_role_id: int | None


def parse_ticket_id(value: str | int | None, label: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (str, int)) and re.fullmatch(r"[0-9]{1,20}", str(value)):
        number = int(value)
        if 0 < number < 2**64:
            return number
    raise ValueError(f"{label} must be a numeric Discord ID.")


def parse_ticket_types(raw: str) -> tuple[TicketType, ...]:
    if not raw.strip():
        return ()
    if len(raw) > 20_000:
        raise ValueError("Ticket types are too large.")
    try:
        values = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ValueError("The ticket types settings need a valid JSON list.") from error
    if not isinstance(values, list) or len(values) > MAX_TICKET_TYPES:
        raise ValueError(f"Use up to {MAX_TICKET_TYPES} ticket types.")
    keys: set[str] = set()
    names: set[str] = set()
    types = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Each ticket type needs a name and a unique ID.")
        key = value.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", key) or key in keys:
            raise ValueError("Each ticket type needs a unique ID of up to 40 lowercase letters, numbers or hyphens.")
        fields = {}
        for name, label, limit in (("name", "Name", 60), ("description", "Menu description", 100), ("question", "Form question", 45)):
            field = value.get(name, "")
            if not isinstance(field, str):
                raise ValueError(f"Ticket type {label.lower()} must be text.")
            field = field.strip()
            if len(field) > limit or (field and not field.isprintable()):
                raise ValueError(f"{label} for a ticket type must be a single line of up to {limit} characters.")
            fields[name] = field
        name = fields["name"]
        if not name or name.casefold() in names:
            raise ValueError("Give each ticket type a different name.")
        keys.add(key)
        names.add(name.casefold())
        types.append(TicketType(
            key=key, **fields,
            category_id=parse_ticket_id(value.get("category_id"), f"{name} category"),
            staff_role_id=parse_ticket_id(value.get("staff_role_id"), f"{name} staff role"),
        ))
    return tuple(types)


def ticket_types_json(types: list[TicketType] | tuple[TicketType, ...]) -> str:
    return json.dumps([asdict(kind) for kind in types], ensure_ascii=False, separators=(",", ":")) if types else ""
