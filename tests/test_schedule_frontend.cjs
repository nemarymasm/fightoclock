const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(require('node:path').join(__dirname, '..', 'index.html'), 'utf8');
const context = vm.createContext({Date, todayKstIso: ()=>'2026-10-25'});
const source = html.slice(html.indexOf('function scheduleStatus('),html.indexOf('function officialScheduleInfo('));
vm.runInContext(source,context);
const event = {date_iso:'2026-10-24',start_time_utc:'2026-10-24T18:00:00Z'};
assert.equal(context.scheduleStatus(event, Date.parse('2026-10-24T16:00:00Z')), 'upcoming', 'Korean midnight must not hide a future event');
assert.equal(context.scheduleStatus(event, Date.parse('2026-10-24T20:00:00Z')), 'upcoming', 'in-progress card remains visible');
assert.equal(context.scheduleStatus(event, Date.parse('2026-10-25T03:00:00Z')), 'expired', 'old scheduled card is not fabricated as a result');
assert.equal(context.scheduleStatus({date_iso:'2026-10-26'}), 'upcoming');
assert.equal(context.scheduleStatus({date_iso:'2026-10-23'}), 'expired');
const scripts=[...html.matchAll(/<script(\s[^>]*)?>([\s\S]*?)<\/script>/g)];
for(const [,attributes,script] of scripts){
  if((attributes||'').includes('application/ld+json')) JSON.parse(script);
  else if((attributes||'').includes('type="module"')) new (Object.getPrototypeOf(async function(){}).constructor)(script);
  else new vm.Script(script);
}
const names=vm.createContext({cleanExternalText:x=>x,FIGHTER_NAME_KO:{reed:'리드',ko:'고석현'},FIGHTER_NAME_EN:{reed:'Reed',ko:'Ko'}});
vm.runInContext(html.slice(html.indexOf('function localizeHistoryEvent('),html.indexOf('const COUNTRY_LABELS_KO')),names);
assert.equal(names.localizeHistoryEvent('UFC Freedom 250'),'UFC Freedom 250');
assert.equal(names.localizeHistoryEvent('UFC Fight Night: Edgar vs The Korean Zombie'),'UFC 파이트 나이트: Edgar 대 The Korean Zombie');
assert.equal(names.localizeHistoryEvent('UFC Fight Night: Ko vs Reed'),'UFC 파이트 나이트: 고석현 대 리드');
console.log('5 event lifecycle checks, 3 name-boundary checks and all inline JavaScript syntax passed');
