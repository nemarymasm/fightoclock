import unittest
from unittest.mock import patch, Mock
from datetime import datetime, timezone

from event_feed import parse_schedule, parse_card
import scrape


def listing(section, title="A vs B", slug="ufc-999"):
    return f'''<details id="{section}"><article class="c-card-event--result">
      <h3 class="c-card-event--result__headline"><a href="/event/{slug}">{title}</a></h3>
      <div class="c-card-event--result__date" data-main-card-timestamp="1793491200" data-prelims-card-timestamp="1793480400"></div>
      <div class="c-card-event--result__location"><h5>Venue</h5><p class="address">City</p></div>
    </article></details>'''


class EventFeedTests(unittest.TestCase):
    def test_official_timestamp_is_utc_and_ticket_times_are_ignored(self):
        html = listing("events-list-upcoming") + listing("events-list-past")
        event = parse_schedule(html)["events"][0]
        self.assertEqual(event["start_time_utc"], "2026-11-01T00:00:00Z")
        self.assertEqual(event["prelims_start_time_utc"], "2026-10-31T21:00:00Z")
        self.assertEqual(event["name"], "UFC 999: A vs B")

    def test_local_event_date_and_korean_start_date_are_distinct(self):
        event = parse_schedule(listing("events-list-upcoming", slug="ufc-fight-night-october-31-2026") + listing("events-list-past"))["events"][0]
        self.assertEqual(event["date_iso"], "2026-10-31")
        self.assertEqual(datetime.fromisoformat(event["start_time_utc"].replace("Z", "+00:00")).astimezone(scrape.KST).day, 1)

    def test_missing_or_empty_official_section_fails(self):
        for html in ("<html>blocked</html>", listing("events-list-past"), '<details id="events-list-upcoming"></details><details id="events-list-past"></details>'):
            with self.assertRaises(ValueError):
                parse_schedule(html)

    def test_translation_preserves_confirmed_start(self):
        event = {"name": "UFC 999", "date_iso": "2026-10-31", "start_time_utc": "2026-11-01T00:00:00Z", "time_status": "confirmed_official"}
        scrape.apply_translations(event)
        self.assertEqual(event["start_time_utc"], "2026-11-01T00:00:00Z")
        self.assertEqual(event["time_status"], "confirmed_official")

    def test_card_sections_and_actual_winner(self):
        row = '''<div class="c-listing-fight"><div class="c-listing-fight__corner-name--red"><a>A</a></div><div class="c-listing-fight__corner-name--blue"><a>B</a></div><div class="c-listing-fight__corner-body--blue"><div class="c-listing-fight__outcome--win">Win</div></div><div class="c-listing-fight__result-text method">KO/TKO</div></div>'''
        card = parse_card(f'<div id="main-card">{row}</div><div id="prelims-card">{row}</div>')
        self.assertEqual(len(card["main_card"]), 1)
        self.assertEqual(len(card["prelims"]), 0)
        self.assertEqual(card["main_card"][0]["winner"], "b")
        self.assertEqual(card["main_card"][0]["method"], "KO/TKO")

    def test_source_failures_preserve_previous_files(self):
        path = Mock()
        path.read_text.return_value = '{"events":[],"past_events":[]}'
        with patch.object(scrape, "EVENTS_FILE", path), patch("event_feed.fetch_schedule", side_effect=ValueError("blocked")), patch.object(scrape, "fetch_wikipedia_upcoming_events", return_value=[]):
            with self.assertRaises(RuntimeError):
                scrape.refresh_event_schedule()
        path.write_text.assert_not_called()
        path.with_suffix.assert_not_called()

    def test_announced_bouts_without_card_divisions_are_not_dropped(self):
        html = '<div class="c-listing-fight"><div class="c-listing-fight__corner-name--red"><a>Fighter A</a></div><div class="c-listing-fight__corner-name--blue"><a>Fighter B</a></div></div>'
        card = parse_card(html)
        self.assertEqual(len(card["announced_card"]), 1)
        self.assertEqual(card["main_card"], [])
        self.assertEqual(card["prelims"], [])


if __name__ == "__main__":
    unittest.main()
