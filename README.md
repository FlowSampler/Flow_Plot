# Ca2+ Kinetik Pipeline

Analysiert alle `.fcs`-Dateien in `Ca2+_kinetic/`:
1. Pregating (Zellpopulation von Interesse, flexibel: global / Gruppe / einzelne Datei)
2. Ratio zweier Marker (`PerCP-A / Qdot 655-A`) pro Event
3. Zeit-gebinnte Kinetik (Median-Ratio je Zeitfenster) als Plot + CSV pro Datei

## Setup

Die Pipeline braucht `flowkit`, `pandas`, `numpy`, `matplotlib`. Diese sind bereits in der
conda-Umgebung **"standard"** installiert:

```
C:\Users\sah\AppData\Local\miniconda3\envs\standard\python.exe
```

Der System-`python` (3.14, `C:\Python314\python.exe`) hat diese Pakete NICHT. Entweder
immer den vollen Pfad benutzen, oder vorher aktivieren:

```powershell
conda activate standard
```

## Schritt 1: Pregates definieren

### Option A: GUI (empfohlen)

```powershell
cd pipeline
python gating_gui.py
```

Ein Fenster öffnet sich mit:
- **links**: Dateiliste (Klick, `<< Prev` / `Next >>`, oder die **Pfeiltasten ← / →** — diese
  funktionieren global im ganzen Fenster, unabhängig davon welches Bedienelement gerade den
  Fokus hat, außer beim Tippen in ein Textfeld, wo ← / → wie gewohnt den Cursor bewegen. Am
  Ende der Liste geht es automatisch wieder von vorne los und umgekehrt, sodass sich mit ←/→
  beliebig oft durch alle Dateien durchklicken lässt, egal von welcher Datei man startet) +
  Liste bereits gespeicherter Gate-Sets, die optional als Vorschau auf die aktuelle Datei
  angewendet werden
- **Mitte**: Streudiagramm mit X-/Y-Kanal-Dropdowns, Form-Auswahl (Polygon / Rectangle /
  Ellipse), Buttons "Gate zeichnen starten" → Form mit der Maus aufziehen/anklicken →
  "Schritt übernehmen" (oder "Abbrechen")
- **rechts**: Liste der aktuell aufgebauten Gate-Schritte (mehrere Schritte = sequentielles
  Gating, z.B. erst Zellen, dann Singlets), Name/Scope-Eingabe, "Gate-Set speichern"

Ablauf: Datei auswählen → Kanäle wählen → Form wählen → zeichnen → "Schritt übernehmen".
Für einen zweiten, verschärfenden Schritt (z.B. Singlets nach dem Zell-Gate) einfach neue
Kanäle/Form wählen und erneut zeichnen — er wird auf die bereits gegatete Population der
aktuellen Vorschau angewendet. Wenn alle Schritte für dieses Gate-Set fertig sind: Name +
Scope eintragen und "Gate-Set speichern" klicken. Danach kann direkt das nächste Gate-Set
(z.B. ein gruppenspezifisches Refinement) begonnen werden.

**Echte Gate-Hierarchie (Baum, wie in FlowJo):** Ein neu gezeichnetes Gate wird als **Kind**
der aktuell angezeigten Population angelegt — die Ansicht bleibt dabei auf der aktuellen Ebene,
sodass man direkt ein zweites, drittes usw. Gate auf derselben Ebene zeichnen kann (z.B.
"Cells" und "Debris" als zwei Geschwister-Gates auf "Alle Events"). Navigation:

- **Doppelklick** in ein gezeichnetes Gate (rot umrandet, im Plot) wechselt in dessen Population
  (nur wenn die Achsen gerade auf das Kanalpaar dieses Gates eingestellt sind)
- **"Kind-Gates dieser Ebene"**-Liste (rechts): zeigt alle Geschwister-Gates der aktuellen
  Ebene mit Event-Zahl; Doppelklick oder "In ausgewähltes Kind wechseln" navigiert hinein
- **Breadcrumb** oben rechts ("Alle Events › Cells › Singlets"): jeder Schritt anklickbar,
  um direkt zu einer Eltern-Ebene zurückzuspringen
- **"▲ Eine Ebene hoch"**: zur Eltern-Population
- **Gates umbenennen**: "Aktuelle Ebene umbenennen" (für die gerade angezeigte Population)
  bzw. "Ausgewähltes Kind umbenennen" (für ein Gate in der Kind-Liste) öffnen einen Dialog mit
  dem bisherigen Namen vorausgefüllt — der neue Name erscheint sofort im Breadcrumb und in der
  Kind-Liste. Namen sind reine Tree-Metadaten in der GUI (werden nicht in die Gate-Set-JSON
  geschrieben), helfen aber beim Wiederfinden/Unterscheiden von Geschwister-Gates
