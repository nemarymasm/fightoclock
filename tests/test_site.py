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

    def test_next_event_uses_official_korean_time_and_full_card(self):
        event = load_json("events.json")["events"][0]
        self.assertEqual(event.get("start_time_utc"), "2026-08-01T17:00:00Z")
        self.assertEqual(len(event.get("main_card", [])) + len(event.get("prelims", [])), 12)

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

    def test_all_ranked_fighters_have_readable_korean_names(self):
        divisions = load_json("rankings.json").get("divisions", [])
        ranked = [
            fighter
            for division in divisions
            for fighter in ([division.get("champion")] + division.get("ranked", []))
            if fighter
        ]
        missing = [
            fighter.get("name")
            for fighter in ranked
            if not re.search(r"[가-힣]", fighter.get("name_ko", ""))
        ]
        self.assertFalse(missing, f"Ranked fighters missing Korean names: {missing}")

    def test_recent_result_cards_have_korean_fighter_names(self):
        events = load_json("events.json").get("past_events", [])
        fighters = {fighter["name"]: fighter for fighter in load_json("fighters.json")["fighters"]}
        missing = [
            name
            for event in events
            for fight in event.get("main_card", [])
            for name in (fight.get("fighter_a"), fight.get("fighter_b"))
            if not re.search(r"[가-힣]", fighters.get(name, {}).get("name_ko", ""))
        ]
        self.assertFalse(missing, f"Recent results contain untranslated names: {missing}")
        self.assertEqual(fighters["Steve Erceg"]["name_ko"], "스티브 얼섹")

    def test_recent_result_event_metadata_is_korean(self):
        events = {event["name"]: event for event in load_json("events.json").get("past_events", [])}
        usman = events["UFC Fight Night: du Plessis vs. Usman"]
        self.assertEqual(usman["name_ko"], "UFC Fight Night: 뒤 플레시 대 우스만")
        self.assertEqual(usman["venue_ko"], "페이컴 센터")
        self.assertEqual(usman["location_ko"], "미국 오클라호마주 오클라호마시티")
        self.assertEqual(
            events["UFC Fight Night: Kape vs. Horiguchi"]["name_ko"],
            "UFC Fight Night: 캅 대 호리구치",
        )

    def test_fighter_photos_use_approved_sources(self):
        fighters = load_json("fighters.json")["fighters"]
        photos = [fighter for fighter in fighters if fighter.get("avatar_url")]
        self.assertGreater(len(photos), 50)
        for fighter in photos:
            self.assertTrue(
                fighter["avatar_url"].startswith(
                    (
                        "/data/avatars/generated/",
                        "https://ufc.com/images/",
                        "https://upload.wikimedia.org/wikipedia/commons/",
                    )
                ),
                fighter["name"],
            )
            if fighter["avatar_url"].startswith("/data/avatars/generated/"):
                self.assertTrue((ROOT / fighter["avatar_url"].lstrip("/")).is_file(), fighter["name"])
            if "wikimedia.org" in fighter["avatar_url"]:
                self.assertNotIn("/512px-thumbnail.", fighter["avatar_url"], fighter["name"])
                self.assertIsNone(
                    re.search(r"(?:flag_of_|medal_icon|logo|icon_)", fighter["avatar_url"], re.IGNORECASE),
                    fighter["name"],
                )
            self.assertTrue(fighter.get("avatar_source"), fighter["name"])

    def test_next_event_headliners_use_official_profiles(self):
        fighters = {fighter["name"]: fighter for fighter in load_json("fighters.json")["fighters"]}
        for name in ("Uroš Medić", "Daniel Rodriguez"):
            fighter = fighters[name]
            self.assertEqual(fighter.get("avatar_provider"), "UFC")
            self.assertTrue(fighter.get("avatar_url", "").endswith("-full.webp"))
            self.assertTrue(fighter.get("avatar_thumb_url", "").endswith("-thumb.webp"))
            self.assertIn("athlete_bio_full_body", fighter.get("avatar_remote_url", ""))

    def test_upcoming_card_photo_coverage(self):
        fighters = load_json("fighters.json")["fighters"]
        upcoming = [fighter for fighter in fighters if fighter.get("next")]
        with_photo = [fighter for fighter in upcoming if fighter.get("avatar_url")]
        official = [fighter for fighter in upcoming if fighter.get("avatar_provider") == "UFC"]
        self.assertGreaterEqual(len(with_photo) / len(upcoming), 0.95)
        self.assertGreaterEqual(len(official), 90)

    def test_weekly_brief_separates_fan_reactions_and_time_estimate(self):
        insight = load_json("insights.json")["events"]["ufc-fight-night-medi-vs-rodriguez"]
        self.assertTrue(insight["schedule"].get("main_event_window"))
        self.assertTrue(insight.get("viewing_hook"))
        self.assertGreaterEqual(len(insight.get("fan_reactions", [])), 3)
        self.assertGreaterEqual(len(insight.get("fan_topics", [])), 3)
        self.assertTrue(insight["fans"].get("source", "").startswith("https://"))
        self.assertEqual(len(insight["market"].get("main_card_odds", [])), 5)
        self.assertTrue(all(row.get("fighter_a_odds") and row.get("fighter_b_odds") for row in insight["market"]["main_card_odds"]))


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
        self.assertIn("FIGHT O’CLOCK", self.html)
        self.assertNotIn("싸울 시간!", self.html)
        self.assertNotIn("UFC 한국어 가이드</", self.html)
        self.assertIn('class="clock-face"', self.html)
        self.assertIn('<b>F</b><span class="clock-face"><i></i></span><b>C</b>', self.html)
        self.assertIn(".brand .mark::before", self.html)
        self.assertNotIn("clip-path:polygon(29% 0", self.html)

    def test_schedule_controls_are_buttons(self):
        self.assertIn('role="group" aria-label="일정 필터"', self.html)
        self.assertIn("setScheduleFilter", self.html)

    def test_supporting_public_files_exist(self):
        for name in ("robots.txt", "sitemap.xml", "site.webmanifest", "favicon.svg", "og-image.svg", "vercel.json"):
            self.assertTrue((ROOT / name).is_file(), name)

    def test_avatar_fallback_is_not_a_stick_figure(self):
        self.assertIn('class="avatar-fallback"', self.html)
        self.assertNotIn('<path d="M25,82', self.html)

    def test_home_surfaces_weekly_fight_intelligence(self):
        for required in (
            "메인이벤트 예상시간",
            "팬 픽",
            "댓글에서 많이 나온 주제",
            "볼 것 세 가지",
            "메인카드 예상 시각",
            "분석 모델",
            "data/insights.json",
            'class="crowd-panel"',
            'class="signal-grid"',
        ):
            self.assertIn(required, self.html)
        for removed in (
            "다들 뭐래?",
            "뭘 보면서 보면 돼?",
            "몇 시쯤 보면 될까?",
            "숫자와 출처도 볼래요?",
            "배당 환산",
            'class="panel-source"',
            'class="poll-note"',
            'class="watch-sub"',
            "출처와 계산 기준",
            'class="brief-sources"',
            "한 줄만 알고 보면",
            'class="week-eyebrow"',
        ):
            self.assertNotIn(removed, self.html)

    def test_home_shows_large_fight_time_cards_after_main_event_comparison(self):
        brief = self.html.split("function weekBrief", 1)[1].split("function homeCardPreview", 1)[0]
        self.assertLess(brief.index('class="brief-grid"'), brief.index("${homeCardPreview(e)}"))
        self.assertLess(brief.index("${homeCardPreview(e)}"), brief.index('class="crowd-panel"'))
        self.assertEqual(self.html.count("${homeCardPreview(e)}"), 1)
        self.assertIn("avatarBox(a,72)", self.html)
        self.assertIn("card-preview-list{display:grid;grid-template-columns:1fr;gap:0}", self.html)
        self.assertIn(".preview-fight:last-child{border-bottom:1px solid var(--line)", self.html)
        self.assertIn("i===1?'코메인'", self.html)
        self.assertIn("?'첫 경기'", self.html)
        self.assertNotIn("grid-template-columns:repeat(2,minmax(0,1fr))", self.html)
        self.assertIn("function remainingTimeLabel(target)", self.html)
        self.assertIn("data-bout-target=", self.html)
        self.assertIn("width:50px;height:50px", self.html)
        self.assertIn("document.querySelectorAll('[data-bout-target]')", self.html)
        self.assertIn('class="preview-center"', self.html)
        self.assertIn('class="preview-odds"', self.html)
        self.assertIn("market.main_card_odds||[]", self.html)
        self.assertIn("`${fights.length-i}번째 경기`", self.html)
        self.assertIn("preview-fight${f.winner?' done':''}", self.html)
        self.assertIn("preview-outcome", self.html)

    def test_week_header_prioritizes_time_place_and_countdown(self):
        self.assertIn('class="week-time-line"', self.html)
        self.assertIn('class="week-event"', self.html)
        self.assertIn("${esc(eventCodeKo(e.code))} · ${esc(e.titleKo||e.title)}", self.html)
        self.assertNotIn('class="week-meta"', self.html)
        self.assertIn(".week-d{display:grid;place-items:center", self.html)
        self.assertIn("font-size:27px", self.html)

    def test_card_order_is_a_visual_guide_not_tiny_explanatory_copy(self):
        self.assertIn('class="card-order-guide"', self.html)
        self.assertIn("아래쪽이 첫 경기", self.html)
        self.assertIn("위쪽이 메인", self.html)
        self.assertNotIn("아래에서 시작해 메인이 마지막이에요", self.html)
        self.assertNotIn(".card-preview-head p{", self.html)

    def test_readability_scale_avoids_tiny_core_ui_text(self):
        self.assertIn("body{padding-bottom:calc(72px + env(safe-area-inset-bottom));min-height:100vh;font-size:16px}", self.html)
        self.assertIn(".event-item .title{font-weight:800;font-size:16px", self.html)
        self.assertIn(".preview-fighter-copy b{max-width:100%;font-size:18px", self.html)
        self.assertIn(".preview-time em{display:block;margin-top:6px;color:#ff8589;font-size:14px", self.html)

    def test_home_uses_direct_matchup_language_and_readable_priority_text(self):
        insight = load_json("insights.json")["events"]["ufc-fight-night-medi-vs-rodriguez"]
        self.assertEqual(
            insight["viewing_hook"],
            "메디치 초반 초살 VS 로드리게스 3라운드 운영",
        )
        self.assertIn(".match-stage .cf-rec{font-size:15px", self.html)
        self.assertIn(".main-eta span{display:block;color:#f1f1f2;font-size:15px", self.html)
        self.assertIn(".eta-note{margin-top:9px;color:#f1bd61;font-size:14px", self.html)

    def test_event_and_fighter_lists_prioritize_korean_labels(self):
        self.assertIn("function eventCodeKo(code)", self.html)
        self.assertIn("UFC 파이트 나이트", self.html)
        fight_row = self.html.split("function fightRow", 1)[1].split("function eventScore", 1)[0]
        fighter_list = self.html.split("function fighterListHtml", 1)[1].split("function renderFighterList", 1)[0]
        self.assertNotIn("name-original", fight_row)
        self.assertNotIn("name-original", fighter_list)

    def test_fight_results_translate_method_round_and_time(self):
        self.assertIn("function resultMethodKo(method)", self.html)
        self.assertIn("'Decision (unanimous)':'전원일치 판정'", self.html)
        self.assertIn("'Submission (rear-naked choke)':'리어네이키드초크 서브미션'", self.html)
        self.assertIn("m.round + '라운드'", self.html)

    def test_upcoming_events_are_visually_separate_from_this_week(self):
        self.assertIn('class="upcoming-section"', self.html)
        self.assertIn("별도 대회 · Up next", self.html)
        self.assertIn("이번 주 대회 이후에 열리는 다른 대회입니다.", self.html)
        self.assertIn(".upcoming-section{margin-top:64px", self.html)


if __name__ == "__main__":
    unittest.main()
