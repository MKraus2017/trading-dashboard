let currentPortfolio = null;
let currentRecs = [];

function showTab(name) {
  const tabs = ['depot', 'real', 'empfehlungen', 'autopilot', 'historie'];
  document.querySelectorAll('.tab').forEach((t, i) => {
    t.classList.toggle('active', tabs[i] === name);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
}

function showLoading(on) {
  document.getElementById('loading').style.display = on ? 'block' : 'none';
}

function showError(msg) {
  const el = document.getElementById('error');
  el.textContent = msg;
  el.style.display = msg ? 'block' : 'none';
}

const SYMBOL_NAMES = {
  AAPL: 'Apple', AMD: 'AMD', AMZN: 'Amazon', 'ASML.AS': 'ASML Holding',
  AVGO: 'Broadcom', GOOGL: 'Alphabet', JPM: 'JPMorgan Chase',
  LMT: 'Lockheed Martin', MA: 'Mastercard', META: 'Meta Platforms',
  MSFT: 'Microsoft', 'NESR.DE': 'Nestlé', NESN: 'Nestlé', NVDA: 'NVIDIA',
  PLTR: 'Palantir', 'SAP.DE': 'SAP', SMH: 'VanEck Semiconductor ETF',
  TSLA: 'Tesla', V: 'Visa', 'VUSA.AS': 'Vanguard S&P 500 ETF'
};

function symbolName(symbol) {
  return SYMBOL_NAMES[symbol] || symbol || 'Unbekannt';
}

function fmtEur(n) {
  if (n === null || n === undefined) return '-';
  return '€' + Number(n).toLocaleString('de-DE', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function fmtPct(n) {
  if (n === null || n === undefined) return '-';
  return Number(n).toFixed(2) + '%';
}

async function loadPortfolio() {
  showLoading(true);
  showError('');
  try {
    const r = await fetch('/api/portfolio');
    const data = await r.json();
    currentPortfolio = data.portfolio;
    renderPortfolio(data.portfolio, data.alerts);
    document.getElementById('last-update').textContent = 'Aktualisiert: ' + new Date().toLocaleTimeString('de-DE');
  } catch (e) {
    showError('Fehler beim Laden des Depots: ' + e.message);
  } finally {
    showLoading(false);
  }
}

function renderPortfolio(p, alerts) {
  const summary = document.getElementById('depot-summary');
  const total = p.total_value || 0;
  const invested = total - p.cash;
  const returnPct = p.total_return_pct || 0;
  summary.innerHTML = `
    <div class="card"><div class="card-label">Depotwert</div><div class="card-value ${returnPct >= 0 ? 'green' : 'red'}">${fmtEur(total)}</div></div>
    <div class="card"><div class="card-label">Cash</div><div class="card-value neutral">${fmtEur(p.cash)}</div></div>
    <div class="card"><div class="card-label">Investiert</div><div class="card-value neutral">${fmtEur(invested)}</div></div>
    <div class="card"><div class="card-label">Gesamtrendite</div><div class="card-value ${returnPct >= 0 ? 'green' : 'red'}">${fmtPct(returnPct)}</div></div>
    <div class="card"><div class="card-label">Offene Positionen</div><div class="card-value neutral">${p.positions.length}</div></div>
  `;

  const posDiv = document.getElementById('depot-positions');
  if (p.positions.length === 0) {
    posDiv.innerHTML = '<div class="no-data">Keine offenen Positionen. Starte eine Analyse und kaufe virtuell.</div>';
  } else {
    let html = '<table><tr><th>Symbol</th><th>Einstieg</th><th>Aktuell</th><th>Stück</th><th>Investiert</th><th>Gewinn</th><th>SL</th><th>TP</th><th></th></tr>';
    for (const pos of p.positions) {
      const pnlClass = pos.unrealized_eur >= 0 ? 'pnl-pos' : 'pnl-neg';
      html += `<tr>
        <td><strong>${pos.symbol}</strong><br><span style="color:#8b949e;font-size:12px;">${symbolName(pos.symbol)}</span></td>
        <td>${fmtEur(pos.entry_price)}</td>
        <td>${fmtEur(pos.last_price)}</td>
        <td>${pos.shares}</td>
        <td>${fmtEur(pos.invested)}</td>
        <td class="${pnlClass}">${fmtEur(pos.unrealized_eur)} (${fmtPct(pos.unrealized_pct)})</td>
        <td>${pos.stop_loss ? fmtEur(pos.stop_loss) : '-'}</td>
        <td>${pos.take_profit ? fmtEur(pos.take_profit) : '-'}</td>
        <td><button class="btn-sell" onclick="quickSell('${pos.symbol}')">Verkaufen</button></td>
      </tr>`;
    }
    html += '</table>';
    posDiv.innerHTML = html;
  }

  // Historie
  const histDiv = document.getElementById('trade-history');
  const trades = (p.trades || []).slice().reverse();
  if (trades.length === 0) {
    histDiv.innerHTML = '<div class="no-data">Noch keine virtuellen Trades.</div>';
  } else {
    let html = '<table><tr><th>Zeit</th><th>Aktion</th><th>Symbol</th><th>Stück</th><th>Kurs</th><th>Betrag</th><th>Grund</th></tr>';
    for (const t of trades) {
      const badge = t.action === 'BUY' ? '<span class="badge buy">KAUF</span>' : '<span class="badge sell">VERKAUF</span>';
      html += `<tr>
        <td>${new Date(t.time).toLocaleString('de-DE')}</td>
        <td>${badge}</td>
        <td>${t.symbol}</td>
        <td>${Number(t.shares).toFixed(4)}</td>
        <td>${fmtEur(t.price)}</td>
        <td>${t.invested ? fmtEur(t.invested) : (t.proceeds ? fmtEur(t.proceeds) : fmtEur(t.pnl_eur))}</td>
        <td>${t.reason || '-'}</td>
      </tr>`;
    }
    html += '</table>';
    histDiv.innerHTML = html;
  }

  // Alerts
  if (alerts && alerts.length > 0) {
    let alertHtml = '';
    for (const a of alerts) {
      alertHtml += `<div class="alert-box">⚠️ <strong>${a.symbol}</strong>: ${a.msg}${a.price ? ' @ ' + fmtEur(a.price) : ''}</div>`;
    }
    summary.insertAdjacentHTML('afterend', alertHtml);
  }

  renderRealPositions(p);
}

function renderRealPositions(p) {
  const div = document.getElementById('real-positions');
  const positions = p.real_positions || [];
  if (positions.length === 0) {
    div.innerHTML = '<div class="no-data">Noch keine echten TR-Positionen eingetragen.</div>';
    return;
  }
  let html = '<table><tr><th>Symbol</th><th>Name</th><th>Stück</th><th>Einstieg</th><th>Investiert</th><th>Aktuell</th><th>Wert</th><th>P&L</th><th>Gekauft</th><th>Verkauft</th></tr>';
  for (const pos of positions) {
    const name = symbolName(pos.symbol);
    const pnlClass = pos.unrealized_eur >= 0 ? 'pnl-pos' : 'pnl-neg';
    const closedAt = pos.closed_at ? new Date(pos.closed_at).toLocaleString('de-DE') : '-';
    html += `<tr>
      <td><strong>${pos.symbol}</strong></td>
      <td>${name}</td>
      <td>${Number(pos.shares).toFixed(4)}</td>
      <td>${fmtEur(pos.entry_price)}</td>
      <td>${fmtEur(pos.invested)}</td>
      <td>${pos.last_price ? fmtEur(pos.last_price) : '-'}</td>
      <td>${pos.current_value ? fmtEur(pos.current_value) : '-'}</td>
      <td class="${pnlClass}">${pos.unrealized_eur ? fmtEur(pos.unrealized_eur) : '-'} (${pos.unrealized_pct ? fmtPct(pos.unrealized_pct) : '-'})</td>
      <td>${new Date(pos.opened_at).toLocaleString('de-DE')}</td>
      <td>${closedAt}</td>
    </tr>`;
  }
  html += '</table>';
  div.innerHTML = html;
}

function buyRealFromRec(symbol, preis) {
  showTab('real');
  document.getElementById('real-symbol').value = symbol;
  document.getElementById('real-action').value = 'buy';
  document.getElementById('real-price').value = preis ? Number(preis).toFixed(2) : '';
  document.getElementById('real-shares').value = '';
  document.getElementById('real-shares').focus();
  if (!preis) {
    fetch(`/api/price?symbol=${encodeURIComponent(symbol)}`)
      .then(r => r.json())
      .then(d => {
        if (d.ok && d.price) document.getElementById('real-price').value = Number(d.price).toFixed(2);
      });
  }
}

async function generateRecommendations(dryRun = false) {
  showLoading(true);
  showError('');
  const btn = document.getElementById('analyze-btn');
  btn.disabled = true; btn.textContent = dryRun ? '⏳ Analysiere (Dry-Run)...' : '⏳ Analysiere & handle...';
  try {
    const url = dryRun ? '/api/recommendations?dry_run=true' : '/api/recommendations';
    const r = await fetch(url, {method: 'POST'});
    const data = await r.json();
    currentRecs = (data.recommendations && data.recommendations.suggestions) || [];
    renderRecommendations(data.recommendations || data);
    if (!dryRun && data.actions && data.actions.length > 0) {
      const actionText = data.actions.map(a => `${a.action} ${a.symbol}`).join(', ');
      alert('🤖 Automatische Trades ausgeführt:\n' + actionText);
    }
    showTab('empfehlungen');
    loadPortfolio();
  } catch (e) {
    showError('Fehler bei der Analyse: ' + e.message);
  } finally {
    showLoading(false);
    btn.disabled = false; btn.textContent = '🔍 Analyse starten';
  }
}

async function loadRecommendations() {
  try {
    const r = await fetch('/api/recommendations');
    const data = await r.json();
    currentRecs = data.suggestions || [];
    renderRecommendations(data);
  } catch (e) {
    console.error(e);
  }
}

function renderRecommendations(data) {
  const container = document.getElementById('recommendations-container');
  if (!data.suggestions || data.suggestions.length === 0) {
    container.innerHTML = '<div class="suggest-loading">Keine klaren Handlungsempfehlungen aktuell. Marktlage ist neutral.</div>';
    return;
  }
  let html = '<div class="suggest-grid">';
  for (const s of data.suggestions) {
    const cls = s.direction === 'KAUF' ? 'buy' : (s.direction === 'VERKAUF' ? 'sell' : 'hold');
    html += `
    <div class="suggest-card ${cls}">
      <div class="suggest-header">
        <div>
          <div class="suggest-name">${s.name}</div>
          <div style="color:#8b949e;font-size:12px;">${s.symbol} · ${s.currency}</div>
        </div>
        <span class="badge ${s.risiko === 'niedrig' ? 'low' : (s.risiko === 'hoch' ? 'high' : 'medium')}">${s.risiko}</span>
      </div>
      <div class="suggest-direction ${cls}">${s.direction}</div>
      <div class="suggest-details">
        <div class="suggest-detail"><div class="suggest-detail-label">Aktueller Kurs</div><div class="suggest-detail-value">${fmtEur(s.preis)} (${fmtPct(s.change_pct)})</div></div>
        <div class="suggest-detail"><div class="suggest-detail-label">Score</div><div class="suggest-detail-value">${s.score}/100</div></div>
        <div class="suggest-detail"><div class="suggest-detail-label">Einstieg</div><div class="suggest-detail-value">${fmtEur(s.einstieg_von)} – ${fmtEur(s.einstieg_bis)}</div></div>
        <div class="suggest-detail"><div class="suggest-detail-label">Stop-Loss</div><div class="suggest-detail-value">${s.stop_loss ? fmtEur(s.stop_loss) : '-'}</div></div>
        <div class="suggest-detail"><div class="suggest-detail-label">Take-Profit</div><div class="suggest-detail-value">${s.take_profit ? fmtEur(s.take_profit) : '-'}</div></div>
        <div class="suggest-detail"><div class="suggest-detail-label">Sentiment</div><div class="suggest-detail-value">${s.sentiment > 0.1 ? '🟢' : (s.sentiment < -0.1 ? '🔴' : '⚪')} ${s.sentiment}</div></div>
      </div>
      ${s.llm_risk && !s.llm_risk.error ? `
      <div class="llm-risk-box">
        <div style="font-weight:600;margin-bottom:6px;">🧠 LLM Risiko: ${s.llm_risk.risk_level} (${s.llm_risk.risk_score}/10)</div>
        <div style="font-size:12px;color:#8b949e;margin-bottom:6px;">${s.llm_risk.summary}</div>
        ${s.llm_risk.main_risks && s.llm_risk.main_risks.length ? `<div style="font-size:12px;"><strong>Risiken:</strong> ${s.llm_risk.main_risks.join(', ')}</div>` : ''}
        ${s.llm_risk.catalyst ? `<div style="font-size:12px;margin-top:4px;"><strong>Kurstreiber:</strong> ${s.llm_risk.catalyst}</div>` : ''}
        ${s.llm_risk.max_position_pct ? `<div style="font-size:12px;margin-top:4px;"><strong>Max. Position:</strong> ${s.llm_risk.max_position_pct}</div>` : ''}
      </div>` : ''}
      <div class="suggest-reason">${s.begruendung}</div>
      <div class="score-bar"><div class="score-fill" style="width:${s.score}%"></div></div>
      ${s.direction === 'KAUF' ? `<button class="suggest-btn" onclick="buyFromRec('${s.symbol}')">💼 Virtuell kaufen</button>` : ''}
      ${s.direction === 'KAUF' ? `<button class="suggest-btn" onclick="buyRealFromRec('${s.symbol}', ${s.preis})" style="background:#28a745;margin-top:6px;">🏦 In reales TR Depot kaufen</button>` : ''}
      ${s.direction === 'VERKAUF' ? `<button class="suggest-btn" onclick="sellFromRec('${s.symbol}')">💼 Virtuell verkaufen</button>` : ''}
    </div>`;
  }
  html += '</div>';
  container.innerHTML = html;
}

async function buyFromRec(symbol) {
  showLoading(true);
  showError('');
  try {
    const r = await fetch('/api/trade/buy', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol: symbol})
    });
    const res = await r.json();
    if (res.ok) {
      alert(`✅ ${symbol} virtuell gekauft.`);
      loadPortfolio();
    } else {
      showError(res.error || 'Kauf fehlgeschlagen');
    }
  } catch (e) {
    showError('Fehler: ' + e.message);
  } finally {
    showLoading(false);
  }
}

async function sellFromRec(symbol) {
  showLoading(true);
  showError('');
  try {
    const r = await fetch('/api/trade/sell', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol: symbol})
    });
    const res = await r.json();
    if (res.ok) {
      alert(`✅ ${symbol} virtuell verkauft.`);
      loadPortfolio();
    } else {
      showError(res.error || 'Verkauf fehlgeschlagen');
    }
  } catch (e) {
    showError('Fehler: ' + e.message);
  } finally {
    showLoading(false);
  }
}

