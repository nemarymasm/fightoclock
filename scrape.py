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
from urllib.parse import unquote, urljoin, urlparse

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
OPINIONS_FILE = DATA_DIR / "opinions.json"
TRANSLATIONS_FILE = DATA_DIR / "translations.json"

# 국가별 MMA 커뮤니티 (레딧 서브레딧 → 지역 매핑). 하나의 레딧 앱으로 전부 커버.
OPINION_COMMUNITIES = [
    {"flag": "🌐", "region": "글로벌", "subreddit": "MMA", "lang": "영어"},
    {"flag": "🇧🇷", "region": "브라질", "subreddit": "mmabr", "lang": "포르투갈어"},
]

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
    "Republic of Ireland": "아일랜드",
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


STANCE_KO = {
    "orthodox": "정통", "southpaw": "사우스포", "switch": "스위치",
    "open stance": "오픈 스탠스",
}


def tr_stance(s):
    if not s:
        return s
    return STANCE_KO.get(s.strip().lower(), s.strip())


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
    # 동아시아식 성-이름 순서처럼 영문 이벤트 타이틀의 토큰이 풀네임 끝과
    # 일치하지 않는 소수 사례.
    overrides = {"song": "송"}
    if s in overrides:
        return overrides[s]
    for en_name, kr_name in TRANSLATIONS.get("fighters", {}).items():
        normalized = en_name.lower().strip()
        en_parts = normalized.split()
        if (en_parts and en_parts[-1] == s) or normalized.endswith(" " + s):
            kr_parts = kr_name.split()
            return kr_parts[-1] if kr_parts else kr_name
    return None


def fill_missing_fighter_translations(client, events):
    """다가오는 카드의 미등록 선수명을 한글 음역해 이번 수집 결과에 보강한다.
    translations.json은 덮어쓰지 않고, 현재 실행의 메모리 사전에만 추가한다."""
    if not client:
        return
    names = sorted({
        name
        for event in events
        for fight in event.get("main_card", []) + event.get("prelims", [])
        for name in (fight.get("fighter_a"), fight.get("fighter_b"))
        if name and tr_fighter(name) == name
    })
    if not names:
        return
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4000,
            messages=[{
                "role": "user",
                "content": (
                    "아래 MMA 선수 이름을 한국 독자가 읽을 수 있도록 외래어 표기 관행에 맞춰 "
                    "한글로 음역하세요. 번역하거나 별명을 만들지 마세요. "
                    "반드시 입력 영문명을 키, 한글명만 값을 가진 JSON 객체 하나만 출력하세요.\n"
                    + json.dumps(names, ensure_ascii=False)
                ),
            }],
        )
        text = msg.content[0].text.strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        generated = json.loads(text)
        valid = {
            name: generated[name].strip()
            for name in names
            if isinstance(generated.get(name), str) and re.search(r"[가-힣]", generated[name])
        }
        TRANSLATIONS.setdefault("fighters", {}).update(valid)
        print(f"  ✓ 신규 선수 한글명 {len(valid)}/{len(names)}명 자동 보강")
    except Exception as e:
        print(f"  ⚠️ 신규 선수 한글명 자동 보강 실패: {e}")


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


def to_local_human(date_iso):
    """출처에 적힌 개최지 기준 날짜를 한국어로 표기한다.

    Wikipedia 목록에는 시작 시각과 타임존이 없으므로 KST로 오해될 이름을
    사용하지 않는다. start_time_utc는 별도 신뢰 가능한 소스가 생겼을 때만
    채운다.
    """
    if not date_iso:
        return None
    try:
        d = datetime.fromisoformat(date_iso).date()
        return f"{d.year}년 {d.month}월 {d.day}일({WEEKDAY_KO[d.weekday()]})"
    except Exception:
        return None


