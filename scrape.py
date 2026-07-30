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
import sys
import time
import unicodedata
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

# ────────────────────────────────────────────────────────────────
# 설정값
# ────────────────────────────────────────────────────────────────
WIKI = "https://en.wikipedia.org/wiki/"
UFC_ATHLETE = "https://www.ufc.com/athlete/"
UFC_SLUG_OVERRIDES = {
    "Robert Valentin": "robert-valentin-frey",
    "Billy Ray Goff": "billy-goff",
    "Wesley Schultz": "wes-schultz",
    "Regina Tarin": "regina-malpica-rivera",
    "Abusupiyan Magomedov": "abus-magomedov",
}
UFC_PHOTO_OVERRIDES = {
    # 프로필 페이지가 현재 soft-404지만 공식 이벤트 카드에 등록된 UFC 원본.
    "Carlos Diego Ferreira": {
        "avatar_url": "https://ufc.com/images/2025-01/FERREIRA_DIEGO_L_01-18.png",
        "avatar_thumb_url": "https://ufc.com/images/2025-01/FERREIRA_DIEGO_L_01-18.png",
        "avatar_source": "https://www.ufc.com/event/ufc-fight-night-august-08-2026",
        "avatar_provider": "UFC",
    },
}
KOREAN_UFC_FIGHTERS = [
    {
        "name": "HyunSung Park", "name_ko": "박현성", "division": "플라이급",
        "record": "10승 2패", "ufc_url": "https://www.ufc.com/athlete/hyunsung-park",
    },
    {
        "name": "SuYoung You", "name_ko": "유수영", "division": "밴텀급",
        "record": "16승 4패", "ufc_url": "https://www.ufc.com/athlete/suyoung-yu",
    },
    {
        "name": "ChangHo Lee", "name_ko": "이창호", "division": "밴텀급",
        "record": "11승 2패", "ufc_url": "https://www.ufc.com/athlete/chang-ho-lee",
    },
    {
        "name": "Dooho Choi", "name_ko": "최두호", "division": "페더급",
        "record": "17승 4패 1무", "ufc_url": "https://www.ufc.com/athlete/dooho-choi",
    },
    {
        "name": "JeongYeong Lee", "name_ko": "이정영", "division": "페더급",
        "record": "11승 3패", "ufc_url": "https://www.ufc.com/athlete/jeongyeong-lee",
    },
    {
        "name": "JooSang Yoo", "name_ko": "유주상", "division": "페더급",
        "record": "9승 1패", "ufc_url": "https://www.ufc.com/athlete/joo-sang-yoo",
    },
    {
        "name": "Seokhyeon Ko", "name_ko": "고석현", "division": "웰터급",
        "record": "13승 3패", "ufc_url": "https://www.ufc.com/athlete/seokhyeon-ko",
    },
    {
        "name": "JunYong Park", "name_ko": "박준용", "division": "미들급",
        "record": "19승 7패", "ufc_url": "https://www.ufc.com/athlete/jun-yong-park",
    },
    {
        "name": "YiSak Lee", "name_ko": "이이삭", "division": "미들급",
        "record": "8승 2패", "ufc_url": "https://www.ufc.com/athlete/yi-sak-lee",
    },
]
HEADERS = {
    "User-Agent": "FightOclockBot/2.0 (https://fightoclock.kr; nemarymasm@gmail.com)"
}
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()

DATA_DIR = Path(__file__).parent / "data"
EVENTS_FILE = DATA_DIR / "events.json"
RANKINGS_FILE = DATA_DIR / "rankings.json"
CHAMPIONS_FILE = DATA_DIR / "champions.json"
FIGHTERS_FILE = DATA_DIR / "fighters.json"
OPINIONS_FILE = DATA_DIR / "opinions.json"
INSIGHTS_FILE = DATA_DIR / "insights.json"
TRANSLATIONS_FILE = DATA_DIR / "translations.json"
FAN_TAGS_FILE = DATA_DIR / "fan_tags.json"
AVATAR_CACHE_DIR = DATA_DIR / "avatars" / "generated"

EVENT_START_OVERRIDES = {
    # UFC 공식 이벤트 페이지의 메인카드 시작 시각. 실제 메인이벤트 입장은 앞 경기 길이에 따라 변동.
    "UFC Fight Night: Medić vs. Rodriguez": "2026-08-01T17:00:00Z",
}
FIGHTER_RANK_OVERRIDES = {
    "Uroš Medić": "#14",
    "Daniel Rodriguez": "#15",
}
EVENT_CARD_ADDITIONS = {
    "UFC Fight Night: Medić vs. Rodriguez": [
        {"weight": "Bantamweight", "fighter_a": "Mark Vologdin", "fighter_b": "Josias Musasa"},
        {"weight": "Heavyweight", "fighter_a": "Jovan Leka", "fighter_b": "Max Gimenis"},
    ],
}

# 경기 결과만으로는 보이지 않는 맥락을 짧게 전달하는 편집 한줄평.
# 자동수집 결과 위에 덧씌우므로 다음 데이터 갱신에서도 유지된다.
RESULT_TALKING_POINTS = {
    ("Magomed Ankalaev", "Bogdan Guskov"): "5라운드까지 몰아붙인 안칼라예프, 마지막에 직접 끝냈다",
    ("Ramazan Temirov", "Steve Erceg"): "얼섹을 1라운드에 재운 테미로프의 한 방",
    ("Magomed Zaynukov", "Damian Rzepecki"): "세 장 모두 30-27, 자이누코프의 완승",
    ("Rizvan Kuniev", "Tyrell Fortune"): "니킥으로 흐름을 끝낸 쿠니예프",
    ("Abubakar Vagaev", "Saygid Izagakhmaev"): "세 장 모두 29-28, 바가예프가 접전을 가져갔다",
    ("Dricus du Plessis", "Kamaru Usman"): "39세 우스만의 5라운드 분전 · 뒤 플레시 괴물 카디오, 다음은 타이틀전?",
    ("Christian Leroy Duncan", "Jared Cannonier"): "노련한 캐노니어를 상대로 던컨이 3라운드 내내 우세",
    ("Chase Hooper", "Mitch Ramirez"): "후퍼가 1라운드부터 등을 잡고 초크로 마무리",
    ("Fatima Kline", "Tabatha Ricci"): "세 장 모두 30-27, 클라인의 깔끔한 셧아웃",
    ("Tommy McMillen", "Alberto Montes"): "맥밀런, 후반 압박으로 3라운드 TKO",
    ("Max Holloway", "Conor McGregor"): "부상으로 너무 일찍 끝난 라이벌전",
    ("Paddy Pimblett", "Benoît Saint Denis"): "52초 페루비안 넥타이, 핌블렛의 깜짝 서브미션",
    ("Mario Bautista", "Cory Sandhagen"): "접전이었지만 바우티스타가 세 장 모두 가져갔다",
    ("Brandon Royval", "Lone'er Kavanagh"): "3라운드 끝자락, 로이발이 결국 등을 잡았다",
    ("King Green", "Terrance McKinney"): "1라운드 종료 1초 전, 킹 그린의 극적인 TKO",
    ("Rafael Fiziev", "Manuel Torres"): "스피닝 휠킥 한 방, 2라운드 시작 15초 만에 끝",
    ("Sharabutdin Magomedov", "Michel Pereira"): "화려함보다 운영, 샤라가 29-28로 정리",
    ("Matheus Camilo", "Nazim Sadykhov"): "카밀루가 91초 만에 화력을 증명했다",
    ("Asu Almabayev", "Charles Johnson"): "보기 드문 설로예프 스트레치로 피니시",
    ("Ikram Aliskerov", "Brunno Ferreira"): "세 장 모두 30-27, 알리스케로프의 압도",
    ("Abusupiyan Magomedov", "Michał Oleksiejczuk"): "1라운드 길로틴, 마고메도프가 기회를 놓치지 않았다",
    ("Manel Kape", "Kyoji Horiguchi"): "두 라운드 접전 뒤, 캅이 3라운드 화력으로 끝냈다",
    ("Navajo Stirling", "Ion Cuțelaba"): "엘보와 펀치를 섞은 스털링의 2라운드 피니시",
    ("Christian Rodriguez", "Hyder Amil"): "로드리게스, 1라운드 길로틴으로 빠르게 정리",
    ("Murtazali Magomedov", "Melsik Baghdasaryan"): "트위스터까지 나왔다, 77초 희귀 서브미션",
    ("Vinicius Oliveira", "Andre Fili"): "올리베이라가 2라운드 막판 엘보로 마무리",
    ("Justin Gaethje", "Ilia Topuria"): "4라운드 코너 스톱, 게이치가 토푸리아의 연승을 끊었다",
    ("Ciryl Gane", "Alex Pereira"): "가네가 페레이라를 2라운드 펀치로 멈췄다",
    ("Sean O'Malley", "Aiemann Zahabi"): "오말리의 정확도가 2라운드에 승부를 갈랐다",
    ("Josh Hokit", "Derrick Lewis"): "호킷, 루이스의 한 방을 넘고 2라운드 TKO",
    ("Maurício Ruffy", "Michael Chandler"): "1라운드 스피닝 휠킥, 후피가 챈들러를 무너뜨렸다",
}

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
    "Myanmar": "미얀마", "Portugal": "포르투갈", "Iraq": "이라크",
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


