# Aktien-Prüfung vom 11.10.2026

## Umfang und Ausgangsstand

Geprüft wurde das virtuelle Aktien-Depot des Trading Bot Dashboards, nicht
DepotLab, Krypto oder ein echtes Brokerkonto. Die folgenden Zeilenangaben beziehen
sich auf den unveränderten Ausgangsstand `337206e87e53eb966159ef4c23949a4a8620d891`.
Bereits vorhandene lokale Änderungen in `analyzer/signals.py`, `yahoo_client.py`
und anderen Dateien wurden bei der Prüfung nicht als veröffentlichter Stand
behandelt. Dieser Bericht ist kein Nachweis einer profitablen Strategie.

## Wesentliche Befunde am Ausgangsstand

1. **Trailing existiert, wird aber nicht angezeigt.** `config.py` aktiviert den
   Chandelier-Exit mit ATR(22) × 2. `analyzer/portfolio.py:147–160` berechnet einen
   nur steigenden `trailing_stop`. `static/app.js:128` zeigt dagegen ausschließlich
   `stop_loss`. Sinnvoll sind die Anzeige des wirksamen Maximums beider Stops,
   die Kennzeichnung der Stop-Art und eine getrennte Erläuterung von Basis-SL,
   Trailing-SL und Gewinnziel. Ein vorhandener Trailing-Wert unterhalb des
   Basis-SL ist noch nicht der wirksame Schutzkurs.
2. **Der alternative Prozent-Trailing enthält einen logischen Fehler.** Nach
   `highest_price = max(highest_price, price)` in `portfolio.py:146` kann die
   Bedingung `price > highest_price * 1.04` in Zeile 167 nicht erfüllt sein.
   Der Zweig ist beim derzeit aktiven Chandelier-Modus nicht maßgebend, sollte
   aber vor einer späteren Umschaltung korrigiert und separat getestet werden.
3. **Preisaktualisierung ist nicht Positionsüberwachung.** Die fünfminütige
   Scheduler-Route ruft `refresh_prices()` auf; diese lädt in
   `scheduler_tasks.py:43–71` lediglich Kurse. `evaluate_all_users()` ist zwar
   vorhanden, aber nicht in diese Route eingebunden. Stops werden unter anderem
   bei `/api/portfolio`, der zweistündigen Analyse und Berichten geprüft. Ein
   geschlossener Browser darf die virtuelle Stop-Prüfung nicht aussetzen.
4. **Die komplette Depot-JSON wird ohne gemeinsamen Ablauf-Lock geladen und
   gespeichert.** Gleichzeitige Auswertungen, Käufe oder Verkäufe können sich
   überschreiben. Vor zusätzlicher Hintergrundüberwachung müssen alle
   konkurrierenden Schreibpfade serialisiert werden; ein Lock nur im neuen
   Monitor reicht nicht.
5. **Die Wertkurve wird gekürzt.** `portfolio.py:197–199` begrenzt
   `value_history` auf 365 Einträge, nicht auf 365 Tage. Häufigere Auswertungen
   würden daher ältere Kurvendaten schneller verdrängen. Das Trade-Journal wird
   an dieser Stelle nicht gekürzt. Historienerhalt muss beide Datenarten umfassen.
6. **Yahoo-Kurse sind verzögerte Referenzkurse.** Der bisherige Yahoo-Adapter
   verwendet 15 Minuten Cache und liefert `fetch_latest_price()` ohne
   Kurszeitpunkt, Handelsstatus oder abgesicherte Umrechnung zurück. Bei
   fehlendem FX liefert `_convert_to_eur()` unveränderte Fremdwährungsdaten,
   während die Depotlogik Euro unterstellt. Für virtuelle Ausführungen braucht
   es einen separaten, bei ungültigen Daten gesperrten Quote-Pfad.

## Warum die vorhandenen Backtests keine Parameter-Optimierung belegen

- Live prüft `portfolio.py:178–184` das feste TP auch bei aktivem Trailing.
  `backtester.py:172` erlaubt TP nur ohne Trailing. ATR-Trailing startet aber
  früh, sodass dort ein anderer Exit-Ansatz getestet wird.
- `backtester.py:145–170` berechnet einen neuen Stop aus dem Tages-Schlusskurs
  und testet ihn anschließend gegen das bereits vergangene Tages-Tief.
  Ohne Intraday-Reihenfolge kann daraus eine zeitlich unmögliche Ausführung
  entstehen. Ein neuer Schlusskurs-Stop darf frühestens danach gelten.
