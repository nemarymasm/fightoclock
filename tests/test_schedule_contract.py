import json
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from scrape import slugify

DATA = Path(__file__).resolve().parents[1] / "data"


class SchedulePublicationTests(unittest.TestCase):
    def test_current_schedule_is_fresh_complete_and_linked(self):
        events = json.loads((DATA / "events.json").read_text(encoding="utf-8"))
        fighters = {f["id"]: f for f in json.loads((DATA / "fighters.json").read_text(encoding="utf-8"))["fighters"]}
        insights = json.loads((DATA / "insights.json").read_text(encoding="utf-8"))["events"]
        now = datetime.now(timezone.utc)
        self.assertLess(now - datetime.fromisoformat(events["generated_at"]), timedelta(hours=72))
        self.assertEqual(events["event_count"], len(events["events"]))
        self.assertTrue(events["events"])
        first = events["events"][0]
        if first.get("start_time_utc"):
            self.assertGreater(datetime.fromisoformat(first["start_time_utc"].replace("Z", "+00:00")) + timedelta(hours=8), now)
        else:
            self.assertGreaterEqual(datetime.fromisoformat(first["date_iso"]).date(), now.date())
        self.assertTrue(events["events"][0].get("main_card") or events["events"][0].get("announced_card"))
        for event in events["events"] + events["past_events"]:
            datetime.fromisoformat(event["date_iso"])
            pairs = set()
            for fight in event.get("main_card", []) + event.get("prelims", []) + event.get("announced_card", []):
                self.assertIn(fight.get("winner"), (None, "a", "b"))
                names = [fight.get("fighter_a"), fight.get("fighter_b")]
                self.assertTrue(all(names))
                pair = frozenset(names)
                self.assertEqual(len(pair), 2)
                self.assertNotIn(pair, pairs)
                pairs.add(pair)
                for name in names:
                    self.assertIn(slugify(name), fighters)
            if event in events["events"]:
                self.assertIn(slugify(event["name"]), insights)
                if event.get("start_time_utc"):
                    self.assertTrue(event.get("time_source"))
                    self.assertIsNotNone(datetime.fromisoformat(event["start_time_utc"].replace("Z", "+00:00")).tzinfo)
