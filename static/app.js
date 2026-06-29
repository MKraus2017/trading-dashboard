let currentPortfolio = null;
let currentRecs = [];
let universeData = [];

function showTab(name) {
  const tabs = ['depot', 'real', 'empfehlungen', 'autopilot', 'historie-v', 'historie-r', 'vergleich', 'verbesserungen'];
  document.querySelectorAll('.tab').forEach((t, i) => {
    t.classList.toggle('active', tabs[i] === name);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  if (name === 'real') renderSymbolSelect();
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
  MSFT: 'Microsoft', 'NESN.SW': 'Nestlé', NESN: 'Nestlé', NVDA: 'NVIDIA',
  PLTR: 'Palantir', 'SAP': 'SAP', SMH: 'VanEck Semiconductor ETF',
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
    let html = '<table><tr><th>Symbol</th><th>Einstieg</th><th>Aktuell</th><th>Stück</th><th>Investiert</th><th>Gewinn</th><th>SL</th><th>TP</th><th></th></tr>';
    for (const pos of positions) {
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
      let html = '<table><tr><th>Symbol</th><th>Name</th><th>Stück</th><th>Einstieg</th><th>Investiert</th><th>Aktuell</th><th>Wert</th><th>P&L</th></tr>';
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
