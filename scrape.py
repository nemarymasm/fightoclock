"""
fightoclock 데이터 자동수집 스크립트 (v2)
==========================================

매일 GitHub Actions가 이 파일을 실행합니다.

하는 일:
1. 위키피디아 "List of UFC events" 페이지에서 다가오는 UFC 이벤트 추출
2. 각 이벤트의 개별 페이지에서 메인카드/방송정보 추출 (강화된 파싱)
3. data/translations.json 사전 적용 (선수/베뉴/위치 한국어화)
4. Claude Haiku 가 영문 정보를 한국어로 요약
5. data/events.json 파일에 결과 저장

translations.json 은 사용자가 직접 편집 — 이 파일을 절대 덮어쓰지 않음.
"""

import os
import re
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

# ────────────────────────────────────────────────────────────────
# 설정값
# ────────────────────────────────────────────────────────────────
WIKI = "https://en.wikipedia.org/wiki/"
HEADERS = {
    "User-Agent": "FightOclockBot/2.0 (https://fightoclock.kr; nemarymasm@gmail.com)"
}
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()

DATA_DIR = Path(__file__).parent / "data"
EVENTS_FILE = DATA_DIR / "events.json"
TRANSLATIONS_FILE = DATA_DIR / "translations.json"

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


# ────────────────────────────────────────────────────────────────
# 번역 사전 로드
# ────────────────────────────────────────────────────────────────
def load_translations():
    """data/translations.json 읽어서 dict 반환. 없거나 오류면 빈 dict."""
    if not TRANSLATIONS_FILE.exists():
        print("⚠️  translations.json 없음 — 영문 그대로 사용")
        return {"fighters": {}, "venues": {}, "locations": {}}
    try:
        data = json.loads(TRANSLATIONS_FILE.read_text(encoding="utf-8"))
        f = len(data.get("fighters", {}))
        v = len(data.get("venues", {}))
        l = len(data.get("locations", {}))
        print(f"✓ 번역 사전 로드: 선수 {f}명 / 베뉴 {v}곳 / 위치 {l}곳")
        return data
    except Exception as e:
        print(f"⚠️  translations.json 로드 실패: {e}")
        return {"fighters": {}, "venues": {}, "locations": {}}


TRANSLATIONS = load_translations()


def tr_fighter(name):
    """선수 이름 영→한. 사전에 없으면 원문 유지."""
    if not name:
        return name
    return TRANSLATIONS.get("fighters", {}).get(name.strip(), name.strip())


def tr_venue(venue):
    if not venue:
        return venue
    return TRANSLATIONS.get("venues", {}).get(venue.strip(), venue.strip())


def tr_location(location):
    if not location:
        return location
    return TRANSLATIONS.get("locations", {}).get(location.strip(), location.strip())


def tr_event_title(name):
    """'UFC 328: Chimaev vs. Strickland' → 'UFC 328: 치마예프 대 스트릭랜드' (성으로 매칭)."""
    if not name:
        return name
    m = re.match(r"^(UFC[^:]*?):\s*(.+?)\s+vs\.?\s+(.+)$", name, re.IGNORECASE)
    if not m:
        return name
    prefix, a, b = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    a_kr = lookup_surname_kr(a) or a
    b_kr = lookup_surname_kr(b) or b
    if a_kr != a or b_kr != b:
        return f"{prefix}: {a_kr} 대 {b_kr}"
    return name


def lookup_surname_kr(surname):
    """'Chimaev' → 사전에서 매칭되는 풀네임 찾아 한국어 성만 반환."""
    s = surname.lower().strip()
    for en_name, kr_name in TRANSLATIONS.get("fighters", {}).items():
        en_parts = en_name.lower().split()
        if en_parts and en_parts[-1] == s:
            kr_parts = kr_name.split()
            return kr_parts[-1] if kr_parts else kr_name
    return None


# ────────────────────────────────────────────────────────────────
# 유틸리티
# ────────────────────────────────────────────────────────────────
def http_get(url):
    """위키피디아에 공손하게 요청. 0.5초씩 쉬어가며 호출."""
    time.sleep(0.5)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def parse_wiki_date(text):
    """위키피디아 날짜 다양한 형태 파싱."""
    if not text:
        return None
    text = text.strip().split("\n")[0]
    text = re.sub(r"\[\d+\]", "", text).strip()
    formats = ["%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d"]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_kst_human(date_iso):
    if not date_iso:
        return None
    try:
        d = datetime.fromisoformat(date_iso).date()
        return f"{d.year}년 {d.month}월 {d.day}일({WEEKDAY_KO[d.weekday()]})"
    except Exception:
        return None