def cell_link(cell):
    """표 셀 안의 위키 링크(선수 개별 페이지) 절대 URL 반환. 없으면 None.
    붉은링크(존재하지 않는 문서)는 제외."""
    if not cell:
        return None
    a = cell.find("a")
    if not a:
        return None
    href = a.get("href", "")
    if not href or "redlink=1" in href or href.startswith("#"):
        return None
    if "/wiki/" not in href:
        return None
    return urljoin("https://en.wikipedia.org/", href)


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
            "fighter_a_url": cell_link(cells[1]),
            "fighter_b_url": cell_link(cells[3]),
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
        "규칙 (뉴스레터 '뉴닉' 말투로):\n"
        "- 3-4문장. 짧고 툭툭 끊어서, 친근하게.\n"
        "- '~예요/~거든요/~고요' 체. AI가 쓴 것 같은 딱딱한 설명 금지.\n"
        "- '근데/그래서' 같은 연결어로 대화하듯. 이모지·미사여구 남발 금지.\n"
        "- UFC 잘 모르는 사람도 알아듣게, 이 경기가 왜 볼만한지 한 줄 짚기.\n"
        "- 결과 예측·추측 금지. 선수 이름은 위 한국어 표기 그대로.\n\n"
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
    ev["date_local_human"] = to_local_human(ev["date_iso"])
    ev["start_time_utc"] = None
    ev["time_status"] = "date_only"
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
                    "url": cell_link(cells[fighter_i]),
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


def fetch_fighter_detail(url):
    """선수 개별 위키 페이지 인포박스에서 신체·전적·피니시 정보 추출.
    반환 dict: nick, height, reach, stance, age, record, name_ko,
              avatar_url/avatar_source,
              win_ko/win_sub/win_dec (승리 방식 분해, 없는 항목은 생략)."""
    out = {}
    if not url:
        return out
    try:
        html = http_get(url)
        soup = BeautifulSoup(html, "html.parser")
        infobox = soup.find("table", class_=lambda c: c and "infobox" in c)
        if not infobox:
            return out

        # 한국어 위키 문서가 있으면 문서 제목을 공식 한글 표기로 우선 사용한다.
        # (문서가 없는 선수는 translations.json의 수동 표기를 유지)
        ko_alt = soup.find(
            "link",
            attrs={"rel": lambda value: value and "alternate" in value, "hreflang": "ko"},
        ) or soup.find("a", attrs={"lang": "ko", "hreflang": "ko"})
        if ko_alt and ko_alt.get("href"):
            title = unquote(urlparse(ko_alt["href"]).path.rsplit("/", 1)[-1]).replace("_", " ")
            if title and re.search(r"[가-힣]", title):
                out["name_ko"] = title

        # 실제 인물 사진은 재사용 조건이 명확한 Wikimedia Commons 파일만 연결한다.
        # Wikipedia 자체의 비자유 파일(wikipedia/en)은 제외한다.
        photo = infobox.find("img")
        if photo:
            src = photo.get("src") or ""
            width = int(photo.get("width") or 0)
            height = int(photo.get("height") or 0)
            if src.startswith("//"):
                src = "https:" + src
            is_decorative = re.search(r"(?:flag_of_|medal_icon|logo|icon_)", src, re.IGNORECASE)
            if (
                "upload.wikimedia.org/wikipedia/commons/" in src
                and max(width, height) >= 120
                and not is_decorative
            ):
                source_link = photo.find_parent("a")
                source_href = source_link.get("href") if source_link else ""
                out["avatar_url"] = src
                out["avatar_source"] = (
                    urljoin("https://commons.wikimedia.org/wiki/", source_href)
                    if "File:" in unquote(source_href)
                    else url
                )

        wins = losses = None
        mode = None  # 'win' | 'loss' — 'By knockout' 등이 어느 섹션 소속인지 추적
        mma_section_seen = False
        in_mma_record = False
        for row in infobox.find_all("tr"):
            th = row.find("th")
            td = row.find("td")
            if not th:
                continue
            label = clean_text(th.get_text()).lower()
            val = clean_text(td.get_text()) if td else ""

            def as_int(s):
                m = re.match(r"\d+", s or "")
                return int(m.group(0)) if m else None

            if "mixed martial arts" in label and "record" in label:
                # 복싱·아마추어 전적이 함께 있는 인포박스에서도 프로 MMA만 사용한다.
                mma_section_seen = True
                in_mma_record = True
                mode = None
                wins = losses = None
                for key in ("win_ko", "win_sub", "win_dec"):
                    out.pop(key, None)
            elif label.endswith("record") and mma_section_seen:
                in_mma_record = False
                mode = None
            elif "height" in label and val:
                m = re.search(r"\(([\d.]+)\s*(cm|m)\)", val)
                if m:
                    num = float(m.group(1))
                    cm = num * 100 if m.group(2) == "m" else num  # 미터면 cm로 변환
                    out["height"] = str(int(round(cm))) + "cm"
            elif "reach" in label and val:
                m = re.search(r"\(([\d.]+)\s*(cm|m)\)", val)
                if m:
                    num = float(m.group(1))
                    cm = num * 100 if m.group(2) == "m" else num
                    out["reach"] = str(int(round(cm))) + "cm"
            elif "stance" in label and val:
                out["stance"] = tr_stance(val.split("[")[0].strip())
            elif "nickname" in label and val:
                nick = val.split("[")[0].strip().strip('"')
                if nick and nick.lower() not in ("none", "n/a"):
                    out["nick"] = nick
            elif "born" in label and val:
                m = re.search(r"age[\s\xa0]*(\d{2})", val)
                if m:
                    out["age"] = m.group(1)
            elif label in ("wins", "win") and (in_mma_record or not mma_section_seen):
                mode = "win"
                if val:
                    wins = as_int(val)
            elif label in ("losses", "loss") and (in_mma_record or not mma_section_seen):
                mode = "loss"
                if val:
                    losses = as_int(val)
            elif label.startswith("by ") and val and (in_mma_record or not mma_section_seen):
                # 승리 방식 분해 (Wins 섹션 소속일 때만)
                n = as_int(val)
                if mode == "win" and n is not None:
                    if "knockout" in label:
                        out["win_ko"] = n
                    elif "submission" in label:
                        out["win_sub"] = n
                    elif "decision" in label:
                        out["win_dec"] = n

        if wins is not None and losses is not None:
            out["record"] = f"{wins}승 {losses}패"
    except Exception as e:
        print(f"  ⚠️  선수 상세 실패 ({url.split('/')[-1]}): {e}")
    return out


