import re
from dataclasses import dataclass

DEFAULT_ACCENT = 0x5865F2

MESSAGE_SETTINGS = {
    "WELCOME_TITLE": "Welcome title",
    "WELCOME_DESCRIPTION": "Welcome description",
    "WELCOME_COLOR": "Welcome hex colour",
    "WELCOME_IMAGE_URL": "Welcome banner URL",
    "GOODBYE_TITLE": "Goodbye title",
    "GOODBYE_DESCRIPTION": "Goodbye description",
    "GOODBYE_COLOR": "Goodbye hex colour",
    "GOODBYE_IMAGE_URL": "Goodbye banner URL",
    "TICKET_PANEL_TITLE": "Ticket panel title",
    "TICKET_PANEL_DESCRIPTION": "Ticket panel description",
    "TICKET_PANEL_COLOR": "Ticket panel hex colour",
    "TICKET_BUTTON_LABEL": "Ticket button label",
}
MESSAGE_DEFAULTS = {
    "WELCOME_TITLE": "Welcome aboard!",
    "WELCOME_DESCRIPTION": "Glad to have you here, {member}. Tell us what you like to fly or share a favorite flight-sim screenshot.",
    "WELCOME_COLOR": "#5865F2",
    "GOODBYE_TITLE": "Safe travels",
    "GOODBYE_DESCRIPTION": "**{member}** has left **{server}**. Thanks for flying with us!",
    "GOODBYE_COLOR": "#3180D8",
    "TICKET_PANEL_TITLE": "Contact server staff",
    "TICKET_PANEL_DESCRIPTION": "Use **{button}** to start a private conversation with staff.",
    "TICKET_PANEL_COLOR": "#5865F2",
    "TICKET_BUTTON_LABEL": "Open ticket",
}
PLACEHOLDER = re.compile(r"\{(member|server|button|type|ticket)\}")


@dataclass(frozen=True, slots=True)
class MessageStyle:
    title: str
    description: str
    color: int = DEFAULT_ACCENT
    image_url: str = ""


WELCOME_STYLE = MessageStyle(MESSAGE_DEFAULTS["WELCOME_TITLE"], MESSAGE_DEFAULTS["WELCOME_DESCRIPTION"])
GOODBYE_STYLE = MessageStyle(MESSAGE_DEFAULTS["GOODBYE_TITLE"], MESSAGE_DEFAULTS["GOODBYE_DESCRIPTION"], 0x3180D8)
TICKET_STYLE = MessageStyle(MESSAGE_DEFAULTS["TICKET_PANEL_TITLE"], MESSAGE_DEFAULTS["TICKET_PANEL_DESCRIPTION"])


def render_description(description: str, **values: str) -> str:
    return PLACEHOLDER.sub(lambda match: values.get(match[1], match[0]), description)[:4096]
