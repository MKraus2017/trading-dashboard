/* Read-only polling. This view never calls trading, refresh/evaluation or Telegram endpoints. */
const shadowEsc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const shadowEuro = value => Number.isFinite(value) ? value.toLocaleString('de-DE', {minimumFractionDigits:2, maximumFractionDigits:2}) + ' €' : '—';
const shadowTime = seconds => Number.isFinite(seconds) ? new Date(seconds * 1000).toLocaleString('de-DE', {timeZone:'Europe/Berlin'}) : '—';
const shadowExclusions = {
  late_adoption:'Nachträglich aufgenommen', entry_not_observed:'Einstieg nicht vollständig beobachtet',
  entry_bar_missed:'Erste Kerze nicht beobachtet', observation_gap:'Beobachtungslücke',
  missed_bar:'Kerzenprüfung verpasst', data_error:'Marktdatenfehler', source_position_missing:'Quellposition fehlt / zurückgesetzt'
};

function renderCryptoExitShadow(data) {
  const status = document.getElementById('crypto-exit-shadow-status');
  const summary = document.getElementById('crypto-exit-shadow-summary');
  const table = document.getElementById('crypto-exit-shadow-rows');
  if (!status || !summary || !table) return;
  if (!data || !data.ok) {
    status.textContent = '⚠️ Vergleich derzeit nicht abrufbar. Alte Ergebnisse sind nicht als aktuell bestätigt.';
    status.style.color = '#d29922';
    return;
  }
  status.textContent = `${data.enabled && data.healthy ? '🟢 Beobachtung aktiv' : data.enabled ? '⚠️ Beobachtung noch nicht bestätigt / verzögert' : '⏸ Beobachtung deaktiviert'} · Nur Vergleich · letzte Prüfung: ${shadowTime(data.last_success_at)} (Berlin)`;
  status.style.color = data.enabled && data.healthy ? '#3fb950' : '#d29922';
  const s = data.summary;
  const complete = s.completed_pairs > 0;
  const cards = [
    ['Vollständige Vergleichspaare', String(s.completed_pairs)],
    ['Bisherige Ausstiege · Modell netto', complete ? shadowEuro(s.baseline_net_eur) : 'Noch offen'],
    ['Mit Volumen-Ausstieg · Modell netto', complete ? shadowEuro(s.shadow_net_eur) : 'Noch offen'],
    ['Differenz (Volumen − bisher)', complete ? shadowEuro(s.difference_eur) : 'Noch kein Ergebnis']
  ];
  summary.innerHTML = cards.map(([label,value]) => `<div class="summary-card"><div class="label">${shadowEsc(label)}</div><div class="value">${shadowEsc(value)}</div></div>`).join('') +
    `<p style="grid-column:1/-1;color:#8b949e;font-size:12px;">${s.total_records} beobachtete Trades · ${s.excluded} nicht für die Erfolgswertung geeignet. ` +
    `Größter beobachteter Trade-Rückgang (bisher / Volumen): ${complete ? shadowEsc(shadowEuro(s.baseline_max_trade_drawdown_eur)) + ' / ' + shadowEsc(shadowEuro(s.shadow_max_trade_drawdown_eur)) : 'noch kein Ergebnis'}.</p>`;
  if (!data.rows.length) {
    table.innerHTML = '<div class="no-data">Wartet auf virtuelle Positionen. Es werden keine Testkäufe eröffnet.</div>';
    return;
  }
  const exitCell = (exit, mark) => exit ? `${shadowEsc(shadowEuro(exit.net_eur))}<br><small>${shadowEsc(exit.reason)}<br>${shadowEsc(shadowTime(exit.at))}</small>` : `Offen<br><small>Modellbewertung: ${shadowEsc(shadowEuro(mark))}</small>`;
  table.innerHTML = '<table><thead><tr><th>Symbol / Einstieg</th><th>Beobachtung</th><th>Bisherige Strategie</th><th>Mit Volumen-Ausstieg</th><th>Differenz netto</th><th>Details</th></tr></thead><tbody>' + data.rows.map(r => {
    const finished = r.baseline_exit && r.shadow_exit;
    const exclusions = r.exclusions.map(x => shadowExclusions[x] || x).join(', ');
    const label = exclusions || (finished ? 'Vollständiges Paar' : 'Laufender Vergleich');
    const sig = (r.shadow_exit && r.shadow_exit.signal) || r.last_signal;
    const details = [
      `Einstieg: ${shadowTime(r.opened_at)} · ${r.entry_price}`,
      `Beobachtung ab: ${shadowTime(r.observed_from)}`,
      `Einsatz: ${shadowEuro(r.margin_eur)} · Hebel: ${r.leverage}×`,
      `Kostenannahmen je Seite: ${r.fee_bps / 100}% Gebühren + ${r.slippage_bps / 100}% Slippage`,
      sig ? `Kerzenschluss: ${shadowTime(sig.bar_end)} · Kurs: ${sig.close} · EMA20: ${sig.ema20.toFixed(6)} · Volumen: ${sig.relative_volume.toFixed(2)}× · Trendbruch: ${sig.crossed ? 'ja' : 'nein'}` : 'Noch keine abgeschlossene Kerze seit Beobachtungsbeginn ausgewertet.',
      r.shadow_exit ? `Alternativer Ausstiegskurs: ${r.shadow_exit.price} · Modellkosten: ${shadowEuro(r.shadow_exit.costs_eur)}` : 'Zusätzlicher Ausstieg: noch nicht erfolgt.',
      r.last_error ? `Letzte Datenmeldung: ${r.last_error}` : ''
    ].filter(Boolean).map(x => `<div>${shadowEsc(x)}</div>`).join('');
    return `<tr><td>${shadowEsc(r.symbol)} · ${shadowEsc(r.direction)}<br><small>${shadowEsc(shadowTime(r.opened_at))}</small></td>` +
      `<td>${shadowEsc(label)}</td><td>${exitCell(r.baseline_exit, r.mark_net_eur)}</td><td>${exitCell(r.shadow_exit, r.mark_net_eur)}</td>` +
      `<td>${finished ? shadowEsc(shadowEuro(r.shadow_exit.net_eur - r.baseline_exit.net_eur)) + (exclusions ? '<br><small>Nicht gewertet</small>' : '') : 'Noch nicht vergleichbar'}</td>` +
      `<td><details><summary>Details</summary>${details}</details></td></tr>`;
  }).join('') + '</tbody></table><p style="font-size:12px;color:#8b949e;">Anzeige: neueste 100 Trades. Die Gesamtauswertung berücksichtigt alle gespeicherten vollständigen Paare.</p>';
}

let shadowLoading = false;
async function refreshCryptoExitShadow() {
  if (!document.getElementById('crypto-exit-shadow-status') || shadowLoading) return;
  shadowLoading = true;
  try {
    const response = await fetch('/api/crypto/exit_shadow', {cache:'no-store', signal:AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error('unavailable');
    renderCryptoExitShadow(await response.json());
  } catch (_) {
    renderCryptoExitShadow(null);
  } finally {
    shadowLoading = false;
  }
}
document.addEventListener('DOMContentLoaded', () => {
  refreshCryptoExitShadow();
  setInterval(refreshCryptoExitShadow, 30000);
});
