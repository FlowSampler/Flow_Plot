# Flow_Plot

Gating, Ca²⁺-Kinetik und Histogramm-Overlays für Durchflusszytometrie-Daten (`.fcs`) —
eine freie, Python-basierte Alternative für typische FlowJo-Arbeitsschritte, mit grafischer
Oberfläche (Tkinter + Matplotlib).

## Funktionen

- **Gating** wie in FlowJo: hierarchischer Gate-Baum (Polygon / Rechteck / Ellipse / Quadrant,
  Boolean-Gates UND / ODER / NICHT), Gate-Sets speicherbar für alle Dateien, eine Gruppe oder
  eine einzelne Datei
- **FlowJo-Import**: Gates, Gate-Hierarchie und angepasste Kompensation aus einem `.wsp`-Workspace
- **Kompensation**: Spillover-Matrix aus der FCS-Datei (Standard, wie FACSDiva/FlowJo) oder eine
  eigene Experiment-Matrix; Anpassen per Greifen-und-Ziehen im Plot oder direkt in der NxN-Tabelle
- **Darstellungen**: Scatter, 2D-Histogramm, Dichte, Konturen; Achsen Linear / Log / Logicle
- **Kinetik**: Einzelkanal oder Ratio zweier Kanäle über die Zeit (Median/Mittelwert pro Bin,
  Einzel-Events, Dichte, Ridgeline pro Zeitfenster)
