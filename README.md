# FightOclock

한국 UFC 팬을 위한 일정·결과·랭킹·선수 가이드입니다.

- 서비스: <https://fightoclock.kr>
- 배포: Vercel 정적 사이트
- 데이터: Wikipedia 기반 Python 수집기
- 갱신: GitHub Actions, 매일 오전 6시 KST
- 선수 사진: 재사용 가능한 Wikimedia Commons 이미지만 자동 연결
- 선수명: 한글명을 기본 표기하고 원문명은 보조 표기

## 로컬 실행

```bash
python -m http.server 8000
```

브라우저에서 <http://localhost:8000>을 엽니다. `file://`로 열면 JSON 로딩이 차단될 수 있습니다.

## 데이터 갱신

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python scrape.py
python -m unittest discover -s tests -v
```

선택 환경변수:

- `ANTHROPIC_API_KEY`: 이벤트 한국어 요약과 새로 등장한 선수의 한글 음역 보강
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`: 커뮤니티 여론
- `ODDS_API_KEY`: 배당 기반 여론 지표

선택 기능에 필요한 키가 없으면 해당 파일이나 필드만 생략됩니다. 워크플로는 선택 파일이 없어도 실패하지 않습니다.

사진이 없는 선수는 가짜 인물 이미지나 실루엣 대신 이름 이니셜 카드로 표시합니다. 직접 사진을 추가할 때는
`data/avatars/README.md`의 파일명·저작권 지침을 따릅니다.

## 시간 데이터 원칙

Wikipedia 이벤트 목록은 날짜만 제공하고 시작 시각·타임존은 제공하지 않습니다.

- `date_iso`: 개최지 기준 날짜
- `date_local_human`: 개최지 날짜의 한국어 표기
- `start_time_utc`: 신뢰 가능한 출처에서 확인된 경우에만 ISO 8601 UTC 시각
- `time_status`: `date_only` 또는 향후 `confirmed`

`start_time_utc`가 없을 때 프런트엔드는 임의의 한국시간이나 카운트다운을 만들지 않습니다.

## 운영 점검

1. GitHub Actions의 `데이터 자동수집` 실행이 성공했는지 확인합니다.
2. `data/events.json`의 `generated_at`이 72시간 이내인지 확인합니다.
3. 루트 URL과 `#schedule`, `#rankings`, `#fighters`를 모바일·데스크톱에서 확인합니다.
4. 테스트가 실패하면 데이터 커밋과 Vercel 배포를 중단합니다.

## 구조

```text
index.html                  정적 SPA
scrape.py                   Wikipedia 데이터 수집·정규화
data/*.json                 배포되는 읽기 전용 데이터
.github/workflows/scrape.yml  정기 수집·검증·커밋
tests/test_site.py          데이터·프런트 계약 테스트
vercel.json                 보안·캐시 헤더
```
