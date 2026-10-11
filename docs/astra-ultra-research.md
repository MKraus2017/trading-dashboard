# Astra Ultra: Forschung und ausgewählter virtueller 4H-Vergleich

> Versionshinweis vom 11.10.2026: Die laufende Strategie wird auf Nutzerwunsch als
> `astra_breakout_2h_v2` mit 2H-Kerzen weitergeführt. Dieser Bericht bleibt der
> unveränderte Forschungsstand der früheren 4H-Version `astra_breakout_4h_v1`.
> Insbesondere +5,79 % über 360 Tage und die historische Signalkonsistenz gelten
> **nicht** als Backtest oder Validierung der neuen 2H-Version. Dafür wurde hier
> kein neuer historischer Lauf ausgeführt. Kapital und Vorgeschichte bleiben im
> laufenden Vergleich erhalten; dessen Gesamtrendite ist keine isolierte
> 2H-Rendite. Aktuelle Regeln und Versionswechsel: `astra-ultra-operations.md`.
> Der unten dokumentierte `--verify-live-signals`-Abgleich lädt die lokale Engine
> und benötigt deshalb den damaligen 4H-Code, etwa Revision `62f520d`, in einer
> separaten sauberen Arbeitskopie. Mit der neuen 2H-Engine reproduziert er nicht
> die historische 4H-Signalkonsistenz. Die gespeicherten Ergebnisse bleiben erhalten.

Forschungsstand: 10.10.2026. Marktbeobachtungen bis 10.10.2026, 15:00 UTC. Vollständig virtuell; keine Kontozugriffe, Orders oder Veröffentlichungen.

**Ergebnis: ein aussichtsreicher, aber unbestätigter 4H-Kandidat für den virtuellen Vorwärtstest.** Die ausgewählte Version erzielt über 360 Tage +5,79 % nach Modellkosten und bleibt in zwei zusätzlichen Kostenszenarien positiv. Der größte historische Rückgang beträgt bei stündlicher Bewertung 9,16 %. Zwei der vier getrennten 90-Tage-Abschnitte sind negativ; ohne den Beitrag des größten Gewinntrades wird die Gesamtbilanz negativ. Das ist eine überprüfbare Forschungshypothese, keine belastbar profitable oder allgemein „beste“ Strategie.

Die anfängliche 1H-Version verlor über 360 Tage 21,83 %. Danach wurde eine kleine Familie von drei langsameren Alternativen vor deren jeweiligem Lauf festgelegt. Das Verfahren und sämtliche Ergebnisse werden unten vollständig ausgewiesen. Da die Erweiterung nach Sichtung der ersten Ergebnisse erfolgte, ist die gesamte Historie **Auswahlmaterial, keine unberührte Validierung**. Bestätigung kann erst aus neu eintreffenden Daten entstehen.

## Gewählte 4H-Hypothese für den Vorwärtstest

`astra_breakout_4h_v1`: BTC/ETH/SOL, nur LONG, fest 1x. Einstieg nach abgeschlossenem Vierstundenschluss über den vorherigen 24 Hochs und EMA200. Exit bei Stop, Vierstundenschluss unter den vorherigen 12 Tiefs oder nach 336 Stunden. ATR14 nach Wilder, Stop 2 ATR unter dem tatsächlichen modellierten Einstieg. Kein Gewinnziel. Die Risikogrenzen bleiben 0,5 % je Position inklusive Kosten, 1 % aggregiertes ursprüngliches Stop-Risiko, höchstens zwei Positionen und 40 % Kapital je Position. Hauptkosten: 0,20 % Gebühr und 0,05 % Slippage je Seite.

Die Indikatoren sind exakt definiert: Bei jeder Entscheidung **die letzten 500 abgeschlossenen, lückenlosen 4H-Kerzen** verwenden. EMA200 startet mit dem Durchschnitt der ersten 200 Kerzen dieses Fensters und verarbeitet anschließend die übrigen 300 mit Faktor2/201. Für ATR ist die erste True Range `Hoch−Tief`; danach gilt `max(Hoch−Tief, abs(Hoch−Vorschluss), abs(Tief−Vorschluss))`. Der Durchschnitt der ersten14True Ranges startet die Wilder-Rekursion. Das ist bewusst eine endliche Fensterdefinition, keine Behauptung eines Indikators mit unendlich langer Historie. Die 24-/12-Kanäle schließen die aktuelle Signalkerze aus.