def apply_result_talking_points(events):
    """최근 결과의 메인카드에 사람이 읽기 좋은 한줄평을 붙인다."""
    applied = 0
    for event in events:
        for fight in event.get("main_card", []):
            key = (fight.get("fighter_a", ""), fight.get("fighter_b", ""))
            note = RESULT_TALKING_POINTS.get(key)
            if note:
                fight["talking_point_ko"] = note
                applied += 1
    return applied


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


def ufc_slugify(name):
    """UFC 프로필 URL용 ASCII slug. 'Uroš Medić' → 'uros-medic'."""
    ascii_name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def fetch_ufc_profile_photos(name, source_override=None):
    """UFC 공식 선수 페이지의 전신 프로필과 헤드샷 URL을 추출한다."""
    if name in UFC_PHOTO_OVERRIDES:
        return dict(UFC_PHOTO_OVERRIDES[name])
    slug = UFC_SLUG_OVERRIDES.get(name) or ufc_slugify(name)
    if not slug:
        return {}
    source = source_override or (UFC_ATHLETE + slug)
    try:
        r = requests.get(source, headers=HEADERS, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        full = next(
            (
                img.get("src")
                for img in soup.find_all("img")
                if "athlete_bio_full_body" in (img.get("src") or "")
            ),
            "",
        )
        og = soup.find("meta", attrs={"property": "og:image"})
        thumb = og.get("content", "") if og else ""
        full = urljoin(r.url, full) if full else ""
        thumb = urljoin(r.url, thumb) if thumb else ""
        if re.search(r"silhouette|placeholder|default", full, re.IGNORECASE):
            full = ""
        if re.search(r"silhouette|placeholder|default", thumb, re.IGNORECASE):
            thumb = ""
        # 신인 프로필은 전신 컷 없이 og:image 헤드샷만 등록된 경우가 있다.
        # 그때도 빈 실루엣보다 공식 헤드샷을 우선한다.
        if not full.startswith("https://ufc.com/images/") and thumb.startswith("https://ufc.com/images/"):
            full = thumb
        if not full.startswith("https://ufc.com/images/"):
            return {}
        if not thumb.startswith("https://ufc.com/images/"):
            thumb = full
        return {
            "avatar_url": full,
            "avatar_thumb_url": thumb,
            "avatar_remote_url": full,
            "avatar_thumb_remote_url": thumb,
            "avatar_source": source,
            "avatar_provider": "UFC",
        }
    except Exception as e:
        print(f"  ⚠️ UFC 프로필 사진 실패 ({name}): {e}")
        return {}


def fetch_ufc_profile_history(name, source_override=None):
    """위키 프로 전적표가 없는 신인은 UFC 공식 프로필의 UFC History를 사용한다."""
    source = source_override or (UFC_ATHLETE + (UFC_SLUG_OVERRIDES.get(name) or ufc_slugify(name)))
    if not source:
        return {}
    try:
        response = requests.get(source, headers=HEADERS, timeout=25)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        paragraphs = soup.select(".field--name-qna-ufc p")
        if not paragraphs:
            return {}
        subject = unicodedata.normalize(
            "NFKD", (name or "").split()[-1]
        ).encode("ascii", "ignore").decode()
        subject = re.sub(r"[^\w'-]", "", subject)
        subject_re = re.escape(subject).replace(r"\'", "['’]")
        rounds = {
            "first": "1", "second": "2", "third": "3",
            "fourth": "4", "fifth": "5",
        }
        history = []
        for paragraph in paragraphs:
            event_tag = paragraph.find("strong")
            event = clean_text(event_tag.get_text()) if event_tag else ""
            text = clean_text(paragraph.get_text(" ", strip=True))
            match_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
            if not event or not re.search(r"\(\d{1,2}/\d{1,2}/\d{2,4}\)", match_text):
                continue
            lower = match_text.lower()
            result = ""
            opponent = ""
            patterns = []
            if "no contest" in lower or "fought to a draw" in lower:
                result = "nc" if "no contest" in lower else "draw"
                patterns = [
                    rf"{subject_re}\s+and\s+(.+?)\s+fought\s+to",
                    rf"between\s+{subject_re}\s+and\s+(.+?)\s+was",
                ]
            elif re.search(rf"{subject_re}\s+(?:was\s+)?(?:submitted|stopped|knocked out)", match_text, re.I):
                result = "loss"
                patterns = [
                    rf"{subject_re}\s+was\s+(?:submitted|stopped|knocked out)\s+by\s+(.+?)(?:\s+via|\s+at|\s+in\s+round|$)",
                ]
            elif re.search(rf"{subject_re}\s+lost\b", match_text, re.I):
                result = "loss"
                patterns = [
                    rf"{subject_re}\s+lost.*?\s+to\s+(.+?)(?:\s+via|\s+by|\s+at|\s+in\s+round|$)",
                ]
            else:
                result = "win"
                patterns = [
                    rf"{subject_re}\s+(?:submitted|stopped|knocked out|defeated)\s+(.+?)(?:\s+via|\s+at|\s+by|\s+in\s+round|$)",
                    rf"{subject_re}\s+won.*?(?:decision\s+)?over\s+(.+?)(?:\s+at|\s+in\s+round|$)",
                ]
            for pattern in patterns:
                match = re.search(pattern, match_text, re.I)
                if match:
                    opponent = clean_text(match.group(1)).strip(" .,-")
                    break
            if not opponent:
                continue

            if "no contest" in lower:
                method = "NC"
            elif "unanimous decision" in lower:
                method = "Decision (unanimous)"
            elif "split decision" in lower:
                method = "Decision (split)"
            elif "majority decision" in lower:
                method = "Decision (majority)"
            elif "decision" in lower:
                method = "Decision"
            elif "submitted" in lower or "submission" in lower:
                via = re.search(r"\bvia\s+(.+?)\s+at\s+", match_text, re.I)
                method = f"Submission ({clean_text(via.group(1))})" if via else "Submission"
            elif "knocked out" in lower:
                method = "KO"
            elif "stopped" in lower or "tko" in lower:
                method = "TKO"
            else:
                method = "UFC 공식 결과"
            date_match = re.search(r"\((\d{1,2}/\d{1,2}/\d{2,4})\)", match_text)
            round_match = re.search(r"\b(first|second|third|fourth|fifth)\s+round\b", lower)
            time_match = re.search(r"\bat\s+(\d{1,2}:\d{2})\b", match_text, re.I)
            history.append({
                "result": result,
                "record": "",
                "opp": opponent,
                "opp_ko": tr_fighter(opponent),
                "method": method,
                "event": tr_event_title(event),
                "date": date_match.group(1) if date_match else "",
                "round": rounds.get(round_match.group(1), "") if round_match else "",
                "time": time_match.group(1) if time_match else "",
            })
        return {
            "history": history,
            "history_source": source,
            "history_scope": "UFC 경기",
        } if history else {}
    except Exception as e:
        print(f"  ⚠️ UFC 공식 전적 실패 ({name}): {e}")
        return {}


def _download_image(url):
    response = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise ValueError(f"이미지 응답 아님: {content_type}")
    image = Image.open(BytesIO(response.content))
    image.load()
    if min(image.size) < 120:
        raise ValueError(f"프로필 사진 해상도 부족: {image.size}")
    return image


def cache_profile_photos(fighter_id, photos):
    """외부 이미지 리다이렉트/CSP에 영향받지 않도록 최적화한 WebP를 사이트 안에 저장한다."""
    remote_full = photos.get("avatar_remote_url") or photos.get("avatar_url") or ""
    remote_thumb = photos.get("avatar_thumb_remote_url") or photos.get("avatar_thumb_url") or remote_full
    if not remote_full.startswith("http"):
        return photos

    AVATAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^a-z0-9-]+", "-", fighter_id.lower()).strip("-")
    full_rel = f"/data/avatars/generated/{safe_id}-full.webp"
    thumb_rel = f"/data/avatars/generated/{safe_id}-thumb.webp"
    full_path = AVATAR_CACHE_DIR / f"{safe_id}-full.webp"
    thumb_path = AVATAR_CACHE_DIR / f"{safe_id}-thumb.webp"

    try:
        full = _download_image(remote_full)
        full.thumbnail((460, 700), Image.Resampling.LANCZOS)
        if full.mode not in ("RGB", "RGBA"):
            full = full.convert("RGBA")
        full.save(full_path, "WEBP", quality=84, method=6)

        try:
            thumb_source = _download_image(remote_thumb)
        except Exception:
            thumb_source = full.copy()
        if thumb_source.mode not in ("RGB", "RGBA"):
            thumb_source = thumb_source.convert("RGB")
        thumb = ImageOps.fit(
            thumb_source,
            (192, 192),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.28),
        )
        thumb.save(thumb_path, "WEBP", quality=82, method=6)
    except Exception as e:
        print(f"  ⚠️ UFC 사진 로컬 저장 실패 ({fighter_id}): {e}")
        if full_path.exists() and thumb_path.exists():
            localized = dict(photos)
            localized.update({
                "avatar_url": full_rel,
                "avatar_thumb_url": thumb_rel,
                "avatar_remote_url": remote_full,
                "avatar_thumb_remote_url": remote_thumb,
            })
            return localized
        return photos

    localized = dict(photos)
    localized.update({
        "avatar_url": full_rel,
        "avatar_thumb_url": thumb_rel,
        "avatar_remote_url": remote_full,
        "avatar_thumb_remote_url": remote_thumb,
    })
    return localized


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


