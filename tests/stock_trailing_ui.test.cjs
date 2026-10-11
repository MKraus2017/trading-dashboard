const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function setup() {
  const ids = ['depot-summary', 'depot-positions', 'trade-history', 'stock-monitor-status', 'stock-rules-note', 'last-update', 'loading', 'error'];
  const elements = Object.fromEntries(ids.map(id => [id, {innerHTML:'', textContent:'', style:{}, contains:() => true, insertAdjacentHTML(){}}]));
  const calls = [];
  const context = vm.createContext({
    document:{addEventListener(){}, getElementById:id => elements[id]},
    window:{}, console, setTimeout(){}, setInterval(){},
    fetch:async (url, options) => { calls.push({url, options}); return {ok:true, json:async () => ({ok:true, portfolio:portfolio()})}; }
  });
  vm.runInContext(source, context);
  context.renderComparison = () => {};
  context.renderImprovements = () => {};
  context.renderRealPositions = () => {};
  calls.length = 0; // Ignore the pre-existing unrelated cached-backtest GET at script load.
  return {context, elements, calls};
}

function position(extra = {}) {
  return {symbol:'AAPL', entry_price:100, last_price:110, shares:2, invested:200, unrealized_eur:20, unrealized_pct:10,
    opened_at:'2026-10-09T12:30:00', stop_loss:100, trailing_stop:105, initial_stop_loss:95,
    highest_price:112, take_profit:120, breakeven_set:true, trailing_method:'chandelier',
    stop_last_raised_at:'2026-10-09T14:00:00Z', quote_at:'2026-10-09T14:30:00Z', quote_status:'ok', quote_tradable:true, ...extra};
}

function portfolio(extra = {}) {
  return {cash:800, total_value:1020, total_return_pct:2, positions:[position()], trades:[],
    stock_monitor:{healthy:true, data_complete:true, last_success_at:'2026-10-09T14:31:00Z', last_attempt_at:'2026-10-09T14:31:00Z', interval_seconds:60, missing_symbols:[], closed_symbols:[]},
    stock_rules:{version:'stock_trailing_monitor_v1', chandelier_enabled:true, atr_period:22, atr_multiple:2, trailing_pct:.08, breakeven_at_pct:4, fixed_activation_pct:25, tp_policy:'always_active'}, ...extra};
}

test('effective stock stop is the higher valid stop and labels trailing on a tie', () => {
  const {context} = setup();
  assert.equal(context.stockEffectiveStop(position()).value, 105);
  assert.equal(context.stockEffectiveStop(position()).kind, 'trailing');
  assert.equal(context.stockEffectiveStop(position({trailing_stop:100})).kind, 'trailing');
  assert.equal(context.stockEffectiveStop(position({trailing_stop:98})).kind, 'breakeven');
  assert.equal(context.stockEffectiveStop(position({trailing_stop:98, breakeven_set:false})).kind, 'stop_loss');
  assert.equal(context.stockEffectiveStop(position({stop_loss:undefined, trailing_stop:103})).value, 103);
  assert.equal(context.stockEffectiveStop(position({stop_loss:0, trailing_stop:NaN})).value, null);
  assert.equal(context.stockEffectiveStop(position({stop_loss:-20, trailing_stop:Infinity})).kind, 'none');
  assert.equal(context.stockEffectiveStop(position({effective_stop_loss:99, effective_stop_kind:'stop_loss'})).value, 105);
});