Diese präzise Definition wurde für reproduzierbare Umsetzung nachgeschärft und erneut vollständig getestet. Gegenüber der zunächst verwendeten fortlaufenden EMA-Geschichte änderte sich das 360-Tage-Ergebnis von+5,18 % auf+5,79 %. Beide Werte und ihre Ursache sind damit nachvollziehbar; der erste Wert ist nicht mehr das Ergebnis der endgültigen Regeln. Es fand keine Suche nach einer renditeoptimalen EMA-Vorlauflänge statt.

**Signalkonsistenz überprüft:** 6.960 historische500-Kerzen-Fenster über drei Coins wurden gegen die pure Signalfunktion der laufenden Implementierung getestet: keine Abweichung der Ein-/Ausstiegsentscheidungen, ATR-Abweichung0, maximale absolute EMA-Abweichung6,4×10⁻¹⁰ durch Gleitkommarechnung. Das bestätigt die Indikator-/Signaldefinition, nicht identische historische und tatsächliche Kursausführung.

## Ursprüngliche 1H-Referenz: festgelegte, nicht ausgewählte Regeln

- Feste Reihenfolge bei gleichzeitigen Signalen: BTC, ETH, SOL; OKX-USDT-Spotkurse, 1H, nur abgeschlossene Stundenkerzen. Eine Kerze gehört über ihren UTC-Eröffnungszeitpunkt genau zu einer Stunde. Mindestens 500 vorbereitende Kerzen für stabile EMA-Berechnung im Forschungslauf.
- LONG-Einstieg, wenn der Schlusskurs einer fertigen Kerze strikt über dem höchsten Hoch der **vorherigen** 24 Kerzen und über der EMA200 liegt. Die Signalkerze zählt nicht zum 24er-Kanal. Kein Score, RSI-, Volumen- oder Taker-Druck-Zusatzfilter.
- Ausführung erst nach dem Signal: im historischen Test nächste Stundenöffnung mit 0,05 % ungünstiger Slippage; im laufenden Papierdepot erster tatsächlich beobachteter frischer Kurs nach dem Signal. Kein rückwirkender Einstieg zum Signal-Schlusskurs.
- ATR14 nach Wilder. Stop ab dem tatsächlich modellierten Einstiegspreis minus 2 ATR. Der ATR-Wert stammt ausschließlich aus der fertigen Signalkerze.
- LONG, fest 1x, kein Kredit, Funding, Short, Pyramiding oder Nachkaufen. Höchstens zwei offene Positionen und jeweils 40 % des aktuellen Depotwerts als Handelsvolumen.
- Geplantes Risiko bis zum Stop inklusive Gebühren beider Seiten und ungünstiger Stop-Ausführung höchstens 0,5 % des aktuell bewerteten Eigenkapitals. Summe der ursprünglichen geplanten Risiken offener Positionen höchstens 1 %. Nach Kurslücken oder verzögerter Beobachtung kann der tatsächliche Verlust darüber liegen; er wird nicht auf das Risikobudget gekürzt.
- Exit beim Stop, nach einem fertigen Stundenschluss strikt unter dem niedrigsten Tief der vorherigen 12 Kerzen oder spätestens 120 Stunden nach Einstieg. Kein festes Gewinnziel. Historisch wird ein Exit-Signal erst zur folgenden Öffnung ausgeführt. Ein Symbol wird in derselben Kerze nach einem Exit nicht erneut eröffnet.
- Hauptmodell: 0,20 % Gebühr je Seite und 0,05 % Slippage je Seite. Die Gebühr entspricht der veröffentlichten regulären EEA-Spot-only-Stufe ab 25.09.2026, ist aber **keine Bestätigung von Martins persönlichem Tarif**. 0,10 % wird nur als günstigere Kostensensitivität gerechnet.

