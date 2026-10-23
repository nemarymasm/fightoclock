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
console.log('5 event lifecycle checks and all inline JavaScript syntax passed');