def build_fighters(upcoming, past, divisions, detail_limit=None):
    """랭킹 + 대진표 + 과거결과를 교차연결해 선수 디렉터리 구성.
    detail_limit: 개별 위키 인포박스를 긁을 최대 인원(None=전원). 우선순위=랭커>다음경기>기타."""
    print("\n→ 선수 디렉터리 구성 중 (수집 데이터 교차연결)...")
    fighters = {}

    def ensure(name, division_ko="", url=None):
        if not name:
            return None
        fid = slugify(name)
        if fid not in fighters:
            fighters[fid] = {
                "id": fid, "name": name, "name_ko": tr_fighter(name),
                "record": "", "country": "", "country_ko": "",
                "division": division_ko, "rank": "랭킹 외",
                "nick": "", "height": "", "reach": "", "stance": "", "age": "",
                "win_ko": 0, "win_sub": 0, "win_dec": 0,
                "url": url,
                "recent": [], "next": None,
            }
        else:
            if division_ko and not fighters[fid]["division"]:
                fighters[fid]["division"] = division_ko
            if url and not fighters[fid].get("url"):
                fighters[fid]["url"] = url
        return fid

    # 1. 랭킹 (전적/국적/랭크의 1차 출처)
    for d in divisions:
        if d.get("champion"):
            fid = ensure(d["champion"]["name"], d["wc"], d["champion"].get("url"))
            f = fighters[fid]
            f["rank"] = "챔피언"
            f["record"] = d["champion"]["record"]
            f["country"] = d["champion"]["country"]
            f["country_ko"] = d["champion"]["country_ko"]
            f["division"] = d["wc"]
        for r in d.get("ranked", []):
            fid = ensure(r["name"], d["wc"], r.get("url"))
            f = fighters[fid]
            if f["rank"] == "랭킹 외":
                f["rank"] = "잠정챔프" if r.get("interim") else ("#" + str(r["rank"]))
            if not f["record"]:
                f["record"] = r["record"]
            if not f["country"]:
                f["country"] = r["country"]
                f["country_ko"] = r["country_ko"]
            f["division"] = d["wc"]

    # 2. 대진표에 등장하는 선수 전원 등록 (선수별 위키 링크 포함)
    for ev in upcoming + past:
        for fight in ev.get("main_card", []) + ev.get("prelims", []):
            wc = tr_weight(fight.get("weight", ""))
            ensure(fight["fighter_a"], wc, fight.get("fighter_a_url"))
            ensure(fight["fighter_b"], wc, fight.get("fighter_b_url"))

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

    # 생성한 아바타 이미지가 data/avatars/<id>.(png|jpg|webp) 에 있으면 경로 연결
    avatars_dir = DATA_DIR / "avatars"
    if avatars_dir.exists():
        for f in fighters.values():
            for ext in ("png", "jpg", "jpeg", "webp"):
                p = avatars_dir / (f["id"] + "." + ext)
                if p.exists():
                    f["avatar"] = "./data/avatars/" + p.name
                    break

    for f in fighters.values():
        f["recent"] = f["recent"][:5]

    # 5. 선수 상세정보 수집 (개별 위키 인포박스) — 랭커·다음경기 선수 우선
    def priority(f):
        if f["rank"] != "랭킹 외":
            return 0
        if f["next"]:
            return 1
        return 2

    targets = sorted((f for f in fighters.values() if f.get("url")), key=priority)
    limit = len(targets) if detail_limit is None else detail_limit
    fetch_list = targets[:limit]
    print(f"\n→ 선수 상세정보(전적·키·리치·스탠스·나이) 수집: {len(fetch_list)}명...")
    for i, f in enumerate(fetch_list):
        det = fetch_fighter_detail(f["url"])
        if f["name_ko"] == f["name"] and det.get("name_ko"):
            f["name_ko"] = det["name_ko"]
        for k in ("nick", "height", "reach", "stance", "age"):
            if det.get(k):
                f[k] = det[k]
        for k in ("avatar_url", "avatar_source"):
            if det.get(k):
                f[k] = det[k]
        for k in ("win_ko", "win_sub", "win_dec"):
            if det.get(k) is not None:
                f[k] = det[k]
        if det.get("record"):
            f["record"] = det["record"]  # 인포박스 전적이 더 정확 (NC 등 정리)
        if (i + 1) % 25 == 0:
            print(f"    {i+1}/{len(fetch_list)}")
    skipped = len(targets) - len(fetch_list)
    if skipped > 0:
        print(f"  ⚠️ {skipped}명은 상세 생략(cap) — 전적/랭크 기본정보는 유지")

    result = list(fighters.values())
    for f in result:
        f.pop("url", None)  # 내부용 링크는 출력에서 제거
    print(f"  ✓ 선수 {len(result)}명 (랭커 + 카드 등장 선수)")
    return result


