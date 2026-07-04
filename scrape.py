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
from urllib.parse import urljoin

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
RANKINGS_FILE = DATA_DIR / "rankings.json"
FIGHTERS_FILE = DATA_DIR / "fighters.json"
TRANSLATIONS_FILE = DATA_DIR / "translations.json"

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]

# 체급 한국어 표기 (랭킹 체급 = 대진표 체급 공용)
WEIGHT_KO = {
    "Heavyweight": "헤비급",
    "Light Heavyweight": "라이트헤비급",
    "Middleweight": "미들급",
    "Welterweight": "웰터급",
    "Lightweight": "라이트급",
    "Featherweight": "페더급",
    "Bantamweight": "밴텀급",
    "Flyweight": "플라이급",
    "Women's Bantamweight": "여성 밴텀급",
    "Women's Flyweight": "여성 플라이급",
    "Women's Strawweight": "여성 스트로급",
    "Women's Featherweight": "여성 페더급",
    "Catchweight": "캐치웨이트",
}

# 국가 한국어 표기 (랭킹 국기 alt = 영문 국가명)
COUNTRY_KO = {
    "United States": "미국", "Brazil": "브라질", "Russia": "러시아",
    "England": "잉글랜드", "United Kingdom": "영국", "Ireland": "아일랜드",
    "France": "프랑스", "Georgia (country)": "조지아", "Georgia": "조지아",
    "Dagestan": "러시아", "Australia": "호주", "New Zealand": "뉴질랜드",
    "Canada": "캐나다", "Mexico": "멕시코", "Poland": "폴란드",
    "Cameroon": "카메룬", "Nigeria": "나이지리아", "China": "중국",
    "South Korea": "대한민국", "Kazakhstan": "카자흐스탄", "Kyrgyzstan": "키르기스스탄",
    "Spain": "스페인", "Netherlands": "네덜란드", "Sweden": "스웨덴",
    "Germany": "독일", "Ecuador": "에콰도르", "Chile": "칠레",
    "Cuba": "쿠바", "Argentina": "아르헨티나", "Japan": "일본",
    "South Africa": "남아공", "Suriname": "수리남", "Jamaica": "자메이카",
    "Moldova": "몰도바", "Czech Republic": "체코", "Peru": "페루",
    "Azerbaijan": "아제르바이잔", "Armenia": "아르메니아", "Turkey": "튀르키예",
}


def tr_weight(w):
    if not w:
        return w
    return WEIGHT_KO.get(w.strip(), w.strip())


def tr_country(c):
    if not c:
        return c
    return COUNTRY_KO.get(c.strip(), c.strip())


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


def slugify(s):
    """프론트엔드 slugify와 동일 규칙 — 선수 id 생성 (링크 매칭용)."""
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9가-힣]+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s or "unknown"


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
                # 위키 링크는 '/wiki/X' 또는 '//en.wikipedia.org/wiki/X'(프로토콜 상대경로) 형태 —
                # urljoin으로 두 경우 모두 안전하게 절대 URL로 변환 (도메인 중복 버그 방지)
                wiki_url = urljoin("https://en.wikipedia.org/", href) if href else None
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

        # ── 대진표 파싱 (메인카드 + 프릴림 + 결과) ──
        card = parse_fight_card(soup)
        if card["main_card"]:
            event["main_card"] = card["main_card"]
        if card["prelims"]:
            event["prelims"] = card["prelims"]

    except Exception as e:
        print(f"  ⚠️  '{event['name']}' 상세 정보 가져오기 실패: {e}")

    return event