def fetch_wikipedia_rankings():
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


def _fighter_name_key(name):
    ascii_name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_name.lower())


def fetch_rankings():
    """UFC 공식 일반 랭킹 11개 체급을 수집하고 위키 데이터로 전적·국적을 보강한다.

    UFC 페이지 뒤쪽에는 별도의 META 랭킹 11개가 반복되므로 체급별 첫 그룹만
    사용한다. 공식 페이지 장애 시에는 Wikipedia 랭킹으로 안전하게 폴백한다.
    """
    wiki_divisions = fetch_wikipedia_rankings()
    wiki_entries = {}
    for division in wiki_divisions:
        for entry in [division.get("champion")] + division.get("ranked", []):
            if entry:
                wiki_entries[_fighter_name_key(entry.get("name"))] = entry

    def official_name_ko(name, base):
        translated = tr_fighter(name)
        if re.search(r"[가-힣]", translated):
            return translated
        return base.get("name_ko") or translated

    print("\n→ UFC 공식 체급 랭킹 가져오는 중...")
    try:
        html = http_get("https://www.ufc.com/rankings")
        soup = BeautifulSoup(html, "html.parser")
        divisions = []
        seen = set()
        valid_weights = {
            re.sub(r"\s+", "", value): value
            for value in WEIGHT_KO.values()
        }
        for group in soup.select(".view-grouping"):
            header = group.select_one(".view-grouping-header")
            wc_raw = clean_text(header.get_text(" ", strip=True)) if header else ""
            wc = valid_weights.get(re.sub(r"\s+", "", wc_raw), "")
            if not wc or wc in seen:
                continue
            seen.add(wc)
            champion_link = group.select_one(
                ".rankings--athlete--champion h5 a, caption h5 a"
            )
            champion = None
            if champion_link:
                name = clean_text(champion_link.get_text())
                base = wiki_entries.get(_fighter_name_key(name), {})
                champion = {
                    "fighter_id": slugify(name),
                    "name": name,
                    "name_ko": official_name_ko(name, base),
                    "record": base.get("record", ""),
                    "country": base.get("country", ""),
                    "country_ko": base.get("country_ko", ""),
                    "url": base.get("url") or (WIKI + name.replace(" ", "_")),
                    "ufc_url": urljoin("https://www.ufc.com/", champion_link.get("href", "")),
                }

            ranked = []
            for row in group.select("tbody tr"):
                rank_cell = row.select_one(".views-field-weight-class-rank")
                fighter_link = row.select_one(".views-field-title a")
                rank_raw = clean_text(rank_cell.get_text()) if rank_cell else ""
                if not fighter_link or not rank_raw.isdigit():
                    continue
                name = clean_text(fighter_link.get_text())
                base = wiki_entries.get(_fighter_name_key(name), {})
                ranked.append({
                    "fighter_id": slugify(name),
                    "name": name,
                    "name_ko": official_name_ko(name, base),
                    "record": base.get("record", ""),
                    "country": base.get("country", ""),
                    "country_ko": base.get("country_ko", ""),
                    "url": base.get("url") or (WIKI + name.replace(" ", "_")),
                    "ufc_url": urljoin("https://www.ufc.com/", fighter_link.get("href", "")),
                    "rank": int(rank_raw),
                })
            if champion and ranked:
                divisions.append({
                    "wc": wc,
                    "wc_en": next((en for en, ko in WEIGHT_KO.items() if ko == wc), wc),
                    "champion": champion,
                    "ranked": ranked,
                })
                print(f"  ✓ {wc}: 챔피언 + 랭커 {len(ranked)}명")
        if len(divisions) >= 11:
            return divisions
        raise ValueError(f"공식 체급을 {len(divisions)}개만 찾음")
    except Exception as e:
        print(f"  ⚠️ UFC 공식 랭킹 실패, Wikipedia 폴백: {e}")
        return wiki_divisions


