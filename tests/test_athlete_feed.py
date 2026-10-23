import unittest
import json
from pathlib import Path
from unittest.mock import Mock
from athlete_feed import parse_rankings, parse_profile, refresh_rankings


WEIGHTS = {f'Weight {n}': f'체급{n}' for n in range(11)}


def ranking_html(english=False):
    sections = []
    for system, container, field in (('media', 'block_1', 'weight-class-rank'), ('meta', 'meta_rankings', 'meta-weight-class-rank')):
        groups=[]
        for en, ko in WEIGHTS.items():
            groups.append(f'''<div class="view-grouping"><div class="view-grouping-header">{en if english else ko}</div><table><caption><h5><a href="/athlete/champion-{ko}">Champion {ko}</a></h5></caption><tbody><tr><td class="views-field-{field}">1</td><td class="views-field-title"><a href="/athlete/{system}-{ko}">{system} Fighter {ko}</a></td></tr></tbody></table></div>''')
        sections.append(f'<div class="view-display-id-{container}">{"".join(groups)}</div>')
    return ''.join(sections)


class AthleteFeedTests(unittest.TestCase):
    def test_korean_and_english_rankings_keep_media_and_meta_separate(self):
        for english in (True, False):
            result=parse_rankings(ranking_html(english),WEIGHTS)
            self.assertEqual(len(result['media']['divisions']),11)
            self.assertEqual(len(result['meta']['divisions']),11)
            self.assertTrue(result['media']['divisions'][0]['ranked'][0]['name'].startswith('media'))
            self.assertTrue(result['meta']['divisions'][0]['ranked'][0]['name'].startswith('meta'))

    def test_incomplete_snapshot_does_not_replace_previous_files(self):
        context=Mock()
        context.WEIGHT_KO=WEIGHTS
        context.http_get.return_value='<html>unavailable</html>'
        with self.assertRaises(ValueError):
            refresh_rankings(context)
        context.FIGHTERS_FILE.read_text.assert_not_called()
        context.FIGHTERS_FILE.write_text.assert_not_called()
        context.RANKINGS_FILE.write_text.assert_not_called()

    def test_tied_ranks_remain_tied(self):
        source=ranking_html().replace('</tbody>', '<tr><td class="views-field-weight-class-rank">1</td><td class="views-field-title"><a href="/athlete/tied">Tied fighter</a></td></tr></tbody>',1)
        rows=parse_rankings(source,WEIGHTS)['media']['divisions'][0]['ranked']
        self.assertEqual([r['rank'] for r in rows],[1,1])

    def test_profile_uses_current_wld_and_explicit_metric_conversion(self):
        html='''<h1 class="hero-profile__name">Test Fighter</h1><p class="hero-profile__division-body">11-2-1 (W-L-D)</p><div class="c-bio__info"><div class="c-bio__field"><div class="c-bio__label">Height</div><div class="c-bio__text">76.00</div></div><div class="c-bio__field"><div class="c-bio__label">Age</div><div class="c-bio__text">28</div></div></div>'''
        profile=parse_profile(html,'https://www.ufc.com/athlete/test')
        self.assertEqual(profile['record'],'11승 2패 1무')
        self.assertEqual(profile['height'],'193cm')
        self.assertEqual(profile['age'],'28')
        self.assertNotIn('reach',profile)

    def test_empty_redirect_is_not_a_verified_profile(self):
        with self.assertRaises(ValueError):
            parse_profile('<h1>All athletes</h1>','https://www.ufc.com/athletes')

    def test_missing_outcome_is_not_invented_as_a_draw_or_win(self):
        html='''<h1 class="hero-profile__name">Test</h1><p class="hero-profile__division-body">1-0-0 (W-L-D)</p><div class="c-bio__info"></div><div class="view-display-id-entity_view_1"><article class="c-card-event--athlete-results"><div class="c-card-event--athlete-results__image"><a href="/athlete/test"></a></div><div class="c-card-event--athlete-results__image"><a href="/athlete/other"></a></div></article></div>'''
        self.assertEqual(parse_profile(html,'https://www.ufc.com/athlete/test')['official_history'],[])


class AthleteSnapshotTests(unittest.TestCase):
    def test_every_ranking_row_matches_the_players_system_and_division(self):
        root=Path(__file__).resolve().parents[1] / 'data'
        rankings=json.loads((root/'rankings.json').read_text(encoding='utf-8'))
        players={f['id']:f for f in json.loads((root/'fighters.json').read_text(encoding='utf-8'))['fighters']}
        for mode,system in rankings['systems'].items():
            self.assertEqual(len(system['divisions']),11)
            for division in system['divisions']:
                for row,rank in [(division.get('champion'),'챔피언')]+[(r,'#'+str(r['rank'])) for r in division['ranked']]:
                    if row:
                        player=players[row['fighter_id']]
                        self.assertIn({'system':mode,'wc':division['wc'],'rank':rank},player['ranking_positions'])
                        self.assertEqual(row['record'],player.get('record',''))

    def test_verified_profiles_have_their_own_sources_and_failure_keeps_old_dates(self):
        root=Path(__file__).resolve().parents[1] / 'data'
        players=json.loads((root/'fighters.json').read_text(encoding='utf-8'))['fighters']
        verified=[f for f in players if f.get('profile_status')=='verified']
        self.assertTrue(verified)
        for player in verified:
            self.assertTrue(player['profile_updated_at'])
            self.assertTrue(player['record_source'].startswith(('https://www.ufc.com/athlete/','https://kr.ufc.com/athlete/')))
            self.assertEqual(len(player['record_wld']),3)
        for player in players:
            if player.get('profile_status')=='unavailable':
                self.assertNotEqual(player.get('profile_updated_at'),player['profile_attempted_at'])
