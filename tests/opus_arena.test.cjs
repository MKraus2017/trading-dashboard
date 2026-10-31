const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/opus_arena.js'),'utf8');
function data(){return {ok:true,started:true,healthy:true,enabled:true,
  run:{version:'opus_pullback_2h_v1',started_at:1800000000,initial_capital:1110,equity:1110,cash:1110,positions:[],trades:[],closed_count:0,
       fees_paid:0,win_rate:null,monitor_success_at:1800000030,seed:{krypto_equity:1020,astra_equity:1200},missed_bars:0,
       rules:{fee_pct:.002,slippage_pct:.0005}},
  leaderboard:[{key:'krypto',name:'Krypto',rank:1,start_equity:1020,equity:1122,return_pct:10,max_drawdown_pct:1,fresh:true,valid:true},
               {key:'opus',name:'Opus 5.5',rank:2,start_equity:1110,equity:1110,return_pct:0,max_drawdown_pct:0,fresh:true,valid:true},
               {key:'astra',name:'Astra Ultra',rank:3,start_equity:1200,equity:1080,return_pct:-10,max_drawdown_pct:10,fresh:false,valid:true}],
  decisions:[],daily:[]};}
function setup(){
  const elements=Object.fromEntries(['status','leaderboard','summary','positions','trades','decisions','daily'].map(n=>['opus-'+n,{innerHTML:'',textContent:''}]));
  const calls=[];
  const context=vm.createContext({document:{addEventListener(){},getElementById:id=>elements[id]},AbortSignal,
    fetch:async(url,opts)=>{calls.push({url,opts});return{ok:true,json:async()=>data()};}});
  vm.runInContext(source,context);return{elements,calls,context};
}
test('GET-only refresh renders leaderboard with ranking',async()=>{
  const {context,calls,elements}=setup();await context.refreshOpusArena();
  assert.equal(calls[0].url,'/api/crypto/opus_arena');assert.equal(calls[0].opts.method,undefined);
  assert.match(elements['opus-status'].textContent,/aktiv/);
  const lb=elements['opus-leaderboard'].innerHTML;
  assert.ok(lb.indexOf('Krypto')<lb.indexOf('Opus 5.5')&&lb.indexOf('Opus 5.5')<lb.indexOf('Astra Ultra'));
  assert.match(lb,/🥇/);assert.match(lb,/\+10%/);assert.match(lb,/Veraltet/);
  assert.match(lb,/Durchschnitt aus Krypto/);
});
test('unstarted and failed states are explicit',async()=>{
  const {context,elements}=setup();
  context.renderOpusArena({ok:true,started:false,enabled:true});
  assert.match(elements['opus-status'].textContent,/Krypto UND Astra/);
  context.fetch=async()=>{throw Error('offline')};await context.refreshOpusArena();
  assert.match(elements['opus-status'].textContent,/nicht abrufbar/);
});
test('content is escaped',()=>{
  const {context,elements}=setup();const d=data();
  d.decisions=[{symbol:'<script>x</script>',action:'<img src=x>',bar_end:1800007200}];
  context.renderOpusArena(d);
  assert.doesNotMatch(elements['opus-decisions'].innerHTML,/<script>|<img/);
});
test('tab index stays consistent with panels',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../templates/index.html'),'utf8');
  const app=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
  const tabs=[...html.matchAll(/class="tab[^"]*" onclick="showTab\('([^']+)'\)"/g)].map(m=>m[1]);
  const list=JSON.parse(app.match(/const tabs = (\[[^\]]+\])/)[1].replace(/'/g,'"'));
  assert.deepEqual(list,tabs);
  assert.ok(tabs.includes('opus'));assert.ok(html.includes('id="panel-opus"'));
  assert.ok(html.includes('/static/opus_arena.js'));
});
