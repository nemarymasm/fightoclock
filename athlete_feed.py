"""Validated official ranking snapshots and independently dated athlete records."""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

RANKING_URL = "https://www.ufc.com/rankings"


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def parse_rankings(html, weights):
    soup = BeautifulSoup(html, "html.parser")
    labels = {re.sub(r"\s+", "", key.lower()): value for key, value in {**weights, **{ko: ko for ko in weights.values()}}.items()}
    result = {}
    for mode, selector in (("media", ".view-display-id-block_1"), ("meta", ".view-display-id-meta_rankings")):
        container = soup.select_one(selector)
        if not container:
            raise ValueError(f"Missing {mode} rankings container")
        divisions = []
        seen = set()
        for group in container.select('.view-grouping'):
            label = re.sub(r"\s+", "", text(group.select_one('.view-grouping-header')).lower())
            wc = labels.get(label)
            if not wc or wc in seen:
                continue
            seen.add(wc)
            champion_link = group.select_one('caption h5 a')
            def entry(link):
                name = text(link)
                return {"name": name, "ufc_url": urljoin(RANKING_URL, link.get('href', ''))}
            champion = entry(champion_link) if champion_link else None
            ranked = []
            for row in group.select('tbody tr'):
                rank = text(row.select_one('.views-field-meta-weight-class-rank' if mode == 'meta' else '.views-field-weight-class-rank'))
                link = row.select_one('.views-field-title a')
                if rank.isdigit() and link:
                    ranked.append({**entry(link), "rank": int(rank)})
            if not ranked or len({e['ufc_url'] for e in ranked}) != len(ranked):
                raise ValueError(f"Invalid {mode}/{wc} ranking rows")
            if not all(1 <= e['rank'] <= 15 for e in ranked):
                raise ValueError(f"Invalid {mode}/{wc} rank")
            divisions.append({"wc": wc, "wc_en": next((en for en, ko in weights.items() if ko == wc), wc), "champion": champion, "ranked": ranked})
        if len(divisions) != 11:
            raise ValueError(f"Incomplete {mode} rankings: {len(divisions)}/11 divisions")
        footer = soup.select_one(f'[data-rankings-footer="{mode}"].list-denotions__updated')
        result[mode] = {"divisions": divisions, "source_updated_label": text(footer)}
    return result