Die Stückzahl folgt `min(Risikobudget / Verlust_pro_Coin, 40% * Eigenkapital / Einstieg, bezahlbare_Stückzahl)`. Bei tatsächlichem Einstieg `E`, Stop `S`, Gebührenrate `f` und Slippage `s` beträgt der geplante Verlust pro Coin `E*(1+f) - S*(1-s)*(1-f)`. Das Eigenkapital enthält in der Forschung die geschätzten Ausstiegskosten noch offener Positionen. Alle Forschungskonten starten mit 1.000 USDT; Euro-Symbole im bisherigen Depot sind keine echte EUR/USDT-Umrechnung.

## Daten und überprüfte Ausführung

`research/astra_ultra_backtest.py` ist ein separater Standardbibliothek-Lauf und importiert weder Anwendung, Portfolio noch Brokerzugriff. Die reinen Indikatorfunktionen werden aus der festgehaltenen Revision `e8ff09c8ca929af412bd3d40dc1e03dabed214ca` gelesen, nicht aus den ungespeicherten Arbeitskopien. Der Lauf hängt deshalb von dieser lokalen Git-Historie ab.

Je Coin liegen nach Ergänzung der längeren Vorlaufhistorie 11.279 gemeinsame Stundenkerzen vom 27.06.2025, 16:00 UTC, bis zur Kerze vom 10.10.2026, 14:00 UTC, vor. Alle drei Zeitreihen und ihr gemeinsamer Kalender sind ohne Stundenlücken. Die Kerze ab15:00UTC war beim Abruf noch offen und wurde ausgeschlossen. Neue und zusätzlich ältere Kurse wurden über öffentliche OKX-Endpunkte geladen. Ein mittlerer Abschnitt stammt aus dem vorhandenen öffentlichen Archiv vom21.09.2026; dort sind die ursprünglichen `confirm`-Felder nicht mehr vorhanden. Es werden nur Archivkerzen verwendet, deren Ende vor dem damaligen Abruf liegt. Diese Herkunft wird ausdrücklich protokolliert und nicht als erneut gelieferte Börsenbestätigung ausgegeben. Die4H-Kerzen entstehen ausschließlich aus jeweils vier vollständigen, aufUTC4H-Grenzen ausgerichteten Stundenkerzen.

Hauptmodell: Stunden-OHLC mit bereits aktiven Stops. Bei einer Eröffnung unter dem Stop gilt die schlechtere Eröffnung, jeweils zuzüglich Slippage. Liegen Stop und Gewinnziel einer Vergleichsposition in derselben Kerze, gilt zuerst der Stop. Aus dem Schlusskurs neu berechnete Trailing-Stops gelten erst in der Folgekerze. Jede Restposition wird am Testende geschlossen und abgerechnet. Das Kapital wird gemeinsam begrenzt; kumulierte Trade-Prozentzahlen werden nicht als Depot-Rendite ausgegeben. Der Depot-Rückgang bezieht sich auf stündlich bewertetes Liquidationseigenkapital, nicht auf jede Bewegung innerhalb einer Stunde.

**Abgrenzung zum laufenden Papierdepot:** Stunden-Hochs und -Tiefs rekonstruieren nicht den tatsächlichen Sekunden-/Minuten-Abfrageweg. Ein historischer Stop-Fill ist deshalb kein Beweis, dass die laufende Beobachtung genau dort ausgestiegen wäre. Als zusätzliche Sensitivität wurde derselbe Kandidat mit nur stündlichen Kursbeobachtungen getestet. Sie ist weder eine obere/untere Schranke noch ein nachträglich ausgewähltes alternatives Regelwerk.

Die eingebauten, erfolgreichen Rechenprüfungen kontrollieren Einstieg nach der Signalkerze, ungünstige Gap-Ausführung, ungekürzte Verluste über dem Risikobudget, kausale EMA/ATR, Gebühren im Positionsrisiko, Stop-Vorrang bei uneindeutigem OHLC sowie den vollständigen Kontenabgleich `Endkapital = Startkapital + Summe Netto-P&L`. Die Prüfungen belegen Rechenregeln, keine wirtschaftliche Profitabilität.