async function quickBuy(symbolOverride) {
  const symbol = symbolOverride || document.getElementById('quick-symbol').value.trim().toUpperCase();
  const amount = document.getElementById('quick-amount').value;
  if (!symbol) return alert('Symbol eingeben');
  showLoading(true);
  showError('');
  try {
    const r = await fetch('/api/trade/buy', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol, amount_eur: amount || null})
    });
    const res = await r.json();
    if (res.ok) { loadPortfolio(); document.getElementById('quick-symbol').value = ''; }
    else showError(res.error || 'Fehler beim Kauf');
  } catch (e) { showError(e.message); }
  finally { showLoading(false); }
}

async function quickSell(symbolOverride) {
  const symbol = symbolOverride || document.getElementById('quick-symbol').value.trim().toUpperCase();
  if (!symbol) return alert('Symbol eingeben');
  if (!confirm(`${symbol} wirklich aus dem virtuellen Depot verkaufen?`)) return;
  showLoading(true);
  showError('');
  try {
    const r = await fetch('/api/trade/sell', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol})
    });
    const res = await r.json();
    if (res.ok) loadPortfolio();
    else showError(res.error || 'Fehler beim Verkauf');
  } catch (e) { showError(e.message); }
  finally { showLoading(false); }
}

async function resetDepot() {
  if (!confirm('Virtuelles Depot wirklich auf 10.000 € zurücksetzen? Alle Positionen und Trades werden gelöscht.')) return;
  showLoading(true);
  try {
    await fetch('/api/reset_portfolio', {method: 'POST'});
    loadPortfolio();
  } catch (e) { showError(e.message); }
  finally { showLoading(false); }
}