# ────────────────────────────────────────────────────────────────
# 국가별 커뮤니티 여론 (Reddit)
# ────────────────────────────────────────────────────────────────
def get_reddit_token():
    """Reddit 앱-전용 OAuth(client_credentials). 환경변수 없으면 None."""
    cid = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not cid or not secret:
        return None
    try:
        r = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(cid, secret),
            data={"grant_type": "client_credentials"},
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        print("WARN Reddit 토큰 실패:", e)
        return None


def reddit_get(path, token, params=None):
    time.sleep(0.6)
    h = dict(HEADERS)
    h["Authorization"] = "bearer " + token
    r = requests.get("https://oauth.reddit.com" + path, headers=h, params=params or {}, timeout=25)
    r.raise_for_status()
    return r.json()


def find_event_thread(subreddit, query, token):
    """서브레딧에서 이벤트 관련 글 중 댓글 가장 많은 스레드 반환."""
    try:
        data = reddit_get(f"/r/{subreddit}/search", token,
                          {"q": query, "restrict_sr": 1, "sort": "relevance", "limit": 8, "t": "month"})
    except Exception as e:
        print(f"    r/{subreddit} 검색 실패: {e}")
        return None
    posts = data.get("data", {}).get("children", [])
    best = None
    for p in posts:
        pd = p.get("data", {})
        nc = pd.get("num_comments", 0)
        if best is None or nc > best.get("num_comments", 0):
            best = pd
    if not best or best.get("num_comments", 0) < 3:
        return None
    return {"id": best.get("id"), "permalink": "https://www.reddit.com" + best.get("permalink", ""),
            "num_comments": best.get("num_comments", 0), "title": best.get("title", "")}


