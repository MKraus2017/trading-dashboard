const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/crypto_exit_shadow.js'), 'utf8');
function setup() {
  const elements = Object.fromEntries(['status','summary','rows'].map(x=>['crypto-exit-shadow-'+x,{innerHTML:'',textContent:'',style:{}}]));
  const requests=[];
  const context=vm.createContext({document:{addEventListener(){},getElementById:id=>elements[id]},AbortSignal,
    fetch:async(url,opts)=>{requests.push({url,opts}); return {ok:true,json:async()=>data()};}});
  vm.runInContext(source, context);
  return {elements,context,requests};
}
function data(){return {ok:true,healthy:true,enabled:true,last_success_at:1700000000,
  summary:{completed_pairs:0,total_records:0,excluded:0},rows:[]};}
test('polling is GET only and never evaluates or trades',async()=>{
  const {context,requests,elements}=setup();await context.refreshCryptoExitShadow();
  assert.equal(requests[0].url,'/api/crypto/exit_shadow');assert.equal(requests[0].opts.method,undefined);
  assert.match(elements['crypto-exit-shadow-status'].textContent,/Beobachtung aktiv/);
  assert.match(elements['crypto-exit-shadow-summary'].innerHTML,/Noch kein Ergebnis/);
});
test('failed poll clears green confirmation',async()=>{
  const {context,elements}=setup();context.renderCryptoExitShadow(data());context.fetch=async()=>{throw Error('offline')};
  await context.refreshCryptoExitShadow();assert.match(elements['crypto-exit-shadow-status'].textContent,/nicht abrufbar/);
  assert.equal(elements['crypto-exit-shadow-status'].style.color,'#d29922');
});
test('late adoption is visible and dynamic content escaped',()=>{
  const {context,elements}=setup();const d=data();d.rows=[{symbol:'<script>bad</script>',direction:'LONG',exclusions:['late_adoption'],
    opened_at:1700000000,observed_from:1700000020,margin_eur:100,leverage:2,entry_price:1,fee_bps:10,slippage_bps:5}];
  context.renderCryptoExitShadow(d);const html=elements['crypto-exit-shadow-rows'].innerHTML;
  assert.match(html,/Nachträglich aufgenommen/);assert.match(html,/Noch nicht vergleichbar/);
  assert.doesNotMatch(html,/<script>/);assert.match(html,/&lt;script&gt;/);
});
