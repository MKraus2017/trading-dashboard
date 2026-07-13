let currentPortfolio = null;
let currentRecs = [];
let universeData = [];

function showTab(name) {
  const tabs = ['depot', 'real', 'empfehlungen', 'autopilot', 'historie-v', 'historie-r', 'vergleich', 'verbesserungen', 'krypto', 'okxlive'];
  document.querySelectorAll('.tab').forEach((t, i) => {
    t.classList.toggle('active', tabs[i] === name);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  if (name === 'real') renderSymbolSelect();
  if (name === 'krypto' && !window._cryptoLoadedOnce) { window._cryptoLoadedOnce = true; loadCryptoPortfolio(); loadCryptoBacktestHistory(); }
  if (name === 'okxlive') { loadOkxLive(); loadOkxSpotHistory(); }
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
  AAPL: 'Apple', AMD: 'AMD', AMZN: 'Amazon', 'ASML': 'ASML Holding',
  AVGO: 'Broadcom', GOOGL: 'Alphabet', JPM: 'JPMorgan Chase',
  LMT: 'Lockheed Martin', MA: 'Mastercard', META: 'Meta Platforms',
  MSFT: 'Microsoft', NSRGY: 'Nestlé ADR (USD)', NVDA: 'NVIDIA',
  PLTR: 'Palantir', 'SAP': 'SAP', SMH: 'VanEck Semiconductor ETF',
  TSLA: 'Tesla', V: 'Visa', 'VUSA.AS': 'Vanguard S&P 500 ETF',
  LLY: 'Eli Lilly', NVO: 'Novo Nordisk ADR', NFLX: 'Netflix', COST: 'Costco',
  'SIE.DE': 'Siemens', 'ALV.DE': 'Allianz', 'AIR.PA': 'Airbus', 'MC.PA': 'LVMH'
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

function adviceBadge(advice) {
  if (!advice) return '';
  const map = {
    'gruen': {bg: '#2ea043', icon: '🟢', label: advice.advice || 'NACHKAUFEN'},
    'gelb':  {bg: '#bb8009', icon: '🟡', label: advice.advice || 'HALTEN'},
    'rot':   {bg: '#da3633', icon: '🔴', label: advice.advice || 'VERKAUFEN'}
  };
  const c = map[advice.color] || map['gelb'];
  const title = (advice.reason || '') + (advice.score != null ? ` (Score ${advice.score}/100)` : '');
  return `<span title="${title}" style="display:inline-block;padding:2px 8px;border-radius:10px;background:${c.bg};color:#fff;font-size:11px;font-weight:600;white-space:nowrap;">${c.icon} ${c.label}</span>`;
}

async function loadPortfolio() {
  showLoading(true);
  showError('');
  try {
    const r = await fetch('/api/portfolio');
    let data;
    try {
      data = await r.json();
    } catch (parseErr) {
      const text = await r.text();
      throw new Error(`Server antwortete mit HTTP ${r.status}: ${text.slice(0, 200)}`);
    }
    if (!data || data.ok === false) {
      throw new Error((data && data.error) || 'Leere/fehlerhafte Antwort vom Server');
    }
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
  const positions = p.positions || [];
  const realPositions = p.real_positions || [];
  const summary = document.getElementById('depot-summary');
  const total = p.total_value || 0;
  const invested = total - p.cash;
  const returnPct = p.total_return_pct || 0;
  summary.innerHTML = `
    <div class="card"><div class="card-label">Depotwert</div><div class="card-value ${returnPct >= 0 ? 'green' : 'red'}">${fmtEur(total)}</div></div>
    <div class="card"><div class="card-label">Cash</div><div class="card-value neutral">${fmtEur(p.cash)}</div></div>
    <div class="card"><div class="card-label">Investiert</div><div class="card-value neutral">${fmtEur(invested)}</div></div>
    <div class="card"><div class="card-label">Gesamtrendite</div><div class="card-value ${returnPct >= 0 ? 'green' : 'red'}">${fmtPct(returnPct)}</div></div>
    <div class="card"><div class="card-label">Offene Positionen</div><div class="card-value neutral">${positions.length}</div></div>
  `;

  const diag = p._diag || {};
  const statsLine = diag.eval_time_seconds !== undefined
    ? `Auswertung: ${diag.eval_time_seconds}s · Fetches: ${(diag.yahoo_fetches && diag.yahoo_fetches.fetches) || 0} · Cache-Hits: ${(diag.yahoo_fetches && diag.yahoo_fetches.cache_hits) || 0} · Fehler: ${(diag.yahoo_fetches && diag.yahoo_fetches.errors) || 0}`
    : '';
  if (statsLine) {
    summary.innerHTML += `<div class="card" style="grid-column:1/-1; padding:8px 12px; font-size:0.8rem; color:#8b949e;">${statsLine}</div>`;
  }

  const posDiv = document.getElementById('depot-positions');
  if (positions.length === 0) {
    posDiv.innerHTML = '<div class="no-data">Keine offenen Positionen. Starte eine Analyse und kaufe virtuell.</div>';
  } else {
    let html = '<table><tr><th>Symbol</th><th>Einstieg</th><th>Aktuell</th><th>Stück</th><th>Investiert</th><th>Gewinn</th><th>Empfehlung</th><th>SL</th><th>TP</th><th></th></tr>';
    for (const pos of positions) {
      const pnlClass = pos.unrealized_eur >= 0 ? 'pnl-pos' : 'pnl-neg';
      html += `<tr>
        <td><strong>${pos.symbol}</strong><br><span style="color:#8b949e;font-size:12px;">${symbolName(pos.symbol)}</span></td>
        <td>${fmtEur(pos.entry_price)}</td>
        <td>${fmtEur(pos.last_price)}</td>
        <td>${pos.shares}</td>
        <td>${fmtEur(pos.invested)}</td>
        <td class="${pnlClass}">${fmtEur(pos.unrealized_eur)} (${fmtPct(pos.unrealized_pct)})</td>
        <td>${adviceBadge(pos.advice)}</td>
        <td>${pos.stop_loss ? fmtEur(pos.stop_loss) : '-'}</td>
        <td>${pos.take_profit ? fmtEur(pos.take_profit) : '-'}</td>
        <td><button class="btn-sell" onclick="quickSell('${pos.symbol}')">Verkaufen</button></td>
      </tr>`;
    }
    html += '</table>';
    posDiv.innerHTML = html;
  }

  // Historie-V GuV
  const virtualGuV = p.virtual_guv || {};
  const virtualGuVDiv = document.getElementById('virtual-guv');
  if (virtualGuVDiv) {
    const totalClass = (virtualGuV.total_return || 0) >= 0 ? 'green' : 'red';
    virtualGuVDiv.innerHTML = `
      <div class="card"><div class="card-label">Investiert</div><div class="card-value neutral">${fmtEur(virtualGuV.invested)}</div></div>
      <div class="card"><div class="card-label">Aktueller Wert</div><div class="card-value neutral">${fmtEur(virtualGuV.current_value)}</div></div>
      <div class="card"><div class="card-label">Unrealisiert</div><div class="card-value ${(virtualGuV.unrealized || 0) >= 0 ? 'green' : 'red'}">${fmtEur(virtualGuV.unrealized)} (${fmtPct(virtualGuV.unrealized_pct)})</div></div>
      <div class="card"><div class="card-label">Realisiert</div><div class="card-value ${(virtualGuV.realized || 0) >= 0 ? 'green' : 'red'}">${fmtEur(virtualGuV.realized)}</div></div>
      <div class="card"><div class="card-label">Gesamtrendite</div><div class="card-value ${totalClass}">${fmtEur(virtualGuV.total_return)} (${fmtPct(virtualGuV.total_return_pct)})</div></div>
    `;
  }

  // Historie-V
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

  // Historie-R GuV
  const realGuV = p.real_guv || {};
  const guvDiv = document.getElementById('real-guv');
  if (guvDiv) {
    const totalClass = (realGuV.total_return || 0) >= 0 ? 'green' : 'red';
    guvDiv.innerHTML = `
      <div class="card"><div class="card-label">Investiert</div><div class="card-value neutral">${fmtEur(realGuV.invested)}</div></div>
      <div class="card"><div class="card-label">Aktueller Wert</div><div class="card-value neutral">${fmtEur(realGuV.current_value)}</div></div>
      <div class="card"><div class="card-label">Unrealisiert</div><div class="card-value ${(realGuV.unrealized || 0) >= 0 ? 'green' : 'red'}">${fmtEur(realGuV.unrealized)} (${fmtPct(realGuV.unrealized_pct)})</div></div>
      <div class="card"><div class="card-label">Realisiert</div><div class="card-value ${(realGuV.realized || 0) >= 0 ? 'green' : 'red'}">${fmtEur(realGuV.realized)}</div></div>
      <div class="card"><div class="card-label">Gesamtrendite</div><div class="card-value ${totalClass}">${fmtEur(realGuV.total_return)} (${fmtPct(realGuV.total_return_pct)})</div></div>
    `;
  }

  // Offene echte Positionen in Historie-R
  const realHistPositionsDiv = document.getElementById('real-historie-positions');
  if (realHistPositionsDiv) {
    if (!realPositions.length) {
      realHistPositionsDiv.innerHTML = '<div class="no-data">Keine offenen echten Positionen.</div>';
    } else {
      let html = '<table><tr><th>Symbol</th><th>Name</th><th>Stück</th><th>Einstieg</th><th>Investiert</th><th>Aktuell</th><th>Wert</th><th>P&L</th><th>Empfehlung</th></tr>';
      for (const pos of realPositions) {
        const pnlClass = (pos.unrealized_eur || 0) >= 0 ? 'pnl-pos' : 'pnl-neg';
        html += `<tr>
          <td><strong>${pos.symbol}</strong></td>
          <td>${symbolName(pos.symbol)}</td>
          <td>${Number(pos.shares).toFixed(4)}</td>
          <td>${fmtEur(pos.entry_price)}</td>
          <td>${fmtEur(pos.invested)}</td>
          <td>${pos.last_price ? fmtEur(pos.last_price) : '-'}</td>
          <td>${pos.current_value ? fmtEur(pos.current_value) : '-'}</td>
          <td class="${pnlClass}">${pos.unrealized_eur ? fmtEur(pos.unrealized_eur) : '-'} (${pos.unrealized_pct ? fmtPct(pos.unrealized_pct) : '-'})</td>
          <td>${adviceBadge(pos.advice)}</td>
        </tr>`;
      }
      html += '</table>';
      realHistPositionsDiv.innerHTML = html;
    }
  }

  // Echte Trades in Historie-R
  const realHistTradesDiv = document.getElementById('real-trade-history');
  if (realHistTradesDiv) {
    const realTrades = (p.real_trades || []).slice().reverse();
    if (!realTrades.length) {
      realHistTradesDiv.innerHTML = '<div class="no-data">Noch keine echten TR-Trades eingetragen.</div>';
    } else {
      let html = '<table><tr><th>Zeit</th><th>Aktion</th><th>Symbol</th><th>Stück</th><th>Kurs</th><th>Betrag</th></tr>';
      for (const t of realTrades) {
        const badge = t.action === 'BUY' ? '<span class="badge buy">KAUF</span>' : '<span class="badge sell">VERKAUF</span>';
        const amount = t.action === 'BUY' ? (t.invested || t.shares * t.price) : (t.shares * t.price);
        html += `<tr>
          <td>${new Date(t.time).toLocaleString('de-DE')}</td>
          <td>${badge}</td>
          <td>${t.symbol}</td>
          <td>${Number(t.shares).toFixed(4)}</td>
          <td>${fmtEur(t.price)}</td>
          <td>${fmtEur(amount)}</td>
        </tr>`;
      }
      html += '</table>';
      realHistTradesDiv.innerHTML = html;
    }
  }

  // Alerts
  if (alerts && alerts.length > 0) {
    let alertHtml = '';
    for (const a of alerts) {
      alertHtml += `<div class="alert-box">⚠️ <strong>${a.symbol}</strong>: ${a.msg}${a.price ? ' @ ' + fmtEur(a.price) : ''}</div>`;
    }
    summary.insertAdjacentHTML('afterend', alertHtml);
  }

  renderComparison(p);
  renderImprovements(p);
  renderRealPositions(p);
}

function renderRealPositions(p) {
  const div = document.getElementById('real-positions');
  const positions = p.real_positions || [];
  if (positions.length === 0) {
    div.innerHTML = '<div class="no-data">Noch keine echten TR-Positionen eingetragen.</div>';
    return;
  }
  let html = '<table><tr><th>Symbol</th><th>Name</th><th>Stück</th><th>Einstieg</th><th>Investiert</th><th>Aktuell</th><th>Wert</th><th>P&L</th><th>Empfehlung</th><th>Gekauft</th><th>Verkauft</th></tr>';
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
      <td>${adviceBadge(pos.advice)}</td>
      <td>${new Date(pos.opened_at).toLocaleString('de-DE')}</td>
      <td>${closedAt}</td>
    </tr>`;
  }
  html += '</table>';
  div.innerHTML = html;
}

function buyRealFromRec(symbol, preis) {
  showTab('real');
  setRealSymbol(symbol);
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
    let data;
    try {
      data = await r.json();
    } catch (parseErr) {
      const text = await r.text();
      throw new Error(`Server antwortete mit HTTP ${r.status}: ${text.slice(0, 200)}`);
    }
    if (!data) {
      throw new Error('Leere Antwort vom Server');
    }
    if (data.ok === false) {
      throw new Error(data.error || 'Analyse fehlgeschlagen');
    }
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

async function loadUniverse() {
  try {
    const r = await fetch('/api/universe');
    const data = await r.json();
    universeData = data.universe || [];
  } catch (e) {
    console.error(e);
  }
}

function renderRecommendations(data) {
  const container = document.getElementById('recommendations-container');
  // Nur Verkaufs-Empfehlungen anzeigen, wenn Position im realen Depot existiert (kein Leerverkauf möglich)
  const heldSymbols = new Set([
    ...(currentPortfolio && currentPortfolio.positions ? currentPortfolio.positions.map(p => p.symbol) : []),
    ...(currentPortfolio && currentPortfolio.real_positions ? currentPortfolio.real_positions.map(p => p.symbol) : [])
  ]);
  const suggestions = (data.suggestions || []).filter(s => {
    if (s.direction === 'VERKAUF') {
      return heldSymbols.has(s.symbol);
    }
    return true;
  });
  if (suggestions.length === 0) {
    container.innerHTML = '<div class="suggest-loading">Keine klaren Handlungsempfehlungen aktuell. Verkaufsempfehlungen werden nur angezeigt, wenn die Position im Depot existiert.</div>';
    return;
  }
  let html = '<div class="suggest-grid">';
  for (const s of suggestions) {
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
    const r = await fetch('/api/buy', {
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
    const r = await fetch('/api/sell', {
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
    const r = await fetch('/api/buy', {
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
    const r = await fetch('/api/sell', {
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

async function sendRecsToTelegram() {
  const btn = document.getElementById('send-recs-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Sende...';
  showError('');
  try {
    const r = await fetch('/api/send_recommendations_telegram', {method: 'POST'});
    const data = await r.json();
    if (data.ok) {
      alert(`✅ Empfehlungen gesendet:\n${data.buy_count} Kauf- / ${data.sell_count} Verkaufsempfehlungen`);
    } else {
      const details = data.telegram_response || {};
      const httpErr = details.status_code ? `HTTP ${details.status_code}: ` : '';
      const msg = details.error || details.description || data.error || 'Unbekannter Fehler';
      showError('Telegram-Fehler: ' + httpErr + msg);
      console.error('Telegram send failed:', data);
    }
  } catch (e) {
    showError('Fehler: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '📤 Empfehlungen an Telegram';
  }
}

async function analyzeRealPositions() {
  await analyzeRealPositionsInTab(false);
}

async function analyzeRealPositionsInTab(useLLM) {
  const btnId = useLLM ? 'analyze-real-llm-tab-btn' : 'analyze-real-tab-btn';
  const label = useLLM ? '🧠 KI-Analyse reale Positionen' : '📊 Reale Positionen analysieren';
  const btn = document.getElementById(btnId) || document.getElementById('analyze-real-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Analysiere...'; }
  showError('');
  try {
    const endpoint = useLLM ? '/api/analyze_real_positions_llm' : '/api/analyze_real_positions';
    const r = await fetch(endpoint, {method: 'POST'});
    let data;
    try {
      data = await r.json();
    } catch (parseErr) {
      const text = await r.text();
      throw new Error(`Server antwortete mit HTTP ${r.status}: ${text.slice(0, 200)}`);
    }
    if (!data) {
      throw new Error('Leere Antwort vom Server');
    }
    if (data.ok) {
      let html = `<div class="section-title">${useLLM ? '🧠 KI-Analyse' : '📊 Technische Analyse'} — Reale TR-Positionen</div>`;
      html += `<div style="margin-bottom:16px;color:#8b949e;">${data.summary}</div>`;
      for (const pos of data.results) {
        let adviceClass = pos.advice === 'VERKAUFEN' || pos.advice === 'VERKAUFEN / AVOID' ? 'sell' : (pos.advice.includes('NACHKAUFEN') ? 'buy' : '');
        html += `
        <div class="position-row">
          <div><b>${pos.symbol}</b> <span class="badge-${adviceClass}">${pos.advice}</span></div>
          ${useLLM ? `<div>KI-Verdict: ${pos.verdict ? pos.verdict.toUpperCase() : 'n/a'} | Risiko: ${pos.risk_level} (${pos.risk_score != null ? pos.risk_score : 'n/a'}/10) | Max: ${pos.max_position_pct}</div>` : `<div>Score: ${pos.score != null ? pos.score : 'n/a'}/100 | Aktuell: ${fmtEur(pos.price)} (${pos.pnl_pct > 0 ? '+' : ''}${pos.pnl_pct}%)</div>`}
          <div>Einstieg: ${fmtEur(pos.entry)} | Stücke: ${pos.shares}</div>
          ${useLLM ? '' : `<div>SL: ${fmtEur(pos.stop_loss)} | TP: ${fmtEur(pos.take_profit)}</div>`}
          ${useLLM && pos.main_risks && pos.main_risks.length ? `<div style="color:#f85149;">Risiken: ${pos.main_risks.join(', ')}</div>` : ''}
          ${useLLM && pos.catalyst ? `<div>Kurstreiber: ${pos.catalyst}</div>` : ''}
          <div style="color:#8b949e;">${useLLM ? pos.summary : pos.reason}</div>
        </div>`;
      }
      const panel = document.getElementById('real-analysis-result');
      panel.innerHTML = html;
      showTab('real');
      if (data.telegram_sent) {
        showError(`✅ ${useLLM ? 'KI-Analyse' : 'Analyse'} berechnet und an Telegram gesendet: ${data.summary}`);
      } else {
        showError(`✅ ${useLLM ? 'KI-Analyse' : 'Analyse'} berechnet. Telegram-Versand fehlgeschlagen oder deaktiviert.`);
      }
      setTimeout(() => showError(''), 7000);
    } else {
      showError(`❌ ${useLLM ? 'KI-Analyse' : 'Analyse'} fehlgeschlagen: ${data.error || JSON.stringify(data)}`);
    }
  } catch (e) {
    showError('Fehler: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
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
      setRealSymbol('');
      loadPortfolio();
    } else {
      showError(res.error || 'Fehler');
    }
  } catch (e) { showError(e.message); }
  finally { showLoading(false); }
}

function setRealSymbol(symbol) {
  document.getElementById('real-symbol').value = symbol;
  const search = document.getElementById('real-symbol-search');
  const item = universeData.find(u => u.symbol === symbol);
  search.value = symbol ? (item ? `${item.name} (${symbol})` : symbol) : '';
  document.getElementById('real-symbol-dropdown').style.display = 'none';
}

function renderSymbolSelect() {
  const dropdown = document.getElementById('real-symbol-dropdown');
  const search = document.getElementById('real-symbol-search');
  if (!dropdown || !search) return;
  filterSymbolSelect();
}

function filterSymbolSelect() {
  const dropdown = document.getElementById('real-symbol-dropdown');
  const search = document.getElementById('real-symbol-search');
  const term = search.value.trim().toLowerCase();
  if (!universeData.length) return;

  // Empfohlene Kauf-Symbole immer ganz oben
  const recBuySymbols = new Set(
    (currentRecs || []).filter(r => r.direction === 'KAUF').map(r => r.symbol)
  );
  const sorted = [...universeData].sort((a, b) => {
    const aRec = recBuySymbols.has(a.symbol);
    const bRec = recBuySymbols.has(b.symbol);
    if (aRec && !bRec) return -1;
    if (bRec && !aRec) return 1;
    return a.name.localeCompare(b.name);
  });

  const filtered = sorted.filter(
    u => u.symbol.toLowerCase().includes(term) || u.name.toLowerCase().includes(term)
  );

  if (!term && !filtered.length) {
    dropdown.style.display = 'none';
    return;
  }

  let html = '';
  let lastGroup = null;
  for (const u of filtered.slice(0, 100)) {
    const group = recBuySymbols.has(u.symbol) ? '⭐ Empfohlen' : 'Alle Aktien';
    if (group !== lastGroup) {
      html += `<div class="symbol-group">${group}</div>`;
      lastGroup = group;
    }
    html += `<div class="symbol-option" onclick="setRealSymbol('${u.symbol}')">
      <strong>${u.symbol}</strong> <span style="color:#8b949e">— ${u.name}</span>
    </div>`;
  }
  dropdown.innerHTML = html;
  dropdown.style.display = filtered.length ? 'block' : 'none';
}

// Initiales Laden
document.addEventListener('DOMContentLoaded', () => {
  loadPortfolio();
  loadRecommendations();
  loadUniverse();
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

function renderComparison(p) {
  const comp = p.comparison || {};
  const v = comp.virtual || {};
  const r = comp.real || {};
  const diff = comp.diff || {};
  const summaryDiv = document.getElementById('comparison-summary');
  if (summaryDiv) {
    summaryDiv.innerHTML = `
      <div class="card"><div class="card-label">V-Investiert</div><div class="card-value neutral">${fmtEur(v.invested)}</div></div>
      <div class="card"><div class="card-label">V-Wert</div><div class="card-value ${(v.total_return || 0) >= 0 ? 'green' : 'red'}">${fmtEur(v.current_value)} (${fmtEur(v.total_return)})</div></div>
      <div class="card"><div class="card-label">R-Investiert</div><div class="card-value neutral">${fmtEur(r.invested)}</div></div>
      <div class="card"><div class="card-label">R-Wert</div><div class="card-value ${(r.total_return || 0) >= 0 ? 'green' : 'red'}">${fmtEur(r.current_value)} (${fmtEur(r.total_return)})</div></div>
      <div class="card"><div class="card-label">V vs. R Differenz</div><div class="card-value ${(diff.total_return || 0) >= 0 ? (diff.total_return > 0 ? 'green' : 'neutral') : 'red'}">${fmtEur(diff.total_return)}</div></div>
    `;
  }

  const bySymbolDiv = document.getElementById('comparison-by-symbol');
  const virtSymbols = new Set(v.trades ? [] : []);
  const realSymbolMap = {};
  for (const pos of (p.real_positions || [])) realSymbolMap[pos.symbol] = pos;

  const rows = [];
  for (const pos of (p.positions || [])) {
    const realPos = realSymbolMap[pos.symbol];
    rows.push({
      symbol: pos.symbol,
      virt_invested: pos.invested || 0,
      virt_value: (pos.shares || 0) * (pos.last_price || pos.entry_price || 0),
      real_invested: realPos ? (realPos.invested || 0) : 0,
      real_value: realPos ? (realPos.current_value || 0) : 0,
      real_only: false
    });
  }
  for (const pos of (p.real_positions || [])) {
    const existing = rows.find(x => x.symbol === pos.symbol);
    if (existing) {
      existing.real_invested = pos.invested || 0;
      existing.real_value = pos.current_value || 0;
      continue;
    }
    rows.push({
      symbol: pos.symbol,
      virt_invested: 0,
      virt_value: 0,
      real_invested: pos.invested || 0,
      real_value: pos.current_value || 0,
      real_only: true
    });
  }

  if (!bySymbolDiv) return;
  if (rows.length === 0) {
    bySymbolDiv.innerHTML = '<div class="no-data">Keine Daten für Vergleich vorhanden.</div>';
    return;
  }
  let html = '<table><tr><th>Symbol</th><th>Name</th><th>V-Invest.</th><th>V-Wert</th><th>R-Invest.</th><th>R-Wert</th><th>V vs. R</th></tr>';
  for (const row of rows) {
    const diffVal = (row.virt_value - row.virt_invested) - (row.real_value - row.real_invested);
    const diffClass = diffVal > 0 ? 'green' : (diffVal < 0 ? 'red' : 'neutral');
    html += `<tr>
      <td><strong>${row.symbol}</strong></td>
      <td>${symbolName(row.symbol)}</td>
      <td>${fmtEur(row.virt_invested)}</td>
      <td>${fmtEur(row.virt_value)}</td>
      <td>${fmtEur(row.real_invested)}</td>
      <td>${fmtEur(row.real_value)}</td>
      <td class="${diffClass}">${fmtEur(diffVal)}</td>
    </tr>`;
  }
  html += '</table>';
  bySymbolDiv.innerHTML = html;
}

function renderImprovements(p) {
  const bt = p.backtest || {};
  const summaryDiv = document.getElementById('improvements-summary');
  if (summaryDiv) {
    summaryDiv.innerHTML = `
      <div class="card"><div class="card-label">Abgeschlossene V-Trades</div><div class="card-value neutral">${bt.completed_trades || 0}</div></div>
      <div class="card"><div class="card-label">Strategie-P&L</div><div class="card-value ${(bt.strategy_pnl || 0) >= 0 ? 'green' : 'red'}">${fmtEur(bt.strategy_pnl)}</div></div>
      <div class="card"><div class="card-label">Buy-&-Hold-P&L</div><div class="card-value ${(bt.buyhold_pnl || 0) >= 0 ? 'green' : 'red'}">${fmtEur(bt.buyhold_pnl)}</div></div>
      <div class="card"><div class="card-label">Alpha vs. B&H</div><div class="card-value ${(bt.alpha || 0) >= 0 ? 'green' : 'red'}">${fmtEur(bt.alpha)}</div></div>
      <div class="card"><div class="card-label">Win-Rate</div><div class="card-value neutral">${(bt.win_rate || 0).toFixed(1)}%</div></div>
    `;
  }

  const listDiv = document.getElementById('improvements-list');
  if (listDiv) {
    const items = bt.improvements || [];
    if (items.length === 0) {
      listDiv.innerHTML = '<div class="no-data">Noch keine Verbesserungsvorschläge verfügbar.</div>';
    } else {
      let html = '';
      for (const item of items) {
        html += `<div class="alert-box">${item}</div>`;
      }
      listDiv.innerHTML = html;
    }
  }

  const detailsDiv = document.getElementById('backtest-details');
  if (detailsDiv) {
    const details = bt.details || [];
    if (details.length === 0) {
      detailsDiv.innerHTML = '<div class="no-data">Noch keine abgeschlossenen virtuellen Trades für Backtesting.</div>';
    } else {
      let html = '<table><tr><th>Symbol</th><th>Kauf</th><th>Verkauf</th><th>Stück</th><th>Tage</th><th>Strategie-P&L</th><th>B&H-P&L</th></tr>';
      for (const d of details) {
        const cls = (d.strategy_pnl || 0) >= 0 ? 'pnl-pos' : 'pnl-neg';
        html += `<tr>
          <td><strong>${d.symbol}</strong></td>
          <td>${fmtEur(d.buy_price)}</td>
          <td>${fmtEur(d.sell_price)}</td>
          <td>${Number(d.shares).toFixed(4)}</td>
          <td>${d.days_held}</td>
          <td class="${cls}">${fmtEur(d.strategy_pnl)}</td>
          <td>${fmtEur(d.buyhold_pnl)}</td>
        </tr>`;
      }
      html += '</table>';
      detailsDiv.innerHTML = html;
    }
  }
}


// --- Historischer Strategie-Backtest ---
function renderFullBacktest(data) {
  const div = document.getElementById('full-backtest-result');
  if (!div) return;
  if (!data || !data.variants) {
    div.innerHTML = '<div class="no-data">Noch kein Backtest ausgeführt.</div>';
    return;
  }
  let html = `<div style="color:#8b949e;margin-bottom:10px;">Stand: ${data.updated ? new Date(data.updated).toLocaleString('de-DE') : '-'} · ${data.symbols_tested} Symbole · ${data.period}${data.cached ? ' · (gespeichertes Ergebnis)' : ''}</div>`;

  if (data.improvements && data.improvements.length) {
    html += '<div style="margin-bottom:14px;">';
    for (const imp of data.improvements) {
      html += `<div class="alert-box" style="margin-bottom:6px;">💡 ${imp}</div>`;
    }
    html += '</div>';
  }

  html += '<table><tr><th>Variante</th><th>Trades</th><th>Win-Rate</th><th>Ø Gewinn</th><th>Ø Verlust</th><th>Profit-Faktor</th><th>Gesamt-PnL</th><th>Max. DD</th><th>Ø Tage</th></tr>';
  for (const v of data.variants) {
    const isBest = v.name === data.best_variant;
    const pfClass = v.profit_factor >= 1.2 ? 'pnl-pos' : (v.profit_factor < 1.0 ? 'pnl-neg' : '');
    html += `<tr style="${isBest ? 'background:rgba(46,160,67,0.15);' : ''}">
      <td>${isBest ? '⭐ ' : ''}<strong>${v.name}</strong></td>
      <td>${v.trades}</td>
      <td>${v.win_rate}%</td>
      <td class="pnl-pos">${v.avg_win}%</td>
      <td class="pnl-neg">${v.avg_loss}%</td>
      <td class="${pfClass}">${v.profit_factor}</td>
      <td class="${v.total_pnl_pct >= 0 ? 'pnl-pos' : 'pnl-neg'}">${v.total_pnl_pct}%</td>
      <td>${v.max_drawdown_pct}%</td>
      <td>${v.avg_days}</td>
    </tr>`;
  }
  html += '</table>';
  html += '<div style="color:#8b949e;font-size:12px;margin-top:8px;">Hinweis: Simulation nur mit technischen Signalen (ohne News-Sentiment/LLM, da historisch nicht verfügbar). Profit-Faktor = Bruttogewinne / Bruttoverluste; >1.2 gilt als solide.</div>';
  div.innerHTML = html;
}

async function runFullBacktest() {
  const btn = document.getElementById('full-backtest-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Backtest läuft (kann 1-2 Min. dauern)...';
  showError('');
  try {
    const r = await fetch('/api/backtest', {method: 'POST'});
    const data = await r.json();
    if (data.ok) {
      renderFullBacktest(data);
    } else {
      showError('❌ Backtest fehlgeschlagen: ' + (data.error || 'Unbekannt'));
    }
  } catch (e) {
    showError('Fehler: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '🧪 Backtest jetzt ausführen';
  }
}

async function loadCachedBacktest() {
  try {
    const r = await fetch('/api/backtest');
    const data = await r.json();
    if (data.ok) renderFullBacktest(data);
  } catch (e) { /* still */ }
}

loadCachedBacktest();


// --- Backtest auf realen Positionen (Historie-R) ---
async function runRealBacktest() {
  const btn = document.getElementById('real-backtest-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Backtest läuft...';
  showError('');
  try {
    const r = await fetch('/api/backtest_real', {method: 'POST'});
    const data = await r.json();
    const div = document.getElementById('real-backtest-result');
    if (data.ok) {
      let html = `<div style="color:#8b949e;margin-bottom:10px;">Stand: ${new Date(data.updated).toLocaleString('de-DE')} · ${data.symbols.length} Symbole · ${data.period} · Parameter: Score≥${data.params.buy_threshold}, SL ${(data.params.stop_pct*100).toFixed(0)} %, RR ${data.params.rr_ratio}:1</div>`;
      if (data.hints && data.hints.length) {
        for (const h of data.hints) {
          html += `<div class="alert-box" style="margin-bottom:6px;">💡 ${h}</div>`;
        }
      }
      html += '<table><tr><th>Symbol</th><th>Trades</th><th>Win-Rate</th><th>Ø Gewinn</th><th>Ø Verlust</th><th>Profit-Faktor</th><th>Gesamt-PnL</th><th>Ø Tage</th></tr>';
      for (const s of data.per_symbol) {
        if (!s.trades) {
          html += `<tr><td><strong>${s.symbol}</strong><br><span style="color:#8b949e;font-size:12px;">${symbolName(s.symbol)}</span></td><td colspan="7" style="color:#8b949e;">${s.note || 'Keine Trades im Zeitraum'}</td></tr>`;
          continue;
        }
        const pfClass = s.profit_factor >= 1.2 ? 'pnl-pos' : (s.profit_factor < 1.0 ? 'pnl-neg' : '');
        html += `<tr>
          <td><strong>${s.symbol}</strong><br><span style="color:#8b949e;font-size:12px;">${symbolName(s.symbol)}</span></td>
          <td>${s.trades}</td>
          <td>${s.win_rate}%</td>
          <td class="pnl-pos">${s.avg_win}%</td>
          <td class="pnl-neg">${s.avg_loss}%</td>
          <td class="${pfClass}">${s.profit_factor}</td>
          <td class="${s.total_pnl_pct >= 0 ? 'pnl-pos' : 'pnl-neg'}">${s.total_pnl_pct}%</td>
          <td>${s.avg_days}</td>
        </tr>`;
      }
      const o = data.overall;
      html += `<tr style="background:rgba(56,139,253,0.1);font-weight:600;">
        <td>GESAMT</td><td>${o.trades}</td><td>${o.win_rate}%</td>
        <td class="pnl-pos">${o.avg_win}%</td><td class="pnl-neg">${o.avg_loss}%</td>
        <td class="${o.profit_factor >= 1.2 ? 'pnl-pos' : (o.profit_factor < 1.0 ? 'pnl-neg' : '')}">${o.profit_factor}</td>
        <td class="${o.total_pnl_pct >= 0 ? 'pnl-pos' : 'pnl-neg'}">${o.total_pnl_pct}%</td>
        <td>${o.avg_days}</td>
      </tr>`;
      html += '</table>';
      div.innerHTML = html;
    } else {
      div.innerHTML = `<div class="no-data">❌ ${data.error || 'Backtest fehlgeschlagen'}</div>`;
    }
  } catch (e) {
    showError('Fehler: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '🧪 Backtest auf reale Positionen';
  }
}

// --- Krypto-Tab (separates virtuelles Depot, OKX Live-Preise) ---

function fmtEur(v) {
  if (v === null || v === undefined) return '–';
  return v.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €';
}

async function loadCryptoPortfolio() {
  const summaryEl = document.getElementById('crypto-summary');
  const posEl = document.getElementById('crypto-positions');
  const histEl = document.getElementById('crypto-trade-history');
  try {
    const r = await fetch('/api/crypto/portfolio');
    let data;
    try { data = await r.json(); } catch (e) {
      showError('Krypto-Depot: Server-Fehler (kein JSON) — evtl. Timeout.');
      return;
    }
    if (!data.ok) {
      showError('Krypto-Depot Fehler: ' + (data.error || 'unbekannt'));
      return;
    }
    const p = data.portfolio;
    const haltedBanner = p.trading_halted
      ? `<div class="no-data" style="color:#f85149;background:rgba(248,81,73,0.1);border:1px solid #f85149;padding:10px;border-radius:6px;margin-bottom:12px;">
           🚨 <b>Handel pausiert</b> — Drawdown ${p.drawdown_pct || 0}% erreicht (Totalverlust-Schutz aktiv). Keine neuen Positionen, bis sich das Depot erholt.
         </div>`
      : '';
    summaryEl.innerHTML = `
      ${haltedBanner}
      <div class="summary-card"><div class="label">Depotwert</div><div class="value">${fmtEur(p.total_value ?? p.cash)}</div></div>
      <div class="summary-card"><div class="label">Cash (frei)</div><div class="value">${fmtEur(p.cash)}</div></div>
      <div class="summary-card"><div class="label">Rendite</div><div class="value ${(p.total_return_pct||0) >= 0 ? 'pnl-pos' : 'pnl-neg'}">${(p.total_return_pct||0).toFixed(2)}%</div></div>
      <div class="summary-card"><div class="label">Offene Positionen</div><div class="value">${(p.positions||[]).length}</div></div>
    `;

    if (data.events && data.events.length) {
      const closedMsgs = data.events.map(e => `${e.type === 'liquidation' ? '💥' : '✅'} ${e.symbol}: ${e.msg}`).join('<br>');
      posEl.insertAdjacentHTML('afterbegin', `<div class="no-data" style="color:#d29922;">${closedMsgs}</div>`);
    }

    const positions = p.positions || [];
    if (!positions.length) {
      posEl.innerHTML = '<div class="no-data">Keine offenen Krypto-Positionen.</div>';
    } else {
      let html = '<table><tr><th>Symbol</th><th>Richtung</th><th>Hebel</th><th>Einsatz</th><th>Entry</th><th>Aktuell</th><th>P&L</th><th>SL</th><th>Liquidation</th><th>Gehalten</th></tr>';
      positions.forEach(pos => {
        const pnlCls = (pos.unrealized_pct||0) >= 0 ? 'pnl-pos' : 'pnl-neg';
        const dirEmoji = pos.direction === 'LONG' ? '🟢' : '🔴';
        const trailTag = pos.trailing_active ? ' 🔒' : '';
        const heldHours = pos.opened_at_ts ? Math.round((Date.now()/1000 - pos.opened_at_ts) / 3600 * 10) / 10 : null;
        const heldStr = heldHours !== null ? `${heldHours}h` : '—';
        html += `<tr>
          <td>${pos.symbol}</td>
          <td>${dirEmoji} ${pos.direction}</td>
          <td>${pos.leverage}x</td>
          <td>${fmtEur(pos.margin_eur)}</td>
          <td>${pos.entry_price}</td>
          <td>${pos.last_price}</td>
          <td class="${pnlCls}">${(pos.unrealized_pct||0).toFixed(2)}% (${fmtEur(pos.unrealized_eur)})</td>
          <td>${pos.stop_loss ?? '—'}${trailTag}</td>
          <td style="color:#f85149;">${pos.liquidation_price}</td>
          <td>${heldStr}</td>
        </tr>`;
      });
      html += '</table>';
      posEl.innerHTML = html;
    }

    const trades = (p.trades || []).slice().reverse().slice(0, 30);
    if (!trades.length) {
      histEl.innerHTML = '<div class="no-data">Noch keine geschlossenen Krypto-Trades.</div>';
    } else {
      let html = '<table><tr><th>Symbol</th><th>Richtung</th><th>Hebel</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Grund</th><th>Geschlossen</th></tr>';
      trades.forEach(t => {
        const pnlCls = (t.pnl_eur||0) >= 0 ? 'pnl-pos' : 'pnl-neg';
        const liqTag = t.liquidated ? ' 💥' : '';
        html += `<tr>
          <td>${t.symbol}</td>
          <td>${t.direction}</td>
          <td>${t.leverage}x</td>
          <td>${t.entry_price}</td>
          <td>${t.exit_price}</td>
          <td class="${pnlCls}">${fmtEur(t.pnl_eur)} (${t.pnl_pct}%)${liqTag}</td>
          <td style="font-size:12px;color:#8b949e;">${t.close_reason || ''}</td>
          <td style="font-size:12px;">${new Date(t.closed_at).toLocaleString('de-DE')}</td>
        </tr>`;
      });
      html += '</table>';
      histEl.innerHTML = html;
    }
  } catch (e) {
    showError('Fehler beim Laden des Krypto-Depots: ' + e.message);
  }
}

async function resetCryptoDepot() {
  if (!confirm('Krypto-Depot wirklich auf 1.000 € zurücksetzen? Alle offenen Positionen und die Trade-Historie gehen verloren.')) return;
  try {
    const r = await fetch('/api/crypto/reset', { method: 'POST' });
    const data = await r.json();
    if (data.ok) {
      loadCryptoPortfolio();
    } else {
      showError('Reset fehlgeschlagen: ' + (data.error || 'unbekannt'));
    }
  } catch (e) {
    showError('Fehler beim Zurücksetzen: ' + e.message);
  }
}

async function runCryptoAnalysis(dryRun) {
  const btn = dryRun ? document.getElementById('crypto-dry-btn') : document.getElementById('crypto-live-btn');
  const recsEl = document.getElementById('crypto-recommendations');
  btn.disabled = true;
  btn.textContent = '⏳ Analysiere...';
  showLoading(true);
  showError('');
  try {
    const r = await fetch(`/api/crypto/auto_trade?dry_run=${dryRun}`, { method: 'POST' });
    let data;
    try { data = await r.json(); } catch (e) {
      showError('Krypto-Analyse: Server-Fehler (kein JSON) — evtl. Timeout.');
      return;
    }
    if (!data.ok) {
      showError('Krypto-Analyse Fehler: ' + (data.error || 'unbekannt'));
      return;
    }
    const suggestions = (data.recommendations && data.recommendations.suggestions) || [];
    if (!suggestions.length) {
      recsEl.innerHTML = '<div class="no-data">Keine klaren Signale aktuell.</div>';
    } else {
      let html = '<table><tr><th>Symbol</th><th>Richtung</th><th>Score</th><th>Hebel</th><th>Preis</th><th>SL</th><th>TP</th><th>Details</th></tr>';
      suggestions.forEach(s => {
        const dirEmoji = s.direction === 'LONG' ? '🟢' : (s.direction === 'SHORT' ? '🔴' : '🟡');
        html += `<tr>
          <td>${s.symbol} (${s.name})</td>
          <td>${dirEmoji} ${s.direction}</td>
          <td>${s.score}/100</td>
          <td>${s.leverage}x</td>
          <td>${s.price}</td>
          <td>${s.stop_loss ?? '–'}</td>
          <td>${s.take_profit ?? '–'}</td>
          <td style="font-size:12px;color:#8b949e;">${(s.details||[]).join(', ')}</td>
        </tr>`;
      });
      html += '</table>';
      recsEl.innerHTML = html;
    }

    if (data.actions && data.actions.length) {
      const actionsText = data.actions.map(a => `${a.action} ${a.symbol}`).join(', ');
      recsEl.insertAdjacentHTML('beforebegin', `<div class="no-data" style="color:#3fb950;">${dryRun ? '🧪 Probelauf-Ergebnis' : '✅ Ausgeführte Aktionen'}: ${actionsText}</div>`);
    }
    if (!dryRun) loadCryptoPortfolio();
  } catch (e) {
    showError('Fehler bei Krypto-Analyse: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = dryRun ? '🧪 Signale anzeigen (Probelauf)' : '✅ Analyse & Auto-Handel';
    showLoading(false);
  }
}

async function runCryptoBacktest() {
  const btn = document.getElementById('crypto-backtest-btn');
  const days = document.getElementById('crypto-backtest-days').value;
  const resultEl = document.getElementById('crypto-backtest-result');
  btn.disabled = true;
  btn.textContent = '⏳ Backtest läuft (kann 1-2 Min dauern)...';
  showLoading(true);
  showError('');
  try {
    const r = await fetch(`/api/crypto/backtest?days=${days}`, { method: 'POST' });
    let data;
    try { data = await r.json(); } catch (e) {
      showError('Krypto-Backtest: Server-Fehler (kein JSON) — evtl. Timeout.');
      return;
    }
    if (!data.ok) {
      resultEl.innerHTML = `<div class="no-data">❌ ${data.error || 'Backtest fehlgeschlagen'}</div>`;
      return;
    }
    let html = `<div class="no-data" style="color:#3fb950;">💡 ${data.recommendation}</div>`;
    html += `<table><tr><th>Variante</th><th>Trades</th><th>Win-Rate</th><th>Gesamt-PnL</th></tr>`;
    (data.variants || []).forEach((v, i) => {
      const isBest = data.best && v.name === data.best.name;
      const pnlCls = v.total_pnl_pct >= 0 ? 'pnl-pos' : 'pnl-neg';
      html += `<tr style="${isBest ? 'background:rgba(63,185,80,0.1);font-weight:600;' : ''}">
        <td>${isBest ? '⭐ ' : ''}${v.name}</td>
        <td>${v.total_trades}</td>
        <td>${v.win_rate}%</td>
        <td class="${pnlCls}">${v.total_pnl_pct}%</td>
      </tr>`;
    });
    html += '</table>';
    resultEl.innerHTML = html;
    loadCryptoBacktestHistory();
  } catch (e) {
    resultEl.innerHTML = `<div class="no-data">❌ Fehler: ${e.message}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = '🧪 Backtest jetzt ausführen';
    showLoading(false);
  }
}

async function loadCryptoBacktestHistory() {
  const el = document.getElementById('crypto-backtest-history');
  try {
    const r = await fetch('/api/crypto/backtest_history');
    const data = await r.json();
    if (!data.ok || !data.history || !data.history.length) {
      el.innerHTML = '<div class="no-data">Noch keine Backtest-Läufe gespeichert.</div>';
      return;
    }
    let html = '<table><tr><th>Datum</th><th>Zeitraum</th><th>Beste Variante</th><th>Win-Rate</th><th>PnL</th></tr>';
    data.history.forEach(h => {
      const best = h.results && h.results.best;
      const pnlCls = best && best.total_pnl_pct >= 0 ? 'pnl-pos' : 'pnl-neg';
      html += `<tr>
        <td style="font-size:12px;">${new Date(h.run_at).toLocaleString('de-DE')}</td>
        <td>${h.params.days} Tage</td>
        <td style="font-size:12px;">${best ? best.name : '–'}</td>
        <td>${best ? best.win_rate + '%' : '–'}</td>
        <td class="${pnlCls}">${best ? best.total_pnl_pct + '%' : '–'}</td>
      </tr>`;
    });
    html += '</table>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<div class="no-data">❌ Fehler beim Laden der Historie: ${e.message}</div>`;
  }
}

// --- OKX Live-Handel (ECHTES GELD) ---

async function loadOkxLive() {
  const summaryEl = document.getElementById('okxlive-summary');
  const balEl = document.getElementById('okxlive-balance');
  const posEl = document.getElementById('okxlive-positions');
  balEl.innerHTML = '<div class="no-data">⏳ Lade echtes OKX-Konto...</div>';
  posEl.innerHTML = '';
  try {
    const [balRes, posRes] = await Promise.all([
      fetch('/api/okx/balance').then(r => r.json()),
      fetch('/api/okx/positions').then(r => r.json()),
    ]);

    if (!balRes.ok) {
      balEl.innerHTML = `<div class="no-data">❌ ${balRes.error || 'Fehler beim Laden des Guthabens'}</div>`;
      summaryEl.innerHTML = '';
    } else {
      const usdc = balRes.balances.find(b => b.ccy === 'USDC' || b.ccy === 'USDT');
      const holdingsValue = posRes.ok ? posRes.positions.reduce((sum, p) => sum + (p.value_usdc || 0), 0) : 0;
      summaryEl.innerHTML = `
        <div class="summary-card"><div class="label">Freies Guthaben</div><div class="value">${usdc ? usdc.available.toFixed(2) + ' ' + usdc.ccy : '–'}</div></div>
        <div class="summary-card"><div class="label">Krypto-Bestände (Wert)</div><div class="value">${holdingsValue.toFixed(2)} USDC</div></div>
        <div class="summary-card"><div class="label">Gesamt</div><div class="value">${(usdc ? usdc.available : 0) + holdingsValue}${' USDC'}</div></div>
      `;
      let html = '<table><tr><th>Currency</th><th>Verfügbar</th><th>Gesamt</th></tr>';
      balRes.balances.forEach(b => {
        html += `<tr><td>${b.ccy}</td><td>${b.available.toFixed(6)}</td><td>${b.total.toFixed(6)}</td></tr>`;
      });
      html += '</table>';
      balEl.innerHTML = html;
    }

    if (!posRes.ok) {
      posEl.innerHTML = `<div class="no-data">❌ ${posRes.error || 'Fehler beim Laden der Bestände'}</div>`;
    } else if (!posRes.positions.length) {
      posEl.innerHTML = '<div class="no-data">Keine echten Krypto-Bestände (nur Spot, kein Hebel-Trading möglich).</div>';
    } else {
      let html = '<table><tr><th>Symbol</th><th>Menge</th><th>Einstieg</th><th>Live-Preis</th><th>Wert (USDC)</th><th>P&L</th><th></th></tr>';
            posRes.positions.forEach(p => {
              const pnlCls = p.pnl_usdc === null ? '' : (p.pnl_usdc >= 0 ? 'positive' : 'negative');
              const pnlText = p.pnl_usdc === null ? '–' : `${p.pnl_usdc >= 0 ? '+' : ''}${p.pnl_usdc.toFixed(2)} USDC (${p.pnl_pct >= 0 ? '+' : ''}${p.pnl_pct.toFixed(2)}%)`;
              html += `<tr>
                <td>${p.ccy}${!p.tracked ? ' ⚠️' : ''}</td>
                <td>${p.amount.toFixed(6)}</td>
                <td>${p.entry_price ? p.entry_price.toFixed(4) : '–'}</td>
                <td>${p.live_price ? p.live_price.toFixed(4) : '–'}</td>
                <td>${p.value_usdc ? p.value_usdc.toFixed(2) : '–'}</td>
                <td class="${pnlCls}">${pnlText}</td>
                <td><button class="refresh-btn" style="background:#f85149;" onclick="closeOkxPosition('${p.inst_id}','${p.ccy}')">🔴 Verkaufen</button></td>
              </tr>`;
            });
            html += '</table>';
            posEl.innerHTML = html;
          }
        } catch (e) {
          balEl.innerHTML = `<div class="no-data">❌ Fehler: ${e.message}</div>`;
  }
}

async function buyOkxSpot() {
  const symbol = document.getElementById('okxlive-buy-symbol').value.trim().toUpperCase();
  const amount = parseFloat(document.getElementById('okxlive-buy-amount').value);
  if (!symbol || !amount || amount <= 0) {
    alert('Bitte Symbol und gültigen USDC-Betrag angeben.');
    return;
  }
  if (!confirm(`ECHTEN Kauf platzieren?\n${symbol} für ${amount} USDC\n\nDies platziert eine echte Market-Order mit echtem Geld!`)) {
    return;
  }
  try {
    const res = await fetch('/api/okx/open_position', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: symbol, amount_usdc: amount }),
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('okxlive-positions').insertAdjacentHTML('afterbegin',
        `<div class="no-data" style="color:#3fb950;">✅ Kauf platziert (ID: ${data.order_id})</div>`);
      setTimeout(loadOkxLive, 2000);
    } else {
      document.getElementById('okxlive-positions').insertAdjacentHTML('afterbegin',
        `<div class="no-data" style="color:#f85149;">❌ Fehler: ${data.error}</div>`);
    }
  } catch (e) {
    alert('Fehler: ' + e.message);
  }
}

async function closeOkxPosition(instId, ccy) {
  if (!confirm(`ECHTEN Bestand wirklich verkaufen?\n${instId} (kompletter ${ccy}-Bestand)\n\nDies platziert eine echte Market-Order mit echtem Geld!`)) {
    return;
  }
  try {
    const res = await fetch('/api/okx/close_position', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ inst_id: instId, ccy: ccy }),
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('okxlive-positions').insertAdjacentHTML('afterbegin',
        `<div class="no-data" style="color:#3fb950;">✅ Verkauft (ID: ${data.order_id})</div>`);
      setTimeout(loadOkxLive, 2000);
    } else {
      document.getElementById('okxlive-positions').insertAdjacentHTML('afterbegin',
        `<div class="no-data" style="color:#f85149;">❌ Fehler: ${data.error}</div>`);
    }
  } catch (e) {
    alert('Fehler: ' + e.message);
  }
}