def fetch_champion_history():
    """UFC 정규 챔피언 계보와 재임별 방어 횟수를 수집한다.

    잠정 챔피언은 번호가 없는 별도 행이므로 제외한다. 같은 선수가 벨트를
    되찾은 경우에는 집권기를 합치지 않고 각각 남겨 실제 계보 순서를 보존한다.
    """
    print("\n→ Wikipedia: 체급별 UFC 챔피언 계보 가져오는 중...")
    source_url = WIKI + "List_of_UFC_champions"
    soup = BeautifulSoup(http_get(source_url), "html.parser")
    division_names = [
        "Heavyweight",
        "Light Heavyweight",
        "Middleweight",
        "Welterweight",
        "Lightweight",
        "Featherweight",
        "Bantamweight",
        "Flyweight",
        "Women's Bantamweight",
        "Women's Flyweight",
        "Women's Strawweight",
    ]
    raw_divisions = []
    wiki_titles = set()

    for division_en in division_names:
        heading = next(
            (
                h for h in soup.select("h3")
                if clean_text(h.get_text(" ", strip=True)) == division_en + " Championship"
            ),
            None,
        )
        if not heading:
            raise ValueError(f"{division_en} 챔피언 표를 찾지 못함")
        table = heading.find_next("table")
        reigns = []
        reign_numbers = {}
        for row in table.select("tr")[1:]:
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 5:
                continue
            number = clean_text(cells[0].get_text(" ", strip=True))
            if not number.isdigit():
                continue
            name_links = [
                link for link in cells[1].find_all("a")
                if clean_text(link.get_text(" ", strip=True))
            ]
            if not name_links:
                continue
            name_link = name_links[0]
            name = clean_text(name_link.get_text(" ", strip=True))
            wiki_title = clean_text(name_link.get("title") or name)
            wiki_titles.add(wiki_title)
            key = _fighter_name_key(name)
            reign_numbers[key] = reign_numbers.get(key, 0) + 1

            defense_text = clean_text(cells[-1].get_text(" ", strip=True))
            defense_numbers = [
                int(value)
                for value in re.findall(r"(\d+)\.\s*(?:def\.|drew)", defense_text, re.I)
            ]
            reigns.append({
                "order": int(number),
                "fighter_id": slugify(name),
                "name": name,
                "name_ko": tr_fighter(name),
                "wiki_title": wiki_title,
                "wiki_url": urljoin("https://en.wikipedia.org/", name_link.get("href", "")),
                "crowned_at": clean_text(cells[3].get_text(" ", strip=True)),
                "reign": reign_numbers[key],
                "defenses": max(defense_numbers, default=0),
            })

        if not reigns:
            raise ValueError(f"{division_en} 챔피언 행을 찾지 못함")
        total_reigns = {}
        for reign in reigns:
            key = _fighter_name_key(reign["name"])
            total_reigns[key] = total_reigns.get(key, 0) + 1
        for reign in reigns:
            reign["reign_total"] = total_reigns[_fighter_name_key(reign["name"])]
            reign["current"] = reign is reigns[-1]
        raw_divisions.append({
            "wc": WEIGHT_KO[division_en],
            "wc_en": division_en,
            "reigns": reigns,
        })

    portraits = {}
    title_list = sorted(wiki_titles)
    for offset in range(0, len(title_list), 45):
        chunk = title_list[offset:offset + 45]
        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "redirects": 1,
                "prop": "pageimages",
                "piprop": "thumbnail",
                "pithumbsize": 240,
                "titles": "|".join(chunk),
            },
            headers=HEADERS,
            timeout=25,
        )
        response.raise_for_status()
        for page in response.json().get("query", {}).get("pages", {}).values():
            thumbnail = page.get("thumbnail", {}).get("source")
            if thumbnail:
                portraits[_fighter_name_key(page.get("title"))] = thumbnail

    for division in raw_divisions:
        for reign in division["reigns"]:
            reign["portrait_url"] = portraits.get(_fighter_name_key(reign["wiki_title"]), "")
            reign.pop("wiki_title", None)
        record_reign = max(division["reigns"], key=lambda item: item["defenses"])
        division["record"] = {
            "label": "최다 연속 방어",
            "defenses": record_reign["defenses"],
            "fighter_id": record_reign["fighter_id"],
            "name": record_reign["name"],
            "name_ko": record_reign["name_ko"],
        }
        print(
            f"  ✓ {division['wc']}: {len(division['reigns'])}번의 집권 · "
            f"최다 {record_reign['defenses']}차 방어"
        )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "Wikipedia: List of UFC champions",
        "source_url": source_url,
        "division_count": len(raw_divisions),
        "divisions": raw_divisions,
    }


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
    """선수 개별 위키 페이지에서 신체·전적·피니시·프로 경기 기록 추출.
    반환 dict: nick, height, reach, stance, age, record, name_ko,
              avatar_url/avatar_source,
              history/history_source,
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

        # 프로 MMA 전적 표 전체를 저장한다. 같은 문서의 아마추어/복싱 전적 표와
        # 혼동하지 않도록 "Mixed martial arts record" 제목 바로 뒤 첫 표만 쓴다.
        record_heading = soup.find(id=re.compile(r"^Mixed_martial_arts_record$"))
        record_table = None
        if record_heading:
            for table in record_heading.find_all_next("table"):
                first_row = table.find("tr")
                headers = [
                    clean_text(cell.get_text()).lower().rstrip(".")
                    for cell in first_row.find_all(["th", "td"])
                ] if first_row else []
                if {"res", "record", "opponent", "method", "event", "date"}.issubset(set(headers)):
                    record_table = table
                    break
        if record_table:
            rows = record_table.find_all("tr")
            header_cells = rows[0].find_all(["th", "td"])
            headers = [clean_text(cell.get_text()).lower().rstrip(".") for cell in header_cells]

            def column(*names):
                return next((i for i, value in enumerate(headers) if value in names), -1)

            res_i = column("res", "result")
            record_i = column("record")
            opponent_i = column("opponent")
            method_i = column("method")
            event_i = column("event")
            date_i = column("date")
            round_i = column("round")
            time_i = column("time")
            history = []
            for row in rows[1:]:
                cells = row.find_all(["th", "td"])
                if opponent_i < 0 or len(cells) <= opponent_i:
                    continue
                result_raw = clean_text(cells[res_i].get_text()) if 0 <= res_i < len(cells) else ""
                opponent = clean_text(cells[opponent_i].get_text())
                if not opponent or result_raw.lower() not in ("win", "loss", "draw", "nc"):
                    continue
                result = {
                    "win": "win",
                    "loss": "loss",
                    "draw": "draw",
                    "nc": "nc",
                }[result_raw.lower()]
                event = clean_text(cells[event_i].get_text()) if 0 <= event_i < len(cells) else ""
                history.append({
                    "result": result,
                    "record": clean_text(cells[record_i].get_text()) if 0 <= record_i < len(cells) else "",
                    "opp": opponent,
                    "opp_ko": tr_fighter(opponent),
                    "method": clean_text(cells[method_i].get_text()) if 0 <= method_i < len(cells) else "",
                    "event": tr_event_title(event),
                    "date": clean_text(cells[date_i].get_text()) if 0 <= date_i < len(cells) else "",
                    "round": clean_text(cells[round_i].get_text()) if 0 <= round_i < len(cells) else "",
                    "time": clean_text(cells[time_i].get_text()) if 0 <= time_i < len(cells) else "",
                })
            if history:
                out["history"] = history
                out["history_source"] = url
    except Exception as e:
        print(f"  ⚠️  선수 상세 실패 ({url.split('/')[-1]}): {e}")
    return out


def build_fighters(upcoming, past, divisions, detail_limit=None):
    """랭킹 + 대진표 + 과거결과를 교차연결해 선수 디렉터리 구성.
    detail_limit: 개별 위키 인포박스를 긁을 최대 인원(None=전원). 우선순위=랭커>다음경기>기타."""
    print("\n→ 선수 디렉터리 구성 중 (수집 데이터 교차연결)...")
    fighters = {}
    previous = {}
    if FIGHTERS_FILE.exists():
        try:
            previous = {
                fighter["id"]: fighter
                for fighter in json.loads(FIGHTERS_FILE.read_text(encoding="utf-8")).get("fighters", [])
            }
        except Exception as e:
            print(f"  ⚠️ 기존 선수 데이터 재사용 실패: {e}")

    def ensure(name, division_ko="", url=None, ufc_url=None):
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
                "ufc_url": ufc_url,
                "recent": [], "next": None,
            }
            old = previous.get(fid, {})
            for key in (
                "avatar", "avatar_url", "avatar_thumb_url", "avatar_remote_url",
                "avatar_thumb_remote_url", "avatar_source", "avatar_provider",
                "history", "history_source", "history_scope",
                "korean_focus",
            ):
                if old.get(key):
                    fighters[fid][key] = old[key]
        else:
            if division_ko and not fighters[fid]["division"]:
                fighters[fid]["division"] = division_ko
            if url and not fighters[fid].get("url"):
                fighters[fid]["url"] = url
            if ufc_url and not fighters[fid].get("ufc_url"):
                fighters[fid]["ufc_url"] = ufc_url
        return fid

    # 1. 랭킹 (전적/국적/랭크의 1차 출처)
    for d in divisions:
        if d.get("champion"):
            fid = ensure(
                d["champion"]["name"], d["wc"], d["champion"].get("url"),
                d["champion"].get("ufc_url"),
            )
            f = fighters[fid]
            f["rank"] = "챔피언"
            f["record"] = d["champion"]["record"]
            f["country"] = d["champion"]["country"]
            f["country_ko"] = d["champion"]["country_ko"]
            f["division"] = d["wc"]
        for r in d.get("ranked", []):
            fid = ensure(r["name"], d["wc"], r.get("url"), r.get("ufc_url"))
            f = fighters[fid]
            if f["rank"] == "랭킹 외":
                f["rank"] = "잠정챔프" if r.get("interim") else ("#" + str(r["rank"]))
            if not f["record"]:
                f["record"] = r["record"]
            if not f["country"]:
                f["country"] = r["country"]
                f["country_ko"] = r["country_ko"]
            f["division"] = d["wc"]

    # 공식 랭킹 밖에 있는 한국 UFC 선수도 체급 화면에서 놓치지 않게 유지한다.
    for seed in KOREAN_UFC_FIGHTERS:
        fid = ensure(seed["name"], seed["division"], ufc_url=seed["ufc_url"])
        fighter = fighters[fid]
        fighter.update({
            "name_ko": seed["name_ko"],
            "record": seed["record"],
            "country": "South Korea",
            "country_ko": "대한민국",
            "division": seed["division"],
            "korean_focus": True,
        })

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

    for name, rank in FIGHTER_RANK_OVERRIDES.items():
        fid = slugify(name)
        if fid in fighters:
            fighters[fid]["rank"] = rank

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
        for k in ("history", "history_source", "history_scope"):
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

    # 다음 카드 선수는 UFC 공식 사진을 우선하고, 사진이 없는 랭커도 함께 보강한다.
    # 큰 화면용 전신 PNG와 작은 카드용 헤드샷을 분리해 저장한다.
    # 월요일에는 전 랭커를 UFC 프로필과 다시 대조해 경기 후 갱신된 공식 컷을 놓치지 않는다.
    weekly_ranked_photo_refresh = TODAY.weekday() == 0
    official_targets = [
        f for f in fighters.values()
        if (f.get("next") and f.get("avatar_provider") != "UFC")
        or (
            f.get("rank") != "랭킹 외"
            and (f.get("avatar_provider") != "UFC" or weekly_ranked_photo_refresh)
        )
        or (f.get("korean_focus") and f.get("avatar_provider") != "UFC")
        or (f.get("recent") and not (f.get("avatar_url") or f.get("avatar")))
    ]
    print(f"\n→ UFC 공식 선수 사진 수집: {len(official_targets)}명...")
    official_count = 0
    for i, f in enumerate(official_targets):
        photos = fetch_ufc_profile_photos(f["name"], f.get("ufc_url"))
        if photos:
            photos = cache_profile_photos(f["id"], photos)
            f.update(photos)
            official_count += 1
        if (i + 1) % 25 == 0:
            print(f"    {i+1}/{len(official_targets)}")
    print(f"  ✓ 공식 프로필 사진 {official_count}/{len(official_targets)}명")

    korean_history_count = 0
    for f in fighters.values():
        if not f.get("korean_focus") or f.get("history"):
            continue
        history = fetch_ufc_profile_history(f["name"], f.get("ufc_url"))
        if history.get("history"):
            f.update(history)
            korean_history_count += 1
    print(f"  ✓ 한국 선수 UFC 전적 {korean_history_count}명 보강")

    result = list(fighters.values())
    for f in result:
        f.pop("url", None)  # 내부용 링크는 출력에서 제거
        f.pop("ufc_url", None)
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


def _kst_clock_label(value):
    """UTC ISO 시각을 한국 팬이 읽는 날짜·오전/오후 시각으로 바꾼다."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(KST)
    except (TypeError, ValueError):
        return ""
    hour = dt.hour % 12 or 12
    minute = f" {dt.minute}분" if dt.minute else ""
    ampm = "오전" if dt.hour < 12 else "오후"
    return f"한국시간 {dt.month}월 {dt.day}일({WEEKDAY_KO[dt.weekday()]}) {ampm} {hour}시{minute}"