def get_top_comments(subreddit, thread_id, token, limit=15):
    """스레드 상위 댓글 본문+점수 목록."""
    try:
        data = reddit_get(f"/r/{subreddit}/comments/{thread_id}", token, {"sort": "top", "limit": 30})
    except Exception as e:
        print(f"    댓글 수집 실패: {e}")
        return []
    if not isinstance(data, list) or len(data) < 2:
        return []
    out = []
    for c in data[1].get("data", {}).get("children", []):
        cd = c.get("data", {})
        body = (cd.get("body") or "").strip()
        author = cd.get("author", "")
        if not body or body in ("[deleted]", "[removed]"):
            continue
        if author.lower() == "automoderator" or cd.get("stickied"):
            continue
        if len(body) > 500:
            body = body[:500]
        out.append({"body": body, "score": cd.get("score", 0)})
        if len(out) >= limit:
            break
    return out


def summarize_opinions_ko(client, event_name, region, lang, comments):
    """커뮤니티 댓글을 Claude로 한국어 요약 + 감정 + 대표댓글 번역. 실패 시 None."""
    if not client or not comments:
        return None
    joined = "\n".join(f"[{c['score']}] {c['body']}" for c in comments[:15])
    prompt = (
        f"다음은 '{event_name}' UFC 이벤트에 대한 {region} MMA 커뮤니티({lang}) 댓글입니다.\n"
        "[숫자]는 추천수입니다.\n\n" + joined + "\n\n"
        "이 커뮤니티의 여론을 한국 팬에게 전달하려 합니다. 아래 JSON만 출력하세요:\n"
        "{\n"
        '  "sentiment": "한 단어 (예: 기대, 회의적, 양분, 무관심)",\n'
        '  "summary_ko": "이 커뮤니티 전반의 분위기·주요 논점 2~3문장. 댓글에 없는 내용 지어내지 말 것.",\n'
        '  "quotes": [{"text_ko": "대표 댓글 한국어 번역", "score": 추천수}]  // 2~3개\n'
        "}\n"
        "반드시 위 JSON 형식만, 다른 말 없이 출력."
    )
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        obj = json.loads(text)
        if obj.get("summary_ko"):
            return obj
    except Exception as e:
        print(f"    여론 요약 실패: {e}")
    return None