def parse_fight_card(soup):
    """이벤트 페이지의 toccolours 대진표에서 경기 추출.

    위키 대진표 구조:
      r0: 'Main card (...)' / 'Preliminary card (...)'  ← 셀 1개, 섹션 구분
      r1: 'Weight class | | | | Method | Round | Time | Notes'  ← 컬럼 헤더
      r2+: '체급 | 선수A | vs./def. | 선수B | 방식 | 라운드 | 시간 | 비고'

    반환: {"main_card": [...], "prelims": [...]}
      각 경기: weight, fighter_a, fighter_b, winner('a'|'b'|None), method, round, time
      · 구분자가 'def.' 이면 종료된 경기(선수A 승), 'vs.' 이면 예정.
    """
    # 'Weight class' 헤더 텍스트가 들어있는 toccolours 대진표 찾기
    table = None
    for t in soup.find_all("table"):
        classes = " ".join(t.get("class") or [])
        if "toccolours" not in classes and "wikitable" not in classes:
            continue
        if "weight class" in t.get_text().lower():
            table = t
            break
    if not table:
        return {"main_card": [], "prelims": []}

    main_card, prelims = [], []
    section = "main"  # main | prelim

    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])

        # 섹션 구분 행 (셀 1개 + colspan)
        if len(cells) == 1:
            label = clean_text(cells[0].get_text()).lower()
            if "main card" in label:
                section = "main"
            elif "prelim" in label or "preliminary" in label:
                section = "prelim"
            continue

        if len(cells) < 4:
            continue

        weight = clean_text(cells[0].get_text())
        # 컬럼 헤더 행 건너뛰기
        if weight.lower() in ("weight class", "weight", ""):
            continue

        fa = clean_fighter_name(cells[1].get_text())
        sep = clean_text(cells[2].get_text()).lower()
        fb = clean_fighter_name(cells[3].get_text())
        if not fa or not fb:
            continue
        if fa.lower() in ("vs.", "vs", "def.", "def", "tba"):
            continue
        if fb.lower() in ("vs.", "vs", "def.", "def", "tba"):
            continue

        method = clean_text(cells[4].get_text()) if len(cells) > 4 else ""
        rnd = clean_text(cells[5].get_text()) if len(cells) > 5 else ""
        tm = clean_text(cells[6].get_text()) if len(cells) > 6 else ""

        # 'def.' → 종료(선수A 승). 'vs.' → 예정.
        winner = "a" if sep.startswith("def") else None

        fight = {
            "weight": weight,
            "fighter_a": fa,
            "fighter_b": fb,
            "winner": winner,
            "method": method,
            "round": rnd,
            "time": tm,
        }
        (main_card if section == "main" else prelims).append(fight)

    # 위키가 메인/프릴림을 안 나눈 경우(구분선 없이 전부 main): 앞 5경기=메인, 나머지=프릴림
    if not prelims and len(main_card) > 6:
        prelims = main_card[5:]
        main_card = main_card[:5]

    return {"main_card": main_card, "prelims": prelims}


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


def apply_translations(ev):
    """이벤트 하나에 한국어 표기 적용 (제목/베뉴/위치/선수/날짜)."""
    ev["name_ko"] = tr_event_title(ev["name"])
    ev["venue_ko"] = tr_venue(ev.get("venue", ""))
    ev["location_ko"] = tr_location(ev.get("location", ""))
    for fight in ev.get("main_card", []) + ev.get("prelims", []):
        fight["fighter_a_ko"] = tr_fighter(fight["fighter_a"])
        fight["fighter_b_ko"] = tr_fighter(fight["fighter_b"])
        fight["weight_ko"] = tr_weight(fight.get("weight", ""))
    ev["date_kst_human"] = to_kst_human(ev["date_iso"])
    return ev


def fetch_rankings():
    """UFC rankings 페이지의 Meta rankings(남 8체급 + 여 3체급) 수집."""
    print("\n→ Wikipedia: UFC 랭킹 가져오는 중...")
    html = http_get(WIKI + "UFC_rankings")
    soup = BeautifulSoup(html, "html.parser")

    divisions = []
    for meta_key in ["men's meta", "women's meta"]:
        h2 = None
        for x in soup.find_all("h2"):
            if meta_key in x.get_text().lower():
                h2 = x
                break
        if not h2:
            continue

        # 이 h2 다음부터 다음 h2 전까지의 h3(체급) 순회
        for node in h2.find_all_next(["h2", "h3"]):
            if node.name == "h2":
                break
            wc_en = clean_text(node.get_text())
            if wc_en not in WEIGHT_KO:
                continue  # P4P 등 체급 아닌 건 건너뜀

            table = node.find_next("table")
            if not table:
                continue
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            headers_text = [clean_text(c.get_text()).lower() for c in rows[0].find_all(["th", "td"])]

            def col(*keys):
                for i, h in enumerate(headers_text):
                    for k in keys:
                        if k in h:
                            return i
                return -1

            rank_i = col("rank")
            fighter_i = col("fighter")
            record_i = col("record")
            iso_i = col("iso")
            if fighter_i < 0:
                continue

            champion = None
            ranked = []
            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= fighter_i:
                    continue
                rank_raw = clean_text(cells[rank_i].get_text()) if 0 <= rank_i < len(cells) else ""
                name = clean_text(cells[fighter_i].get_text())
                if not name or name.lower() in ("fighter", "opponent"):
                    continue
                record = clean_text(cells[record_i].get_text()) if 0 <= record_i < len(cells) else ""
                country = ""
                if 0 <= iso_i < len(cells):
                    img = cells[iso_i].find("img")
                    if img:
                        country = img.get("alt", "")

                entry = {
                    "name": name,
                    "name_ko": tr_fighter(name),
                    "record": record,
                    "country": country,
                    "country_ko": tr_country(country),
                }
                if rank_raw.upper() in ("C", "CHAMPION"):
                    if not champion:
                        champion = entry
                elif rank_raw.upper() == "IC":
                    entry["interim"] = True
                    ranked.append({**entry, "rank": "잠정챔프"})
                elif rank_raw.isdigit():
                    ranked.append({**entry, "rank": int(rank_raw)})

            divisions.append({
                "wc": WEIGHT_KO[wc_en],
                "wc_en": wc_en,
                "champion": champion,
                "ranked": ranked,
            })
            print(f"  ✓ {WEIGHT_KO[wc_en]}: 챔프 {'O' if champion else 'X'} / 랭커 {len(ranked)}명")

    return divisions