## Vollständige kleine Kandidatenfamilie und endgültige Auswahl

Alle Familienvergleiche haben denselben Endzeitpunkt10.10.2026,12:00UTC, identisches Startkapital, Gebühren und Risikogrenzen. Die Vorlaufdaten reichen auch für500Vierstundenkerzen plus den vollen360-Tage-Test. Die langsamen1H-Referenzen behalten ihre ursprüngliche durchlaufende Indikatorberechnung; nur die ausgewählte4H-Fassung wird exakt über ein rollierendes500er-Fenster definiert.

| Vorab benannte Alternative | 90 Tage | 180 Tage | 360 Tage | Trades360T |
|---|---:|---:|---:|---:|
| 1H, Kanal72/24, ATR3, maximal240h | +9,15 % | +2,96 % | −11,24 % | 112 |
| 1H, Kanal168/48, ATR3, maximal336h | +10,20 % | +7,80 % | +1,91 % | 52 |
| **4H, Kanal24/12, ATR2, maximal336h, rollierende500Kerzen** | **+10,98 %** | **+7,85 %** | **+5,79 %** | **44** |
| Supertrend10/3+ADX20, angeglichene LONG-Kontrolle | +14,60 % | +2,78 % | −9,37 % | 163 |

Die Supertrend-Kontrolle prüft den bestätigten Wechsel vonAbwärts- zuAufwärtstrend beiADX≥20 und steigt beim bestätigten Gegentrend aus. Für gleiche Risikorechnung bekommt sie1x-LONG, ATR2-Anfangsstop und dieselben Kapitalgrenzen. Sie rekonstruiert ausdrücklich nicht die alte gehebelte Stop-and-Reverse-Fassung.

Für den ausgewählten4H-Kandidaten ergeben die vier voneinander getrennten90-Tage-Blöcke **+0,35 %, −2,77 %, −2,51 %, +10,98 %**. Der360-Tage-Profit-Faktor ist1,45; es gibt10Gewinne bei44Abschlüssen. Durchschnittlich sind nur8,35 % des Kapitals gebunden. Auf1.000USDT Start wurden27,65USDT Gebühren belastet und6,91USDT Slippage in den Fills berücksichtigt. Diese niedrige Handelsfrequenz erklärt einen Teil des Kostenvorteils; sie begrenzt zugleich die statistische Aussagekraft.

Der größte Rückgang beträgt8,94 % bei Bewertung nur alle4Stunden. Ein zusätzlicher, kausal ausgerichteter Replay mit4H-Signalen und **stündlicher Stop-Prüfung/Bewertung** ergibt dieselben44Trades und+5,79 % Rendite, aber **9,16 %** Rückgang. Das löst keine Sekunden-/Minuten-Reihenfolge auf und ersetzt keine Messung des laufenden Monitors.

Vorher festgelegte Kostensensitivitäten für denselben360-Tage-Lauf: Gebühr0,30 % bei Slippage0,05 % ergibt **+3,48 %**; Gebühr0,20 % bei Slippage0,10 % ergibt **+4,95 %**. Bei nur einer Kursbeobachtung je4H-Bar ergibt sich+6,41 %; dieses günstigere Szenario wird nicht zum Hauptresultat gewählt. Ohne den Beitrag des größten Trades von+71,28USDT verbleiben **−13,40USDT** Gesamt-P&L; ohne die drei größten verbleiben−87,86USDT. Das ist eine arithmetische Konzentrationsprüfung, kein erneut finanzierter alternativer Handelspfad.

Der4H-Kandidat ist damit innerhalb dieser kleinen untersuchten Familie der plausibelste **virtuelle Vorwärtstest**, aber kein nachgewiesener Sieger. Die starke Abhängigkeit vom jüngsten Marktabschnitt und wenigen Gewinnen bleibt sichtbar. Es werden keine vergangenen Parameteränderungen als unabhängige Erfolge gezählt.