def fetch_opinions(client, events):
    """다가오는 이벤트별 국가별 커뮤니티 여론 수집. 레딧 토큰 없으면 None(수집 생략)."""
    token = get_reddit_token()
    if not token:
        print("\nWARN REDDIT_CLIENT_ID/SECRET 없음 — 여론 수집 생략 (opinions.json 미갱신)")
        return None
    if not client:
        print("\nWARN Claude 없음 — 여론 번역 불가, 수집 생략")
        return None

    print("\n→ 국가별 커뮤니티 여론 수집 중...")
    result = {}
    for ev in events:
        code = eventCodeFromName_py(ev["name"])
        query = code if code != "UFC" else ev["name"][:30]
        communities = []
        for comm in OPINION_COMMUNITIES:
            thread = find_event_thread(comm["subreddit"], query, token)
            if not thread:
                continue
            comments = get_top_comments(comm["subreddit"], thread["id"], token)
            if not comments:
                continue
            summary = summarize_opinions_ko(client, ev["name"], comm["region"], comm["lang"], comments)
            if not summary:
                continue
            communities.append({
                "flag": comm["flag"],
                "region": comm["region"],
                "community": "r/" + comm["subreddit"],
                "thread_url": thread["permalink"],
                "sentiment": summary.get("sentiment", ""),
                "summary_ko": summary.get("summary_ko", ""),
                "quotes": summary.get("quotes", [])[:3],
            })
        if communities:
            result[slugify(ev["name"])] = {
                "event_name_ko": ev.get("name_ko") or ev["name"],
                "communities": communities,
            }
            print(f"  ✓ {ev['name'][:36]}: {len(communities)}개 커뮤니티")
    return result


def eventCodeFromName_py(name):
    m = re.match(r"^(UFC[^:]*?)(?::|$)", name or "", re.IGNORECASE)
    return m.group(1).strip() if m else "UFC"


# ────────────────────────────────────────────────────────────────
# 도박사 배당 (여론 지표) — The Odds API
# ────────────────────────────────────────────────────────────────
def _surname(name):
    parts = (name or "").strip().split()
    return parts[-1].lower() if parts else ""


