"""
fightoclock 데이터 자동수집 스크립트
=====================================

매일 GitHub Actions가 이 파일을 실행합니다.

하는 일:
1. 위키피디아 "List of UFC events" 페이지에서 다가오는 UFC 이벤트 추출
2. 각 이벤트의 개별 페이지에서 메인카드/방송정보 추출
3. Claude Haiku 가 영문 정보를 한국어로 요약
4. data/events.json 파일에 결과 저장

사용자가 직접 실행할 일은 없습니다 (자동화).
디버깅 시: python scrape.py 로 로컬에서도 실행 가능.
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
    "User-Agent": "FightOclockBot/1.0 (https://fightoclock.kr; nemarymasm@gmail.com)"
}
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()

DATA_DIR = Path(__file__).parent / "data"
EVENTS_FILE = DATA_DIR / "events.json"

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


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
    """위키피디아 날짜 문자열을 datetime.date 로 변환."""
    if not text:
        return None
    text = text.strip().split("\n")[0]
    text = re.sub(r"\[\d+\]", "", text).strip()  # 각주 마커 제거

    formats = [
        "%B %d, %Y",  # June 14, 2026
        "%b %d, %Y",  # Jun 14, 2026
        "%d %B %Y",   # 14 June 2026
        "%d %b %Y",   # 14 Jun 2026
        "%Y-%m-%d",   # 2026-06-14
    ]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def to_kst_human(date_iso):
    """2026-06-14 → 2026년 6월 14일(일)"""
    if not date_iso:
        return None
    try:
        d = datetime.fromisoformat(date_iso).date()
        return f"{d.year}년 {d.month}월 {d.day}일({WEEKDAY_KO[d.weekday()]})"
    except Exception:
        return None


def clean_text(s):
    """텍스트 정리: 각주 제거, 공백 정리."""
    if not s:
        return ""
    s = re.sub(r"\[\d+\]", "", s)  # 위키 각주
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ────────────────────────────────────────────────────────────────
# Wikipedia 파싱
# ────────────────────────────────────────────────────────────────
def fetch_upcoming_events():
    """List of UFC events 페이지에서 Scheduled events 표 가져오기."""
    print("→ Wikipedia: List of UFC events 가져오는 중...")
    html = http_get(WIKI + "List_of_UFC_events")
    soup = BeautifulSoup(html, "html.parser")

    # "Scheduled events" 헤딩 찾기
    heading = None
    for h in soup.find_all(["h2", "h3"]):
        text = h.get_text().lower()
        if "scheduled" in text:
            heading = h
            break

    if not heading:
        print("  ⚠️  Scheduled events 섹션을 찾지 못함")
        return []

    table = heading.find_next("table")
    if not table:
        print("  ⚠️  Scheduled events 테이블을 찾지 못함")
        return []

    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    # 컬럼 위치 찾기 (헤더 키워드 기반)
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
            # 이벤트 이름 + 위키 URL
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

            # 날짜
            date_cell = cells[date_col] if date_col < len(cells) else None
            date_obj = parse_wiki_date(date_cell.get_text()) if date_cell else None
            if not date_obj or date_obj < TODAY:
                continue

            # 장소
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
            continue

    events.sort(key=lambda e: e["date_iso"])
    print(f"  ✓ {len(events)}개 이벤트 발견")
    return events[:8]  # 다음 8개까지


def fetch_event_detail(event):
    """개별 이벤트 페이지에서 메인 카드, 방송 정보 추출."""
    if not event.get("wiki_url"):
        return event

    try:
        html = http_get(event["wiki_url"])
        soup = BeautifulSoup(html, "html.parser")

        # Infobox 에서 방송/시간 정보
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
                elif label in ("date",):
                    # 정확한 날짜 한 번 더 (이미 있으면 무시)
                    pass

        # 메인 카드 찾기 (h2/h3 'Main card' 다음 표)
        main_card = []
        for h in soup.find_all(["h2", "h3"]):
            heading_text = h.get_text().lower()
            if "main card" in heading_text:
                table = h.find_next("table")
                if table:
                    for row in table.find_all("tr")[1:]:
                        cells = row.find_all(["td", "th"])
                        if len(cells) >= 4:
                            weight = clean_text(cells[0].get_text())
                            fighter_a = clean_text(cells[1].get_text())
                            vs = clean_text(cells[2].get_text())
                            fighter_b = clean_text(cells[3].get_text())
                            # vs 칸은 'def.' 같은 텍스트가 있어서, 종료 매치 표시일 수도 있음.
                            # 다가오는 매치는 vs 칸이 'vs.' 일 것.
                            if fighter_a and fighter_b:
                                main_card.append({
                                    "weight": weight,
                                    "fighter_a": fighter_a,
                                    "fighter_b": fighter_b,
                                })
                break

        if main_card:
            event["main_card"] = main_card[:6]  # 최대 6경기

    except Exception as e:
        print(f"  ⚠️  '{event['name']}' 상세 정보 가져오기 실패: {e}")

    return event


# ────────────────────────────────────────────────────────────────
# Claude Haiku 한국어 요약
# ────────────────────────────────────────────────────────────────
def summarize_korean(client, event):
    """Claude Haiku 에게 한국어 요약 요청."""
    if not client:
        return None

    main_card_str = ""
    if event.get("main_card"):
        cards = event["main_card"][:3]
        main_card_str = "\n주요 경기:\n" + "\n".join(
            f"- {c['weight']}: {c['fighter_a']} vs {c['fighter_b']}" for c in cards
        )

    prompt = f"""다음 UFC 이벤트를 한국 팬들에게 친근한 톤으로 한국어로 요약해주세요.

