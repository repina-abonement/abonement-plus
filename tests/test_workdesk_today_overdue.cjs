const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(path.resolve(__dirname,'../crm/static/app.js'),'utf8');
const start=source.indexOf('function filterWorkdeskBoard(');
const end=source.indexOf('\nasync function renderWorkdesk',start);
assert(start>=0&&end>start,'real workdesk filter is present');
const sandbox={};vm.createContext(sandbox);vm.runInContext(source.slice(start,end),sandbox);
const cards=[
  {id:1,due:'2026-11-23T23:59'},
  {id:2,due:'2026-11-24T10:00'},
  {id:3,due:'2026-11-25T10:00'},
  {id:4,stage_changed_at:'2026-11-24T08:00'},
  {id:5},
];
const board={columns:[{key:'lead',cards}]};
const ids=result=>result.columns[0].cards.map(card=>card.id);
assert.deepStrictEqual([...ids(sandbox.filterWorkdeskBoard(board,'today','2026-11-24','2026-11-24'))],[2,4]);
assert.deepStrictEqual([...ids(sandbox.filterWorkdeskBoard(board,'overdue','2026-11-24','2026-11-24'))],[1]);
assert.deepStrictEqual([...ids(sandbox.filterWorkdeskBoard(board,'date','2026-11-25','2026-11-24'))],[3]);
assert.deepStrictEqual(cards.map(card=>card.id),[1,2,3,4,5],'filter does not mutate the source board');
assert(source.includes('data-pipeline-today>Сегодня'));
assert(source.includes('data-pipeline-overdue>Просрок'));
console.log('ok: workdesk separates today, overdue and selected-date cards before counters');
