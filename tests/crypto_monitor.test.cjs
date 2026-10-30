const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function setup() {
  const element = {style:{}, textContent:''};
  const context = vm.createContext({document:{addEventListener(){},getElementById:id=>id==='crypto-monitor-status'?element:null},
    AbortSignal, fetch:async()=>({ok:true,json:async()=>({healthy:true,age_seconds:12})})});
  vm.runInContext(source, context);
  return {context,element};
}
test('healthy background monitor is visibly confirmed', async()=>{
  const {context,element}=setup();
  await context.refreshCryptoMonitor();
  assert.match(element.textContent,/aktiv.*12 Sekunden/);
  assert.equal(element.style.color,'#3fb950');
});
test('failed polling clears old green confirmation', async()=>{
  const {context,element}=setup();
  context.renderCryptoMonitor({healthy:true,age_seconds:2});
  context.fetch=async()=>{throw Error('offline')};
  await context.refreshCryptoMonitor();
  assert.match(element.textContent,/nicht bestätigt/);
  assert.equal(element.style.color,'#f85149');
});

test('invalid quote clock explains missing protection and cannot appear healthy',()=>{
  const {context,element}=setup();
  context.renderCryptoMonitor({healthy:true,age_seconds:2,oldest_quote_age_seconds:80,quote_errors:{BTC:'stale_quote',ETH:'missing_timestamp'}});
  assert.match(element.textContent,/Börsenkurs veraltet/);
  assert.match(element.textContent,/Börsenzeit fehlt/);
  assert.match(element.textContent,/Ältester Positionskurs: 80 Sekunden/);
  assert.equal(element.style.color,'#f85149');
  context.renderCryptoMonitor({healthy:false,age_seconds:2,quote_errors:{'<img>':'<script>'}});
  assert.match(element.textContent,/Kurs nicht validierbar/);
  assert.equal(element.innerHTML,undefined);
});
test('stale or absent server state is never shown as healthy',()=>{
  const {context,element}=setup();
  for(const state of [null,{healthy:false,age_seconds:500}]){
    context.renderCryptoMonitor(state);
    assert.match(element.textContent,/nicht bestätigt/);
  }
});
