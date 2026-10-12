/* GET only. Polling cannot place an order or start/reset an experiment. */
const astraEsc = v => String(v ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const astraMoney = n => Number.isFinite(n) ? n.toLocaleString('de-DE', {minimumFractionDigits:2,maximumFractionDigits:2}) + ' €' : '—';
const astraNum = (n, digits=2) => Number.isFinite(n) ? n.toLocaleString('de-DE',{maximumFractionDigits:digits}) : '—';
const astraTime = n => Number.isFinite(n) ? new Date(n*1000).toLocaleString('de-DE',{timeZone:'Europe/Berlin'}) : '—';
function astraBarHours(record) {
  const hours = record?.timeframe_hours ?? (record?.rules?.bar_seconds ?? record?.signal?.bar_seconds ?? record?.bar_seconds) / 3600;
  if (Number.isFinite(hours) && hours > 0) return hours;
  if (record?.version?.includes('4h')) return 4;
  if (record?.version?.includes('2h')) return 2;
  return null;
}
const astraRaster = record => astraBarHours(record) ? `${astraNum(astraBarHours(record))}H` : '—';
function astraTable(headers, rows, empty) {
  if (!rows.length) return `<div class="no-data">${astraEsc(empty)}</div>`;
  return '<table><thead><tr>'+headers.map(h=>'<th>'+astraEsc(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(cells=>'<tr>'+cells.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
}
function astraDetails(p) {
  const management = {version:p.management_version ?? p.version,rules:p.management_rules ?? p.rules};
  const hours = astraBarHours(management) ?? astraBarHours(p);
  const exitBars = management.rules?.exit_channel ?? 12;
  const holdHours = management.rules?.max_hold_hours ?? 336;
  const data = [
    `Version beim Kauf: ${p.version} · Kerzen: ${astraRaster(p)}`, `Kaufdatum: ${astraTime(p.opened_at)} (Berlin)`,
    `Ausstiegsregeln: ${management.version} · Kerzen: ${hours ? `${astraNum(hours)}H` : '—'}`,
    `Richtung: LONG · Hebel 1× · Menge (Modell): ${astraNum(p.quantity,8)}`,
    `Kaufkurs (mit Slippage): ${astraNum(p.entry_price,8)} USDT`,
    `Gesamtbelastung inkl. Kaufgebühr: ${astraMoney(p.entry_total)}`,
    `Kaufgebühr: ${astraMoney(p.entry_fee)} · Stop-Kurs: ${astraNum(p.stop_loss,8)} USDT`,
    `Geplantes Stop-Risiko inkl. Kosten: ${astraMoney(p.planned_risk)}`,
    `TP: kein fester Kurs; ${exitBars}-Kerzen-Kanalbruch${hours ? ` (${exitBars * hours}h)` : ''} / Zeit-Exit, maximal ${holdHours}h`,
    `Signal: ${astraTime(p.signal?.bar_end)} · EMA200 ${astraNum(p.signal?.ema200,8)} · ATR14 ${astraNum(p.signal?.atr14,8)}`,
    p.closed_at ? `Verkauf: ${astraTime(p.closed_at)} · Gebühr: ${astraMoney(p.exit_fee)} · ${p.close_reason}` : `Letzter Kurszeitpunkt: ${astraTime(p.quote_at)}`
  ];
  return '<details><summary>Details</summary>'+data.map(x=>'<div>'+astraEsc(x)+'</div>').join('')+'</details>';
}
function astraDecisionDetails(d) {
  const attempts = d.attempt_history || [];
  if (!attempts.length) return astraEsc(d.action);
  const rows = attempts.map(a => `<div>${astraEsc(astraTime(a.observed_at))} · Versuch ${astraEsc(a.attempt)}: ${astraEsc(a.action)}${a.data_error ? ' · '+astraEsc(a.data_error) : ''}</div>`).join('');
  const executions = (d.execution_history || []).map(e => `<div>${astraEsc(astraTime(e.observed_at))} · ${astraEsc(e.action)}</div>`).join('');
  return astraEsc(d.action) + `<details><summary>Prüfversuche (${attempts.length})${d.final ? ' · abgeschlossen' : ' · ausstehend'}</summary>${rows}${executions ? '<strong>Virtuelle Ausführungen</strong>'+executions : ''}</details>`;
}
function renderAstraUltra(data) {
  const el = id => document.getElementById('astra-'+id);
  if (!el('status')) return;
  if (!data?.ok) { el('status').textContent = '⚠️ Aktueller Vergleich nicht abrufbar. Vorherige Werte sind nicht als aktuell bestätigt.'; return; }
  if (!data.started) { el('status').textContent = 'Wartet auf den ersten frischen Krypto-Depotstand. Noch kein Testkonto gestartet.'; return; }
  const s = data.run;
  const returnPct = value => (value/s.initial_capital-1)*100;
  const monitorAt = s.monitor_success_at ?? s.last_success_at;
  const candleStatus = s.last_candle_success_at && s.last_candle_bar_end
    ? `Zuletzt vollständig bestätigte Kerze: ${astraTime(s.last_candle_bar_end)} · geprüft: ${astraTime(s.last_candle_success_at)}`
    : 'Kerzenprüfung: noch nicht separat bestätigt (ältere Historie bleibt erhalten)';
  const pending = s.pending_bar;
  const retryStatus = pending
    ? ` · Wiederholung für Kerze ${astraTime(pending.bar_end)}: Versuch ${pending.attempts}/${pending.max_attempts} · Frist ${astraTime(pending.expires_at)}${pending.waiting_symbols?.length ? ' · Daten ausstehend: '+pending.waiting_symbols.join(', ') : ''}`
    : '';
  el('status').textContent = `${data.enabled && data.healthy ? '🟢 Virtuelle Überwachung aktiv' : '⚠️ Virtuelle Überwachung / Signalprüfung nicht vollständig bestätigt'} · ${s.version} · ${astraRaster(s)} · Testbeginn: ${astraTime(s.started_at)} · letzter erfolgreicher Überwachungslauf: ${astraTime(monitorAt)} (Berlin) · ${candleStatus}${retryStatus}${s.candle_error ? ' · ⚠️ '+s.candle_error : ''}`;
  if (el('versions')) {
    const versions = (s.prior_versions || []).map(v => [v.version,astraRaster(v),astraTime(v.version_started_at ?? v.started_at),astraTime(v.ended_at),astraMoney(v.equity)]);
    versions.push([s.version,astraRaster(s),astraTime(s.version_started_at ?? s.started_at),'Aktiv','—']);
    el('versions').innerHTML = astraTable(['Version','Kerzen','Beginn (Berlin)','Ende (Berlin)','Depotwert beim Wechsel'],versions.map(r=>r.map(astraEsc)),'') +
      '<p style="color:#8b949e;font-size:12px;">Kapital und Historie werden beim Versionswechsel weitergeführt. Rendite, Rückgang und Vergleich beziehen sich auf den ursprünglichen Testbeginn und können mehrere Versionen enthalten – keine isolierte 2H-Rendite.</p>';
  }
  const cards = [['Ursprüngliches Startkapital',astraMoney(s.initial_capital)],['Astra-Depotwert · Modell netto',astraMoney(s.equity)],['Cash',astraMoney(s.cash)],['Rendite seit Testbeginn · alle Versionen',astraNum(returnPct(s.equity))+'%'],['Offene Positionen',s.positions.length]];
  el('summary').innerHTML = cards.map(([k,v])=>`<div class="summary-card"><div class="label">${astraEsc(k)}</div><div class="value">${astraEsc(v)}</div></div>`).join('');
  const baseValid = s.baseline_fresh && !s.baseline_invalid.length;
  const compare = [
    ['✨ Astra Ultra · Spot 1×',astraMoney(s.equity),astraNum(returnPct(s.equity))+'%',astraNum(s.max_drawdown_pct)+'%',s.closed_count],
    [`Bisheriges Krypto · ${s.baseline_last_strategy || s.baseline_strategy}`,baseValid?astraMoney(s.baseline_equity):'Nicht belastbar / veraltet',baseValid?astraNum(returnPct(s.baseline_equity))+'%':'—',astraNum(s.baseline_max_drawdown_pct)+'%',s.baseline_closed_count]
  ];
  el('comparison').innerHTML = astraTable(['Ansatz','Depotwert · Modell netto','Rendite seit Testbeginn','Max. beobachteter Rückgang','Geschlossene Trades seit Testbeginn'],compare.map(r=>r.map(astraEsc)),'') +
    `<p style="color:#8b949e;font-size:12px;">Startkapital beider Seiten: ${astraEsc(astraMoney(s.initial_capital))}. Referenz: ${s.baseline_inherited_count} übernommene Altpositionen, ${s.baseline_strategy_changes.length} Strategiewechsel. Unterschiedliche Risiken/Hebel – noch kein nachgewiesener Sieger. Astra-Planrisiko 0,5% pro Kauf, kein Garantielimit.</p>` +
    `<p style="color:#d29922;font-size:12px;">Verpasste Kerzenprüfungen (alle Versionen): ${s.missed_hours} · Kurs-/Datenfehler: ${s.data_errors} · Beobachtungslücken: ${s.observation_gaps}${s.last_error?' · '+astraEsc(s.last_error):''}${s.baseline_invalid.length?' · Referenzhistorie verändert; Vergleich ungültig.':''}</p>`;
  el('positions').innerHTML = astraTable(['Symbol','Kaufdatum (Berlin)','Einsatz','Entry / aktuell (USDT)','P&L · Modell netto','SL / TP','Details'],s.positions.map(p=>{
    const rules=p.rules || s.rules;
    const pnl=p.quantity*p.last_price*(1-rules.slippage_pct)*(1-rules.fee_pct)-p.entry_total;
    return [astraEsc(p.symbol)+' · LONG 1×',astraEsc(astraTime(p.opened_at)),astraEsc(astraMoney(p.entry_total)),astraEsc(astraNum(p.entry_price,8)+' / '+astraNum(p.last_price,8)),astraEsc(astraMoney(pnl)),astraEsc(astraNum(p.stop_loss,8)+' / Kanal-Exit'),astraDetails(p)];
  }),'Noch kein passendes Einstiegssignal. Cash zu halten ist eine bewusste Strategieentscheidung.');
  el('trades').innerHTML = astraTable(['Symbol','Kauf','Verkauf','P&L netto','Grund','Details'],s.trades.map(p=>[astraEsc(p.symbol),astraEsc(astraTime(p.opened_at)),astraEsc(astraTime(p.closed_at)),astraEsc(astraMoney(p.pnl_eur)),astraEsc(p.close_reason),astraDetails(p)]),'Noch keine abgeschlossenen Astra-Trades.');
  el('decisions').innerHTML = astraTable(['Kerzenschluss (Berlin)','Raster / Version','Symbol','Ergebnis','Einstiegssignal','EMA200','ATR14'],data.decisions.map(d=>[astraEsc(astraTime(d.bar_end)),astraEsc(astraRaster(d)+' · '+(d.version ?? 'Version unbekannt')),astraEsc(d.symbol),astraDecisionDetails(d),typeof d.enter === 'boolean' ? (d.enter?'Ja':'Nein') : '—',astraEsc(astraNum(d.ema200,6)),astraEsc(astraNum(d.atr14,6))]),`Die erste vollständige ${astraRaster(s)}-Kerze nach dem Versionsstart wird abgewartet.`);
  if (el('daily')) el('daily').innerHTML = astraTable(['Beobachtet (Berlin)','Astra · Modell netto','Referenz · Modell netto','Datenstatus'],(data.daily||[]).map(d=>[astraEsc(astraTime(d.at)),astraEsc(astraMoney(d.equity)),astraEsc(astraMoney(d.baseline_equity)),d.data_ok&&d.baseline_fresh?'Vollständig':'Unvollständig / veraltet']),'Noch keine Tagesstände.');
}
let astraLoading = false;
async function refreshAstraUltra() {
  if (astraLoading || !document.getElementById('astra-status')) return;
  astraLoading = true;
  try {
    const r = await fetch('/api/crypto/astra_ultra',{cache:'no-store',signal:AbortSignal.timeout(10000)});
    if (!r.ok) throw new Error('unavailable');
    renderAstraUltra(await r.json());
  } catch (_) { renderAstraUltra(null); }
  finally { astraLoading = false; }
}
document.addEventListener('DOMContentLoaded',()=>{
  refreshAstraUltra();
  setInterval(()=>{if(document.getElementById('panel-astra')?.classList.contains('active')) refreshAstraUltra();},30000);
});
