/* GET only. Polling cannot place an order or start/reset an experiment. */
const astraEsc = v => String(v ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const astraMoney = n => Number.isFinite(n) ? n.toLocaleString('de-DE', {minimumFractionDigits:2,maximumFractionDigits:2}) + ' €' : '—';
const astraNum = (n, digits=2) => Number.isFinite(n) ? n.toLocaleString('de-DE',{maximumFractionDigits:digits}) : '—';
const astraTime = n => Number.isFinite(n) ? new Date(n*1000).toLocaleString('de-DE',{timeZone:'Europe/Berlin'}) : '—';
function astraTable(headers, rows, empty) {
  if (!rows.length) return `<div class="no-data">${astraEsc(empty)}</div>`;
  return '<table><thead><tr>'+headers.map(h=>'<th>'+astraEsc(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(cells=>'<tr>'+cells.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
}
function astraDetails(p) {
  const data = [
    `Version: ${p.version}`, `Kaufdatum: ${astraTime(p.opened_at)} (Berlin)`,
    `Richtung: LONG · Hebel 1× · Menge (Modell): ${astraNum(p.quantity,8)}`,
    `Kaufkurs (mit Slippage): ${astraNum(p.entry_price,8)} USDT`,
    `Gesamtbelastung inkl. Kaufgebühr: ${astraMoney(p.entry_total)}`,
    `Kaufgebühr: ${astraMoney(p.entry_fee)} · Stop-Kurs: ${astraNum(p.stop_loss,8)} USDT`,
    `Geplantes Stop-Risiko inkl. Kosten: ${astraMoney(p.planned_risk)}`,
    'TP: kein fester Kurs; 12-Kerzen-Kanalbruch (48h) / Zeit-Exit, maximal 336h',
    `Signal: ${astraTime(p.signal?.bar_end)} · EMA200 ${astraNum(p.signal?.ema200,8)} · ATR14 ${astraNum(p.signal?.atr14,8)}`,
    p.closed_at ? `Verkauf: ${astraTime(p.closed_at)} · Gebühr: ${astraMoney(p.exit_fee)} · ${p.close_reason}` : `Letzter Kurszeitpunkt: ${astraTime(p.quote_at)}`
  ];
  return '<details><summary>Details</summary>'+data.map(x=>'<div>'+astraEsc(x)+'</div>').join('')+'</details>';
}
function renderAstraUltra(data) {
  const el = id => document.getElementById('astra-'+id);
  if (!el('status')) return;
  if (!data?.ok) { el('status').textContent = '⚠️ Aktueller Vergleich nicht abrufbar. Vorherige Werte sind nicht als aktuell bestätigt.'; return; }
  if (!data.started) { el('status').textContent = 'Wartet auf den ersten frischen Krypto-Depotstand. Noch kein Testkonto gestartet.'; return; }
  const s = data.run;
  const returnPct = value => (value/s.initial_capital-1)*100;
  el('status').textContent = `${data.enabled && data.healthy ? '🟢 Virtuelle Überwachung aktiv' : '⚠️ Virtuelle Überwachung verzögert / nicht bestätigt'} · ${s.version} · Start: ${astraTime(s.started_at)} · letzte Prüfung: ${astraTime(s.last_success_at)} (Berlin)`;
  const cards = [['Startkapital',astraMoney(s.initial_capital)],['Astra-Depotwert · Modell netto',astraMoney(s.equity)],['Cash',astraMoney(s.cash)],['Rendite seit Start',astraNum(returnPct(s.equity))+'%'],['Offene Positionen',s.positions.length]];
  el('summary').innerHTML = cards.map(([k,v])=>`<div class="summary-card"><div class="label">${astraEsc(k)}</div><div class="value">${astraEsc(v)}</div></div>`).join('');
  const baseValid = s.baseline_fresh && !s.baseline_invalid.length;
  const compare = [
    ['✨ Astra Ultra · Spot 1×',astraMoney(s.equity),astraNum(returnPct(s.equity))+'%',astraNum(s.max_drawdown_pct)+'%',s.closed_count],
    [`Bisheriges Krypto · ${s.baseline_last_strategy || s.baseline_strategy}`,baseValid?astraMoney(s.baseline_equity):'Nicht belastbar / veraltet',baseValid?astraNum(returnPct(s.baseline_equity))+'%':'—',astraNum(s.baseline_max_drawdown_pct)+'%',s.baseline_closed_count]
  ];
  el('comparison').innerHTML = astraTable(['Ansatz','Depotwert · Modell netto','Rendite seit Start','Max. beobachteter Rückgang','Geschlossene Trades seit Start'],compare.map(r=>r.map(astraEsc)),'') +
    `<p style="color:#8b949e;font-size:12px;">Startkapital beider Seiten: ${astraEsc(astraMoney(s.initial_capital))}. Referenz: ${s.baseline_inherited_count} übernommene Altpositionen, ${s.baseline_strategy_changes.length} Strategiewechsel. Unterschiedliche Risiken/Hebel – noch kein nachgewiesener Sieger. Astra-Planrisiko 0,5% pro Kauf, kein Garantielimit.</p>` +
    `<p style="color:#d29922;font-size:12px;">Verpasste 4H-Prüfungen: ${s.missed_hours} · Kurs-/Datenfehler: ${s.data_errors} · Beobachtungslücken: ${s.observation_gaps}${s.last_error?' · '+astraEsc(s.last_error):''}${s.baseline_invalid.length?' · Referenzhistorie verändert; Vergleich ungültig.':''}</p>`;
  el('positions').innerHTML = astraTable(['Symbol','Kaufdatum (Berlin)','Einsatz','Entry / aktuell (USDT)','P&L · Modell netto','SL / TP','Details'],s.positions.map(p=>{
    const pnl=p.quantity*p.last_price*(1-s.rules.slippage_pct)*(1-s.rules.fee_pct)-p.entry_total;
    return [astraEsc(p.symbol)+' · LONG 1×',astraEsc(astraTime(p.opened_at)),astraEsc(astraMoney(p.entry_total)),astraEsc(astraNum(p.entry_price,8)+' / '+astraNum(p.last_price,8)),astraEsc(astraMoney(pnl)),astraEsc(astraNum(p.stop_loss,8)+' / Kanal-Exit'),astraDetails(p)];
  }),'Noch kein passendes Einstiegssignal. Cash zu halten ist eine bewusste Strategieentscheidung.');
  el('trades').innerHTML = astraTable(['Symbol','Kauf','Verkauf','P&L netto','Grund','Details'],s.trades.map(p=>[astraEsc(p.symbol),astraEsc(astraTime(p.opened_at)),astraEsc(astraTime(p.closed_at)),astraEsc(astraMoney(p.pnl_eur)),astraEsc(p.close_reason),astraDetails(p)]),'Noch keine abgeschlossenen Astra-Trades.');
  el('decisions').innerHTML = astraTable(['4H-Schluss (Berlin)','Symbol','Ergebnis','24-Kerzen-Ausbruch','EMA200','ATR14'],data.decisions.map(d=>[astraEsc(astraTime(d.bar_end)),astraEsc(d.symbol),astraEsc(d.action),d.enter?'Ja':'Nein',astraEsc(astraNum(d.ema200,6)),astraEsc(astraNum(d.atr14,6))]),'Die erste vollständige 4H-Kerze nach dem Start wird abgewartet.');
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