## Ergebnisse der ursprünglichen, unveränderten 1H-Referenz

Gemeinsames Ende: 10.10.2026, 15:00 UTC. Die 90-/180-/360-Tage-Fenster überlappen und sind keine unabhängigen Stichproben.

| Astra, 0,20 % Gebühr + 0,05 % Slippage je Seite | 90 Tage | 180 Tage | 360 Tage |
|---|---:|---:|---:|
| Netto-Depot-Rendite | +3,59 % | −0,52 % | −21,83 % |
| Größter stündlicher Rückgang vom Höchstwert | 11,18 % | 11,18 % | 28,10 % |
| Abgeschlossene Trades, inklusive Testende | 64 | 116 | 220 |
| Netto-Profit-Faktor | 1,18 | 0,98 | 0,63 |
| Durchschnittliche Kapitalbindung | 22,23 % | 19,91 % | 15,22 % |
| Tatsächlich abgezogene Modellgebühren bei 1.000 USDT Start | 85,09 | 138,44 | 203,42 |
| In den Fills enthaltene Slippage | 21,27 | 34,61 | 50,86 |

Die vier **getrennten**, jeweils neu und ohne Bestand gestarteten 90-Tage-Blöcke ergeben der Reihe nach **−15,72 %, −7,06 %, −3,96 %, +3,59 %**. Das gute jüngste Quartal kompensiert die schwachen früheren Abschnitte nicht. Auch mit nur 0,10 % Gebühren bleibt das 360-Tage-Ergebnis negativ: **−12,57 %**, PF **0,79**. Ein Wechsel auf einmal stündliche Stop-Beobachtung ergibt **−21,41 %** über 360 Tage; 90 Tage ergeben dort +5,63 %. Die Ausführungsempfindlichkeit rechtfertigt kein Auswählen des schöneren Ergebnisses.

BTC durchgehend gehalten erreicht nach denselben proportionalen Basiskosten **+28,61 % / +14,07 % / −25,65 %** für 90/180/360 Tage. Dieser Vergleich hält fast 100 % Kapital im Markt und ist risikomäßig nicht mit Astra gleichzusetzen. Cash ohne Zinsen und Wechselkurseffekt ergibt im USDT-Modell 0 %. Keine dieser Vergleichszahlen ist eine Anlageempfehlung.

## Was die bestehende Strategie macht – und weshalb alte Zahlen nicht reichen

Die deployed betrachtete Revision berechnet ab 50 Punkten: EMA20>EMA50 +15, sonst −5; RSI<40 +12, RSI>68 −15; MACD über Signallinie +10, sonst −10; unteres Bollinger-Band +10, oberes −10. ADX<20 zieht das Ergebnis mit Faktor0,4 zu50 zurück; ab ADX25 kommt ein begrenzter richtungsabhängiger Zuschlag. LONG ab66, SHORT bis34. Die Volumenvariante verwendet zusätzlich RVOL und die jüngsten Taker-Trades.

Das mischt Trendfolge und Rückkehr zum Mittelwert. Die neue Donchian-Hypothese prüft dagegen, ob klare Ausbrüche mit langsamer Trendrichtung nach Kosten nutzbar sind. Die Indikatornamen und bekannte Herkunft der Bausteine beweisen keinen Vorteil.

Prüfbare Unterschiede der bisherigen Module:

