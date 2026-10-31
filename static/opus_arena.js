/* GET only. Polling cannot place an order or start/reset the Opus experiment. */
const opusEsc = v => String(v ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const opusMoney = n => Number.isFinite(n) ? n.toLocaleString('de-DE', {minimumFractionDigits:2,maximumFractionDigits:2}) + ' €' : '—';
const opusNum = (n, digits=2) => Number.isFinite(n) ? n.toLocaleString('de-DE',{maximumFractionDigits:digits}) : '—';
const opusPct = n => Number.isFinite(n) ? (n > 0 ? '+' : '') + opusNum(n) + '%' : '—';
const opusTime = n => Number.isFinite(n) ? new Date(n*1000).toLocaleString('de-DE',{timeZone:'Europe/Berlin'}) : '—';
const OPUS_ICONS = {opus:'🧠', astra:'✨', krypto:'🪙'};

function opusTable(headers, rows, empty) {
  if (!rows.length) return `<div class="no-data">${opusEsc(empty)}</div>`;
  return '<table><thead><tr>'+headers.map(h=>'<th>'+opusEsc(h)+'</th>').join('')+'</tr></thead><tbody>'+
    rows.map(cells=>'<tr>'+cells.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
}
function opusYesNo(v) { return typeof v === 'boolean' ? (v ? 'Ja' : 'Nein') : 'nicht bestätigt'; }
function opusSignalEvidence(d) {
  if (d.close === undefined) return '';
  const lines = [
    `Signal-Schlusskurs: ${opusNum(d.close,8)} USDT`,
    `Aufwärtstrend (Schluss > EMA200 ${opusNum(d.ema200,8)} und EMA50 ${opusNum(d.ema50,8)} > EMA200): ${opusYesNo(d.trend_ok)}`,
    `Rücksetzer (tiefster RSI14 der 4 Kerzen davor: ${opusNum(d.rsi_pullback_min,1)} ≤ 40): ${opusYesNo(d.pullback_ok)}`,
    `Umkehr (grüne Kerze, Schluss über Vorkerzen-Hoch ${opusNum(d.prev_high,8)}): ${opusYesNo(d.reversal_ok)}`,
    `RSI14 aktuell: ${opusNum(d.rsi14,1)} · ATR14: ${opusNum(d.atr14,8)}`,
    d.enter ? 'Einstiegssignal erfüllt. Datenfrische und Risikobudget entscheiden zusätzlich.' : 'Kein Einstiegssignal.'
  ];
  return '<details><summary>Signalprüfung</summary>'+lines.map(x=>'<div>'+opusEsc(x)+'</div>').join('')+'</details>';
}
function opusDetails(p) {
  const lines = [
    `Kaufdatum: ${opusTime(p.opened_at)} (Berlin) · LONG 1× · Menge (Modell): ${opusNum(p.quantity,8)}`,
    `Kaufkurs inkl. Slippage: ${opusNum(p.entry_price,8)} USDT · Gesamtbelastung: ${opusMoney(p.entry_total)}`,
    `Start-Stop (1,5× ATR): ${opusNum(p.initial_stop,8)} · aktueller Stop: ${opusNum(p.stop_loss,8)}`,
    `Höchstkurs seit Kauf: ${opusNum(p.peak_price,8)} · geplantes Risiko: ${opusMoney(p.planned_risk)}`,
    p.closed_at ? `Verkauf: ${opusTime(p.closed_at)} · ${p.close_reason} · Gebühr ${opusMoney(p.exit_fee)}` : `Letzter Kurs: ${opusTime(p.quote_at)}`
  ];
  return '<details><summary>Details</summary>'+lines.map(x=>'<div>'+opusEsc(x)+'</div>').join('')+opusSignalEvidence(p.signal || {})+'</details>';
}
function renderOpusArena(data) {
  const el = id => document.getElementById('opus-'+id);
  if (!el('status')) return;
  if (!data?.ok) { el('status').textContent = '⚠️ Opus-Stand nicht abrufbar. Vorherige Werte sind nicht als aktuell bestätigt.'; return; }
  if (!data.started) {
    el('status').textContent = data.enabled === false
      ? '⏸ Opus-Experiment ist deaktiviert (OPUS_ARENA_ENABLED=0).'
      : 'Wartet auf frische Depotstände von Krypto UND Astra. Noch kein Opus-Testkonto gestartet.';
    ['leaderboard','summary','positions','trades','decisions','daily'].forEach(k => { if (el(k)) el(k).innerHTML = ''; });
    return;
  }
  const s = data.run;
  const candle = s.last_candle_bar_end ? `Zuletzt geprüfte Kerze: ${opusTime(s.last_candle_bar_end)}` : 'Erste 2H-Kerze nach Start wird abgewartet';
  const retry = s.pending ? ` · Wiederholung Kerze ${opusTime(s.pending.bar_end)}: Versuch ${s.pending.attempts}` : '';
  el('status').textContent = `${data.enabled && data.healthy ? '🟢 Virtuelle Überwachung aktiv' : '⚠️ Überwachung / Kerzenprüfung nicht vollständig bestätigt'} · ${s.version} · Start: ${opusTime(s.started_at)} · letzter Überwachungslauf: ${opusTime(s.monitor_success_at)} (Berlin) · ${candle}${retry}${s.last_error ? ' · ⚠️ '+s.last_error : ''}`;

  const board = (data.leaderboard || []).map(r => {
    const medal = !r.valid ? '—' : ['🥇','🥈','🥉'][r.rank-1] || r.rank;
    const state = !r.valid ? 'Vergleich ungültig (Reset/Neustart)' : r.fresh ? 'Aktuell' : 'Veraltet';
    return [opusEsc(medal), opusEsc((OPUS_ICONS[r.key]||'')+' '+r.name), opusEsc(opusMoney(r.start_equity)), opusEsc(opusMoney(r.equity)),
            `<strong>${opusEsc(opusPct(r.return_pct))}</strong>`, opusEsc(opusNum(r.max_drawdown_pct)+'%'), opusEsc(state)];
  });
  el('leaderboard').innerHTML = opusTable(['Platz','Teilnehmer','Wert bei Opus-Start','Aktueller Wert','Rendite seit Opus-Start','Max. Rückgang seit Opus-Start','Status'], board, '') +
    `<p style="color:#8b949e;font-size:12px;">Opus startete mit ${opusEsc(opusMoney(s.initial_capital))} = Durchschnitt aus Krypto (${opusEsc(opusMoney(s.seed?.krypto_equity))}) und Astra (${opusEsc(opusMoney(s.seed?.astra_equity))}) am ${opusEsc(opusTime(s.started_at))}. Alle drei werden ab diesem Zeitpunkt prozentual verglichen; frühere Gewinne/Verluste zählen nicht. Unterschiedliche Hebel, Coins und Risiken – noch kein nachgewiesener Sieger.</p>`;

  const cards = [['Startkapital',opusMoney(s.initial_capital)],['Opus-Depotwert · Modell netto',opusMoney(s.equity)],['Cash',opusMoney(s.cash)],
    ['Rendite seit Start',opusPct((s.equity/s.initial_capital-1)*100)],['Offene Positionen',s.positions.length],
    ['Abgeschlossene Trades',s.closed_count],['Trefferquote',Number.isFinite(s.win_rate)?opusNum(s.win_rate,0)+'%':'—'],
    ['Gebühren gesamt',opusMoney(s.fees_paid)]];
  el('summary').innerHTML = cards.map(([k,v])=>`<div class="summary-card"><div class="label">${opusEsc(k)}</div><div class="value">${opusEsc(v)}</div></div>`).join('');

  el('positions').innerHTML = opusTable(['Symbol','Kaufdatum (Berlin)','Einsatz','Entry / aktuell (USDT)','P&L · Modell netto','Stop','Details'], s.positions.map(p => {
    const pnl = p.quantity*p.last_price*(1-s.rules.slippage_pct)*(1-s.rules.fee_pct)-p.entry_total;
    const trailing = p.stop_loss > p.initial_stop ? ' (nachgezogen)' : '';
    return [opusEsc(p.symbol+' · LONG 1×'), opusEsc(opusTime(p.opened_at)), opusEsc(opusMoney(p.entry_total)),
            opusEsc(opusNum(p.entry_price,8)+' / '+opusNum(p.last_price,8)), opusEsc(opusMoney(pnl)), opusEsc(opusNum(p.stop_loss,8)+trailing), opusDetails(p)];
  }), 'Kein Einstieg: Opus wartet auf einen bestätigten Rücksetzer im Aufwärtstrend.');
  el('trades').innerHTML = opusTable(['Symbol','Kauf','Verkauf','P&L netto','Grund','Details'], s.trades.map(p =>
    [opusEsc(p.symbol), opusEsc(opusTime(p.opened_at)), opusEsc(opusTime(p.closed_at)), opusEsc(opusMoney(p.pnl_eur)+' ('+opusPct(p.pnl_pct)+')'), opusEsc(p.close_reason), opusDetails(p)]),
    'Noch keine abgeschlossenen Opus-Trades.');
  el('decisions').innerHTML = opusTable(['Kerzenschluss (Berlin)','Symbol','Ergebnis','Signal','RSI-Tief','Prüfdetails'], (data.decisions||[]).map(d =>
    [opusEsc(opusTime(d.bar_end)), opusEsc(d.symbol), opusEsc(d.action), typeof d.enter==='boolean'?(d.enter?'Ja':'Nein'):'—', opusEsc(opusNum(d.rsi_pullback_min,1)), opusSignalEvidence(d)]),
    'Die erste vollständige 2H-Kerze nach dem Start wird abgewartet.');
  if (el('daily')) el('daily').innerHTML = opusTable(['Beobachtet (Berlin)','🧠 Opus','✨ Astra','🪙 Krypto','Datenstatus'], (data.daily||[]).map(d =>
    [opusEsc(opusTime(d.at)), opusEsc(opusMoney(d.equity)), opusEsc(opusMoney(d.astra_equity)), opusEsc(opusMoney(d.krypto_equity)),
     d.data_ok && d.astra_fresh && d.krypto_fresh ? 'Vollständig' : 'Unvollständig / veraltet']), 'Noch keine Tagesstände.');
}
let opusLoading = false;
async function refreshOpusArena() {
  if (opusLoading || !document.getElementById('opus-status')) return;
  opusLoading = true;
  try {
    const r = await fetch('/api/crypto/opus_arena',{cache:'no-store',signal:AbortSignal.timeout(10000)});
    if (!r.ok) throw new Error('unavailable');
    renderOpusArena(await r.json());
  } catch (_) { renderOpusArena(null); }
  finally { opusLoading = false; }
}
document.addEventListener('DOMContentLoaded',()=>{
  refreshOpusArena();
  setInterval(()=>{if(document.getElementById('panel-opus')?.classList.contains('active')) refreshOpusArena();},30000);
});