def fetch_odds(events):
    """The Odds API로 다가오는 이벤트 메인경기의 배당→승률% 변환.
    ODDS_API_KEY 없으면 생략. 각 이벤트 prediction 필드에 저장(기존 UI 재사용)."""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        print("\nWARN ODDS_API_KEY 없음 — 배당(여론지표) 수집 생략")
        return
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/mma_mixed_martial_arts/odds",
            params={"apiKey": key, "regions": "us", "markets": "h2h", "oddsFormat": "decimal"},
            headers=HEADERS, timeout=25,
        )
        r.raise_for_status()
        odds_events = r.json()
    except Exception as e:
        print("WARN 배당 수집 실패:", e)
        return

    print(f"\n→ 배당(여론지표) 수집: {len(odds_events)}경기 매칭 중...")
    matched = 0
    for ev in events:
        mc = ev.get("main_card") or []
        if not mc:
            continue
        fa, fb = mc[0]["fighter_a"], mc[0]["fighter_b"]
        sa, sb = _surname(fa), _surname(fb)

        for oe in odds_events:
            names = [oe.get("home_team", ""), oe.get("away_team", "")]
            low = [n.lower() for n in names]
            if not (any(sa in n for n in low) and any(sb in n for n in low)):
                continue
            # 여러 북메이커 배당 평균 → 내재확률 → 정규화(마진 제거)
            probs = {}  # name → [implied prob...]
            for bk in oe.get("bookmakers", []):
                for mk in bk.get("markets", []):
                    if mk.get("key") != "h2h":
                        continue
                    for oc in mk.get("outcomes", []):
                        price = oc.get("price")
                        if price and price > 1:
                            probs.setdefault(oc["name"], []).append(1.0 / price)
            if len(probs) < 2:
                continue
            avg = {n: sum(v) / len(v) for n, v in probs.items()}
            # fa/fb에 해당하는 이름 매칭
            def pick(surname):
                for n in avg:
                    if surname in n.lower():
                        return n
                return None
            na, nb = pick(sa), pick(sb)
            if not na or not nb or na == nb:
                continue
            total = avg[na] + avg[nb]
            aPct = round(avg[na] / total * 100)
            bPct = 100 - aPct
            nbk = len(oe.get("bookmakers", []))
            ev["prediction"] = {
                "aId": slugify(fa), "bId": slugify(fb),
                "aPct": aPct, "bPct": bPct,
                "note": f"해외 북메이커 {nbk}곳의 배당을 승률로 환산한 값입니다. 시장(=베터들)이 보는 우세를 나타내는 여론 지표예요.",
                "sources": ["The Odds API", f"북메이커 {nbk}곳 평균"],
            }
            matched += 1
            break
    print(f"  ✓ {matched}경기 배당 매칭")


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

    print("\n-> Filling missing Korean fighter names...")
    fill_missing_fighter_translations(client, events)

    print("\n-> Applying translations (fighter/venue/location)...")
    for ev in events:
        apply_translations(ev)
    for ev in past:
        apply_translations(ev)

    # ── 랭킹 수집 ──
    divisions = []
    try:
        divisions = fetch_rankings()
    except Exception as e:
        print("WARN rankings 수집 실패:", e)

    # ── 선수 디렉터리 구성 (events + rankings 교차연결, 개별 상세 수집) ──
    fighters = []
    try:
        fighters = build_fighters(events, past, divisions)
    except Exception as e:
        print("WARN fighters 구성 실패:", e)

    # ── 도박사 배당(여론지표) 수집 → 이벤트 prediction 필드 ──
    try:
        fetch_odds(events)
    except Exception as e:
        print("WARN 배당 수집 실패:", e)

    # 내부용 선수 링크는 events.json 출력에서 제거 (build_fighters 가 다 쓴 뒤)
    for ev in events + past:
        for fight in ev.get("main_card", []) + ev.get("prelims", []):
            fight.pop("fighter_a_url", None)
            fight.pop("fighter_b_url", None)

    now_iso = datetime.now(KST).isoformat(timespec="seconds")

    # ── 저장 ──
    events_out = {
        "schema_version": 2,
        "generated_at": now_iso,
        "source": "Wikipedia: List of UFC events",
        "translations_applied": True,
        "event_count": len(events),
        "events": events,
        "past_count": len(past),
        "past_events": past,
    }
    EVENTS_FILE.write_text(json.dumps(events_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== Saved:", EVENTS_FILE.name, "(upcoming " + str(len(events)) + " / past " + str(len(past)) + ") ===")

    if divisions:
        rankings_out = {
            "schema_version": 1,
            "generated_at": now_iso,
            "source": "Wikipedia: UFC rankings (Meta)",
            "division_count": len(divisions),
            "divisions": divisions,
        }
        RANKINGS_FILE.write_text(json.dumps(rankings_out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("=== Saved:", RANKINGS_FILE.name, "(" + str(len(divisions)) + " divisions) ===")

    if fighters:
        fighters_out = {
            "schema_version": 1,
            "generated_at": now_iso,
            "source": "Wikipedia: 랭킹 + 대진표 + 개별 인포박스",
            "fighter_count": len(fighters),
            "fighters": fighters,
        }
        FIGHTERS_FILE.write_text(json.dumps(fighters_out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("=== Saved:", FIGHTERS_FILE.name, "(" + str(len(fighters)) + " fighters) ===")

    # ── 국가별 커뮤니티 여론 (Reddit, 열쇠 있을 때만) ──
    try:
        opinions = fetch_opinions(client, events)
        if opinions:
            opinions_out = {
                "schema_version": 1,
                "generated_at": now_iso,
                "source": "Reddit 커뮤니티 (" + ", ".join("r/" + c["subreddit"] for c in OPINION_COMMUNITIES) + ")",
                "event_count": len(opinions),
                "events": opinions,
            }
            OPINIONS_FILE.write_text(json.dumps(opinions_out, ensure_ascii=False, indent=2), encoding="utf-8")
            print("=== Saved:", OPINIONS_FILE.name, "(" + str(len(opinions)) + " events) ===")
    except Exception as e:
        print("WARN opinions 수집 실패:", e)


if __name__ == "__main__":
    main()