- Einstieg am signalgebenden Schlusskurs (`backtester.py:132–137`), Stop-Fills
  stets genau am Stop und fehlende Eröffnungskurse bilden Kurslücken und
  handelbare Folgekurse nicht ausreichend ab.
- Der Live-Score enthält ATR- und Volumenwerte sowie aktuelle News/LLM; der
  historische Score verwendet einen pauschalen Volatilitätsbonus und keine
  historische News- oder Volumenprüfung. `signals.py:128–131` gegenüber
  `backtester.py:81–82` ist daher kein gleiches Modell.
- Die Live-Paper-Buchführung berücksichtigt keine Handelskosten; der Backtest
  modelliert 0,05 % pro Seite, aber keine feste Transaktionspauschale.
- Über verschiedene Symbole werden Prozent-Ergebnisse addiert und ein
  vereinfachter Drawdown in Symbol-Reihenfolge berechnet (`backtester.py:192–217`,
  `305–318`). Das ist kein chronologisch kapitalbegrenztes Depot mit fünf
  gleichzeitigen Positionen und 20 % Cashreserve. Die Zeitfenster 1J/2J überlappen.
- Der interne `portfolio.run_backtest()` vergleicht alte Journal-Paare mit einer
  angenommenen Marktrendite von 8 % p.a., nicht mit einer historischen
  tatsächlich beobachteten Benchmark.

**Folgerung:** Zuerst Ausführung, Stop-Anzeige und Überwachung zuverlässig
machen. Keine Schwelle, Stop-Distanz oder bestehende TP allein aufgrund der
vorliegenden Bestparameter-Kommentare verändern. Anschließend chronologische
Out-of-sample-Tests mit identischen Regeln, Kosten und Kapitalbindung durchführen.
Ein alternatives TP-/Trailing-Modell benötigt eine neue Version für neue Käufe;
bestehende Stops und Ziele dürfen nicht stillschweigend ersetzt werden.

## Trade-Republic-Bezug und zusätzliche Titel

Die bestehende Watchlist umfasst 27 Titel und ist deutlich technologiebetont.
Mehr Titel sind ohne Diversifikations- und Vergleichstest noch keine
Strategieverbesserung. Vor Erweiterung sind ISIN, identisches Kursinstrument,
Handelbarkeit, Liquidität und Portfolioauswirkung zu prüfen.