- `crypto_signals.py` verwendet für technische Indikatoren die letzte gelieferte Kerze, ohne sie anhand `confirmed` herauszufiltern; nur die Volumenmetrik sucht gezielt eine bestätigte Kerze. Aktuelle Signale lassen sich aus abgeschlossenen historischen Kerzen deshalb nicht exakt rekonstruieren.
- Die Live-Hebelheuristik nutzt Score und Volatilitätsfaktor bis10x. Der vorhandene Krypto-Backtest verwendet einen anderen Hebel ohne denselben Volatilitätsfaktor, in der Regel andere Kerzenbreite, keine gemeinsame Portfoliofinanzierung und addiert Trade-Prozentzahlen.
- `crypto_portfolio.py` bucht in der geprüften Revision reguläre Ein-/Ausstiege ohne Gebühren oder Slippage. Bei Liquidation wird der Geldverlust auf die Margin begrenzt, während `pnl_pct` weiterhin aus dem ungekappten Kursweg berechnet wird. Dollar-/Euro-P&L und Prozentanzeige können dort voneinander abweichen. Das ist keine Vorlage für Astra-Verlustrechnung.
- Der alte Kontostopp misst Verlust gegenüber Startkapital. Er ist kein Rückgang vom jeweiligen höchsten Depotwert. Die frühere Schwelle25 % greift im Code vor der nominellen50-%-Schwelle.
- Eine korrekte jetzige Kursabfrage beweist nicht rückwirkend lückenlose Ausführung. Der Bericht vom 21.09.2026 dokumentierte bereits Abweichungen zwischen berührten Stops/Zielen und späteren verbuchten Exits sowie instabile historische Ergebnisse.

Der neue Forschungs-Kontrolllauf übernimmt die reine LONG-Scoreentscheidung ab66, das ATR-Stop-/Zielpaar1,1/1,4, das nachlaufende Stop-Verhalten,72h-Zeitregel sowie historische 24h-Volumen-/BTC-Filter. Kapital,1x-Hebel,0,5-%-Risiko, Gebühren, drei Coins und stündliche Ausführung sind auf Astra vereinheitlicht. Er ergibt **−39,09 % / −68,41 % / −90,00 %** für90/180/360Tage. Diese extrem schwachen Werte gehören nur zu dieser ausdrücklich benannten Kontrolle. Insbesondere fehlen die bisherige2h-Tagesfenster-Steuerung, Shorts, übrige Coins,10x-Hebelwahl, reale Laufzeitverzögerungen und der bisherige Kontoverluststopp. Sie dürfen **nicht** als historische Rendite des deployed Kontos bezeichnet werden. Eine Kontrolle mit gleichem Risiko beantwortet eine andere Frage als die tatsächliche Kontoperformance.

## Sauberer laufender Vergleich und Prüfung alle fünf Tage

Beide beobachteten Konten erhalten einen dokumentierten gemeinsamen Startzeitpunkt und werden ab ihrem jeweiligen Start-Eigenkapital auf100 normiert. Astra beginnt ohne Positionen mit gleichem nominalen Startkapital. Falls das bestehende Konto zu diesem Zeitpunkt ebenfalls leer ist, entfällt der Altbestandsnachteil. Andernfalls müssen die übernommenen offenen Positionen samt Startbewertung separat erfasst werden.

Die Beobachtungs-Benchmark zeigt die Eigenkapitaländerung des bestehenden Kontos seit dem Start. Nachträglich modellierte Kosten werden nur für **danach** erfolgte Fills belastet; historische Einstiegsgebühren dürfen nicht beim späteren Schließen ein zweites Mal in die gemeinsame Startperiode rutschen. Offene Gewinne/Verluste und erwartete Ausstiegskosten müssen zu jedem Vergleichszeitpunkt enthalten sein. Noch alte Brutto-Kontowerte dürfen nicht neben Astra-Nettozahlen als gleichartig erscheinen. Falls der Benchmark Cashflow-Import, Neubuchungen, Reset oder verlorene Trades feststellt, ist der betroffene Vergleich zu markieren und nicht still fortzuschreiben.

Das bestehende Konto behält andere Coins, Hebel und Auswahlregeln. Der Vergleich muss deshalb „beobachtete Konten, unterschiedliches Risiko“ heißen; er beweist keinen kausalen Strategievorteil. Eine wissenschaftlich fairere zusätzliche Kontrolle müsste gleichzeitig ohne Bestand mit gleichen Coins, Positionsrisiken, Ausführung und Gebühren starten. Der offline vorhandene Kontrolllauf ist eine transparente Annäherung daran und kein Ersatz für eine solche laufende Kontrolle.

