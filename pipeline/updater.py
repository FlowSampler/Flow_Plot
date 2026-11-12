"""Update-Pruefung fuer Flow_Plot.

Wird von start.bat vor der GUI aufgerufen. Fragt bei GitHub nach der neuesten
Release (https://github.com/FlowSampler/Flow_Plot/releases). Ist sie neuer als
die installierte Version (pipeline/version.py), wird gefragt, ob aktualisiert
werden soll. Bei "Ja" wird die Release heruntergeladen und die Programmdateien
werden ersetzt; start.bat startet danach neu (und installiert ggf. geaenderte
Pakete aus requirements.txt).

Nicht angefasst werden: .venv, output/, exports/, Messdaten und eigene
Gate-Sets in gates/ (dort werden nur neue Beispieldateien hinzugefuegt).

Exit-Codes: 0 = weiter mit der GUI starten, 3 = Update installiert -> Neustart.

Abschalten: Umgebungsvariable FLOWPLOT_NO_UPDATE=1 setzen.
Nur Standardbibliothek - laeuft also auch, wenn Pakete fehlen.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = "FlowSampler/Flow_Plot"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_TIMEOUT = 5  # Sekunden; ohne Internet soll der Start nicht haengen

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / ".update_manifest.json"  # welche Dateien der Updater installiert hat
SKIP_FILE = ROOT / ".update_skip"           # "diese Version ueberspringen"
BACKUP_DIR = ROOT / ".update_backup"        # Stand vor dem letzten Update

EXIT_CONTINUE = 0
EXIT_UPDATED = 3

# Ordner, die nie veraendert werden (Umgebung, Ergebnisse, Git).
PROTECTED_DIRS = {".git", ".venv", "venv", "env", "output", "exports", ".update_backup"}
# Ordner mit Nutzerdaten: nur fehlende Dateien hinzufuegen, nie ueberschreiben/loeschen.
ADD_ONLY_DIRS = {"gates"}


# --------------------------------------------------------------------------- Version

def local_version() -> str:
    text = (ROOT / "pipeline" / "version.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m.group(1) if m else "0"


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v.split("-")[0].split("+")[0])
    return tuple(int(n) for n in nums) or (0,)


def is_newer(remote: str, local: str) -> bool:
    r, l = parse_version(remote), parse_version(local)
    n = max(len(r), len(l))
    return r + (0,) * (n - len(r)) > l + (0,) * (n - len(l))


def _request(url: str, timeout: float):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Flow_Plot-Updater",
    })
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_latest_release() -> dict | None:
    try:
        with _request(API_LATEST, CHECK_TIMEOUT) as resp:
            return json.load(resp)
    except Exception as exc:  # offline, Proxy, noch keine Release (404), Rate-Limit ...
        print(f"Update-Pruefung uebersprungen ({exc.__class__.__name__}).")
        return None


# --------------------------------------------------------------------------- Installation

def _rel_parts(rel: Path) -> tuple[str, ...]:
    return tuple(p.lower() for p in rel.parts)


def _is_protected(rel: Path) -> bool:
    return bool(rel.parts) and rel.parts[0].lower() in PROTECTED_DIRS


def _is_add_only(rel: Path) -> bool:
    return bool(rel.parts) and rel.parts[0].lower() in ADD_ONLY_DIRS


def download_and_extract(zip_url: str, workdir: Path, progress=None) -> Path:
    """Laedt das Release-ZIP herunter, entpackt es und gibt den Quellordner zurueck."""
    zip_path = workdir / "release.zip"
    with _request(zip_url, 60) as resp, open(zip_path, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(256 * 1024):
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)

    extract_dir = (workdir / "src").resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():  # Schutz vor Pfaden ausserhalb des Zielordners
            target = (extract_dir / member).resolve()
            if extract_dir not in target.parents and target != extract_dir:
                raise RuntimeError(f"Ungueltiger Pfad im Archiv: {member}")
        zf.extractall(extract_dir)

    # GitHub packt alles in einen Ordner "FlowSampler-Flow_Plot-<sha>/"
    entries = [p for p in extract_dir.iterdir()]
    src = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_dir
    if not (src / "pipeline" / "gating_gui.py").exists():
        raise RuntimeError("Das heruntergeladene Archiv sieht nicht wie Flow_Plot aus.")
    return src


def install_from(src: Path) -> None:
    """Kopiert die neuen Dateien nach ROOT; bei einem Fehler wird zurueckgerollt."""
    new_files: list[Path] = []
    for path in src.rglob("*"):
        if path.is_file():
            rel = path.relative_to(src)
            if _is_protected(rel) or "__pycache__" in _rel_parts(rel):
                continue
            new_files.append(rel)

    # Dateien, die das letzte Update installiert hat, es jetzt aber nicht mehr gibt
    old_manifest: list[str] = []
    if MANIFEST.exists():
        try:
            old_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            old_manifest = []
    new_set = {r.as_posix() for r in new_files}
    to_delete = [Path(p) for p in old_manifest
                 if p not in new_set and not _is_protected(Path(p)) and not _is_add_only(Path(p))]

    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    BACKUP_DIR.mkdir()
    created: list[Path] = []   # neu angelegt -> bei Rollback loeschen
    backed_up: list[Path] = [] # ueberschrieben/geloescht -> bei Rollback zurueckkopieren

    try:
        for rel in new_files:
            dst = ROOT / rel
            if dst.exists():
                if _is_add_only(rel):
                    continue  # eigenes Gate-Set o.ae. nie ueberschreiben
                if dst.read_bytes() == (src / rel).read_bytes():
                    continue
                (BACKUP_DIR / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, BACKUP_DIR / rel)
                backed_up.append(rel)
            else:
                created.append(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".update_tmp")
            try:
                shutil.copy2(src / rel, tmp)
                os.replace(tmp, dst)
            finally:
                tmp.unlink(missing_ok=True)

        for rel in to_delete:
            dst = ROOT / rel
            if dst.is_file():
                (BACKUP_DIR / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, BACKUP_DIR / rel)
                backed_up.append(rel)
                dst.unlink()
    except Exception:
        for rel in backed_up:
            try:
                shutil.copy2(BACKUP_DIR / rel, ROOT / rel)
            except Exception:
                pass
        for rel in created:
            try:
                (ROOT / rel).unlink()
            except Exception:
                pass
        raise

    MANIFEST.write_text(json.dumps(sorted(new_set), indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- Dialog

def ask_user(local: str, release: dict) -> str:
    """Zeigt den Update-Dialog. Rueckgabe: 'update', 'later' oder 'skip'."""
    import tkinter as tk
    from tkinter import ttk

    result = {"choice": "later"}
    tag = release.get("tag_name", "?")
    notes = (release.get("body") or "").strip() or "(keine Beschreibung)"

    root = tk.Tk()
    root.title("Flow_Plot - Update verfuegbar")
    root.resizable(True, True)
    root.attributes("-topmost", True)
    frm = ttk.Frame(root, padding=14)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Eine neue Version von Flow_Plot ist verfuegbar.",
              font=("Segoe UI", 11, "bold")).pack(anchor="w")
    ttk.Label(frm, text=f"Installiert: {local}     Neu: {tag}").pack(anchor="w", pady=(4, 8))
    ttk.Label(frm, text="Aenderungen:").pack(anchor="w")

    txt_frame = ttk.Frame(frm)
    txt_frame.pack(fill="both", expand=True, pady=(2, 8))
    txt = tk.Text(txt_frame, width=70, height=14, wrap="word", relief="solid", borderwidth=1)
    sb = ttk.Scrollbar(txt_frame, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    txt.insert("1.0", notes)
    txt.configure(state="disabled")
    txt.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    ttk.Label(frm, text="Eigene Gate-Sets, Ergebnisse und Messdaten bleiben erhalten.",
              foreground="#555").pack(anchor="w", pady=(0, 8))

    btns = ttk.Frame(frm)
    btns.pack(fill="x")

    def choose(c: str) -> None:
        result["choice"] = c
        root.destroy()

    ttk.Button(btns, text="Diese Version ueberspringen",
               command=lambda: choose("skip")).pack(side="left")
    ttk.Button(btns, text="Spaeter", command=lambda: choose("later")).pack(side="right")
    upd = ttk.Button(btns, text="Jetzt aktualisieren", command=lambda: choose("update"))
    upd.pack(side="right", padx=(0, 8))
    upd.focus_set()
    root.bind("<Return>", lambda e: choose("update"))
    root.bind("<Escape>", lambda e: choose("later"))
    root.protocol("WM_DELETE_WINDOW", lambda: choose("later"))

    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    root.mainloop()
    return result["choice"]


def run_update(release: dict) -> bool:
    """Download + Installation mit Fortschrittsfenster. True bei Erfolg."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("Flow_Plot - Update")
    root.attributes("-topmost", True)
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)
    status = ttk.Label(frm, text="Lade Update herunter ...", width=50)
    status.pack(anchor="w")
    bar = ttk.Progressbar(frm, length=360, mode="determinate", maximum=100)
    bar.pack(pady=(8, 0))
    root.update()

    def progress(done: int, total: int) -> None:
        if total:
            bar["value"] = 100 * done / total
        else:
            bar.configure(mode="indeterminate")
            bar.step(2)
        status.configure(text=f"Lade Update herunter ... {done / 1e6:.1f} MB")
        root.update()

    ok = False
    try:
        with tempfile.TemporaryDirectory(prefix="flowplot_update_") as tmp:
            src = download_and_extract(release["zipball_url"], Path(tmp), progress)
            status.configure(text="Installiere Update ...")
            bar.configure(mode="determinate", value=100)
            root.update()
            install_from(src)
        ok = True
        messagebox.showinfo(
            "Flow_Plot - Update",
            f"Version {release.get('tag_name', '')} wurde installiert.\n"
            "Flow_Plot startet jetzt neu.", parent=root)
    except Exception as exc:
        messagebox.showerror(
            "Flow_Plot - Update",
            f"Das Update ist fehlgeschlagen:\n{exc}\n\n"
            "Die bisherige Version bleibt unveraendert und wird jetzt gestartet.", parent=root)
    finally:
        root.destroy()
    return ok


