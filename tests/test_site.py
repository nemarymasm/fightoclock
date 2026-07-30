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

    def test_ranked_names_follow_curated_korean_broadcast_labels(self):
        translations = load_json("translations.json").get("fighters", {})
        expected = {
            "Jean Silva": "제앙 실바",
            "Alexandre Pantoja": "알렉산드레 판토자",
            "Arman Tsarukyan": "아르만 사루키안",
            "Khamzat Chimaev": "함자트 치마예프",
            "Carlos Prates": "카를로스 프라치스",
            "Ian Machado Garry": "이안 마샤두 개리",
            "Reinier de Ridder": "레이니어 더 리더",
        }
        self.assertEqual({name: translations.get(name) for name in expected}, expected)
        ranked = {
            entry["name"]: entry.get("name_ko")
            for division in load_json("rankings.json").get("divisions", [])
            for entry in ([division.get("champion")] + division.get("ranked", []))
            if entry
        }
        for name, korean in expected.items():
            if name in ranked:
                self.assertEqual(ranked[name], korean)

    def test_unranked_korean_ufc_roster_is_kept_with_official_profiles(self):
        fighters = load_json("fighters.json").get("fighters", [])
        korean = [fighter for fighter in fighters if fighter.get("korean_focus")]
        self.assertGreaterEqual(len(korean), 9)
        featherweights = {
            fighter["name_ko"]
            for fighter in korean
            if fighter.get("division") == "페더급"
        }
        self.assertEqual(featherweights, {"최두호", "이정영", "유주상"})
        for fighter in korean:
            self.assertEqual(fighter.get("country_ko"), "대한민국")
            self.assertEqual(fighter.get("avatar_provider"), "UFC")
            self.assertTrue(fighter.get("avatar_url", "").endswith("-full.webp"))
            self.assertTrue(fighter.get("history"), fighter["name"])

    def test_every_official_ranked_fighter_uses_an_official_ufc_photo(self):
        rankings = load_json("rankings.json").get("divisions", [])
        fighters = {
            fighter["id"]: fighter
            for fighter in load_json("fighters.json").get("fighters", [])
        }
        ranked_ids = {
            entry["fighter_id"]
            for division in rankings
            for entry in ([division.get("champion")] + division.get("ranked", []))
            if entry
        }
        self.assertGreaterEqual(len(ranked_ids), 165)
        non_official = [
            fighters[fighter_id]["name"]
            for fighter_id in ranked_ids
            if fighters.get(fighter_id, {}).get("avatar_provider") != "UFC"
        ]
        self.assertFalse(non_official, f"Rankers without current UFC photos: {non_official}")
        self.assertIn("weekly_ranked_photo_refresh", (ROOT / "scrape.py").read_text(encoding="utf-8"))

    def test_fan_tags_are_researched_curated_and_reviewable(self):
        data = load_json("fan_tags.json")
        self.assertEqual(data["fallback"]["text"], "")
        tags = data.get("tags", {})
        expected = {
            "ciryl-gane": "드릴러",
            "tom-aspinall": "눈이 불편함",
            "alexander-volkanovski": "볼황",
            "conor-mcgregor": "다리가 불편함",
            "diego-lopes": "볼카 아들",
            "max-holloway": "맥또 당첨자",
            "chan-sung-jung": "코리안좀비",
            "ilia-topuria": "끌어당김 1회 실패함",
            "justin-gaethje": "안 끌어당겨짐 1회 성공함",
            "jon-jones": "자동사냥중",
            "alex-pereira": "샤마",
            "ji-proch-zka": "오륜서 압수",
            "dricus-du-plessis": "뒷점멸",
            "khamzat-chimaev": "스트릭랜드한테 입양됨",
            "kamaru-usman": "차은우스만",
            "sean-o-malley": "ㄲㅂ",
            "marlon-vera": "페스",
            "dustin-poirier": "간바레 다이아몬드",
            "islam-makhachev": "은퇴전까지 승리?",
            "michael-chandler": "한게임만 이기자",
            "sean-strickland": "뒤플이 노리고있음",
        }
        self.assertEqual({fighter_id: tags.get(fighter_id, {}).get("text") for fighter_id in expected}, expected)
        for fighter_id, tag in tags.items():
            self.assertIn(tag.get("status"), {"verified", "owner_approved"}, fighter_id)
            self.assertTrue(tag.get("context"), fighter_id)
            self.assertRegex(tag.get("reviewed_at", ""), r"^\d{4}-\d{2}-\d{2}$")
            self.assertRegex(tag.get("review_after", ""), r"^\d{4}-\d{2}-\d{2}$")
            if tag.get("status") == "verified":
                sources = [source for source in tag.get("sources", []) if source.get("url")]
                self.assertGreaterEqual(len(sources), 2, fighter_id)
                self.assertTrue(all(source["url"].startswith("https://") for source in sources))
            else:
                self.assertRegex(tag.get("approved_at", ""), r"^\d{4}-\d{2}-\d{2}$")
        stats = data.get("review_stats", {})
        self.assertGreaterEqual(stats.get("waiting_count", 0), 100)
        self.assertLessEqual(len(data.get("review_queue", [])), stats.get("queue_limit", 40))

    def test_official_rankings_link_to_complete_fighter_profiles(self):
        rankings = load_json("rankings.json")
        fighters = {fighter["id"]: fighter for fighter in load_json("fighters.json")["fighters"]}
        self.assertIn("UFC 공식", rankings.get("source", ""))
        entries = [
            fighter
            for division in rankings.get("divisions", [])
            for fighter in ([division.get("champion")] + division.get("ranked", []))
            if fighter
        ]
        self.assertEqual(len(entries), 176)
        linked = [fighters.get(entry.get("fighter_id")) for entry in entries]
        self.assertTrue(all(linked), "every official ranking row must link to a fighter")
        self.assertTrue(all(fighter.get("avatar_url") or fighter.get("avatar") for fighter in linked))
        self.assertTrue(all(fighter.get("history") for fighter in linked))
        self.assertTrue(all(re.search(r"[가-힣]", fighter.get("name_ko", "")) for fighter in linked))

    def test_tom_aspinall_has_photo_and_full_professional_record(self):
        fighters = {fighter["name"]: fighter for fighter in load_json("fighters.json")["fighters"]}
        tom = fighters["Tom Aspinall"]
        self.assertEqual(tom.get("avatar_provider"), "UFC")
        self.assertTrue(tom.get("avatar_url", "").endswith("tom-aspinall-full.webp"))
        self.assertGreaterEqual(len(tom.get("history", [])), 19)
        latest = tom["history"][0]
        self.assertEqual(latest.get("opp_ko"), "시릴 가네")
        self.assertEqual(latest.get("result"), "nc")

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

    def test_recent_result_main_cards_have_fighter_photos(self):
        events = load_json("events.json").get("past_events", [])
        fighters = {fighter["name"]: fighter for fighter in load_json("fighters.json")["fighters"]}
        names = {
            name
            for event in events
            for fight in event.get("main_card", [])
            for name in (fight.get("fighter_a"), fight.get("fighter_b"))
            if name
        }
        missing = [
            name
            for name in names
            if not (fighters.get(name, {}).get("avatar_url") or fighters.get(name, {}).get("avatar"))
        ]
        self.assertFalse(missing, f"Recent result fighters missing photos: {missing}")

    def test_recent_result_matches_have_editorial_talking_points(self):
        events = load_json("events.json").get("past_events", [])
        fights = [
            fight
            for event in events
            for fight in event.get("main_card", [])
        ]
        missing = [
            f"{fight.get('fighter_a')} vs {fight.get('fighter_b')}"
            for fight in fights
            if not fight.get("talking_point_ko")
        ]
        self.assertFalse(missing, f"Recent result fights missing talking points: {missing}")
        usman_fight = next(
            fight for fight in fights
            if fight.get("fighter_b") == "Kamaru Usman"
        )
        self.assertIn("우스만의 5라운드 분전", usman_fight["talking_point_ko"])
        self.assertIn("타이틀전?", usman_fight["talking_point_ko"])

    def test_alexander_volkov_uses_current_official_ufc_photo(self):
        fighters = {fighter["name"]: fighter for fighter in load_json("fighters.json")["fighters"]}
        volkov = fighters["Alexander Volkov"]
        self.assertEqual(volkov.get("avatar_provider"), "UFC")
        self.assertTrue(volkov.get("avatar_url", "").endswith("alexander-volkov-full.webp"))
        self.assertTrue(volkov.get("avatar_thumb_url", "").endswith("alexander-volkov-thumb.webp"))
        self.assertEqual(volkov.get("avatar_source"), "https://www.ufc.com/athlete/alexander-volkov")

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

    def test_this_week_roster_has_korean_profiles_and_near_complete_photos(self):
        event = load_json("events.json")["events"][0]
        fighters = {fighter["name"]: fighter for fighter in load_json("fighters.json")["fighters"]}
        names = {
            name
            for fight in event.get("main_card", []) + event.get("prelims", [])
            for name in (fight.get("fighter_a"), fight.get("fighter_b"))
        }
        self.assertTrue(all(name in fighters for name in names))
        self.assertTrue(all(re.search(r"[가-힣]", fighters[name].get("name_ko", "")) for name in names))
        with_photo = [
            name for name in names
            if fighters[name].get("avatar_url") or fighters[name].get("avatar")
        ]
        self.assertGreaterEqual(len(with_photo) / len(names), 0.95)
        self.assertEqual(
            next(f for f in event["prelims"] if f["fighter_a"] == "Mark Vologdin")["weight_ko"],
            "밴텀급",
        )
        self.assertEqual(
            next(f for f in event["prelims"] if f["fighter_a"] == "Jovan Leka")["weight_ko"],
            "헤비급",
        )

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

    def test_weekly_intelligence_is_part_of_daily_refresh(self):
        scraper = (ROOT / "scrape.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "scrape.yml").read_text(encoding="utf-8")
        self.assertIn("INSIGHTS_FILE = DATA_DIR / \"insights.json\"", scraper)
        self.assertIn("def refresh_insights(events, opinions, now_iso)", scraper)
        self.assertIn("refresh_insights(events, opinions, now_iso)", scraper)
        self.assertIn("data/insights.json", workflow)
        self.assertIn(
            '{"weight": "Bantamweight", "fighter_a": "Mark Vologdin", "fighter_b": "Josias Musasa"}',
            scraper,
        )
        self.assertIn(
            '{"weight": "Heavyweight", "fighter_a": "Jovan Leka", "fighter_b": "Max Gimenis"}',
            scraper,
        )

    def test_fan_tags_render_beside_fighters_and_refresh_a_review_queue(self):
        scraper = (ROOT / "scrape.py").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "scrape.yml").read_text(encoding="utf-8")
        for required in (
            "let FAN_TAGS",
            "function fanTagFor",
            "function fanTagHtml",
            "data/fan_tags.json",
            'class="fan-tag ${tone}"',
            "${fanTagHtml(champ)}",
            "${fanTagHtml(f)}",
            ".fan-tag.warning",
            ".fan-tag.quiet",
            "if(!tag) return '';",
        ):
            self.assertIn(required, self.html)
        self.assertIn("def refresh_fan_tag_review_queue", scraper)
        self.assertIn('{"verified", "owner_approved"}', scraper)
        self.assertIn("refresh_fan_tag_review_queue(divisions, fighters, now_iso)", scraper)
        self.assertIn("--fan-tags-review", scraper)
        self.assertIn("data/fan_tags.json", workflow)

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

    def test_rankings_show_photos_and_fighter_page_shows_full_history(self):
        ranking_view = self.html.split("function viewRankings", 1)[1].split("function fighterMatches", 1)[0]
        fighter_view = self.html.split("function viewFighter(id)", 1)[1].split("const REACTIONS", 1)[0]
        self.assertIn("${avatarBox(champ,112)}", ranking_view)
        self.assertIn("${avatarBox(f,72)}", ranking_view)
        self.assertIn("fighter_id || slugify", self.html)
        self.assertIn("f.history&&f.history.length", self.html)
        self.assertIn("UFC 전적", fighter_view)
        self.assertIn("UFC 입성 이전 전적", fighter_view)
        self.assertIn("onlyUfcHistory", fighter_view)
        self.assertIn("fighterHistoryList(ufcHistory)", fighter_view)
        self.assertIn('class="pre-ufc-history"', fighter_view)
        self.assertIn("resultMethodKo(r.way||'')", self.html)

    def test_event_detail_reuses_time_odds_and_editorial_intelligence(self):
        event_view = self.html.split("function viewEvent(id)", 1)[1].split("/* ---------- 라우터", 1)[0]
        for required in (
            "homeCardPreview(e,{detail:true})",
            "eventIntelligence(e)",
            "이 경기, 이렇게 보면 됩니다",
            "팬 투표",
            "UFC 일정",
            "배당 ${market.as_of",
        ):
            self.assertIn(required, self.html if required != "homeCardPreview(e,{detail:true})" else event_view)

    def test_fighter_history_localizes_dates_opponents_and_common_methods(self):
        translations = load_json("translations.json")["fighters"]
        for name in (
            "Tai Tuivasa",
            "Jairzinho Rozenstruik",
            "Alistair Overeem",
            "Fabrício Werdum",
            "Derek Brunson",
            "Darren Till",
            "Brad Tavares",
            "Trevin Giles",
            "Markus Perez",
        ):
            self.assertRegex(translations.get(name, ""), r"[가-힣]")
        for required in (
            "function formatHistoryDateKo(value)",
            "function localizeFighterName(value)",
            "function localizeHistoryEvent(value)",
            "match=raw.match(/^(\\d{1,2})\\s+([A-Za-z]+)\\s+(\\d{4})$/)",
            "'face crank':'페이스 크랭크'",
            "'body kick and punches':'보디킥·펀치'",
            '<div class="opp">${r.opp}전</div>',
            ".replace(/UFC on ESPN/gi,'UFC ESPN 대회')",
        ):
            self.assertIn(required, self.html)
        fighter_view = self.html.split("function fighterHistoryList", 1)[1].split("function viewFighter", 1)[0]
        self.assertNotIn('>vs ${r.opp}', fighter_view)

    def test_rankings_use_large_faces_and_one_shared_column_header(self):
        ranking_view = self.html.split("function viewRankings", 1)[1].split("function fighterMatches", 1)[0]
        for required in (
            'class="rankings-page"',
            'class="champion-card"',
            'class="champion-profile"',
            'class="champion-data"',
            'class="ranking-list-head"',
            'class="rank-photo"',
            'class="rank-athlete"',
            'class="rank-row-stats"',
            'class="rank-country"',
            'class="rank-record"',
            "<span>순위</span>",
            "<span>선수</span>",
            "<span>국가</span>",
            "<span>전적</span>",
        ):
            self.assertIn(required, ranking_view)
        self.assertNotIn('<div class="rank-country"><span>국가</span>', ranking_view)
        self.assertNotIn('<div class="rank-record"><span>전적</span>', ranking_view)
        for css_class in (
            ".view.rankings-view",
            ".champion-photo",
            ".champion-name",
            ".ranking-list-head,.rank-row",
            ".rank-photo",
            ".rank-athlete .nm",
            ".rank-country b,.rank-record b",
        ):
            self.assertIn(css_class, self.html)
        self.assertIn("base==='rankings' ? 'rankings-view'", self.html)
        self.assertIn("function countryLabelKo(country)", self.html)
        self.assertIn("function countryValueHtml(country)", self.html)
        self.assertIn("const COUNTRY_FLAGS", self.html)
        self.assertIn('class="country-value"', ranking_view)
        for flag in ("'미얀마':'mm'", "'브라질':'br'", "'미국':'us'", "'일본':'jp'", "'카자흐스탄':'kz'"):
            self.assertIn(flag, self.html)
        self.assertIn('src="https://flagcdn.com/w40/${flagCode}.png"', self.html)
        self.assertIn("https://flagcdn.com", (ROOT / "vercel.json").read_text(encoding="utf-8"))
        for required in (
            "function koreanRosterPanel(group)",
            "한국 UFC 선수",
            "공식 랭킹 밖",
            "koreanFocus: Boolean(f.korean_focus)",
            "'대한민국':'kr'",
        ):
            self.assertIn(required, self.html)
        for css_class in (
            ".korean-roster",
            ".korean-roster-grid",
            ".korean-athlete",
        ):
            self.assertIn(css_class, self.html)

    def test_navigation_uses_large_pictograms_without_covering_desktop_content(self):
        nav = self.html.split('<nav class="tabbar"', 1)[1].split("</nav>", 1)[0]
        self.assertEqual(nav.count("<svg "), 5)
        for label in ("홈", "일정", "결과", "랭킹", "선수"):
            self.assertIn(f"<span>{label}</span>", nav)
        self.assertIn(".topbar{\n    position:relative", self.html)
        self.assertIn(".tabbar{position:relative;top:auto", self.html)
        self.assertIn(".tabbar a{height:72px;font-size:17px}", self.html)

    def test_fight_results_translate_method_round_and_time(self):
        self.assertIn("function resultMethodKo(method)", self.html)
        self.assertIn("'decision':'판정'", self.html)
        self.assertIn("'unanimous':'전원일치'", self.html)
        self.assertIn("'rear-naked choke':'리어네이키드 초크'", self.html)
        self.assertIn("if(prefix==='submission')", self.html)
        self.assertIn("m.round + '라운드'", self.html)

    def test_result_cards_distinguish_method_date_and_place(self):
        results = self.html.split("function resultMethodMeta", 1)[1].split("function setRankingFilter", 1)[0]
        for required in (
            "method-badge",
            "kind='tko'",
            "kind='ko'",
            "kind='sub'",
            "kind='split'",
            "kind='decision'",
            "result-meta-item",
            "<span>일시</span>",
            "<span>장소</span>",
            "대회 전체 결과 보기",
        ):
            self.assertIn(required, results if not required.startswith(".") else self.html)
        for css_class in (
            ".method-badge.ko",
            ".method-badge.tko",
            ".method-badge.sub",
            ".method-badge.decision",
            ".method-badge.split",
            ".result-card h3",
            ".result-meta-item b",
        ):
            self.assertIn(css_class, self.html)

    def test_result_cards_separate_fight_night_numbered_and_show_faces(self):
        results = self.html.split("function resultMethodMeta", 1)[1].split("function setRankingFilter", 1)[0]
        for required in (
            "function resultEventStyle(ev)",
            "{kind:'numbered',format:'넘버링 · PPV'}",
            "{kind:'fight-night',format:'주간 이벤트'}",
            'class="result-card ${eventStyle.kind}"',
            "${avatarBox(a,48)}",
            "${avatarBox(b,48)}",
            'href="#fighter/${esc(f.a)}"',
            'href="#fighter/${esc(f.b)}"',
        ):
            self.assertIn(required, results)
        self.assertIn("function fighterNameKey(name)", self.html)
        self.assertIn("function fighterIdFromName(name)", self.html)
        self.assertIn("const a = fighterIdFromName(m.fighter_a)", self.html)
        self.assertIn("Object.values(FIGHTERS).find", self.html)
        for css_class in (
            ".result-card.fight-night",
            ".result-card.numbered",
            ".result-card.numbered .result-code",
            ".result-fighter .avatar-box",
            ".result-fighter.win .avatar-box",
        ):
            self.assertIn(css_class, self.html)

    def test_result_cards_show_short_match_talking_points(self):
        results = self.html.split("function resultMethodMeta", 1)[1].split("function setRankingFilter", 1)[0]
        self.assertIn("talkingPoint: cleanExternalText(m.talking_point_ko || '')", self.html)
        self.assertIn('class="result-talking-point"', results)
        self.assertIn("${esc(f.talkingPoint)}", results)
        self.assertIn(".result-talking-point{", self.html)
        self.assertIn(".result-card.numbered .result-talking-point", self.html)

    def test_upcoming_events_are_visually_separate_from_this_week(self):
        self.assertIn('class="upcoming-section"', self.html)
        self.assertIn("별도 대회 · Up next", self.html)
        self.assertIn("이번 주 대회 이후에 열리는 다른 대회입니다.", self.html)
        self.assertIn(".upcoming-section{margin-top:64px", self.html)

    def test_champion_history_covers_every_ranked_division(self):
        champions = load_json("champions.json")
        rankings = load_json("rankings.json")
        divisions = champions.get("divisions", [])
        self.assertEqual(champions.get("division_count"), 11)
        self.assertEqual(
            {division["wc"] for division in divisions},
            {division["wc"] for division in rankings.get("divisions", [])},
        )
        reigns = [reign for division in divisions for reign in division.get("reigns", [])]
        self.assertGreaterEqual(len(reigns), 130)
        self.assertTrue(all(re.search(r"[가-힣]", reign.get("name_ko", "")) for reign in reigns))
        self.assertTrue(all(isinstance(reign.get("defenses"), int) for reign in reigns))
        self.assertGreaterEqual(
            sum(bool(reign.get("portrait_url")) for reign in reigns) / len(reigns),
            0.9,
        )
        flyweight = next(division for division in divisions if division["wc"] == "플라이급")
        self.assertEqual(flyweight["record"]["name_ko"], "드미트리우스 존슨")
        self.assertEqual(flyweight["record"]["defenses"], 11)

    def test_rankings_have_responsive_champion_lineage(self):
        ranking_view = self.html.split("function setRankingFilter", 1)[1].split(
            "function fighterMatches", 1
        )[0]
        for required in (
            "function championLineagePanel(group,variant)",
            "체급 최다 연속 방어",
            "현재 챔피언부터 초대 챔피언까지",
            "lineage-mobile",
            "lineage-desktop",
            "현재 타이틀 방어",
            "lineage-record-photo",
            "lineage-record-count",
            "방어 0회",
            "data/champions.json",
        ):
            self.assertIn(required, ranking_view if required != "data/champions.json" else self.html)
        self.assertNotIn("이 재임", ranking_view)
        self.assertNotIn("defenseSub", ranking_view)
        for css_class in (
            ".champion-lineage",
            ".lineage-record",
            ".lineage-row",
            ".lineage-defense",
            ".lineage-mobile .lineage-list",
            ".rankings-layout",
        ):
            self.assertIn(css_class, self.html)
        self.assertIn("@media (min-width:1180px)", self.html)
        self.assertIn("grid-template-columns:minmax(790px,1fr) 330px", self.html)


if __name__ == "__main__":
    unittest.main()