def _clock_only(dt):
    hour = dt.hour % 12 or 12
    ampm = "오전" if dt.hour < 12 else "오후"
    return f"{ampm} {hour}:{dt.minute:02d}"


def refresh_insights(events, opinions, now_iso):
    """주간 브리핑이 다음 대회로 넘어가도 빈 화면이 되지 않게 기본 인사이트를 만든다.

    사람이 편집한 문구·팬 투표·전체 메인카드 배당은 그대로 보존하고,
    공식 시각·대진 수·The Odds API 메인이벤트 값만 매일 새로 맞춘다.
    """
    try:
        current = json.loads(INSIGHTS_FILE.read_text(encoding="utf-8")) if INSIGHTS_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        current = {}
    previous = current.get("events", {})
    refreshed = {}
    for event in events:
        event_id = slugify(event.get("name", ""))
        entry = dict(previous.get(event_id, {}))
        fights = event.get("main_card", []) + event.get("prelims", [])
        schedule = dict(entry.get("schedule", {}))
        start_value = event.get("start_time_utc")
        start = None
        if start_value:
            try:
                start = datetime.fromisoformat(start_value.replace("Z", "+00:00")).astimezone(KST)
            except (TypeError, ValueError):
                start = None
        schedule.update({
            "label": _kst_clock_label(start_value) or "한국시간 확인 중",
            "main_card": _kst_clock_label(start_value) or "시작 시각 확인 중",
            "bout_count": len(fights),
            "source": event.get("time_source") or event.get("wiki_url") or "",
        })
        if start and event.get("main_card"):
            target = start + timedelta(minutes=10 + max(0, len(event["main_card"]) - 1) * 30 + 10)
            schedule.setdefault("main_event_estimate", _clock_only(target))
            schedule.setdefault(
                "main_event_window",
                f"{_clock_only(target - timedelta(minutes=10))}–{_clock_only(target + timedelta(minutes=20)).split(' ', 1)[1]}",
            )
            schedule.setdefault("estimate_note", "앞 경기에서 KO가 많이 나오면 더 빨라집니다.")
        entry["schedule"] = schedule

        prediction = event.get("prediction") or {}
        if prediction:
            market = dict(entry.get("market", {}))
            market.update({
                "fighter_a_pct": prediction.get("aPct"),
                "fighter_b_pct": prediction.get("bPct"),
                "label": prediction.get("note", "공개 배당을 승률로 환산한 값"),
                "as_of": prediction.get("as_of", now_iso),
                "source": prediction.get("source_url", ""),
            })
            if prediction.get("aOdds") and prediction.get("bOdds"):
                market["fighter_a_odds"] = prediction["aOdds"]
                market["fighter_b_odds"] = prediction["bOdds"]
            entry["market"] = market

        opinion = (opinions or {}).get(event_id)
        if opinion and not entry.get("fan_reactions"):
            quotes = [
                quote
                for community in opinion.get("communities", [])
                for quote in community.get("quotes", [])
            ]
            entry["fan_reactions"] = [
                {"label": quote.get("text_ko", ""), "text": "팬 댓글"}
                for quote in quotes[:3] if quote.get("text_ko")
            ]
            entry["fan_summary"] = "해외 MMA 커뮤니티에서 많이 공감한 댓글을 정리했습니다."

        main = (event.get("main_card") or [{}])[0]
        a_name = tr_fighter(main.get("fighter_a", ""))
        b_name = tr_fighter(main.get("fighter_b", ""))
        entry.setdefault("viewing_hook", f"{a_name} 대 {b_name}".strip())
        entry.setdefault("thesis", entry["viewing_hook"])
        refreshed[event_id] = entry

    output = {
        "schema_version": 1,
        "generated_at": now_iso,
        "source": "UFC 공식 일정 · The Odds API · 공개 MMA 커뮤니티",
        "events": refreshed,
    }
    INSIGHTS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"=== Saved: {INSIGHTS_FILE.name} ({len(refreshed)} events) ===")