- **"Ausgewähltes Kind löschen"**: löscht ein einzelnes Geschwister-Gate (+ dessen Unterbaum)
  ohne die aktuelle Ansicht zu verlassen
- **"Diesen Knoten + Kinder löschen"**: löscht die aktuell angezeigte Population selbst und
  wechselt automatisch zur Eltern-Ebene
- **"Baum zurücksetzen"**: verwirft den gesamten Gate-Baum

Weggegatete Zellen verschwinden beim Hineinzoomen komplett aus der Ansicht (nicht nur
eingefärbt). **"Gate-Set speichern"** speichert den Pfad von der Wurzel bis zur aktuell
angezeigten Population als ein Gate-Set — man navigiert also zuerst (per Doppelklick) zur
gewünschten Zielpopulation und klickt dann speichern.

**Bestehendes Gate anpassen (Ecken/Ränder nachziehen):** Ein Klick **auf die rote Linie**
eines bereits gezeichneten Gates (nicht ins Innere — das navigiert per Doppelklick hinein)
aktiviert den Bearbeitungsmodus für genau dieses Gate: der passende Selektor (Polygon-Punkte,
Rechteck- oder Ellipsen-Griffe) wird mit den vorhandenen Koordinaten vorbelegt. Nach dem
Anpassen "Änderungen speichern" klicken — **Name und alle Kind-Gates bleiben dabei exakt
erhalten**, es wird nur die Form selbst ersetzt. Alternativ: Kind in der Liste rechts
auswählen und "Ausgewähltes Kind bearbeiten" klicken (robuster als der Klick auf die dünne
Linie, falls der mal knapp daneben trifft).

Das ist besonders nützlich, wenn eine Population von Datei zu Datei leicht verschoben ist:
Datei wechseln → Gate-Linie anpassen → mit **demselben Namen/Kind-Gates** unter `--scope file`
für genau diese Datei speichern (siehe Scope-Tabelle oben). Die übrigen Dateien nutzen
weiterhin das `all`/`group`-Gate mit der ursprünglichen Form, da file-Scope in der
Priorität vorgeht. Hinweis: Die Baum-Ansicht selbst zeigt nach einer solchen Anpassung die
zuletzt bearbeitete Form, auch wenn man zu einer anderen Datei zurückwechselt — vor dem
Speichern für eine andere Datei ggf. zurück-anpassen oder neu zeichnen.

**Darstellung zur Grenz-Abschätzung:** Über das Dropdown "Darstellung" lässt sich die
Populationsansicht (egal auf welcher Gating-Stufe) wechseln zwischen:
- **Scatter**: klassischer Dot-Plot
- **Histogram**: 2D-Histogramm (Pseudocolor)
- **Density**: geglättete Dichteschätzung (hilfreich bei verrauschten/spärlichen Populationen)
- **Contour**: Konturlinien der geglätteten Dichte

Bereits gezeichnete (aber noch nicht gespeicherte) Gate-Grenzen werden in Rot über jede
Darstellung gelegt, sobald man zum gleichen Kanalpaar zurückkehrt.

**Achsen-Transformation:** Über "X-Transform" / "Y-Transform" lässt sich pro Achse zwischen
drei Darstellungen wählen:
- **Linear**: Rohwerte unverändert
- **Log**: symmetrisches Log (verträgt die in FCS-Rohdaten üblichen leicht negativen
  Rauschwerte nahe Null), Schwellenwert über "Log linthresh" einstellbar
- **Logicle**: FlowKit-Standardtransform (biexponentiell, Industriestandard in der
  Durchflusszytometrie), Parameter "Logicle W" (Linearisierungsbreite, Default 0.5) und
  "Logicle M" (Anzahl Dekaden, Default 4.5) einstellbar; der Top-Wert T wird automatisch aus
  dem $PnR-Messbereich des jeweiligen Kanals gelesen

Wichtig: Das ändert **nur die Darstellung** — alle Gates werden weiterhin in Rohwerten
gespeichert, unabhängig davon, welche Achsen-Transformation beim Zeichnen gerade aktiv war.

**Schnelle Kanal-Auswahl per Rechtsklick:** Rechtsklick auf das X- oder Y-Achsenlabel im Plot
öffnet ein Kontextmenü mit allen verfügbaren Kanälen — praktisch als Alternative zu den
Dropdowns oben, direkt am Plot.

**Gate-Formen:**
- **Polygon**: Punkte anklicken, letzten Punkt erneut anklicken/doppelklicken zum Schließen
- **Rectangle**: Ziehen zum Aufziehen, Ecken danach nachziehbar
- **Ellipse**: wie Rectangle, aber elliptisch (achsenparallel)

**Scope-Optionen** (beliebig viele Gate-Sets können kombiniert werden, sie werden pro Datei
per UND verknüpft):