이벤트: {event['name']}
날짜: {event['date_iso']}
장소: {event['venue']}, {event['location']}{main_card_str}

규칙:
- 3-4문장. 짧고 자연스럽게.
- 결과 예측이나 추측은 금지.
- 메인 이벤트의 의미나 관전 포인트를 한 줄 짚어주기.
- "~입니다" 톤. 너무 격식 차리지 말기.

요약:"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠️  Claude 요약 에러: {e}")
        return None


# ────────────────────────────────────────────────────────────────
# 메인 파이프라인
# ────────────────────────────────────────────────────────────────
def main():
    print(f"=== fightoclock 데이터 수집 시작: {datetime.now(KST).isoformat(timespec='seconds')} ===")
    DATA_DIR.mkdir(exist_ok=True)

    # Anthropic 클라이언트 (있으면)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = None
    if api_key and Anthropic:
        client = Anthropic(api_key=api_key)
        print("✓ Anthropic 클라이언트 준비")
    else:
        print("⚠️  ANTHROPIC_API_KEY 없음 — 한국어 요약 건너뜀")

    # 1. 다가오는 이벤트 목록
    events = fetch_upcoming_events()
    if not events:
        print("⚠️  이벤트가 없습니다. 기존 events.json 보존하고 종료.")
        return

    # 2. 각 이벤트 상세 정보
    print(f"\n→ 각 이벤트의 상세 정보 가져오는 중...")
    for i, ev in enumerate(events):
        print(f"  [{i+1}/{len(events)}] {ev['name']}")
        events[i] = fetch_event_detail(ev)

    # 3. 한국어 요약
    if client:
        print(f"\n→ Claude Haiku 한국어 요약 생성 중...")
        for i, ev in enumerate(events):
            summary = summarize_korean(client, ev)
            if summary:
                events[i]["summary_ko"] = summary
                print(f"  [{i+1}/{len(events)}] {ev['name'][:30]}... ✓")
            time.sleep(1)  # rate limit 보호

    # 4. 한국시간 형식 추가
    for ev in events:
        ev["date_kst_human"] = to_kst_human(ev["date_iso"])

    # 5. JSON 저장
    output = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "Wikipedia: List of UFC events",
        "event_count": len(events),
        "events": events,
    }
    EVENTS_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n=== 저장 완료: {EVENTS_FILE.name} ({len(events)}개 이벤트) ===")


if __name__ == "__main__":
    main()