def eventCodeFromName_py(name):
    m = re.match(r"^(UFC[^:]*?)(?::|$)", name or "", re.IGNORECASE)
    return m.group(1).strip() if m else "UFC"


def apply_official_event_overrides(events):
    """Wikipedia 반영이 늦는 공식 UFC 시작 시각·추가 대진을 보강한다."""
    for event in events:
        name = event.get("name", "")
        if name in EVENT_START_OVERRIDES:
            event["start_time_utc"] = EVENT_START_OVERRIDES[name]
            event["time_status"] = "confirmed_official"
            event["time_source"] = "https://www.ufc.com/event/ufc-fight-night-august-01-2026"
        additions = EVENT_CARD_ADDITIONS.get(name, [])
        if not additions:
            continue
        existing = {
            frozenset((fight.get("fighter_a"), fight.get("fighter_b")))
            for fight in event.get("main_card", []) + event.get("prelims", [])
        }
        event.setdefault("prelims", [])
        for fight in additions:
            pair = frozenset((fight["fighter_a"], fight["fighter_b"]))
            if pair not in existing:
                event["prelims"].append({
                    **fight,
                    "winner": None,
                    "method": "",
                    "round": "",
                    "time": "",
                })
                existing.add(pair)


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
                "aOdds": (
                    f"+{round(((1 / avg[na]) - 1) * 100)}"
                    if (1 / avg[na]) >= 2
                    else str(round(-100 / ((1 / avg[na]) - 1)))
                ),
                "bOdds": (
                    f"+{round(((1 / avg[nb]) - 1) * 100)}"
                    if (1 / avg[nb]) >= 2
                    else str(round(-100 / ((1 / avg[nb]) - 1)))
                ),
                "as_of": datetime.now(KST).isoformat(timespec="minutes"),
                "source_url": "https://the-odds-api.com/",
                "note": f"해외 북메이커 {nbk}곳의 배당을 승률로 환산한 값입니다. 시장(=베터들)이 보는 우세를 나타내는 여론 지표예요.",
                "sources": ["The Odds API", f"북메이커 {nbk}곳 평균"],
            }
            matched += 1
            break
    print(f"  ✓ {matched}경기 배당 매칭")


