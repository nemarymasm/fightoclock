import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load_json(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class DataContractTests(unittest.TestCase):
    def test_events_are_recent_and_well_formed(self):
        data = load_json("events.json")
        generated = datetime.fromisoformat(data["generated_at"])
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - generated.astimezone(timezone.utc)).total_seconds() / 3600
        self.assertLess(age_hours, 72, "events.json is older than 72 hours")
        events = data.get("events", [])
        self.assertTrue(events, "at least one upcoming event is required")
        for event in events + data.get("past_events", []):
            self.assertRegex(event.get("date_iso", ""), r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(event.get("name"))
            start = event.get("start_time_utc")
            if start:
                parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
                self.assertIsNotNone(parsed.tzinfo, "start_time_utc must contain a timezone")

    def test_fighters_and_rankings_are_not_empty(self):
        fighters = load_json("fighters.json")
        rankings = load_json("rankings.json")
        self.assertGreater(len(fighters.get("fighters", [])), 50)
        self.assertGreaterEqual(len(rankings.get("divisions", [])), 8)
        ids = [fighter["id"] for fighter in fighters["fighters"]]
        self.assertEqual(len(ids), len(set(ids)), "fighter ids must be unique")

    def test_upcoming_fighters_have_readable_korean_names(self):
        events = load_json("events.json").get("events", [])
        fighters = {fighter["name"]: fighter for fighter in load_json("fighters.json")["fighters"]}
        names = {
            name
            for event in events
            for fight in event.get("main_card", []) + event.get("prelims", [])
            for name in (fight.get("fighter_a"), fight.get("fighter_b"))
            if name
        }
        missing = [
            name for name in names
            if name not in fighters or not re.search(r"[가-힣]", fighters[name].get("name_ko", ""))
        ]
        coverage = 1 - (len(missing) / max(len(names), 1))
        self.assertGreaterEqual(coverage, 0.85, f"Korean-name coverage too low; missing: {missing}")
        self.assertEqual(fighters["Uroš Medić"]["name_ko"], "우로시 메디치")

    def test_fighter_photos_are_reusable_wikimedia_assets(self):
        fighters = load_json("fighters.json")["fighters"]
        photos = [fighter for fighter in fighters if fighter.get("avatar_url")]
        self.assertGreater(len(photos), 50)
        for fighter in photos:
            self.assertTrue(
                fighter["avatar_url"].startswith("https://upload.wikimedia.org/wikipedia/commons/"),
                fighter["name"],
            )
            self.assertNotIn("/512px-thumbnail.", fighter["avatar_url"], fighter["name"])
            self.assertIsNone(
                re.search(r"(?:flag_of_|medal_icon|logo|icon_)", fighter["avatar_url"], re.IGNORECASE),
                fighter["name"],
            )
            self.assertTrue(fighter.get("avatar_source"), fighter["name"])


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_no_fabricated_utc_time(self):
        self.assertNotIn("date_iso + 'T22:00:00Z'", self.html)
        self.assertIn("start_time_utc", self.html)

    def test_initial_render_waits_for_real_data(self):
        self.assertRegex(self.html, r"await loadRealData\(\);\s*route\(\);")
        self.assertIn("appState==='loading'", self.html)

    def test_accessibility_and_metadata(self):
        for required in (
            'class="skip-link"',
            'aria-label="주요 메뉴"',
            'rel="canonical"',
            'application/ld+json',
            'id="appDialog"',
        ):
            self.assertIn(required, self.html)

    def test_schedule_controls_are_buttons(self):
        self.assertIn('role="group" aria-label="일정 필터"', self.html)
        self.assertIn("setScheduleFilter", self.html)

    def test_supporting_public_files_exist(self):
        for name in ("robots.txt", "sitemap.xml", "site.webmanifest", "favicon.svg", "og-image.svg", "vercel.json"):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_avatar_fallback_is_not_a_stick_figure(self):
        self.assertIn('class="avatar-fallback"', self.html)
        self.assertNotIn('<path d="M25,82', self.html)


if __name__ == "__main__":
    unittest.main()