test('stock row shows actual trailing while details retain distinct base and original stops', () => {
  const {context, elements} = setup(); context.renderPortfolio(portfolio());
  const html = elements['depot-positions'].innerHTML;
  assert.match(html, /SL \(wirksam\)/);
  assert.match(html, /105,00 €<br><span[^>]*>🔒 Trailing/);
  assert.match(html, /Ursprünglicher Stop-Loss<\/span><br><strong>95,00 €/);
  assert.match(html, /Aktueller Basis-SL \(Breakeven\)<\/span><br><strong>100,00 €/);
  assert.match(html, /Gespeicherter Trailing-Stop<\/span><br><strong>105,00 €/);
  assert.match(html, /Höchster beobachteter Kurs seit Kauf/);
  assert.match(html, /ATR\(22\) × 2/);
  assert.match(html, /Take-Profit \(weiterhin aktiv\)/);
  assert.match(html, /Wirksamen Stop zuletzt angehoben/);
  assert.match(html, /<th>Details<\/th>/);
  assert.match(html, /<details class="stock-position-details">/);
});

test('lower stored trailing is not misrepresented as the applied stop', () => {
  const {context, elements} = setup(); context.renderPortfolio(portfolio({positions:[position({trailing_stop:97})]}));
  const html = elements['depot-positions'].innerHTML;
  assert.match(html, /100,00 €<br><span[^>]*>🔒 Breakeven/);
  assert.doesNotMatch(html, /97,00 €<br><span[^>]*>🔒 Trailing/);
  assert.match(html, /unter dem wirksamen Stop; derzeit nicht maßgeblich/);
});

test('old missing stop metadata remains unknown and is never invented from entry or current SL', () => {
  const {context} = setup();
  const html = context.stockPositionDetails(position({initial_stop_loss:undefined, trailing_method:undefined, stop_last_raised_at:undefined, quote_status:undefined, quote_at:undefined}));
  assert.match(html, /Nicht gespeichert \(Altbestand\)/);
  assert.match(html, /Für diesen Altbestand nicht protokolliert/);
  assert.match(html, /Kursstatus noch nicht protokolliert/);
  assert.match(html, /Wirksamen Stop zuletzt angehoben \(Berlin\)<\/span><br><strong>—/);
  assert.doesNotMatch(html, /Ursprünglicher Stop-Loss<\/span><br><strong>100,00 €/);
});

test('stock timestamps display Berlin and legacy naive timestamps mean UTC', () => {
  const {context} = setup();
  assert.match(context.stockBerlinTime('2026-10-09T12:30:00'), /9\.10\.2026, 14:30:00/);
  assert.match(context.stockBerlinTime('2026-10-09T12:30:00Z'), /14:30:00/);
  assert.match(context.stockBerlinTime('2026-01-09T12:30:00'), /13:30:00/);
  assert.equal(context.stockBerlinTime(null), '—');
  assert.equal(context.stockBerlinTime('<img>'), '—');
  assert.equal(context.stockBerlinTime(1791727200), context.stockBerlinTime(1791727200000));
});

test('closed markets are labelled neutrally rather than as a failed worker', () => {
  const {context, elements} = setup(); const p = portfolio();
  p.stock_monitor.closed_symbols = ['AAPL']; p.positions[0].quote_status = 'market_closed'; p.positions[0].quote_tradable = false;
  context.renderPortfolio(p);
  const status = elements['stock-monitor-status'].textContent;
  assert.match(status, /Stop-Prüfung: aktiv/);
  assert.match(status, /Markt geschlossen: AAPL/);
  assert.doesNotMatch(status, /Fehler|Status prüfen|⚠/);
  assert.match(elements['depot-positions'].innerHTML, /Markt geschlossen – letzter verfügbarer Referenzkurs/);
  assert.match(status, /9\.10\.2026, 16:31:00/);
});

test('missing quotes and heartbeat failure are visible independently', () => {
  const {context, elements} = setup(); const p = portfolio();
  p.stock_monitor.missing_symbols = ['NVDA']; p.stock_monitor.data_complete = false;
  context.renderPortfolio(p);
  assert.match(elements['stock-monitor-status'].textContent, /⚠ Keine nutzbaren Kurse: NVDA/);
  p.stock_monitor.healthy = false; context.renderPortfolio(p);
  assert.match(elements['stock-monitor-status'].textContent, /Status prüfen/);
  assert.match(elements['stock-monitor-status'].textContent, /Letzter Versuch/);
  delete p.stock_monitor; context.renderPortfolio(p);
  assert.match(elements['stock-monitor-status'].textContent, /kein Nachweis einer Kursprüfung/);
});

