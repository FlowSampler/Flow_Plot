# Flow_Plot

Gating, Ca²⁺-Kinetik und Histogramm-Overlays für Durchflusszytometrie-Daten (`.fcs`) —
eine freie, Python-basierte Alternative für typische FlowJo-Arbeitsschritte, mit grafischer
Oberfläche (Tkinter + Matplotlib) und einer Batch-Pipeline für die Kommandozeile.

## Funktionen

- **Gating** wie in FlowJo: hierarchischer Gate-Baum (Polygon / Rechteck / Ellipse), Gate-Sets
  speicherbar für alle Dateien, eine Gruppe oder eine einzelne Datei
- **Darstellungen**: Scatter, 2D-Histogramm, Dichte, Konturen; Achsen Linear / Log / Logicle
- **Kinetik**: Einzelkanal oder Ratio zweier Kanäle über die Zeit (Median/Mittelwert pro Bin,
  Einzel-Events, Dichte, Ridgeline pro Zeitfenster)
- **Statistik**: Count / %positiv / %negativ pro Datei oder für die ganze Kohorte
- **Histogramm-Overlay** mehrerer Dateien (übereinander, versetzt, gestaffelt, getrennte Panels)
- **Export** als CSV/PNG, Plots per Klick in die Zwischenablage (Strg+V in PowerPoint/Word)

## Installation

Voraussetzung: **Python 3.10 oder neuer** (getestet mit 3.13 und 3.14). Tkinter ist in den
Python-Installern von python.org und in Anaconda bereits enthalten.

### Einfachster Weg (Windows): `start.bat`

1. Python von [python.org](https://www.python.org/downloads/) installieren und dabei
   **„Add python.exe to PATH“** anhaken
2. Repo herunterladen (grüner Knopf **„Code“ → „Download ZIP“**) und entpacken
3. **`start.bat` doppelklicken**

Beim ersten Start legt das Skript im Projektordner eine eigene Python-Umgebung (`.venv`) an
und installiert alle Pakete (dauert einige Minuten). Danach startet die GUI immer direkt mit
dieser Umgebung, und die übrige Python-Installation bleibt unberührt. Hat sich
`requirements.txt` geändert (z.B. nach einem Update), werden fehlende Pakete automatisch
nachinstalliert. Bei Problemen den Ordner `.venv` löschen und `start.bat` erneut starten.

### Manuell (alle Betriebssysteme)

```powershell
git clone https://github.com/FlowSampler/Flow_Plot.git
cd Flow_Plot
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Alternativ mit conda:

```powershell
conda create -n flow_plot python=3.13
conda activate flow_plot
pip install -r requirements.txt
```

**Plattform:** entwickelt und getestet unter Windows. „Plot in Zwischenablage kopieren“
nutzt die Windows-Zwischenablage (`pywin32`) und funktioniert nur dort; alles andere ist
reines Python und sollte auch unter macOS/Linux laufen.

## Projektstruktur

```
Flow_Plot/
├── pipeline/
│   ├── gating_gui.py         # GUI (Gating, Kinetik, Statistik, Histogramm-Overlay)
│   ├── run_pipeline.py       # Batch-Auswertung aller Dateien (Kommandozeile)
│   ├── define_gates.py       # Gates per Kommandozeile definieren (nur Polygon)
│   ├── gating.py             # Gate-Sets: Datenmodell, Laden/Speichern, Anwenden
│   ├── kinetics.py           # Ratio, Zeit-Binning, Kinetik- und Ridgeline-Plots
│   ├── histogram_overlay.py  # Histogramm-Overlays mehrerer Dateien
│   ├── transforms.py         # Achsen-Transformationen (Linear / Log / Logicle)
│   ├── io_utils.py           # FCS-Dateien finden und laden
│   └── clipboard_utils.py    # Plot → Windows-Zwischenablage
├── gates/                    # gespeicherte Gate-Sets (JSON)
├── Ca2+_kinetic/             # eigene .fcs-Dateien hier ablegen (nicht im Repo)
├── output/                   # Ergebnisse der Pipeline (nicht im Repo)
├── start.bat                 # Windows: Umgebung einrichten + GUI starten
└── requirements.txt
```

Messdaten (`*.fcs`) und Ergebnisse (`*.csv`, `*.png`, `output/`) werden per `.gitignore`
bewusst **nicht** versioniert. Die Standard-Ordner lassen sich über `--input-dir`,
`--gates-dir` und `--output-dir` beliebig ändern; in der GUI kann der Datenordner auch direkt
gewählt werden.

**Dateinamen-Konvention:** Steht im Dateinamen `baseline <N>` (z.B.
`MEC-1 baseline 60 + Lysate.fcs`), wird das als Baseline-/Stimulus-Zeitpunkt in Sekunden
gelesen — er wird in den Kinetik-Plots markiert und für Fold-Change und Ridgeline-Farben genutzt.

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
- **Darstellung**: Median oder Mittelwert pro Bin (± SEM), Einzel-Events als Punkte oder als
  Dichte-Heatmap, oder **Ridgeline** (eine Verteilung pro Zeitfenster, Kämme vor/nach dem
  Stimulus unterschiedlich eingefärbt)
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

### Histogramm-Overlay-Tab (direkt in der GUI)

Vierter Tab **"Histogramm-Overlay"**: die Verteilung eines Kanals oder Ratios für mehrere
Dateien in einem Plot vergleichen.

- Dateien links auswählen (Strg/Shift-Klick für Mehrfachauswahl, "Name enthält" zum Filtern)
- **Darstellung**: Overlay (übereinander), Offset (vertikal versetzt), Staggered (vertikal +
  horizontal versetzt) oder Getrennt (ein Panel je Datei)
- **Normierung**, Bins, Glättung, X-Transform und X-Achsen-Bereich einstellbar
- **Population**: wie im Kinetik-Tab; "Live" wendet den aktuell angezeigten Knoten des
  Gate-Baums auf jede Datei an
- **Farben**: pro Datei (Doppelklick auf die Datei), eine Einheitsfarbe oder ein Farbverlauf
- Export der Histogramm-Daten als CSV oder des Plots in die Zwischenablage

Einstellungen, die für die gewählte Darstellung keine Wirkung haben (z.B. "Offset Y" bei
"Overlay"), werden in allen Tabs automatisch ausgeblendet.

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

## Abhängigkeiten

Das Einlesen der FCS-Dateien und die Logicle-Transformation basieren auf
[FlowKit](https://github.com/whitews/FlowKit). Alle Pakete stehen in `requirements.txt`.

## Lizenz

[PolyForm Noncommercial 1.0.0](LICENSE.md) — Nutzung, Änderung und Weitergabe sind für
**nicht-kommerzielle Zwecke** frei erlaubt: private Nutzung, Forschung und Lehre sowie
Hochschulen, öffentliche Forschungseinrichtungen und gemeinnützige Organisationen
(unabhängig von deren Finanzierung). **Kommerzielle Nutzung** ist nicht gestattet — bei
Interesse bitte über ein GitHub-Issue Kontakt aufnehmen.

Bei Weitergabe müssen der Lizenztext und die Zeile `Required Notice: …` aus `LICENSE.md`
mitgegeben werden.
