const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/astra_ultra.js'),'utf8');
function data(){return {ok:true,started:true,healthy:true,enabled:true,run:{version:'astra_breakout_2h_v2',started_at:1791630000,version_started_at:1791720000,initial_capital:1000,equity:1000,cash:1000,positions:[],trades:[],closed_count:0,prior_versions:[],baseline_fresh:true,baseline_invalid:[],baseline_equity:1000,baseline_strategy:'classic',baseline_inherited_count:0,baseline_strategy_changes:[],missed_hours:0,data_errors:0,observation_gaps:0,rules:{bar_seconds:7200,exit_channel:12,max_hold_hours:336,fee_pct:.002,slippage_pct:.0005}},decisions:[]};}
function setup(){
  const elements=Object.fromEntries(['status','versions','summary','comparison','positions','trades','decisions','daily'].map(n=>['astra-'+n,{innerHTML:'',textContent:''}]));
  const calls=[];
  const context=vm.createContext({document:{addEventListener(){},getElementById:id=>elements[id]},AbortSignal,fetch:async(url,opts)=>{calls.push({url,opts});return{ok:true,json:async()=>data()};}});
  vm.runInContext(source,context);return{elements,calls,context};
}
test('GET only refresh neither creates experiment nor trades',async()=>{
  const {context,calls,elements}=setup();await context.refreshAstraUltra();
  assert.equal(calls[0].url,'/api/crypto/astra_ultra');assert.equal(calls[0].opts.method,undefined);
  assert.match(elements['astra-status'].textContent,/aktiv/);
  assert.match(elements['astra-positions'].innerHTML,/Cash zu halten/);
});
test('failed request does not leave healthy confirmation',async()=>{
  const {context,elements}=setup();context.renderAstraUltra(data());context.fetch=async()=>{throw Error('offline')};
  await context.refreshAstraUltra();assert.match(elements['astra-status'].textContent,/nicht abrufbar/);
});
test('unstarted account and broken baseline clearly separated',()=>{
  const {context,elements}=setup();context.renderAstraUltra({ok:true,started:false});
  assert.match(elements['astra-status'].textContent,/Noch kein Testkonto/);
  const d=data();d.run.baseline_invalid=['reset'];context.renderAstraUltra(d);
  assert.match(elements['astra-comparison'].innerHTML,/Nicht belastbar/);
  assert.match(elements['astra-comparison'].innerHTML,/ungültig/);
});
test('all user/source content is escaped',()=>{
  const {context,elements}=setup();const d=data();d.run.baseline_strategy='<img src=x onerror=bad>';
  d.decisions=[{symbol:'<script>bad</script>',action:'<img>'}];context.renderAstraUltra(d);
  assert.doesNotMatch(elements['astra-comparison'].innerHTML,/<img/);
  assert.doesNotMatch(elements['astra-decisions'].innerHTML,/<script>/);
  assert.match(elements['astra-decisions'].innerHTML,/&lt;script&gt;/);
});
test('new tab index is consistent with panel and renderer',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../templates/index.html'),'utf8');
  const app=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
  const tabs=[...html.matchAll(/class="tab[^\"]*" onclick="showTab\('([^']+)'\)/g)].map(m=>m[1]);
  const mapped=JSON.parse(app.match(/const tabs = (\[[^;]+\]);/)[1].replaceAll("'",'"'));
  assert.deepEqual(tabs,mapped);assert.ok(html.includes('id="panel-astra"'));
});

test('current 2H status and pending decision use the new interval',()=>{
  const {context,elements}=setup();context.renderAstraUltra(data());
  assert.match(elements['astra-status'].textContent,/astra_breakout_2h_v2 · 2H/);
  assert.match(elements['astra-decisions'].innerHTML,/2H-Kerze nach dem Versionsstart/);
  assert.doesNotMatch(elements['astra-comparison'].innerHTML,/Verpasste 4H/);
  assert.match(elements['astra-comparison'].innerHTML,/Kerzenprüfungen \(alle Versionen\)/);
});

test('mixed decision history labels each original version and candle length',()=>{
  const {context,elements}=setup();const d=data();
  d.decisions=[
    {bar_end:1791727200,symbol:'BTC',action:'Abwarten',enter:false,version:'astra_breakout_2h_v2',timeframe_hours:2},
    {bar_end:1791720000,symbol:'ETH',action:'Abwarten',enter:false,version:'astra_breakout_4h_v1',timeframe_hours:4}
  ];context.renderAstraUltra(d);
  assert.match(elements['astra-decisions'].innerHTML,/2H · astra_breakout_2h_v2/);
  assert.match(elements['astra-decisions'].innerHTML,/4H · astra_breakout_4h_v1/);
  assert.match(elements['astra-decisions'].innerHTML,/Kerzenschluss \(Berlin\)/);
  assert.doesNotMatch(elements['astra-decisions'].innerHTML,/4H-Schluss/);
});

test('version transition keeps original capital and all-time return basis visible',()=>{
  const {context,elements}=setup();const d=data();d.run.equity=950;
  d.run.prior_versions=[{version:'astra_breakout_4h_v1',started_at:d.run.started_at,ended_at:d.run.version_started_at,equity:900,rules:{bar_seconds:14400}}];
  context.renderAstraUltra(d);
  const html=elements['astra-versions'].innerHTML;
  assert.match(html,/astra_breakout_4h_v1/);assert.match(html,/astra_breakout_2h_v2/);
  assert.match(html,/900,00/);assert.match(html,/Aktiv/);
  assert.match(html,/keine isolierte 2H-Rendite/);
  assert.match(elements['astra-summary'].innerHTML,/Ursprüngliches Startkapital/);
  assert.match(elements['astra-summary'].innerHTML,/alle Versionen/);
  assert.match(elements['astra-summary'].innerHTML,/>-5%</);
});

test('entry and management rules stay distinct for carried 4H positions',()=>{
  const {context,elements}=setup();const d=data();
  const p={symbol:'BTC',version:'astra_breakout_4h_v1',quantity:1,entry_price:100,entry_total:101,entry_fee:1,last_price:110,stop_loss:95,
    rules:{bar_seconds:14400,exit_channel:12,max_hold_hours:336,fee_pct:.01,slippage_pct:0},signal:{bar_seconds:14400},
    management_version:'astra_breakout_2h_v2',management_rules:d.run.rules};
  d.run.positions=[p];context.renderAstraUltra(d);
  const html=elements['astra-positions'].innerHTML;
  assert.match(html,/Version beim Kauf: astra_breakout_4h_v1 · Kerzen: 4H/);
  assert.match(html,/Ausstiegsregeln: astra_breakout_2h_v2 · Kerzen: 2H/);
  assert.match(html,/12-Kerzen-Kanalbruch \(24h\)/);
  assert.match(html,/7,90 €/); // Original 1% position costs, not the current run's fee.
});

test('historic closed positions retain 4H exits while new positions show 2H',()=>{
  const {context}=setup();
  const legacy=context.astraDetails({version:'astra_breakout_4h_v1',closed_at:1791720000,signal:{}});
  const current=context.astraDetails({version:'astra_breakout_2h_v2',signal:{bar_seconds:7200}});
  assert.match(legacy,/Kerzen: 4H/);assert.match(legacy,/12-Kerzen-Kanalbruch \(48h\)/);
  assert.match(current,/Kerzen: 2H/);assert.match(current,/12-Kerzen-Kanalbruch \(24h\)/);
  assert.match(current,/maximal 336h/);
});

test('missing error decision signal is unknown, not a fabricated negative signal',()=>{
  const {context,elements}=setup();const d=data();
  d.decisions=[{symbol:'SYSTEM',action:'Datenfehler',version:'astra_breakout_2h_v2',timeframe_hours:2}];
  context.renderAstraUltra(d);
  assert.doesNotMatch(elements['astra-decisions'].innerHTML,/<td>Nein<\/td>/);
});

test('version metadata and position management labels are escaped',()=>{
  const {context,elements}=setup();const d=data();
  d.run.prior_versions=[{version:'<img src=x>',started_at:1,ended_at:2,equity:1000}];
  d.decisions=[{version:'<script>bad</script>',symbol:'BTC',timeframe_hours:2,enter:false}];
  context.renderAstraUltra(d);
  assert.doesNotMatch(elements['astra-versions'].innerHTML,/<img/);
  assert.match(elements['astra-versions'].innerHTML,/&lt;img/);
  assert.doesNotMatch(elements['astra-decisions'].innerHTML,/<script>/);
  assert.doesNotMatch(context.astraDetails({management_version:'<img>',version:'astra_breakout_2h_v2'}),/<img>/);
});

test('static instructions separate 2H current rules from old 4H evidence',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../templates/index.html'),'utf8');
  assert.match(html,/astra_breakout_2h_v2/);
  assert.match(html,/24 Kerzen \(48 Stunden\)/);
  assert.match(html,/12-Kerzen-Tief \(24 Stunden\)/);
  assert.match(html,/500 abgeschlossenen 2H-Kerzen/);
  assert.match(html,/etwa alle 30 Sekunden/);
  assert.match(html,/kein Backtestnachweis für die aktuelle 2H-Version/);
  assert.match(html,/Kein eigener historischer Backtest vorgelegt/);
  assert.match(html,/\+5,79%/);
  assert.match(html,/astra_ultra\.js\?v=2/);
});