def clean_text(s):
    if not s:
        return ""
    s = re.sub(r"\[\d+\]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_fighter_name(s):
    """선수 이름에서 (c), (ic) 같은 챔피언 표기 제거."""
    if not s:
        return ""
    s = clean_text(s)
    s = re.sub(r"\s*\((c|ic|interim)\)", "", s, flags=re.IGNORECASE).strip()
    return s


# ────────────────────────────────────────────────────────────────
# Wikipedia 파싱: 다가오는 이벤트 목록
# ────────────────────────────────────────────────────────────────
def fetch_upcoming_events():
    """List of UFC events 페이지에서 Scheduled events 표 가져오기."""
    print("\n→ Wikipedia: List of UFC events 가져오는 중...")
    html = http_get(WIKI + "List_of_UFC_events")
    soup = BeautifulSoup(html, "html.parser")

    heading = None
    for h in soup.find_all(["h2", "h3"]):
        if "scheduled" in h.get_text().lower():
            heading = h
            break

    if not heading:
        print("  ⚠️  Scheduled events 섹션 없음")
        return []

    table = heading.find_next("table")
    if not table:
        print("  ⚠️  Scheduled events 테이블 없음")
        return []

    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    header_cells = rows[0].find_all(["th", "td"])
    headers_text = [clean_text(c.get_text()).lower() for c in header_cells]

    def find_col(*keys):
        for i, h in enumerate(headers_text):
            for k in keys:
                if k in h:
                    return i
        return -1

    name_col = find_col("event")
    date_col = find_col("date")
    venue_col = find_col("venue")
    location_col = find_col("location")
    if name_col < 0:
        name_col = 0
    if date_col < 0:
        date_col = 1

    events = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        try:
            name_cell = cells[name_col] if name_col < len(cells) else cells[0]
            link = name_cell.find("a")
            if link:
                name = clean_text(link.get_text())
                href = link.get("href", "")
                wiki_url = "https://en.wikipedia.org" + href if href.startswith("/") else None
            else:
                name = clean_text(name_cell.get_text())
                wiki_url = None
            if not name:
                continue

            date_cell = cells[date_col] if date_col < len(cells) else None
            date_obj = parse_wiki_date(date_cell.get_text()) if date_cell else None
            if not date_obj or date_obj < TODAY:
                continue

            venue = clean_text(cells[venue_col].get_text()) if 0 <= venue_col < len(cells) else ""
            location = clean_text(cells[location_col].get_text()) if 0 <= location_col < len(cells) else ""

            events.append({
                "name": name,
                "wiki_url": wiki_url,
                "date_iso": date_obj.isoformat(),
                "venue": venue,
                "location": location,
            })
        except Exception as e:
            print(f"  ⚠️  행 파싱 에러: {e}")

    events.sort(key=lambda e: e["date_iso"])
    print(f"  ✓ {len(events)}개 이벤트 발견")
    return events[:8]


# ────────────────────────────────────────────────────────────────
# Wikipedia 파싱: 개별 이벤트 (강화된 메인카드 파싱)
# ────────────────────────────────────────────────────────────────
def fetch_event_detail(event):
    """개별 이벤트 페이지에서 메인 카드, 방송 정보 추출."""
    if not event.get("wiki_url"):
        return event

    try:
        html = http_get(event["wiki_url"])
        soup = BeautifulSoup(html, "html.parser")

        # ── Infobox 정보 ──
        infobox = soup.find("table", class_=lambda c: c and "infobox" in c)
        if infobox:
            for row in infobox.find_all("tr"):
                th = row.find("th")
                td = row.find("td")
                if not th or not td:
                    continue
                label = clean_text(th.get_text()).lower()
                value = clean_text(td.get_text())
                if "broadcast" in label:
                    event["broadcast"] = value
                elif "purse" in label:
                    event["purse"] = value
                elif "attendance" in label:
                    event["attendance"] = value

        # ── 메인 카드 파싱 (강화) ──
        # 전략 1: "Main card" 라는 명시적 헤딩 찾기
        # 전략 2: 모든 wikitable/toccolours 테이블 중 "weight class" 헤더가 있는 거 추출
        # 결과를 합치고 첫 5개를 메인카드로 사용

        main_card = parse_main_card_v2(soup)
        if main_card:
            event["main_card"] = main_card

    except Exception as e:
        print(f"  ⚠️  '{event['name']}' 상세 정보 가져오기 실패: {e}")

    return event


def parse_main_card_v2(soup):
    """이벤트 페이지에서 메인 카드 추출 (강화 버전)."""
    # 1. 전체 페이지에서 "fight card" 비슷한 표 후보 모으기
    candidate_tables = []

    # 1-a. "Main card" 헤딩 다음 테이블 우선
    for h in soup.find_all(["h2", "h3", "h4"]):
        text = h.get_text().lower()
        if "main card" in text:
            t = h.find_next("table")
            if t:
                candidate_tables.append(("main_card_heading", t))
            break  # 첫 'main card' 헤딩만 사용

    # 1-b. "Main card" 못 찾으면 weight class 컬럼 있는 모든 wikitable
    if not candidate_tables:
        for table in soup.find_all("table"):
            classes = " ".join(table.get("class") or [])
            if "wikitable" not in classes and "toccolours" not in classes:
                continue
            rows = table.find_all("tr")
            if not rows:
                continue
            header_text = " ".join(c.get_text().lower() for c in rows[0].find_all(["th", "td"]))
            if "weight class" in header_text or "weight" in header_text:
                candidate_tables.append(("weight_header", table))

    # 2. 후보 테이블에서 매치 추출
    fights = []
    for source, table in candidate_tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        header_cells = rows[0].find_all(["th", "td"])
        headers_text = [clean_text(c.get_text()).lower() for c in header_cells]

        # 컬럼 위치 자동 감지
        wc_idx = next((i for i, h in enumerate(headers_text) if "weight" in h), 0)
        # 보통 weight 다음이 fighter A, 그 다음 vs/def, 그 다음 fighter B
        fa_idx = wc_idx + 1
        vs_idx = wc_idx + 2
        fb_idx = wc_idx + 3

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) <= fb_idx:
                continue
            try:
                weight = clean_text(cells[wc_idx].get_text())
                fa_raw = clean_fighter_name(cells[fa_idx].get_text())
                fb_raw = clean_fighter_name(cells[fb_idx].get_text())
                vs_text = clean_text(cells[vs_idx].get_text()).lower() if vs_idx < len(cells) else ""

                if not fa_raw or not fb_raw:
                    continue
                if fa_raw.lower() in ("vs.", "vs", "def.", "def", "tba"):
                    continue
                if fb_raw.lower() in ("vs.", "vs", "def.", "def", "tba"):
                    continue

                fights.append({
                    "weight": weight,
                    "fighter_a": fa_raw,
                    "fighter_b": fb_raw,
                    "vs_text": vs_text,
                })
            except Exception:
                continue

        if fights and source == "main_card_heading":
            break

    return fights[:5]


