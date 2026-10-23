"""UFC official schedule and fight cards. No inferred start times or results."""
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

OFFICIAL_EVENTS = "https://www.ufc.com/events"


def text(node):
    return re.sub(r"\s+,", ",", node.get_text(" ", strip=True)) if node else ""


def timestamp(value):
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_schedule(html):
    soup = BeautifulSoup(html, "html.parser")
    groups = {}
    for section, key in (("events-list-upcoming", "events"), ("events-list-past", "past_events")):
        container = soup.find(id=section)
        if not container:
            raise ValueError(f"UFC schedule section missing: {section}")
        events = []
        for card in container.select(".c-card-event--result"):
            heading = card.select_one(".c-card-event--result__headline a")
            date = card.select_one(".c-card-event--result__date")
            if not heading or not date:
                continue
            url = urljoin(OFFICIAL_EVENTS, heading.get("href", ""))
            if urlparse(url).hostname != "www.ufc.com" or not urlparse(url).path.startswith("/event/"):
                continue
            start = timestamp(date.get("data-main-card-timestamp"))
            if not start:
                # A ticket-presale timestamp must never become the fight start.
                continue
            slug = urlparse(url).path.rsplit("/", 1)[-1]
            number = re.fullmatch(r"ufc-(\d+)", slug)
            code = f"UFC {number[1]}" if number else "UFC Fight Night"
            title = text(heading)
            name = f"{code}: {title}" if title else code
            local_date = re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{2})-(\d{4})$", slug)
            date_iso = datetime.strptime("-".join(local_date.groups()), "%B-%d-%Y").date().isoformat() if local_date else start[:10]
            event = {
                "name": name, "date_iso": date_iso,
                "official_url": url, "source_url": url,
                "start_time_utc": start, "time_status": "confirmed_official", "time_source": url,
                "venue": text(card.select_one(".c-card-event--result__location h5")),
                "location": text(card.select_one(".c-card-event--result__location .address")),
            }
            for field, attr in (("prelims_start_time_utc", "data-prelims-card-timestamp"), ("early_prelims_start_time_utc", "data-early-card-timestamp")):
                value = timestamp(date.get(attr))
                if value:
                    event[field] = value
            events.append(event)
        groups[key] = sorted({e["official_url"]: e for e in events}.values(), key=lambda e: e["start_time_utc"], reverse=key == "past_events")
    if not groups["events"]:
        raise ValueError("UFC schedule returned no upcoming events; keep the previous data")
    return groups


def parse_card(html):
    soup = BeautifulSoup(html, "html.parser")
    output = {"main_card": [], "prelims": [], "announced_card": []}
    seen = set()
    sections = [("main-card", "main_card"), ("prelims-card", "prelims"), ("early-prelims", "prelims")]
    if not any(soup.find(id=section) for section, _ in sections):
        sections = [("announced", "announced_card")]
    for section_id, key in sections:
        section = soup if section_id == "announced" else soup.find(id=section_id)
        if not section:
            continue
        for row in section.select(".c-listing-fight"):
            corners = [row.select_one(f".c-listing-fight__corner-name--{color} a") for color in ("red", "blue")]
            names = [text(corner) for corner in corners]
            if not all(names):
                continue
            pair = tuple(sorted(names))
            if pair in seen:
                continue
            seen.add(pair)
            fight = {"weight": text(row.select_one(".c-listing-fight__class-text")), "fighter_a": names[0], "fighter_b": names[1], "winner": None}
            for side, color, corner in zip(("a", "b"), ("red", "blue"), corners):
                profile = corner.get("href", "")
                if re.match(r"https://(?:www|kr|jp)\.ufc\.com/athlete/", profile):
                    fight[f"fighter_{side}_ufc_url"] = "https://www.ufc.com" + urlparse(profile).path
                image = row.select_one(f".c-listing-fight__corner-image--{color} img")
                if image:
                    fight[f"fighter_{side}_photo"] = image.get("src", "")
                outcome = text(row.select_one(f".c-listing-fight__corner-body--{color} .c-listing-fight__outcome")).lower()
                if row.select_one(f".c-listing-fight__corner-body--{color} .c-listing-fight__outcome--win") or outcome in ("win", "승", "승리"):
                    fight["winner"] = side
            for field in ("method", "round", "time"):
                fight[field] = text(row.select_one(f".c-listing-fight__result-text.{field}"))
            if section_id == "early-prelims":
                fight["card_section"] = "early_prelims"
            output[key].append(fight)
    return output


def fetch_schedule(get_html):
    return parse_schedule(get_html(OFFICIAL_EVENTS))


def enrich_event(event, get_html):
    """Empty announced cards are valid; a failed detail request is not an empty card."""
    try:
        card = parse_card(get_html(event["official_url"]))
        event.update(card)
        event["card_status"] = "announced" if any(card.values()) else "to_be_announced"
    except Exception as exc:
        event["card_status"] = "unavailable"
        print(f"WARN UFC card unavailable: {event['name']}: {type(exc).__name__}")
    return event