async function reportRealTrade() {
  const symbol = document.getElementById('real-symbol').value.trim().toUpperCase();
  const action = document.getElementById('real-action').value;
  const shares = document.getElementById('real-shares').value;
  const price = document.getElementById('real-price').value;
  if (!symbol || !shares || !price) return alert('Bitte alle Felder ausfüllen');
  showLoading(true);
  showError('');
  try {
    const r = await fetch('/api/real_position', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol, action, shares, price})
    });
    const res = await r.json();
    if (res.ok) {
      alert('✅ Echtgeld-Trade gespeichert. Ich werde diese Position besonders im Blick behalten.');
      loadPortfolio();
    } else {
      showError(res.error || 'Fehler');
    }
  } catch (e) { showError(e.message); }
  finally { showLoading(false); }
}

// Initiales Laden
document.addEventListener('DOMContentLoaded', () => {
  loadPortfolio();
  loadRecommendations();
});

let autopilotPlanData = null;

async function runAutopilotDry() {
  showLoading(true);
  showError('');
  const btn = document.getElementById('autopilot-dry-btn');
  btn.disabled = true; btn.textContent = '⏳ Plane...';
  try {
    const r = await fetch('/api/recommendations?dry_run=true', {method: 'POST', headers: {'Content-Type': 'application/json'}});
    if (!r.ok) throw new Error('Serverantwort: ' + r.status);
    const data = await r.json();
    autopilotPlanData = data;
    renderAutopilotPlan(data);
    document.getElementById('autopilot-live-btn').disabled = false;
  } catch (e) {
    showError('Autopilot-Fehler: ' + e.message);
  } finally {
    showLoading(false);
    btn.disabled = false; btn.textContent = '🧪 Probelauf anzeigen';
  }
}