Alle fünf Tage: Daten-/Monitorlücken, Alter der Kurse, doppelte/fehlende Orders, P&L-Abgleich, Gebühren, tatsächliche Risiken, offene Positionen, Netto-Rendite und Rückgang kontrollieren. Ein fünf-Tage-Fenster enthält bei der ausgewählten4H-Hypothese nach dieser Historie durchschnittlich **weniger als einen Abschluss**. Deshalb keine routinemäßige Parameteränderung, kein Zurücksetzen schlechter Ergebnisse und kein Gewinnerwechsel anhand dieses kleinen Ausschnitts. Fehlerkorrekturen dürfen sofort erforderlich sein; sie erhalten eine neue technische Version und einen dokumentierten Bruch im Experiment.

Als erste Forschungsphase mindestens90Kalendertage und ausreichend neue, voneinander nicht als unabhängig angenommene Trades vorsehen. Auch100Abschlüsse sind kein automatischer Erfolgsnachweis: Marktphasen, Korrelation von BTC/ETH/SOL, Kostenstress, einzelne Großgewinne und Unsicherheit müssen gemeinsam betrachtet werden. Bei44Abschlüssen pro360Tagen kann es lange dauern, eine größere neue Stichprobe zu erhalten. Die ausgewählte Hypothese bleibt trotz positiver Jahreshistorie **nicht bestätigt**. Änderungen am Signal benötigen eine neue vorab festgelegte Version und spätere, ungesehene Daten; die bestehende Historie bleibt erhalten. Daraus folgt keine Freigabe für reales Kapital.

## Nachvollziehbarkeit und Quellen

Reproduktion aus dem Repository:

```text
python research/astra_ultra_backtest.py --self-test --download
python research/astra_ultra_backtest.py --self-test --family --verify-live-signals
```

Die erste Zeile lädt ausschließlich öffentliche Kurse nach; die zweite nutzt den gespeicherten Stand ohne Netzwerk. Lokale Ergebnisse außerhalb des Repositorys: `../astra-research/results.json` und `trade_ledgers.json` für die ursprüngliche1H-Referenz; `candidate_family.json` und `candidate_family_ledgers.json` für die ausgewählte Familie; `signal_parity.json`, `download_metadata.json` und `BTC_1H.json`, `ETH_1H.json`, `SOL_1H.json` für Konsistenz/Herkunft. Ergebnisse enthalten Kursdatei- und Skript-Prüfsummen. Der Lauf schreibt keine Einstellungen oder Depotdateien. Der optionale Konsistenztest lädt ausschließlich die lokale pure Signaldefinition und ruft weder `start` noch `cycle` oder Netzfunktionen der Anwendung auf.

- [TradingView: Donchian Channels](https://www.tradingview.com/support/solutions/43000502253-donchian-channels-dc/) dokumentiert die Kanaldefinition. Die gewählten 24/12 Kerzen, in der endgültigen Variante also 96/48 Stunden, sind eine eigene Testhypothese, keine von TradingView zugesicherte Rendite.
- [TradingView: Strategy-Dokumentation](https://www.tradingview.com/pine-script-docs/concepts/strategies/) erläutert nächste mögliche Ausführung, Broker-Simulation, Gebühren, Slippage, Zukunftsdaten und Überanpassung. Verwendet wird keine öffentliche fremde TradingView-Performance als Erfolgsbeleg.
- [OKX API: History Candles](https://app.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history) dokumentiert OHLC-Struktur, `confirm=1`, UTC-Millisekunden und Pagination; die Daten stammen aus öffentlichen REST-Abfragen.
- [OKX Europe: Gebühren ab25.09.2026](https://www.okx.com/en-eu/help/important-notice-upcoming-spot-fee-adjustment-eea) nennt0,20 % reguläre Spot-only-Taker-Gebühr und0,10 % Maker. Andere Kontostufen und Paare können abweichen.
- [Bailey, Borwein, López de Prado, Zhu: The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) begründet die Vorsicht bei wiederholter Auswahl auf bekannten historischen Ergebnissen. Für diesen Lauf wurde kein Parametergitter nach einem Gewinner durchsucht.