| Scope | Wirkung | Beispiel |
|---|---|---|
| `all` | gilt für **alle** Dateien im Ordner | generisches Zell-/Singlet-Gate |
| `group` + Pattern `ZCL278` | gilt für alle Dateien, deren Name `ZCL278` enthält | bedingungsspezifisches Gate |
| `file` | gilt nur für die aktuell ausgewählte Datei | Ausreißer-Datei manuell nachjustieren |

### Kinetik-Tab (direkt in der GUI)

Im selben Fenster gibt es oben einen zweiten Tab **"Kinetik"**, der sofort die Zeitkinetik der
aktuell gegateten Population der ausgewählten Datei zeigt — praktisch um den Effekt eines
Gates sofort zu kontrollieren, ganz ohne die Batch-Pipeline zu starten:

- **Einzelkanal**: ein beliebiger Kanal (z.B. `PerCP-A`) über die Zeit
- **Ratio (Kanal A / Kanal B)**: z.B. `PerCP-A / Qdot 655-A`, mit einstellbarem "Min. Signal"
  (Rausch-Ausschluss) wie in der Batch-Pipeline
- **Bin-Breite** einstellbar, rote Linie markiert das erkannte Ende der Baseline
- **"Population:"-Dropdown** — wählt explizit, welche Population gemeint ist:
  - **Live (Gating-Tab-Ansicht)**: die Population, die gerade im Gating-Tab angezeigt wird
    (gespeicherte Gate-Sets + gerade gezeichnete, noch nicht gespeicherte Schritte) — nur für
    die aktuell gewählte Datei, nicht für Batch-Export über alle Dateien geeignet
  - **Ungegated**: alle Events, kein Gate
  - **ein konkreter gespeicherter Gate-Set-Name** (z.B. "Lebend" oder "Dead"): wendet **nur**
    dieses eine Gate-Set an (dessen file/group/all-Scope-Varianten werden wie gewohnt mit
    Priorität aufgelöst)

  **Wichtig:** Hat man mehrere sich gegenseitig ausschließende Populationen gespeichert (z.B.
  "Lebend" und "Dead", beide mit Scope `all`), muss man hier explizit eine davon auswählen.
  Andernfalls — das war früher automatisches Verhalten — würden ALLE zum Dateinamen passenden
  Gate-Sets per UND verknüpft, was bei "Lebend" UND "Dead" zur (fast) leeren Schnittmenge führt
  statt zu einer der beiden Populationen. Plot-Titel und Statuszeile zeigen immer an, welche
  Population aktuell aktiv ist.
- Datei wechseln (links) und zum Kinetik-Tab wechseln aktualisiert den Plot automatisch
- **"Kinetik dieser Datei exportieren (CSV)"**: speichert die gerade angezeigten Zeit-Bins
  (mit Dateiname & Kanal/Ratio-Spalte) über einen Datei-Dialog
- **"Kinetik ALLER Dateien exportieren (CSV, für Overlays)"**: berechnet dieselbe Kinetik für
  alle Dateien im Ordner (nutzt gespeicherte Gate-Sets) und schreibt sie als eine kombinierte
  Long-Format-CSV (eine Zeile pro Datei & Zeit-Bin, mit `filename`-Spalte) — damit lassen sich
  in Excel/Prism/Python leicht Overlays aller Kinetiken übereinander zeichnen (z.B. nach
  `filename` filtern/pivotieren)
- **"Alle Kinetik-Plots als PNG exportieren"**: schreibt für jede Datei im Ordner einen
  einzelnen Kinetik-Plot als PNG (`<Dateiname>_kinetics.png`) in einen frei wählbaren Zielordner
  — dieselben Plots wie `run_pipeline.py` erzeugt, aber direkt aus der GUI mit den aktuellen
  Kanal-/Ratio-/Bin-Einstellungen, ohne Kommandozeile
- **"Plot in Zwischenablage kopieren"** (verfügbar im Gating-, Kinetik- und Statistik-Tab):
  kopiert den aktuell angezeigten Plot als Bild in die Windows-Zwischenablage — direkt mit
  **Strg+V** in PowerPoint, Word o.ä. einfügbar, kein Screenshot nötig

### Statistik-Tab (direkt in der GUI)

Dritter Tab **"Statistik"**: Count / %positive / %negative für einen Kanal oder ein Ratio,
pro Einzeldatei oder für die gesamte Kohorte — exportierbar als CSV.

- **Einzelkanal** oder **Ratio (Kanal A / Kanal B)** wie im Kinetik-Tab wählen
- **Schwelle (pos/neg)**: Zahl eingeben ODER einfach in das angezeigte Histogramm klicken —
  die Schwelle wird als rote Linie übernommen, praktisch um visuell zwischen negativer und
  positiver Population zu unterscheiden
