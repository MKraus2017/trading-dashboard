const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/astra_ultra.js'),'utf8');
function data(){return {ok:true,started:true,healthy:true,enabled:true,run:{version:'v1',initial_capital:1000,equity:1000,cash:1000,positions:[],trades:[],closed_count:0,baseline_fresh:true,baseline_invalid:[],baseline_equity:1000,baseline_strategy:'classic',baseline_inherited_count:0,baseline_strategy_changes:[],missed_hours:0,data_errors:0,observation_gaps:0,rules:{fee_pct:.002,slippage_pct:.0005}},decisions:[]};}
function setup(){
  const elements=Object.fromEntries(['status','summary','comparison','positions','trades','decisions'].map(n=>['astra-'+n,{innerHTML:'',textContent:''}]));
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