test('Chandelier keeps protection without ATR and does not claim a percentage fallback', () => {
  const {context, elements} = setup(); context.renderPortfolio(portfolio());
  const note = elements['stock-rules-note'].textContent;
  assert.match(note, /ATR\(22\) × 2/);
  assert.match(note, /Bei fehlendem ATR bleibt der vorhandene Stop unverändert/);
  assert.match(note, /Prozent-Trailing gilt nur bei deaktiviertem Chandelier/);
  assert.doesNotMatch(note, /als Rückfallregel|ab 25 % Kursgewinn/);
  assert.match(note, /Breakeven ab 4 %/);
  assert.match(note, /nicht abgesenkt/);
  assert.match(note, /Take-Profit bleibt aktiv und kann vorher auslösen/);
  assert.match(context.stockPositionDetails(position({trailing_method:'fixed_pct'})), /Fester Abstand 8 %/);
});

test('percentage trailing is described as active only with Chandelier disabled', () => {
  const {context, elements} = setup(); const p = portfolio();
  p.stock_rules.chandelier_enabled = false;
  context.renderPortfolio(p);
  const note = elements['stock-rules-note'].textContent;
  assert.match(note, /Trailing-Regel: Fester Abstand 8 %/);
  assert.match(note, /Aktivierung ab 25 % Kursgewinn/);
  assert.doesNotMatch(note, /ATR\(22\)|Rückfallregel/);
});

test('position, advice and metadata are escaped without inline JavaScript interpolation', () => {
  const {context, elements} = setup(); const hostile = '<img src=x onerror="bad()">\'&';
  const p = portfolio({positions:[position({symbol:hostile, shares:hostile, trailing_method:hostile, quote_status:hostile, advice:{color:hostile, advice:hostile, reason:hostile, score:hostile}})], stock_rules_note:hostile});
  context.renderPortfolio(p);
  const html = elements['depot-positions'].innerHTML;
  assert.doesNotMatch(html, /<img|onclick=|title="<|data-stock-sell-symbol="</);
  assert.match(html, /&lt;img/);
  assert.match(html, /&quot;bad\(\)&quot;/);
  assert.match(html, /&#39;&amp;/);
  assert.match(elements['stock-rules-note'].textContent, /<img/); // Assigned as textContent, not markup.
});

test('rendering is nonmutating and refresh remains a read-only GET', async () => {
  const {context, elements, calls} = setup(); const p = portfolio(); const before = JSON.stringify(p);
  context.renderPortfolio(p); assert.equal(JSON.stringify(p), before);
  await context.loadPortfolio();
  assert.equal(calls.length, 1); assert.equal(calls[0].url, '/api/portfolio'); assert.equal(calls[0].options, undefined);
  let sold; context.quickSell = symbol => { sold = symbol; };
  const symbol = "A'B"; const button = {getAttribute:() => symbol};
  elements['depot-positions'].onclick({target:{closest:() => button}});
  assert.equal(sold, symbol); // Symbol is passed as data, never evaluated as code.
});

test('stock template explains reference prices and virtual-only stops with a cache bump', () => {
  const html = fs.readFileSync(path.join(__dirname, '../templates/index.html'), 'utf8');
  assert.match(html, /id="stock-monitor-status"/);
  assert.match(html, /Anzeige aktualisieren/);
  assert.match(html, /Referenzkurse können verzögert sein/);
  assert.match(html, /keine Broker-Orders und keine garantierten Ausstiegskurse/);
  assert.match(html, /id="stock-rules-note"/);
  assert.match(html, /app\.js\?v=30/);
});
