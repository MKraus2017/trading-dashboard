// Run with: node tests/crypto_positions.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function setup(positions = []) {
  const elements = Object.fromEntries(
    ['crypto-summary', 'crypto-positions', 'crypto-trade-history', 'error']
      .map(id => [id, { innerHTML: '', style: {} }])
  );
  const requests = [];
  const context = vm.createContext({
    document: { addEventListener() {}, getElementById: id => elements[id] },
    fetch: async url => {
      requests.push(url);
      return { json: async () => ({ ok: true, portfolio: { cash: 1000, positions } }) };
    }
  });
  vm.runInContext(source, context);
  // The existing script also loads backtest history at startup, outside this test.
  requests.length = 0;
  return { context, elements, requests };
}

test('purchase dates support ISO, legacy Unix seconds, and missing data', () => {
  const { context } = setup();
  const parse = context.cryptoOpenedAt;
  const iso = '2026-09-21T16:30:00+00:00';
  assert.equal(parse({ opened_at: iso }).toISOString(), '2026-09-21T16:30:00.000Z');
  assert.equal(parse({ opened_at_ts: Date.parse(iso) / 1000 }).toISOString(), '2026-09-21T16:30:00.000Z');
  assert.equal(parse({ opened_at: 'invalid', opened_at_ts: String(Date.parse(iso) / 1000) }).toISOString(), '2026-09-21T16:30:00.000Z');
  for (const pos of [{}, { opened_at: null }, { opened_at: 'invalid' }, { opened_at_ts: 0 }, { opened_at_ts: 'bad' }, { opened_at_ts: 1e30 }]) {
    assert.equal(parse(pos), null);
  }
});

test('open simulation rows display purchase date, planned TP, and current trailing SL', async () => {
  const positions = [
    { symbol: 'BTC', direction: 'LONG', leverage: 2, entry_price: 100, last_price: 105,
      opened_at: '2026-09-21T16:30:00+00:00', take_profit: 120, stop_loss: 102, trailing_active: true },
    { symbol: 'ETH', direction: 'SHORT', leverage: 3, entry_price: 50, last_price: 49,
      opened_at_ts: Date.parse('2026-01-21T16:30:00Z') / 1000, take_profit: 40, stop_loss: 55 },
    { symbol: 'DOT', direction: 'LONG', leverage: 1, entry_price: 5, last_price: 5 }
  ];
  const original = JSON.stringify(positions);
  const { context, elements, requests } = setup(positions);
  await context.loadCryptoPortfolio();
  const html = elements['crypto-positions'].innerHTML;
  assert.match(html, /Kaufdatum \(Berlin\)/);
  assert.match(html, /TP \(geplant\)/);
  assert.match(html, /SL \(aktuell\)/);
  assert.match(html, /21\.09\.2026, 18:30/);
  assert.match(html, /21\.01\.2026, 17:30/);
  assert.match(html, /<td>120<\/td>/);
  assert.match(html, /<td>102 .*Trailing/);
  assert.match(html, /<td>40<\/td>/);
  assert.match(html, /<td>55<\/td>/);
  assert.doesNotMatch(html, /Invalid Date|NaN|1970/);
  const rows = [...html.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].slice(1);
  assert.equal(rows.length, 3);
  for (const row of rows) assert.equal((row[1].match(/<td[ >]/g) || []).length, 12);
  assert.match(rows[2][1], /<td style="white-space:nowrap;">—<\/td>/);
  assert.equal((rows[2][1].match(/<td>—<\/td>/g) || []).length, 3);
  assert.equal(JSON.stringify(positions), original, 'rendering must not alter trading data');
  assert.deepEqual(requests, ['/api/crypto/portfolio']);
});

test('empty simulation portfolio still renders its existing empty state', async () => {
  const { context, elements } = setup();
  await context.loadCryptoPortfolio();
  assert.match(elements['crypto-positions'].innerHTML, /Keine offenen Krypto-Positionen/);
});