def parse_profile(html, source):
    soup = BeautifulSoup(html, 'html.parser')
    name = text(soup.select_one('h1.hero-profile__name'))
    record = re.search(r'(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*\(W-L-D\)', text(soup.select_one('.hero-profile__division-body')))
    if not name or not record or not soup.select_one('.c-bio__info'):
        raise ValueError('Not a complete UFC athlete profile')
    wins, losses, draws = map(int, record.groups())
    output = {"official_name": name, "record": f"{wins}승 {losses}패" + (f" {draws}무" if draws else ''), "record_wld": [wins, losses, draws], "record_source": source, "profile_source": source}
    for field in soup.select('.c-bio__field'):
        label, value = text(field.select_one('.c-bio__label')).lower(), text(field.select_one('.c-bio__text'))
        number = re.fullmatch(r'\d+(?:\.\d+)?', value)
        if label in ('age', '나이') and number and 16 <= float(value) <= 80:
            output['age'] = str(int(float(value)))
        if label in ('height', '높이', 'reach', '리치') and number and 40 <= float(value) <= 100:
            output['height' if label in ('height', '높이') else 'reach'] = f'{round(float(value)*2.54)}cm'
    for label in soup.select('.c-stat-3bar__label'):
        key = {'ko/tko': 'win_ko', 'decision': 'win_dec', '판정승': 'win_dec', 'submission': 'win_sub', '서브미션승': 'win_sub'}.get(text(label).lower())
        value = label.find_next_sibling(class_='c-stat-3bar__value')
        if key and value:
            match = re.match(r'\d+', text(value))
            if match:
                output[key] = int(match[0])
    photos = soup.select('img')
    photo = next((img.get('src') for img in photos if 'athlete_bio_full_body' in img.get('src', '')), None)
    if photo and photo.startswith('https://ufc.com/images/'):
        output['official_photo_url'] = photo
    # These cards cover UFC bouts. They never replace a complete professional ledger.
    history = []
    subject = urlparse(source).path.rstrip('/')
    for card in soup.select('.view-display-id-entity_view_1 .c-card-event--athlete-results'):
        images = card.select('.c-card-event--athlete-results__image')
        mine = next((image for image in images if any(urlparse(a.get('href', '')).path.rstrip('/') == subject for a in image.select('a'))), None)
        other = next((image for image in images if image is not mine), None)
        if mine is None or other is None:
            continue
        classes = mine.get('class', [])
        result = next((state for state in ('win', 'loss', 'draw', 'nc') if state in classes), None)
        # Missing result class is unknown, not a draw or a win.
        if not result:
            continue
        opponent_img = other.select_one('img')
        opponent = opponent_img.get('alt', '') if opponent_img else ''
        opponent_link = other.select_one('a')
        date = text(card.select_one('.c-card-event--athlete-results__date'))
        try:
            date = datetime.strptime(date, '%d.%m.%y').date().isoformat()
        except ValueError:
            continue
        fields = [text(x) for x in card.select('.c-card-event--athlete-results__result-text')]
        event_link = next((a for a in card.select('a[href]') if '/event/' in a['href']), None)
        history.append({"result": result, "opp": opponent or text(card.select_one('.c-card-event--athlete-results__headline')), "opp_ufc_url": urljoin(source, opponent_link.get('href', '')) if opponent_link else '', "date": date, "round": fields[0] if fields else '', "time": fields[1] if len(fields)>1 else '', "method": fields[2] if len(fields)>2 else '', "event": 'UFC', "event_url": event_link['href'] if event_link else ''})
    output['official_history'] = history
    return output


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def refresh_rankings(ctx):
    snapshots = parse_rankings(ctx.http_get(RANKING_URL), ctx.WEIGHT_KO)
    # Supplement identity links and nationality only; Wikipedia never chooses
    # either official ranking order and cannot block the official snapshot.
    wiki_entries = {}
    try:
        for division in ctx.fetch_wikipedia_rankings():
            for entry in [division.get('champion')] + division.get('ranked', []):
                if entry:
                    wiki_entries[ctx._fighter_name_key(entry.get('name'))] = entry
    except Exception:
        pass
    now = datetime.now(ctx.KST).isoformat(timespec='seconds')
    data = json.loads(ctx.FIGHTERS_FILE.read_text(encoding='utf-8'))
    fighters = data['fighters']
    by_name = {ctx._fighter_name_key(f['name']): f for f in fighters}
    by_url = {urlparse(f.get('ufc_url') or f.get('avatar_source') or '').path: f for f in fighters if '/athlete/' in (f.get('ufc_url') or f.get('avatar_source') or '')}
    for fighter in fighters:
        fighter['ranking_positions'] = []
        fighter['rank'] = '랭킹 외'
        fighter['rankings_updated_at'] = now
    for mode, snapshot in snapshots.items():
        for division in snapshot['divisions']:
            entries = [(division['champion'], '챔피언')] + [(e, f"#{e['rank']}") for e in division['ranked']]
            for entry, rank in entries:
                if not entry:
                    continue
                fighter = by_url.get(urlparse(entry['ufc_url']).path) or by_name.get(ctx._fighter_name_key(entry['name']))
                if fighter is None:
                    fighter = {"id": ctx.slugify(entry['name']), "name": entry['name'], "name_ko": ctx.tr_fighter(entry['name']), "recent": [], "history": [], "ranking_positions": [], "rank": '랭킹 외', "rankings_updated_at": now}
                    fighters.append(fighter)
                    by_name[ctx._fighter_name_key(entry['name'])] = fighter
                fighter['ufc_url'] = entry['ufc_url']
                translated = ctx.tr_fighter(entry['name'])
                if re.search(r'[가-힣]', translated):
                    fighter['name_ko'] = translated
                wiki = wiki_entries.get(ctx._fighter_name_key(entry['name']), {})
                for field in ('country', 'country_ko', 'url'):
                    if wiki.get(field):
                        fighter[field] = wiki[field]
                by_url[urlparse(entry['ufc_url']).path] = fighter
                entry.update(fighter_id=fighter['id'], name_ko=fighter.get('name_ko') or ctx.tr_fighter(entry['name']), record=fighter.get('record',''), country=fighter.get('country',''), country_ko=fighter.get('country_ko',''))
                fighter['ranking_positions'].append({"system": mode, "wc": division['wc'], "rank": rank})
                if mode == 'media' and (fighter.get('division') == division['wc'] or fighter['rank'] == '랭킹 외' or rank == '챔피언'):
                    fighter.update(rank=rank, division=division['wc'])
    # Promote data only after both independent systems validate completely.
    rankings = {"schema_version": 2, "generated_at": now, "source": 'UFC 공식 미디어 랭킹 · Meta 랭킹', "source_url": RANKING_URL, "division_count": len(snapshots['media']['divisions']), "divisions": snapshots['media']['divisions'], "systems": snapshots}
    data.update(fighters=fighters, fighter_count=len(fighters), rankings_updated_at=now)
    write_json(ctx.FIGHTERS_FILE, data)
    write_json(ctx.RANKINGS_FILE, rankings)
    print(f'Official rankings: media/meta {len(rankings["divisions"])} divisions each')
    return rankings


