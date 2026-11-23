import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlsplit

STEAM_APPS = {
    2537590: "Microsoft Flight Simulator 2024",
    1250410: "Microsoft Flight Simulator 2020",
    2014780: "X-Plane 12",
}
NEWS_APPS = (2537590, 2014780)
STEAM_PRICES = "https://store.steampowered.com/api/appdetails"


@dataclass(frozen=True, slots=True)
class SteamPrice:
    currency: str
    initial: int
    final: int
    discount: int
    initial_formatted: str
    final_formatted: str

    @property
    def offer_key(self) -> str:
        if not self.discount:
            return "regular"
        return f"{self.currency}:{self.initial}:{self.final}:{self.discount}"


def steam_news(body: bytes, app_id: int) -> list[tuple[str, str, str]]:
    if b"<!DOCTYPE" in body.upper():
        raise ValueError("Unexpected Steam announcement feed format.")
    feed = ET.fromstring(body).find("channel")
    if feed is None:
        raise ValueError("The Steam announcement feed has no RSS channel.")
    items = []
    for post in feed.findall("item")[:20]:
        title = (post.findtext("title") or "").strip()
        link = urlsplit((post.findtext("link") or "").strip())
        match = re.fullmatch(rf"/games/{app_id}/announcements/detail/([0-9]+)/?", link.path)
        if title and match and link.scheme == "https" and link.netloc == "steamcommunity.com":
            post_id, = match.groups()
            items.append((post_id, title, f"https://steamcommunity.com{link.path}"))
    return items


def steam_price(body: bytes, app_id: int) -> SteamPrice:
    document = json.loads(body)
    try:
        raw = document[str(app_id)]
        prices = raw["data"]["price_overview"]
        if raw["success"] is not True:
            raise ValueError("Steam did not return a price.")
        price = SteamPrice(prices["currency"], prices["initial"], prices["final"], prices["discount_percent"],
                           prices["initial_formatted"], prices["final_formatted"])
    except (TypeError, KeyError) as error:
        raise ValueError("Steam did not return a price.") from error
    if (any(type(value) is not int for value in (price.initial, price.final, price.discount))
            or not 0 <= price.final <= price.initial or not 0 <= price.discount <= 100
            or not isinstance(price.currency, str) or not re.fullmatch(r"[A-Z]{3}", price.currency)
            or not all(isinstance(value, str) and len(value) <= 32 for value in (price.initial_formatted, price.final_formatted))
            or (price.discount and (price.initial == price.final or not price.final_formatted))):
        raise ValueError("Steam returned an inconsistent price.")
    return price