async function runAutopilotLive() {
  if (!autopilotPlanData || autopilotPlanData.actions.length === 0) {
    return alert('Kein Plan vorhanden. Bitte zuerst Probelauf starten.');
  }
  if (!confirm('Soll der Bot die geplanten Trades jetzt im virtuellen Depot ausführen?')) return;
  showLoading(true);
  showError('');
  const btn = document.getElementById('autopilot-live-btn');
  btn.disabled = true; btn.textContent = '⏳ Führe aus...';
  try {
    const r = await fetch('/api/recommendations?dry_run=false', {method: 'POST', headers: {'Content-Type': 'application/json'}});
    if (!r.ok) throw new Error('Serverantwort: ' + r.status);
    const data = await r.json();
    renderAutopilotResult(data);
    loadPortfolio();
  } catch (e) {
    showError('Fehler beim Ausführen: ' + e.message);
  } finally {
    showLoading(false);
    btn.textContent = '✅ Plan ausführen';
  }
}

function renderAutopilotPlan(data) {
  const div = document.getElementById('autopilot-plan');
  const resDiv = document.getElementById('autopilot-result');
  resDiv.innerHTML = '';
  if (!data.actions || data.actions.length === 0) {
    div.innerHTML = '<div class="no-data">Keine Aktionen geplant. Mögliche Gründe: keine klaren Signale, Cash-Reserve zu hoch oder max. Positionen erreicht.</div>';
    return;
  }
  let html = '<div class="section-title">Geplante Aktionen</div><table><tr><th>Aktion</th><th>Symbol</th><th>Menge/€</th><th>Kurs</th><th>SL</th><th>TP</th><th>Grund</th></tr>';
  for (const a of data.actions) {
    const badge = a.action === 'BUY' ? '<span class="badge buy">KAUF</span>' : '<span class="badge sell">VERKAUF</span>';
    html += `<tr>
      <td>${badge}</td>
      <td><strong>${a.symbol}</strong></td>
      <td>${a.action === 'BUY' ? fmtEur(a.amount_eur) : Number(a.shares).toFixed(4)}</td>
      <td>${fmtEur(a.expected_price)}</td>
      <td>${a.stop_loss ? fmtEur(a.stop_loss) : '-'}</td>
      <td>${a.take_profit ? fmtEur(a.take_profit) : '-'}</td>
      <td>${a.reason}</td>
    </tr>`;
  }
  html += '</table>';

  if (data.skipped && data.skipped.length > 0) {
    html += '<div class="section-title">Übersprungen</div><table><tr><th>Symbol</th><th>Grund</th></tr>';
    for (const s of data.skipped) {
      html += `<tr><td>${s.symbol}</td><td>${s.reason}</td></tr>`;
    }
    html += '</table>';
  }

  div.innerHTML = html;
}

function renderAutopilotResult(data) {
  const div = document.getElementById('autopilot-result');
  const after = data.portfolio_after || {};
  div.innerHTML = `
    <div class="alert-box">✅ Autopilot ausgeführt. Offene Positionen: ${after.positions_count}, Cash: ${fmtEur(after.cash)}, Depotwert: ${fmtEur(after.total_value)}, Rendite: ${fmtPct(after.total_return_pct)}</div>
  `;
  renderAutopilotPlan(data);
}