def summarize_korean(client, event):
    if not client:
        return None

    main_card_str = ""
    if event.get("main_card"):
        cards = event["main_card"][:3]
        lines = []
        for c in cards:
            a = tr_fighter(c["fighter_a"])
            b = tr_fighter(c["fighter_b"])
            lines.append("- " + c["weight"] + ": " + a + " vs " + b)
        main_card_str = "\n주요 경기:\n" + "\n".join(lines)

    venue_kr = tr_venue(event.get("venue", ""))
    loc_kr = tr_location(event.get("location", ""))

    prompt = (
        "다음 UFC 이벤트를 한국 팬들에게 친근한 톤으로 한국어로 요약해주세요.\n\n"
        "이벤트: " + event["name"] + "\n"
        "날짜: " + event["date_iso"] + "\n"
        "장소: " + venue_kr + ", " + loc_kr + main_card_str + "\n\n"
        "규칙:\n"
        "- 3-4문장. 짧고 자연스럽게.\n"
        "- 결과 예측이나 추측은 금지.\n"
        "- 메인 이벤트의 의미나 관전 포인트를 한 줄 짚어주기.\n"
        "- '~입니다' 톤. 너무 격식 차리지 말기.\n"
        "- 선수 이름은 위에 적힌 한국어 표기 그대로 써주세요.\n\n"
        "요약:"
    )

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print("  WARN Claude error:", e)
        return None


def main():
    print("=== fightoclock 데이터 수집 시작:", datetime.now(KST).isoformat(timespec="seconds"), "===")
    DATA_DIR.mkdir(exist_ok=True)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = None
    if api_key and Anthropic:
        client = Anthropic(api_key=api_key)
        print("OK Anthropic client ready")
    else:
        print("WARN ANTHROPIC_API_KEY missing, skipping summary")

    events = fetch_upcoming_events()
    if not events:
        print("WARN no events, keeping existing events.json")
        return

    print("\n-> Fetching event details...")
    for i, ev in enumerate(events):
        print("  [" + str(i+1) + "/" + str(len(events)) + "]", ev["name"])
        events[i] = fetch_event_detail(ev)
        mc_count = len(events[i].get("main_card", []))
        print("      main card:", mc_count, "fights")

    if client:
        print("\n-> Generating Korean summaries via Claude Haiku...")
        for i, ev in enumerate(events):
            summary = summarize_korean(client, ev)
            if summary:
                events[i]["summary_ko"] = summary
                print("  [" + str(i+1) + "/" + str(len(events)) + "] OK")
            time.sleep(1)

    print("\n-> Applying translations (fighter/venue/location)...")
    for ev in events:
        ev["name_ko"] = tr_event_title(ev["name"])
        ev["venue_ko"] = tr_venue(ev["venue"])
        ev["location_ko"] = tr_location(ev["location"])
        for fight in ev.get("main_card", []):
            fight["fighter_a_ko"] = tr_fighter(fight["fighter_a"])
            fight["fighter_b_ko"] = tr_fighter(fight["fighter_b"])
        ev["date_kst_human"] = to_kst_human(ev["date_iso"])

    output = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "Wikipedia: List of UFC events",
        "translations_applied": True,
        "event_count": len(events),
        "events": events,
    }
    EVENTS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== Saved:", EVENTS_FILE.name, "(" + str(len(events)) + " events) ===")


if __name__ == "__main__":
    main()