- **Statistik**: Count / %positiv / %negativ pro Datei oder für die ganze Kohorte
- **Histogramm-Overlay** mehrerer Dateien (übereinander, versetzt, gestaffelt, getrennte Panels)
- **Panel-Export**: beliebig viele Populationen × Marker × Statistiken (Anzahl, % Eltern-Gate,
  MFI Median / Mean / geom. Mean, rCV) für alle Dateien in **einer** Excel-Tabelle mit Spalten-Heatmap
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
│   ├── gating_gui.py         # GUI (alle Tabs)
│   ├── gating.py             # Gates & Gate-Sets: Datenmodell, Laden/Speichern, Anwenden
│   ├── compensation.py       # Spillover-Matrizen: lesen, anwenden, speichern, CSV
│   ├── wsp_import.py         # FlowJo-Workspace (.wsp) importieren
│   ├── panel_export.py       # Panel-Export (viele Statistiken, eine Excel-Tabelle)
│   ├── kinetics.py           # Ratio, Zeit-Binning, Kinetik- und Ridgeline-Plots
│   ├── histogram_overlay.py  # Histogramm-Overlays mehrerer Dateien
│   ├── transforms.py         # Achsen-Transformationen (Linear / Log / Logicle)
│   ├── io_utils.py           # FCS-Dateien finden und laden
│   └── clipboard_utils.py    # Plot → Windows-Zwischenablage
├── start.bat                 # Windows: Umgebung einrichten + GUI starten
└── requirements.txt
```

Pro Experiment-Ordner (über "Experiment-Ordner wählen..." links in der GUI) legt das Tool
Unterordner an: `gates/` (Gate-Sets), `strategies/` (ganze Gate-Bäume), `compensation/`
(Experiment-Matrix), `panels/` (Panel-Vorlagen) und `exports/` (Exporte). Messdaten (`*.fcs`)
und Ergebnisse (`*.csv`, `*.png`, `exports/`) werden per `.gitignore` bewusst **nicht**
versioniert. Beim Start lassen sich die Ordner auch über `--input-dir` und `--gates-dir` angeben.

**Dateinamen-Konvention:** Steht im Dateinamen `baseline <N>` (z.B.
`MEC-1 baseline 60 + Lysate.fcs`), wird das als Baseline-/Stimulus-Zeitpunkt in Sekunden
gelesen — er wird in den Kinetik-Plots markiert und für Fold-Change und Ridgeline-Farben genutzt.

## Bedienung

### Start und Gating

Unter Windows `start.bat` doppelklicken, sonst:

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
Datei wechseln → Gate-Linie anpassen → mit **demselben Namen/Kind-Gates** unter Scope `file`
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
- **Boolean-Gate**: Button "Boolean-Gate (UND / ODER / NICHT)..." unter dem Gate-Baum kombiniert
  Kind-Gates der aktuell angezeigten Population, z.B. `CD4+` UND NICHT `CD8+` → CD4+CD8-, oder
  nur NICHT `Debris` → alles im Parent außer Debris. Das neue Gate liegt (wie in FlowJo) unter
  derselben Population und enthält Kopien der verwendeten Gate-Formen — wird ein verwendetes
  Gate später umgezeichnet, das Boolean-Gate neu anlegen
- **Bereichs-Gate (1D)**: entsteht beim FlowJo-Import (z.B. `Comp-PE-A+`), wird als zwei
  senkrechte Linien angezeigt, sobald der X-Kanal passt

**Scope-Optionen:**

| Scope | Wirkung | Beispiel |
|---|---|---|
| `all` | gilt für **alle** Dateien im Ordner | generisches Zell-/Singlet-Gate |
| `group` + Pattern `ZCL278` | gilt für alle Dateien, deren Name `ZCL278` enthält | bedingungsspezifisches Gate |
| `file` | gilt nur für die aktuell ausgewählte Datei | Ausreißer-Datei manuell nachjustieren |

**Welches Gate gilt für eine Datei?** Gate-Sets mit gleichem Namen sind Varianten derselben
Population. Pro Datei wird davon **genau eine** verwendet, die spezifischste:
`file` vor `group` vor `all` (bei mehreren passenden Gruppen das längere Pattern). Eine Datei
mit eigenem `file`-Gate rechnet also nur mit diesem — nie mit einer Mischung aus `all`- und
`file`-Gate; alle anderen Dateien behalten das `all`-Gate. Das gilt einheitlich für Statistik,
Kinetik, Overlay und Panel-Export (Blatt "Gates" im Excel-Export zeigt je Datei die verwendete
Variante). Gate-Sets mit **verschiedenen** Namen werden dagegen per UND gestapelt (z.B. ein
allgemeines Singlet-Gate plus ein gruppenspezifisches Debris-Gate).

Jede Variante liegt in einer eigenen Datei (`Name.json`, `Name__grp_<Pattern>.json`,
`Name__<Dateiname>.json`). Wird eine Population für eine Datei/Gruppe neu gespeichert, bietet
die GUI an, die **darunterliegenden Populationen** (z.B. `CD19+` unter `Lymphocytes`) für diese
Datei/Gruppe mitzuziehen — sonst würden sie dort weiter mit dem alten Eltern-Gate rechnen.

### FlowJo-Workspace importieren

Links **"FlowJo-Workspace (.wsp) importieren..."**: liest einen FlowJo-Workspace und übernimmt
nach einer Übersicht (Proben, Populationen, Kompensation) wahlweise:

- **Gate-Sets** — jede Population wird ein Gate-Set (Name wie in FlowJo; kommt ein Name unter
  mehreren Eltern vor, mit Eltern-Präfix, z.B. `CD3+CD56+/Comp-APC-A+`). Die häufigste
  Gate-Definition → Scope `all`; Proben mit individuell angepasstem Gate (oder angepasstem
  Gate darüber) → eigene `file`-Variante, bzw. `group`, wenn genau eine FlowJo-Gruppe gleich
  abweicht und ihr Name im Dateinamen steht. Fehlt eine Population in manchen Proben, gibt es
  kein `all`, nur `group`/`file`
- **Gate-Hierarchie** als Gating-Strategie (bei unterschiedlichen Bäumen eine pro Gruppe), die
  direkt im Gating-Tab geöffnet wird
- **Kompensation** — die in FlowJo verwendete (ggf. dort bearbeitete) Matrix als
  Experiment-Matrix; nötig, damit die Gates zu den Daten passen

Polygon-Kanten werden wie in FlowJo auf der Biex-/Logicle-Skala gerade gezogen, Events am
Skalenrand bleiben in randständigen Gates. Nach dem Import werden die Zählungen der aktuellen
Datei mit den in FlowJo gespeicherten verglichen; Abweichungen liegen typischerweise unter
0,5 %, da FlowJos Biex-Berechnung nicht offengelegt ist (die Umrechnung entspricht exakt der
Auswertung durch FlowKit).

### Kinetik-Tab (direkt in der GUI)

Im selben Fenster gibt es oben einen zweiten Tab **"Kinetik"**, der sofort die Zeitkinetik der
aktuell gegateten Population der ausgewählten Datei zeigt — praktisch um den Effekt eines
Gates sofort zu kontrollieren:

- **Einzelkanal**: ein beliebiger Kanal (z.B. `PerCP-A`) über die Zeit
- **Ratio (Kanal A / Kanal B)**: z.B. `PerCP-A / Qdot 655-A`, mit einstellbarem "Min. Signal"
  (Rausch-Ausschluss)
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
  einzelnen Kinetik-Plot als PNG (`<Dateiname>_kinetics.png`) in einen frei wählbaren Zielordner,
  mit den aktuellen Kanal-/Ratio-/Bin-Einstellungen
- **"Plot in Zwischenablage kopieren"** (verfügbar im Gating-, Kinetik- und Statistik-Tab):
  kopiert den aktuell angezeigten Plot als Bild in die Windows-Zwischenablage — direkt mit
  **Strg+V** in PowerPoint, Word o.ä. einfügbar, kein Screenshot nötig

### Statistik-Tab (direkt in der GUI)

Tab **"Statistik"**: Count / %positive / %negative für einen Kanal oder ein Ratio,
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

Tab **"Histogramm-Overlay"**: die Verteilung eines Kanals oder Ratios für mehrere
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

### Kompensation

FACSDiva speichert die Messwerte in der `.fcs`-Datei **unkompensiert** und hängt nur die
Spillover-Matrix an (`$SPILL`/`$SPILLOVER`; `APPLY COMPENSATION=TRUE` ist bloß ein Hinweis an die
Auswertesoftware). Links unter **"Kompensation"** wird gewählt, womit ALLE Tabs und Exporte rechnen:

- **Matrix aus der FCS-Datei** (Standard) — entspricht der Anzeige in Diva/FlowJo
- **Experiment-Matrix (bearbeitet)** — gespeichert in `<Experiment>/compensation/matrix.json`
- **Keine** — Rohdaten

Tab **"Kompensation"**: X (Fluorochrom) und Y (Detektor) wählen, dann die Population im Plot
**greifen und ziehen** — senkrecht ändert den Spillover X → Y, waagerecht Y → X. Die rote Linie
(Median von Y entlang X) sollte bei korrekter Kompensation waagerecht auf Höhe der grauen Linie
(Median der X-negativen Zellen) liegen; am besten an den Einzelfärbungen prüfen. Werte lassen sich
auch direkt in der NxN-Tabelle (in %) eintippen; Klick auf eine Zelle zeigt das Paar.
"Rückgängig", CSV-Import/-Export, Start aus Datei-Matrix oder Einheitsmatrix.
**"Speichern & auf alle Dateien anwenden"** speichert die Experiment-Matrix und schaltet alle Tabs
darauf um. Gates auf Fluoreszenzkanälen danach kurz kontrollieren.

### Panel-Export-Tab (direkt in der GUI)

Tab **"Panel-Export"**: mehrere Messgrößen auf einmal für alle Dateien, als eine
Tabelle im Wide-Format (eine Zeile pro Probe, eine Spalte pro Population | Marker | Statistik).

- **Population** = gespeichertes Gate-Set. Die Statistik wird immer auf genau dieser Population
  berechnet: `CD4+CD8-` → MFI aller CD4+CD8- Zellen; `CD4+CD107a+` → nur die CD107a+ Zellen.
  Pro Datei gilt die passende Gate-Variante (Datei > Gruppe > alle), wie überall in der GUI.
- **Bezug für %**: Standard ist das **Eltern-Gate** (Gate-Set ohne seinen letzten Schritt), d.h.
  das gezeichnete Gate ist die Positiv-Grenze: `CD4+CD107a+` → % CD107a+ von CD4+. Alternativ
  "Alle Events" oder ein beliebiges anderes Gate-Set.
- **Marker**: mehrere auswählbar, pro Marker wird eine Zeile angelegt (keiner = nur Anzahl / %).
- **Statistiken**: Anzahl, % Bezug, % aller Events, MFI Median, Mean, geometrischer Mean
  (nur Werte > 0) und rCV % — pro Zeile individuell.
- **Schwelle (optional)**: liefert zusätzlich "% > Schwelle" und die Statistiken nur der Zellen
  darüber (Rohwert des Kanals).
- Panels als Vorlage speichern/laden (`panels/`), um sie in anderen Experimenten wiederzuverwenden.
- Export als **Excel** (Blatt "Daten" mit Heatmap pro Spalte, "Panel" = Definition,
  "Gates" = verwendete Gate-Variante je Datei) oder als CSV.

## Hinweise

- Ratio wird nur aus Events berechnet, bei denen **beide** Kanäle > "Min. Signal" sind
  (Default 1.0), um Rauschen nahe Null/negative Rohwerte (treten in FCS-Rohdaten normal auf)
  nicht als Artefakte ins Ratio einfließen zu lassen.
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