Trade Republic verweist für konkrete Instrumente auf seinen
[Aktienkatalog](https://app.traderepublic.com/browse/stock) und erklärt das
[Handelsuniversum](https://support.traderepublic.com/de-de/758-Which-stocks-can-I-trade-at-Trade-Republic).
Der öffentlich auslesbare Katalog lieferte bei dieser Prüfung keine
maschinenlesbaren Einzelinstrumente. Deshalb ist hier **keine neue Aktie als
aktuell auf Martins Konto handelbar bestätigt**.

Ein bestehendes Identitätsproblem ist `SMH`: Der Code bezeichnet den unqualifizierten
Yahoo-US-Ticker als UCITS-ETF. Der
[US-Fonds](https://www.vaneck.com/cl/es/inversiones/semiconductor-etf-smh/informacion-general/)
hat ISIN US92189F6768, der
[UCITS-Fonds](https://www.vaneck.com/ucits/investments/semiconductor-etf)
ISIN IE00BMC38736. Diese sind nicht austauschbar. Bestehende Positionen und ihre
Historie dürfen nicht durch bloßes Umbenennen des Symbols auf einen anderen
Fondspreis umgestellt werden.

Die offiziellen TR-Informationen unterscheiden inzwischen
[Bestpreis](https://support.traderepublic.com/de-de/3372dc64-59cb-4521-a424-1ee812f264a4)
(1 € je Trade) und
[Direktpreis](https://support.traderepublic.com/de-de/835f9deb-b864-4587-b428-7facfc55296c)
(2 € je Trade einschließlich Handelsplatzgebühr).
[Handelszeiten](https://support.traderepublic.com/de-de/2483-Wann-sind-die-Handelszeiten)
hängen vom Ausführungsmodus ab. Ein EUR-umgerechneter Yahoo-Referenzkurs ist
deshalb kein garantierter TR-Ausführungskurs. Die Quellen wurden am 11.10.2026
geprüft; sie ersetzen keine Prüfung des gewählten Instruments in der Broker-App.

## Quote-Adapter dieser Überarbeitung

`analyzer/stock_quotes.py` ist ein eigenständiger, nur lesender Adapter:

- öffentliche Yahoo-Chart-Anfrage, 1m/1d, 6 Sekunden Timeout, keine Wiederholung;
- nur EQUITY/ETF, positive endliche Preise, identisches Antwort-Symbol;
- maximal 20 Minuten alte Aktienkurse und 24 Stunden alte FX-Referenzen;
- EURUSD/EURCHF/EURGBP werden als Fremdwährung pro EUR **dividiert**;
- unbekannte Währungen einschließlich GBp werden nicht als Euro oder GBP geraten;
- nur die vom Quellanbieter gemeldete reguläre Börsensitzung erlaubt virtuelle
  Fills; nicht die längere TR-Handelszeit mit einem stale US-Kurs simulieren;
- geschlossene Märkte: `tradable=False`; bei altem Kurs/FX zusätzlich
  `price=None`, damit der letzte gespeicherte Depotwert nicht durch eine
  vorgetäuschte aktuelle Umrechnung ersetzt wird;
- rein prozesslokaler, auf 256 Symbole und 60 Sekunden begrenzter Cache,
  mit erneuter Frische- und Handelszeitprüfung bei jedem Lesen;
- keine Änderung an Depot, Historie, Broker, Telegram oder Konfiguration.

Die Tests mocken HTTP und Uhr vollständig. Sie prüfen Grenzen, ungültige
Antworten, Wochenenden, FX, Cache-Ablauf und gleichzeitige Leser. Sie sind keine
Live-Erreichbarkeits-, Last- oder Profitabilitätsgarantie.

## Daten-Erhalt bei Veröffentlichung

Vorher ein konsistentes SQLite-Backup erstellen; nachher mindestens Positionen,
Mengen, Einstieg, Investition, Kaufzeit, SL/TP/Trailing, Cash, vollständiges
Trade-Journal und Wertkurve vergleichen. Keine Reset-Route aufrufen, keine
Althistorie neu berechnen und keine rückwirkenden Kosten buchen. Neue
Diagnose-/Versionsfelder dürfen bestehende wirtschaftliche Daten nicht ersetzen.

## Umgesetzte technische Verbesserungen

- Eigenständiger Aktien-Paper-Monitor mit vorgesehenem 60-Sekunden-Zyklus,
  Prozessführer-Sperre und Start erst im Web-Worker. Keine Broker-Orders,
  keine neuen Positionen und kein Nachrichtenversand durch diesen Monitor.
- Gemeinsame, wiedereintrittsfähige Sperre um alle Aktien-Depot-Schreibabläufe,
  einschließlich der im gleichen JSON gespeicherten manuellen Realpositionen.
- `/api/portfolio` ist jetzt eine lesende Anzeige. Auch ein Analyse-Probelauf
  löst keine Stop-Verkäufe mehr aus. Die Aktienanzeige enthält Monitorstatus,
  wirksamen SL, Trailing-/Breakeven-Kennzeichnung und eine Detailansicht.
- Bereits aktive SL/Trailing/TP werden vor neuen Stop-Berechnungen geprüft.
  Kurssprünge werden zum beobachteten Referenzkurs gebucht, nicht zu einem
  fiktiv garantiert erreichten Stop-Kurs. Quotes werden nach langsamen
  Datenabrufen nochmals auf Alter und laufende Börsensitzung geprüft.
- Prozent-Trailing folgt neuen beobachteten Hochs; Chandelier bleibt die
  bestehende Standardregel. Fehlende ATR-Daten lassen vorhandene Stops bestehen.
- Nachkäufe senken den vorher wirksamen Stop nicht und ersetzen ein bestehendes
  TP nicht. Keine sofortige Wiedereröffnung desselben Symbols innerhalb eines
  Analyselaufs nach dessen Stop-/TP-Verkauf.
- Vorhandene Trade- und Wertkurven-Einträge werden nicht gekürzt. Neue
  Wertkurvenpunkte werden höchstens alle fünf Minuten sowie bei Exits ergänzt.
  Fehlende Kaufdaten im Realdepot werden nicht mehr erfunden.
- Hintergrundspeicherung verwendet private SQLite-Sicherungen; sie löst weder
  Git-Push privater Daten noch Überschreiben des alten gemeinsamen JSON-Backups aus.

Die Eintrittsschwellen, ATR(22) × 2, Breakeven ab +4 %, bestehenden SL/TP und
die bisherige Time-Exit-Regel bleiben bestehen. Es wurden keine zusätzlichen
Aktien aufgenommen. Die Änderungen beseitigen Funktions- und Messprobleme;
ein nachgewiesener Renditevorteil ist damit nicht behauptet. Bisherige
Paper-Ergebnisse bleiben ohne nachträglich erfundene Gebühren unverändert.