async function runOkxAutoTrade(dryRun) {
  const resEl = document.getElementById('okxlive-autotrade-result');
  if (!dryRun && !confirm('ECHTEN Auto-Trade-Zyklus jetzt ausführen?\n\nDer Bot kauft/verkauft automatisch basierend auf aktuellen Signalen mit ECHTEM Geld!')) {
    return;
  }
  resEl.innerHTML = `<div class="no-data">⏳ ${dryRun ? 'Probelauf' : 'Führe echten Auto-Trade aus'}...</div>`;
  try {
    const res = await fetch(`/api/okx/auto_trade?dry_run=${dryRun}`, { method: 'POST' });
    const data = await res.json();
    if (!data.ok) {
      resEl.innerHTML = `<div class="no-data">❌ ${data.error}</div>`;
      return;
    }
    if (!data.actions.length) {
      resEl.innerHTML = '<div class="no-data">Keine Aktionen (keine passenden Signale oder Limits erreicht).</div>';
    } else {
      let html = '<table><tr><th>Aktion</th><th>Symbol</th><th>Details</th></tr>';
      data.actions.forEach(a => {
        let details = '';
        if (a.action === 'WOULD-BUY' || a.action === 'BOUGHT') details = `${a.amount_usdc} USDC (Score ${a.score})`;
        else if (a.action === 'SOLD') details = `${a.reason} | P&L: ${a.pnl_usdc.toFixed(2)} USDC (${a.pnl_pct.toFixed(2)}%)`;
        else if (a.error) details = `Fehler: ${a.error}`;
        else if (a.reason) details = a.reason;
        html += `<tr><td>${a.action}</td><td>${a.symbol || '–'}</td><td>${details}</td></tr>`;
      });
      html += '</table>';
      resEl.innerHTML = html;
    }
    if (!dryRun) setTimeout(loadOkxLive, 2000);
  } catch (e) {
    resEl.innerHTML = `<div class="no-data">❌ Fehler: ${e.message}</div>`;
  }
}