def refresh_profiles(ctx, all_profiles=False):
    data = json.loads(ctx.FIGHTERS_FILE.read_text(encoding='utf-8'))
    now = datetime.now(ctx.KST)
    now_iso = now.isoformat(timespec='seconds')
    targets = []
    for fighter in data['fighters']:
        important = bool(fighter.get('ranking_positions') or fighter.get('next') or fighter.get('korean_focus'))
        if all_profiles or important:
            targets.append(fighter)
    # Historical profiles get a bounded rotating refresh without delaying current athletes.
    if not all_profiles:
        rest = [f for f in data['fighters'] if f not in targets]
        targets += sorted(rest, key=lambda f:f.get('profile_attempted_at',''))[:30]
    name_by_url = {urlparse(f.get('ufc_url') or '').path: f['name'] for f in data['fighters'] if f.get('ufc_url')}

    def fetch(fighter):
        source = fighter.get('ufc_url') or ctx.UFC_ATHLETE + (ctx.UFC_SLUG_OVERRIDES.get(fighter['name']) or ctx.ufc_slugify(fighter['name']))
        out = {"id": fighter['id'], "attempted_at": now_iso}
        try:
            response = requests.get(source, headers=ctx.HEADERS, timeout=15)
            response.raise_for_status()
            parsed = parse_profile(response.text, response.url)
            out['profile'] = parsed
            out['source'] = response.url
        except Exception as exc:
            out['error'] = type(exc).__name__
        # An existing history must be re-read too; retaining it is not verification.
        wiki = fighter.get('url')
        if not wiki or 'en.wikipedia.org/wiki/' not in wiki:
            wiki = ctx.WIKI + fighter['name'].replace(' ', '_')
        detail = ctx.fetch_fighter_detail(wiki)
        if detail.get('history'):
            out['history'] = detail
        return out

    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch, fighter) for fighter in targets]
        for future in as_completed(futures):
            results.append(future.result())
            if len(results)%25 == 0:
                print(f'Profiles: {len(results)}/{len(targets)} checked', flush=True)
    by_id = {f['id']: f for f in data['fighters']}
    success = histories = 0
    for result in results:
        fighter = by_id[result['id']]
        fighter['profile_attempted_at'] = now_iso
        if result.get('history'):
            detail = result['history']
            for field in ('history','history_source','stance','nick','win_ko','win_sub','win_dec'):
                if detail.get(field) not in (None, '', []):
                    fighter[field] = detail[field]
            fighter['history_updated_at'] = now_iso
            fighter.pop('history_scope', None)
            histories += 1
        if result.get('profile'):
            profile = result['profile']
            for field in ('record','record_wld','record_source','profile_source','height','reach','age','win_ko','win_sub','win_dec'):
                if profile.get(field) is not None:
                    fighter[field] = profile[field]
            fighter['profile_verified_fields'] = [k for k in ('record','height','reach','age') if k in profile]
            fighter['profile_updated_at'] = now_iso
            fighter['profile_status'] = 'verified'
            fighter['ufc_url'] = result['source']
            if not fighter.get('avatar_url') and profile.get('official_photo_url'):
                fighter.update(avatar_url=profile['official_photo_url'], avatar_thumb_url=profile['official_photo_url'], avatar_source=result['source'], avatar_provider='UFC')
            official = profile.get('official_history', [])
            for row in official:
                opponent = name_by_url.get(urlparse(row['opp_ufc_url']).path) or row['opp']
                row.update(opp=opponent, opp_ko=ctx.tr_fighter(opponent))
            if official:
                fighter['ufc_recent'] = official
                fighter['ufc_recent_updated_at'] = now_iso
                if not fighter.get('history'):
                    fighter.update(history=official, history_source=result['source'], history_scope='UFC 경기', history_updated_at=now_iso)
            success += 1
        else:
            fighter['profile_status'] = 'unavailable'
            # Previous values and their actual verification dates remain intact.
    data.update(fighters=list(by_id.values()), fighter_count=len(by_id), profiles_checked_at=now_iso, profile_refresh={"attempted": len(results), "official_verified": success, "career_histories_verified": histories, "unavailable": len(results)-success})
    write_json(ctx.FIGHTERS_FILE, data)
    # The ranking table uses the same player records; sync row metadata too.
    rankings = json.loads(ctx.RANKINGS_FILE.read_text(encoding='utf-8'))
    for system in rankings.get('systems', {}).values():
        for division in system['divisions']:
            for entry in [division.get('champion')] + division['ranked']:
                if entry:
                    fighter = by_id[entry['fighter_id']]
                    for field in ('record','name_ko','country','country_ko'):
                        entry[field] = fighter.get(field, entry.get(field,''))
    rankings['divisions'] = rankings['systems']['media']['divisions']
    write_json(ctx.RANKINGS_FILE, rankings)
    print(json.dumps(data['profile_refresh']))
    return data['profile_refresh']