def fetch_recent_past_events(limit=6):
    """List of UFC events 의 'Past events' 표에서 최근 종료 이벤트 목록.
    표는 최신이 맨 위(내림차순)라 앞에서부터 limit 개만 취함."""
    print("\n→ Wikipedia: 최근 종료 이벤트(결과) 가져오는 중...")
    html = http_get(WIKI + "List_of_UFC_events")
    soup = BeautifulSoup(html, "html.parser")

    heading = None
    for h in soup.find_all(["h2", "h3"]):
        if "past events" in h.get_text().lower():
            heading = h
            break
    if not heading:
        print("  ⚠️  Past events 섹션 없음")
        return []

    table = heading.find_next("table")
    if not table:
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
        name_col = 1

    events = []
    for row in rows[1:]:
        if len(events) >= limit:
            break
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        try:
            name_cell = cells[name_col] if name_col < len(cells) else cells[1]
            link = name_cell.find("a")
            if link:
                name = clean_text(link.get_text())
                href = link.get("href", "")
                wiki_url = urljoin("https://en.wikipedia.org/", href) if href else None
            else:
                name = clean_text(name_cell.get_text())
                wiki_url = None
            if not name:
                continue

            date_cell = cells[date_col] if 0 <= date_col < len(cells) else None
            date_obj = parse_wiki_date(date_cell.get_text()) if date_cell else None
            if not date_obj or date_obj >= TODAY:
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

    print(f"  ✓ 최근 종료 {len(events)}개")
    return events