- Statistik-Feld rechts zeigt für die **aktuelle Datei**: Events gesamt/gegated, gültige Werte,
  Mean/Median, sowie bei gesetzter Schwelle Anzahl & Prozent positiv/negativ
- Gleiches **"Population:"-Dropdown** wie im Kinetik-Tab (Live / Ungegated / ein konkreter
  gespeicherter Gate-Set-Name) — explizit auswählen, welche Population gemeint ist
- **"Kohorte berechnen"**: berechnet dieselbe Statistik für **alle** Dateien im Ordner für die
  oben gewählte Population (muss ein gespeichertes Gate-Set oder "Ungegated" sein, nicht
  "Live") und zeigt sie als Tabelle unten im Fenster
- **"Als CSV exportieren"**: speichert die zuletzt berechnete Kohorten-Tabelle über einen
  Datei-Dialog

### Option B: CLI (nicht-interaktiv, nur Polygon)

```powershell
cd pipeline
python define_gates.py --sample "..\Ca2+_kinetic\Lysate Buffer_MEC-1 baseline 60 + Lysate.fcs" --name cells_singlets --scope all
```

Gleiches Prinzip wie oben, aber nur Polygon-Gates, Kanalpaare per `--steps` und Reihenfolge
fix vorgegeben (praktisch für Skripting/Wiederholung). Details siehe `--help`.

## Schritt 2: Pipeline laufen lassen

```powershell
cd pipeline
python run_pipeline.py
```

Wichtige Optionen (alle optional, mit sinnvollen Defaults):

```powershell
python run_pipeline.py `
  --input-dir "..\Ca2+_kinetic" `
  --gates-dir "..\gates" `
  --output-dir "..\output" `
  --num-channel "PerCP-A" `
  --denom-channel "Qdot 655-A" `
  --bin-width-s 1.0 `
  --min-signal 1.0 `
  --pos-threshold 3.0
```

`--pos-threshold` ist optional: wenn gesetzt, ergänzt `summary.csv` pro Datei `n_positive`,
`pct_positive`, `n_negative`, `pct_negative` (Ratio > Schwelle = positiv). Für interaktives
Austesten der Schwelle und Kohorten-Export eignet sich der Statistik-Tab der GUI besser,
da man die Schwelle dort direkt im Histogramm anklicken kann.

**Wichtig bei mehreren Populationen:** Hat man mehrere gespeicherte Gate-Sets mit
überlappendem Scope (z.B. "Lebend" und "Dead", beide `all`), werden ohne `--gate-set-name`
**alle** zum Dateinamen passenden Gate-Sets per UND verknüpft — bei sich ausschließenden
Populationen ergibt das eine (fast) leere Schnittmenge statt einer der beiden Populationen
(die Pipeline warnt in diesem Fall). Mit `--gate-set-name Lebend` wird gezielt nur dieses eine
Gate-Set angewendet (seine file/group/all-Varianten werden weiterhin mit Priorität aufgelöst).
Die Ausgabedateien (`summary.csv`, `*_kinetics.csv/png`) bekommen dann automatisch den
Gate-Set-Namen angehängt, sodass ein zweiter Lauf mit `--gate-set-name Dead` nichts überschreibt.

## Output (`output/`)

Pro Datei:
- `<name>_kinetics.csv` — Zeit-Bins mit `n_events`, `median_ratio`, `mean_ratio`, `sem_ratio`
- `<name>_kinetics.png` — Ratio-Kinetik-Plot, rote gestrichelte Linie = erkanntes Ende der
  Baseline (aus `"baseline <N>"` im Dateinamen, z.B. `baseline 60` → 60 s)
- `<name>_gating_qc.png` — Kontrollplot: welche Events wurden vom Gate behalten/verworfen

Zusätzlich:
- `summary.csv` — eine Zeile pro Datei: Baseline-Ratio, Peak-Ratio, Peak-Zeitpunkt, Fold-Change,
  Anzahl gegateter Events, optional (mit `--pos-threshold`) %positive/%negative
- Kohorten-Statistik-CSV (Count/%positive/%negative je Kanal oder Ratio, frei wählbare
  Schwelle) über den **Statistik-Tab** der GUI exportierbar — Speicherort frei wählbar

## Hinweise

- Ratio wird nur aus Events berechnet, bei denen **beide** Kanäle > `--min-signal` sind
  (Default 1.0), um Rauschen nahe Null/negative Rohwerte (treten in FCS-Rohdaten normal auf)
  nicht als Artefakte ins Ratio einfließen zu lassen.
- Ohne definierte Gates läuft die Pipeline trotzdem (mit Warnung) auf der ungegateten
  Population — praktisch zum schnellen Testen, aber für echte Auswertung sollten vorher
  Gates definiert werden.
- Die Zeitachse (`Time`-Kanal) ist bereits in Sekunden (FlowKit skaliert automatisch mit
  `$TIMESTEP` aus der FCS-Datei).