# --------------------------------------------------------------------------- main

def main() -> int:
    if os.environ.get("FLOWPLOT_NO_UPDATE"):
        return EXIT_CONTINUE
    if (ROOT / ".git").exists() and not os.environ.get("FLOWPLOT_UPDATE_IN_GIT"):
        # Entwickler-Checkout: Updates per "git pull", Dateien nicht ueberschreiben.
        return EXIT_CONTINUE

    print("Suche nach Updates ...")
    release = fetch_latest_release()
    if not release or release.get("draft") or release.get("prerelease"):
        return EXIT_CONTINUE

    tag = release.get("tag_name", "")
    local = local_version()
    if not is_newer(tag, local):
        print(f"Flow_Plot ist aktuell ({local}).")
        return EXIT_CONTINUE
    try:
        if SKIP_FILE.read_text(encoding="utf-8").strip() == tag:
            return EXIT_CONTINUE
    except OSError:
        pass

    choice = ask_user(local, release)
    if choice == "skip":
        SKIP_FILE.write_text(tag, encoding="utf-8")
        return EXIT_CONTINUE
    if choice != "update":
        return EXIT_CONTINUE

    return EXIT_UPDATED if run_update(release) else EXIT_CONTINUE


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # der Updater darf den Programmstart nie verhindern
        print(f"Update-Pruefung fehlgeschlagen: {exc}")
        sys.exit(EXIT_CONTINUE)