def build_fighters(upcoming, past, divisions):
    """랭킹 + 대진표 + 과거결과를 교차연결해 선수 디렉터리 구성 (추가 요청 없음)."""
    print("\n→ 선수 디렉터리 구성 중 (수집 데이터 교차연결)...")
    fighters = {}

    def ensure(name, division_ko=""):
        if not name:
            return None
        fid = slugify(name)
        if fid not in fighters:
            fighters[fid] = {
                "id": fid, "name": name, "name_ko": tr_fighter(name),
                "record": "", "country": "", "country_ko": "",
                "division": division_ko, "rank": "랭킹 외",
                "recent": [], "next": None,
            }
        elif division_ko and not fighters[fid]["division"]:
            fighters[fid]["division"] = division_ko
        return fid

    # 1. 랭킹 (전적/국적/랭크의 1차 출처)
    for d in divisions:
        if d.get("champion"):
            fid = ensure(d["champion"]["name"], d["wc"])
            f = fighters[fid]
            f["rank"] = "챔피언"
            f["record"] = d["champion"]["record"]
            f["country"] = d["champion"]["country"]
            f["country_ko"] = d["champion"]["country_ko"]
            f["division"] = d["wc"]
        for r in d.get("ranked", []):
            fid = ensure(r["name"], d["wc"])
            f = fighters[fid]
            if f["rank"] == "랭킹 외":
                f["rank"] = "잠정챔프" if r.get("interim") else ("#" + str(r["rank"]))
            if not f["record"]:
                f["record"] = r["record"]
            if not f["country"]:
                f["country"] = r["country"]
                f["country_ko"] = r["country_ko"]
            f["division"] = d["wc"]

    # 2. 대진표에 등장하는 선수 전원 등록
    for ev in upcoming + past:
        for fight in ev.get("main_card", []) + ev.get("prelims", []):
            wc = tr_weight(fight.get("weight", ""))
            ensure(fight["fighter_a"], wc)
            ensure(fight["fighter_b"], wc)

    # 3. 최근 전적 (과거 이벤트, 최신순으로 이미 정렬됨)
    for ev in past:
        ename = ev.get("name_ko") or ev.get("name")
        edate = ev.get("date_iso")
        for fight in ev.get("main_card", []) + ev.get("prelims", []):
            a, b = slugify(fight["fighter_a"]), slugify(fight["fighter_b"])
            w = fight.get("winner")
            method = fight.get("method", "")
            if a in fighters:
                fighters[a]["recent"].append({
                    "opp": fight["fighter_b"], "opp_ko": fight.get("fighter_b_ko"),
                    "result": "win" if w == "a" else ("loss" if w == "b" else "-"),
                    "method": method, "event": ename, "date": edate,
                })
            if b in fighters:
                fighters[b]["recent"].append({
                    "opp": fight["fighter_a"], "opp_ko": fight.get("fighter_a_ko"),
                    "result": "win" if w == "b" else ("loss" if w == "a" else "-"),
                    "method": method, "event": ename, "date": edate,
                })

    # 4. 다음 경기 (다가오는 이벤트)
    for ev in upcoming:
        ename = ev.get("name_ko") or ev.get("name")
        edate = ev.get("date_iso")
        for fight in ev.get("main_card", []) + ev.get("prelims", []):
            a, b = slugify(fight["fighter_a"]), slugify(fight["fighter_b"])
            if a in fighters and not fighters[a]["next"]:
                fighters[a]["next"] = {"opp": fight["fighter_b"], "opp_ko": fight.get("fighter_b_ko"), "event": ename, "date": edate}
            if b in fighters and not fighters[b]["next"]:
                fighters[b]["next"] = {"opp": fight["fighter_a"], "opp_ko": fight.get("fighter_a_ko"), "event": ename, "date": edate}

    for f in fighters.values():
        f["recent"] = f["recent"][:5]

    result = list(fighters.values())
    print(f"  ✓ 선수 {len(result)}명 (랭커 + 카드 등장 선수)")
    return result


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

    # ── 최근 종료 이벤트(결과) 수집 ──
    past = fetch_recent_past_events(limit=6)
    if past:
        print("\n-> Fetching past event results...")
        for i, ev in enumerate(past):
            print("  [" + str(i+1) + "/" + str(len(past)) + "]", ev["name"])
            past[i] = fetch_event_detail(ev)
            mc = len(past[i].get("main_card", []))
            wins = sum(1 for f in past[i].get("main_card", []) if f.get("winner"))
            print("      main card:", mc, "fights /", wins, "승자 확정")

    print("\n-> Applying translations (fighter/venue/location)...")
    for ev in events:
        apply_translations(ev)
    for ev in past:
        apply_translations(ev)

    output = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "Wikipedia: List of UFC events",
        "translations_applied": True,
        "event_count": len(events),
        "events": events,
        "past_count": len(past),
        "past_events": past,
    }
    EVENTS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== Saved:", EVENTS_FILE.name, "(upcoming " + str(len(events)) + " / past " + str(len(past)) + ") ===")

    # ── 랭킹 수집 ──
    divisions = []
    try:
        divisions = fetch_rankings()
        if divisions:
            rankings_out = {
                "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
                "source": "Wikipedia: UFC rankings (Meta)",
                "division_count": len(divisions),
                "divisions": divisions,
            }
            RANKINGS_FILE.write_text(json.dumps(rankings_out, ensure_ascii=False, indent=2), encoding="utf-8")
            print("=== Saved:", RANKINGS_FILE.name, "(" + str(len(divisions)) + " divisions) ===")
    except Exception as e:
        print("WARN rankings 수집 실패:", e)

    # ── 선수 디렉터리 구성 (events + rankings 교차연결) ──
    try:
        fighters = build_fighters(events, past, divisions)
        if fighters:
            fighters_out = {
                "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
                "source": "Wikipedia: 랭킹 + 대진표 교차연결",
                "fighter_count": len(fighters),
                "fighters": fighters,
            }
            FIGHTERS_FILE.write_text(json.dumps(fighters_out, ensure_ascii=False, indent=2), encoding="utf-8")
            print("=== Saved:", FIGHTERS_FILE.name, "(" + str(len(fighters)) + " fighters) ===")
    except Exception as e:
        print("WARN fighters 구성 실패:", e)


if __name__ == "__main__":
    main()