async function loadOkxSpotHistory() {
  const el = document.getElementById('okxlive-history');
  el.innerHTML = '<div class="no-data">⏳ Lade Historie...</div>';
  try {
    const res = await fetch('/api/okx/spot_history');
    const data = await res.json();
    if (!data.ok) {
      el.innerHTML = `<div class="no-data">❌ ${data.error}</div>`;
      return;
    }
    let html = '';
    if (data.open_positions.length) {
      html += '<div class="section-title" style="font-size:14px;">Offene automatisierte Positionen</div>';
      html += '<table><tr><th>Symbol</th><th>Einstieg</th><th>Menge</th><th>Investiert</th><th>SL</th><th>TP</th></tr>';
      data.open_positions.forEach(p => {
        html += `<tr><td>${p.symbol}</td><td>${p.entry_price}</td><td>${p.amount_base}</td><td>${p.amount_usdc} USDC</td><td>${p.stop_loss || '–'}</td><td>${p.take_profit || '–'}</td></tr>`;
      });
      html += '</table>';
    }
    if (data.history.length) {
      html += '<div class="section-title" style="font-size:14px;margin-top:12px;">Abgeschlossene Trades</div>';
      html += '<table><tr><th>Symbol</th><th>Einstieg</th><th>Ausstieg</th><th>Grund</th><th>P&L</th></tr>';
      data.history.forEach(t => {
        const pnlCls = t.pnl_usdc >= 0 ? 'positive' : 'negative';
        html += `<tr><td>${t.symbol}</td><td>${t.entry_price}</td><td>${t.exit_price}</td><td>${t.close_reason}</td><td class="${pnlCls}">${t.pnl_usdc.toFixed(2)} USDC (${t.pnl_pct.toFixed(2)}%)</td></tr>`;
      });
      html += '</table>';
    }
    el.innerHTML = html || '<div class="no-data">Noch keine automatisierten Trades.</div>';
  } catch (e) {
    el.innerHTML = `<div class="no-data">❌ Fehler: ${e.message}</div>`;
  }
}