def refresh_fan_tag_review_queue(divisions, fighters, now_iso=None):
    """팬 밈을 자동 창작하지 않고, 재조사가 필요한 선수 대기열만 매일 갱신한다."""
    if not FAN_TAGS_FILE.exists():
        print("WARN fan_tags.json 없음 — 팬 별명 검수 대기열 생략")
        return

    data = json.loads(FAN_TAGS_FILE.read_text(encoding="utf-8"))
    tags = data.get("tags", {})
    fighter_by_id = {fighter.get("id"): fighter for fighter in fighters if fighter.get("id")}
    candidates = {}

    def add_candidate(entry, division, rank, is_champion=False):
        if not entry:
            return
        fighter_id = entry.get("fighter_id") or slugify(entry.get("name", ""))
        if not fighter_id:
            return
        candidate = candidates.setdefault(
            fighter_id,
            {
                "fighter_id": fighter_id,
                "name": entry.get("name_ko") or entry.get("name") or fighter_id,
                "division": division,
                "rank": "챔피언" if is_champion else f"#{rank}",
                "is_champion": is_champion,
                "rank_number": 0 if is_champion else int(rank or 99),
            },
        )
        fighter = fighter_by_id.get(fighter_id, {})
        candidate["has_next_fight"] = bool(fighter.get("next"))

    for division in divisions:
        add_candidate(division.get("champion"), division.get("wc", ""), 0, True)
        for index, entry in enumerate(division.get("ranked", []), start=1):
            add_candidate(
                entry,
                division.get("wc", ""),
                entry.get("rank") or index,
                False,
            )

    queue = []
    for fighter_id, candidate in candidates.items():
        tag = tags.get(fighter_id)
        due = False
        if tag and tag.get("review_after"):
            try:
                due = datetime.fromisoformat(tag["review_after"]).date() <= TODAY
            except ValueError:
                due = True

        if tag and tag.get("status") == "verified" and not due:
            continue

        if due:
            priority, reason = 0, "재검수 날짜가 지났습니다."
        elif candidate.get("has_next_fight"):
            priority, reason = 1, "다음 경기 전 팬 반응을 먼저 조사합니다."
        elif candidate.get("is_champion") or candidate.get("rank_number", 99) <= 5:
            priority, reason = 2, "챔피언·상위 랭커 우선 조사 대상입니다."
        else:
            priority, reason = 3, "반복 사용되는 국내 팬 표현을 아직 확인하지 못했습니다."

        queue.append(
            {
                "fighter_id": fighter_id,
                "name": candidate["name"],
                "division": candidate["division"],
                "rank": candidate["rank"],
                "reason": reason,
                "_priority": priority,
                "_rank_number": candidate.get("rank_number", 99),
            }
        )

    queue.sort(
        key=lambda item: (
            item["_priority"],
            item["_rank_number"],
            item["division"],
            item["name"],
        )
    )
    queue_limit = int(data.get("review_stats", {}).get("queue_limit", 40))
    published_queue = []
    for item in queue[:queue_limit]:
        item.pop("_priority", None)
        item.pop("_rank_number", None)
        published_queue.append(item)

    data["generated_at"] = now_iso or datetime.now(KST).isoformat(timespec="seconds")
    data["review_queue"] = published_queue
    data["review_stats"] = {
        "verified_count": sum(
            1 for tag in tags.values() if tag.get("status") == "verified"
        ),
        "waiting_count": len(queue),
        "queue_limit": queue_limit,
    }
    FAN_TAGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        "=== Saved:",
        FAN_TAGS_FILE.name,
        f"(검증 {data['review_stats']['verified_count']} / 재조사 {len(queue)}) ===",
    )


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

    apply_official_event_overrides(events)

    print("\n-> Filling missing Korean fighter names...")
    fill_missing_fighter_translations(client, events)

    print("\n-> Applying translations (fighter/venue/location)...")
    for ev in events:
        apply_translations(ev)
    for ev in past:
        apply_translations(ev)
    apply_result_talking_points(past)

    # ── 랭킹 수집 ──
    divisions = []
    try:
        divisions = fetch_rankings()
    except Exception as e:
        print("WARN rankings 수집 실패:", e)

    champion_history = None
    try:
        champion_history = fetch_champion_history()
    except Exception as e:
        print("WARN 챔피언 계보 수집 실패:", e)

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
            "source": "UFC 공식 체급 랭킹 (Wikipedia 전적·국적 보강)",
            "division_count": len(divisions),
            "divisions": divisions,
        }
        RANKINGS_FILE.write_text(json.dumps(rankings_out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("=== Saved:", RANKINGS_FILE.name, "(" + str(len(divisions)) + " divisions) ===")

    if champion_history:
        CHAMPIONS_FILE.write_text(
            json.dumps(champion_history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            "=== Saved:",
            CHAMPIONS_FILE.name,
            "(" + str(champion_history["division_count"]) + " divisions) ===",
        )

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

    # 팬 별명은 자동으로 쓰지 않는다. 대신 매일 다음 경기·상위 랭커 순으로
    # 리서치 대기열을 다시 계산해 오래된 밈이 방치되지 않게 한다.
    if divisions and fighters:
        refresh_fan_tag_review_queue(divisions, fighters, now_iso)

    # ── 국가별 커뮤니티 여론 (Reddit, 열쇠 있을 때만) ──
    opinions = None
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

    # 이번 주 이벤트가 바뀌어도 시간·배당·커뮤니티 브리핑 뼈대가 자동으로 따라간다.
    refresh_insights(events, opinions, now_iso)


def refresh_official_photos():
    """기존 fighters.json의 다음 경기 선수 사진만 빠르게 다시 수집한다."""
    data = json.loads(FIGHTERS_FILE.read_text(encoding="utf-8"))
    fighters = data.get("fighters", [])
    targets = [fighter for fighter in fighters if fighter.get("next")]
    print(f"→ UFC 공식 선수 사진 빠른 갱신: {len(targets)}명")
    success = 0
    for i, fighter in enumerate(targets):
        if fighter.get("avatar_provider") == "UFC" and (
            fighter.get("avatar_remote_url") or str(fighter.get("avatar_url", "")).startswith("http")
        ):
            photos = {
                "avatar_url": fighter.get("avatar_url", ""),
                "avatar_thumb_url": fighter.get("avatar_thumb_url", ""),
                "avatar_remote_url": fighter.get("avatar_remote_url") or fighter.get("avatar_url", ""),
                "avatar_thumb_remote_url": fighter.get("avatar_thumb_remote_url") or fighter.get("avatar_thumb_url", ""),
                "avatar_source": fighter.get("avatar_source", ""),
                "avatar_provider": "UFC",
            }
        else:
            photos = fetch_ufc_profile_photos(fighter["name"])
        if photos:
            photos = cache_profile_photos(fighter["id"], photos)
            fighter.update(photos)
            success += 1
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(targets)}")
    data["official_photos_refreshed_at"] = datetime.now(KST).isoformat(timespec="seconds")
    FIGHTERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ UFC 공식 프로필 {success}/{len(targets)}명 저장")


def refresh_result_photos():
    """최근 결과 메인카드 선수 사진을 최신 UFC 공식 프로필로 교체한다."""
    fighters_data = json.loads(FIGHTERS_FILE.read_text(encoding="utf-8"))
    events_data = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    fighters = fighters_data.get("fighters", [])
    by_name = {fighter.get("name"): fighter for fighter in fighters}
    result_names = {
        name
        for event in events_data.get("past_events", [])
        for fight in event.get("main_card", [])
        for name in (fight.get("fighter_a"), fight.get("fighter_b"))
        if name
    }
    targets = [
        by_name[name]
        for name in sorted(result_names)
        if name in by_name and by_name[name].get("avatar_provider") != "UFC"
    ]
    print(f"→ 최근 결과 선수 공식 사진 갱신: {len(targets)}명")
    success = 0
    for i, fighter in enumerate(targets):
        photos = fetch_ufc_profile_photos(fighter["name"])
        if photos:
            photos = cache_profile_photos(fighter["id"], photos)
            fighter.update(photos)
            success += 1
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(targets)} · 새 사진 {success}명")
    now_iso = datetime.now(KST).isoformat(timespec="seconds")
    fighters_data["generated_at"] = now_iso
    fighters_data["result_photos_refreshed_at"] = now_iso
    FIGHTERS_FILE.write_text(
        json.dumps(fighters_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ 최근 결과 공식 사진 {success}/{len(targets)}명 저장")


def refresh_result_talking_points():
    """현재 events.json에 편집 한줄평을 즉시 반영한다."""
    events_data = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    count = apply_result_talking_points(events_data.get("past_events", []))
    events_data["talking_points_refreshed_at"] = datetime.now(KST).isoformat(timespec="seconds")
    EVENTS_FILE.write_text(
        json.dumps(events_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ 최근 결과 한줄평 {count}경기 저장")


def refresh_named_profile_photos(names):
    """지정한 선수의 사진만 UFC 공식 프로필로 갱신한다."""
    fighters_data = json.loads(FIGHTERS_FILE.read_text(encoding="utf-8"))
    fighters = fighters_data.get("fighters", [])
    wanted = {_fighter_name_key(name) for name in names}
    targets = [
        fighter for fighter in fighters
        if _fighter_name_key(fighter.get("name")) in wanted
    ]
    success = 0
    for fighter in targets:
        photos = fetch_ufc_profile_photos(fighter["name"])
        if photos:
            fighter.update(cache_profile_photos(fighter["id"], photos))
            success += 1
    fighters_data["profile_photos_refreshed_at"] = datetime.now(KST).isoformat(timespec="seconds")
    FIGHTERS_FILE.write_text(
        json.dumps(fighters_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ 지정 선수 공식 사진 {success}/{len(targets)}명 저장")


def refresh_korean_roster():
    """공식 랭킹 밖 한국 UFC 선수의 프로필·사진·UFC 전적을 유지한다."""
    fighters_data = json.loads(FIGHTERS_FILE.read_text(encoding="utf-8"))
    fighters = fighters_data.get("fighters", [])
    by_key = {_fighter_name_key(fighter.get("name")): fighter for fighter in fighters}
    photos = histories = 0
    for seed in KOREAN_UFC_FIGHTERS:
        key = _fighter_name_key(seed["name"])
        fighter = by_key.get(key)
        if not fighter:
            fighter = {
                "id": slugify(seed["name"]),
                "name": seed["name"],
                "nick": "", "height": "", "reach": "", "stance": "", "age": "",
                "win_ko": 0, "win_sub": 0, "win_dec": 0,
                "recent": [], "next": None,
            }
            fighters.append(fighter)
            by_key[key] = fighter
        fighter.update({
            "name_ko": seed["name_ko"],
            "record": seed["record"],
            "country": "South Korea",
            "country_ko": "대한민국",
            "division": seed["division"],
            "korean_focus": True,
        })
        if fighter.get("rank") in (None, ""):
            fighter["rank"] = "랭킹 외"
        profile_photos = fetch_ufc_profile_photos(seed["name"], seed["ufc_url"])
        if profile_photos:
            fighter.update(cache_profile_photos(fighter["id"], profile_photos))
            photos += 1
        if not fighter.get("history"):
            history = fetch_ufc_profile_history(seed["name"], seed["ufc_url"])
            if history.get("history"):
                fighter.update(history)
                histories += 1
    now_iso = datetime.now(KST).isoformat(timespec="seconds")
    fighters_data["generated_at"] = now_iso
    fighters_data["fighter_count"] = len(fighters)
    fighters_data["korean_roster_refreshed_at"] = now_iso
    FIGHTERS_FILE.write_text(
        json.dumps(fighters_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ 한국 UFC 선수 {len(KOREAN_UFC_FIGHTERS)}명 · 사진 {photos}명 · 전적 {histories}명 저장")


def refresh_ranked_profiles():
    """현재 랭커의 전체 프로 전적과 누락 사진을 기존 JSON에 보강한다."""
    fighters_data = json.loads(FIGHTERS_FILE.read_text(encoding="utf-8"))
    rankings_data = json.loads(RANKINGS_FILE.read_text(encoding="utf-8"))
    fighters = fighters_data.get("fighters", [])
    by_key = {_fighter_name_key(fighter.get("name")): fighter for fighter in fighters}
    ranked_profiles = {}
    for division in rankings_data.get("divisions", []):
        entries = [division.get("champion")] + division.get("ranked", [])
        for entry in entries:
            if entry and entry.get("name"):
                ranked_profiles.setdefault(_fighter_name_key(entry["name"]), entry)

    targets = [
        (by_key[key], entry)
        for key, entry in ranked_profiles.items()
        if key in by_key
    ]
    print(f"→ 랭커 프로필 보강: {len(targets)}명")
    histories = photos = 0
    for i, (fighter, ranking_entry) in enumerate(targets):
        detail = (
            fetch_fighter_detail(ranking_entry.get("url"))
            if not fighter.get("history") and ranking_entry.get("url")
            else {}
        )
        for key in (
            "name_ko", "nick", "height", "reach", "stance", "age",
            "record", "win_ko", "win_sub", "win_dec",
        ):
            if detail.get(key) not in (None, ""):
                fighter[key] = detail[key]
        if detail.get("history"):
            fighter["history"] = detail["history"]
            fighter["history_source"] = detail["history_source"]
            fighter.pop("history_scope", None)
        if not fighter.get("history"):
            official_history = fetch_ufc_profile_history(
                fighter["name"],
                ranking_entry.get("ufc_url"),
            )
            if official_history.get("history"):
                fighter.update(official_history)
        if fighter.get("history"):
            histories += 1
        if fighter.get("avatar_provider") != "UFC":
            profile_photos = fetch_ufc_profile_photos(
                fighter["name"],
                ranking_entry.get("ufc_url"),
            )
            if not profile_photos and detail.get("avatar_url"):
                profile_photos = {
                    "avatar_url": detail["avatar_url"],
                    "avatar_thumb_url": detail["avatar_url"],
                    "avatar_source": detail.get("avatar_source", ranking_entry.get("url", "")),
                    "avatar_provider": "Wikimedia",
                }
            if profile_photos:
                if profile_photos.get("avatar_provider") == "UFC":
                    profile_photos = cache_profile_photos(fighter["id"], profile_photos)
                fighter.update(profile_photos)
                photos += 1
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(targets)} · 전체 전적 {histories}명 · 새 사진 {photos}명")

    now_iso = datetime.now(KST).isoformat(timespec="seconds")
    fighters_data["generated_at"] = now_iso
    fighters_data["ranked_profiles_refreshed_at"] = now_iso
    fighters_data["source"] = "Wikipedia 랭킹·프로 MMA 전적 + UFC 공식 프로필 사진"
    FIGHTERS_FILE.write_text(
        json.dumps(fighters_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ 전체 전적 {histories}/{len(targets)}명 · 새 사진 {photos}명 저장")


def refresh_rankings_and_profiles():
    """공식 UFC 랭킹을 저장하고 선수 디렉터리의 랭크를 같은 기준으로 맞춘다."""
    divisions = fetch_rankings()
    champion_history = fetch_champion_history()
    now_iso = datetime.now(KST).isoformat(timespec="seconds")
    rankings_data = {
        "schema_version": 1,
        "generated_at": now_iso,
        "source": "UFC 공식 체급 랭킹 (Wikipedia 전적·국적 보강)",
        "division_count": len(divisions),
        "divisions": divisions,
    }
    RANKINGS_FILE.write_text(
        json.dumps(rankings_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    CHAMPIONS_FILE.write_text(
        json.dumps(champion_history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fighters_data = json.loads(FIGHTERS_FILE.read_text(encoding="utf-8"))
    fighters = fighters_data.get("fighters", [])
    by_key = {_fighter_name_key(fighter.get("name")): fighter for fighter in fighters}
    for fighter in fighters:
        fighter["rank"] = "랭킹 외"
    for division in divisions:
        entries = [(division.get("champion"), "챔피언")]
        entries.extend((entry, "#" + str(entry["rank"])) for entry in division.get("ranked", []))
        for entry, rank in entries:
            if not entry:
                continue
            key = _fighter_name_key(entry.get("name"))
            fighter = by_key.get(key)
            if not fighter:
                fighter = {
                    "id": slugify(entry["name"]),
                    "name": entry["name"],
                    "name_ko": entry.get("name_ko") or tr_fighter(entry["name"]),
                    "record": entry.get("record", ""),
                    "country": entry.get("country", ""),
                    "country_ko": entry.get("country_ko", ""),
                    "division": division["wc"],
                    "rank": rank,
                    "nick": "", "height": "", "reach": "", "stance": "", "age": "",
                    "win_ko": 0, "win_sub": 0, "win_dec": 0,
                    "recent": [], "next": None,
                }
                fighters.append(fighter)
                by_key[key] = fighter
            entry["fighter_id"] = fighter["id"]
            if entry.get("name_ko") and re.search(r"[가-힣]", entry["name_ko"]):
                fighter["name_ko"] = entry["name_ko"]
            fighter["rank"] = rank
            fighter["division"] = division["wc"]
            for key_name in ("record", "country", "country_ko"):
                if entry.get(key_name) and not fighter.get(key_name):
                    fighter[key_name] = entry[key_name]
    for name, rank in FIGHTER_RANK_OVERRIDES.items():
        fighter = by_key.get(_fighter_name_key(name))
        if fighter:
            fighter["rank"] = rank
    fighters_data["generated_at"] = now_iso
    fighters_data["source"] = "UFC 공식 랭킹 + Wikipedia 프로 MMA 전적 + UFC 공식 프로필 사진"
    fighters_data["fighter_count"] = len(fighters)
    # 위의 이름 정규화로 연결한 실제 선수 id를 랭킹 JSON에도 반영한다.
    RANKINGS_FILE.write_text(
        json.dumps(rankings_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    FIGHTERS_FILE.write_text(
        json.dumps(fighters_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ UFC 공식 랭킹 {len(divisions)}체급 저장 · 선수 랭크 동기화")
    refresh_ranked_profiles()


def refresh_champion_history():
    history = fetch_champion_history()
    CHAMPIONS_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓ UFC 챔피언 계보 {history['division_count']}체급 저장")


def refresh_fan_tags_from_files():
    """전체 수집 없이 현재 랭킹 기준 팬 별명 검수 대기열만 다시 만든다."""
    rankings = json.loads(RANKINGS_FILE.read_text(encoding="utf-8"))
    fighters = json.loads(FIGHTERS_FILE.read_text(encoding="utf-8"))
    refresh_fan_tag_review_queue(
        rankings.get("divisions", []),
        fighters.get("fighters", []),
        datetime.now(KST).isoformat(timespec="seconds"),
    )


if __name__ == "__main__":
    if "--champions-only" in sys.argv:
        refresh_champion_history()
    elif "--rankings-only" in sys.argv:
        refresh_rankings_and_profiles()
    elif "--result-photos" in sys.argv:
        refresh_result_photos()
    elif "--result-notes" in sys.argv:
        refresh_result_talking_points()
    elif "--profile-photo" in sys.argv:
        flag_index = sys.argv.index("--profile-photo")
        refresh_named_profile_photos(sys.argv[flag_index + 1:])
    elif "--korean-roster" in sys.argv:
        refresh_korean_roster()
    elif "--ranked-profiles" in sys.argv:
        refresh_ranked_profiles()
    elif "--fan-tags-review" in sys.argv:
        refresh_fan_tags_from_files()
    elif "--photos-only" in sys.argv:
        refresh_official_photos()
    else:
        main()
