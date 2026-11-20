"""Read and interpret the TEXT segment (metadata) of an FCS file.

Only the HEADER and TEXT segments are read — never the event data — so this is
fast enough to scan hundreds of files (e.g. to compare PMT voltages across a
whole experiment). Keywords are kept exactly as written in the file (upper-
cased, with or without the leading '$'), unlike flowkit which lower-cases
them and strips the '$'.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from compensation import CompMatrix, parse_spill


class FcsMetaError(Exception):
    pass


def _parse_text(raw: bytes) -> dict[str, str]:
    """Split a TEXT segment into keyword/value pairs. The first byte is the
    delimiter; a doubled delimiter inside a keyword or value is an escaped
    literal delimiter."""
    if not raw:
        return {}
    for enc in ("utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    delim = text[0]
    body = text[1:]
    if body.endswith(delim):
        body = body[:-1]
    placeholder = "\x00\x01"
    parts = body.replace(delim * 2, placeholder).split(delim)
    parts = [p.replace(placeholder, delim) for p in parts]
    out: dict[str, str] = {}
    for i in range(0, len(parts) - 1, 2):
        key = parts[i].strip().upper()
        if key:
            out[key] = parts[i + 1].strip()
    return out


def read_keywords(path: str) -> tuple[str, dict[str, str]]:
    """(FCS version, {KEYWORD: value}) of the primary + supplemental TEXT."""
    with open(path, "rb") as fh:
        header = fh.read(58)
        if len(header) < 26 or not header.startswith(b"FCS"):
            raise FcsMetaError(f"{os.path.basename(path)} ist keine gültige FCS-Datei.")
        version = header[:6].decode("ascii", "replace").strip()
        try:
            t_begin, t_end = int(header[10:18]), int(header[18:26])
        except ValueError as exc:
            raise FcsMetaError(f"FCS-Header nicht lesbar: {exc}") from exc
        fh.seek(t_begin)
        keywords = _parse_text(fh.read(t_end - t_begin + 1))

        # FCS 3.x: optional supplemental TEXT segment (rare, but part of the spec)
        try:
            s_begin, s_end = int(_get(keywords, "BEGINSTEXT") or 0), int(_get(keywords, "ENDSTEXT") or 0)
        except ValueError:
            s_begin = s_end = 0
        if s_begin and s_end > s_begin and s_begin != t_begin:
            fh.seek(s_begin)
            raw = fh.read(s_end - s_begin + 1)
            try:
                for k, v in _parse_text(raw).items():
                    keywords.setdefault(k, v)
            except Exception:
                pass
    return version, keywords


def _get(kw: dict[str, str], name: str) -> str | None:
    """Value of a keyword, with or without the '$' prefix."""
    name = name.upper()
    for k in (f"${name}", name):
        v = kw.get(k)
        if v is not None and v != "":
            return v
    return None


# ---- channel parameters -------------------------------------------------------
# (keyword suffix in $Pn<suffix>, column header, short explanation)
PARAM_FIELDS: list[tuple[str, str, str]] = [
    ("N", "Kanal", "$PnN — Kurzname des Kanals"),
    ("S", "Marker / Farbstoff", "$PnS — Marker-/Stain-Name"),
    ("V", "Spannung", "$PnV — Detektor-/PMT-Spannung (Volt)"),
    ("G", "Gain", "$PnG — Verstärkungsfaktor"),
    ("R", "Bereich", "$PnR — Messbereich (max. Wert)"),
    ("B", "Bits", "$PnB — Bits pro Wert"),
    ("E", "Verstärkung", "$PnE — linear (0,0) oder log (Dekaden,Offset)"),
    ("DISPLAY", "Anzeige", "$PnDISPLAY — vorgeschlagene Skala"),
    ("D", "Skala", "$PnD — vorgeschlagene Skala (FCS 3.1)"),
    ("TYPE", "Typ", "$PnTYPE — Art des Kanals"),
    ("L", "Laser (nm)", "$PnL — Anregungswellenlänge"),
    ("O", "Leistung (mW)", "$PnO — Laserleistung"),
    ("F", "Filter", "$PnF — optischer Filter"),
    ("DET", "Detektor", "$PnDET — Detektorname"),
    ("T", "Detektortyp", "$PnT — Detektortyp"),
    ("P", "Emission (%)", "$PnP — Anteil gemessener Emission"),
    ("BS", "BS", "PnBS — (BD) Baseline/Bead-Setting"),
    ("MS", "MS", "PnMS — (BD) herstellerspezifisch"),
]


def _format_param_value(suffix: str, value: str) -> str:
    if suffix == "E":
        try:
            decades, offset = (float(x) for x in value.split(","))
        except ValueError:
            return value
        if decades == 0:
            return "linear"
        return f"log, {decades:g} Dek." + (f" (Offset {offset:g})" if offset not in (0, 1) else "")
    if suffix == "R":
        try:
            return f"{float(value):,.0f}".replace(",", ".")
        except ValueError:
            return value
    return value


@dataclass
class Laser:
    index: int
    name: str = ""
    delay: str = ""
    asf: str = ""


@dataclass
class FcsMetadata:
    path: str
    version: str
    keywords: dict[str, str]
    params: list[dict[str, str]] = field(default_factory=list)  # per channel: suffix -> raw value
    lasers: list[Laser] = field(default_factory=list)
    spill: CompMatrix | None = None

    # -- convenience -----------------------------------------------------------
    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    def get(self, name: str) -> str | None:
        return _get(self.keywords, name)

    def param_value(self, index: int, suffix: str) -> str | None:
        return self.params[index].get(suffix) if 0 <= index < len(self.params) else None

    def used_param_fields(self) -> list[tuple[str, str, str]]:
        """PARAM_FIELDS that at least one channel of this file has."""
        return [f for f in PARAM_FIELDS if any(p.get(f[0]) for p in self.params)]

    def channel_rows(self) -> list[list[str]]:
        fields = self.used_param_fields()
        return [[str(i + 1)] + [_format_param_value(s, p.get(s, "")) if p.get(s) else "" for s, _, _ in fields]
                for i, p in enumerate(self.params)]

    def is_param_keyword(self, key: str) -> bool:
        return bool(_PARAM_KEY_RE.match(key))

    # -- derived values --------------------------------------------------------
    def acquisition_start(self) -> datetime | None:
        return _parse_datetime(self.get("DATE"), self.get("BTIM"))

    def duration(self) -> timedelta | None:
        start = _parse_time(self.get("BTIM"))
        end = _parse_time(self.get("ETIM"))
        if start is None or end is None:
            return None
        d = end - start
        if d.total_seconds() < 0:  # acquisition crossed midnight
            d += timedelta(days=1)
        return d

    def event_count(self) -> int | None:
        try:
            return int(float(self.get("TOT") or ""))
        except ValueError:
            return None

    def event_rate(self) -> float | None:
        n, d = self.event_count(), self.duration()
        if n is None or d is None or d.total_seconds() <= 0:
            return None
        return n / d.total_seconds()

    def summary_sections(self) -> list[tuple[str, list[tuple[str, str, str]]]]:
        """[(section title, [(label, value, keyword source), ...]), ...] —
        only entries that are present in the file."""

        def kw(label, *names, fmt=None):
            for n in names:
                v = self.get(n)
                if v is not None and v.strip():
                    key = next(k for k in (f"${n.upper()}", n.upper()) if self.keywords.get(k) not in (None, ""))
                    return (label, fmt(v) if fmt else v, key)
            return None

        def val(label, value, source=""):
            return (label, value, source) if value not in (None, "") else None

        n = self.event_count()
        d = self.duration()
        rate = self.event_rate()
        start = self.acquisition_start()

        measurement = [
            val("Datum", start.strftime("%d.%m.%Y") if start else self.get("DATE"), "$DATE"),
            kw("Start", "BTIM"),
            kw("Ende", "ETIM"),
            val("Dauer", _format_duration(d) if d else None, "ETIM − BTIM"),
            val("Events", f"{n:,}".replace(",", ".") if n is not None else None, "$TOT"),
            val("Ereignisrate", f"{rate:,.0f} /s".replace(",", ".") if rate else None, "TOT / Dauer"),
            kw("Parameter", "PAR"),
            kw("Zeitschritt", "TIMESTEP", fmt=lambda v: f"{v} s"),
            kw("Volumen", "VOL"),
            kw("Schwelle (Threshold)", "THRESHOLD"),
            kw("Window Extension", "WINDOW EXTENSION"),
            kw("FSC Area Scaling", "FSC ASF"),
            kw("Kompensation anwenden", "APPLY COMPENSATION"),
            kw("Auto-Biexp. (AUTOBS)", "AUTOBS"),
        ]
        sample = [
            kw("Experiment", "EXPERIMENT NAME", "PROJ"),
            kw("Probe / Specimen", "SRC", "SPECIMEN NAME"),
            kw("Tube", "TUBE NAME"),
            kw("Well", "WELL ID"),
            kw("Platte", "PLATE NAME"),
            kw("Proben-Nr.", "SMNO"),
            kw("Sample ID", "SAMPLE ID"),
            kw("Patient ID", "PATIENT ID"),
            kw("Zellen", "CELLS"),
            kw("Kommentar", "COM"),
            kw("Operator", "OP"),
            kw("Exportiert von", "EXPORT USER NAME"),
            kw("Exportiert am", "EXPORT TIME"),
        ]
        instrument = [
            kw("Zytometer", "CYT"),
            kw("Seriennummer", "CYTSN", "CYTNUM"),
            kw("Institut", "INST"),
            kw("Software", "CREATOR"),
            kw("Betriebssystem", "SYS"),
            kw("Gerätekonfiguration", "CYTOMETER CONFIG NAME"),
            kw("Konfiguration erstellt", "CYTOMETER CONFIG CREATE DATE"),
            kw("Assay-/User-Setting", "USERSETTINGNAME"),
            kw("CST Status", "CST SETUP STATUS"),
            kw("CST Setup", "CST SETUP DATE"),
            kw("CST Baseline", "CST BASELINE DATE"),
            kw("CST Beads Lot", "CST BEADS LOT ID"),
        ]
        size = None
        try:
            size = os.path.getsize(self.path)
        except OSError:
            pass
        datatypes = {"F": "Float (32 bit)", "D": "Double (64 bit)", "I": "Integer", "A": "ASCII"}
        byteorders = {"1,2,3,4": "Little-Endian", "4,3,2,1": "Big-Endian", "1,2": "Little-Endian", "2,1": "Big-Endian"}
        file_info = [
            val("FCS-Version", self.version, "Header"),
            kw("Originaler Dateiname", "FIL"),
            val("Dateigröße", _format_bytes(size) if size is not None else None, "Dateisystem"),
            kw("Datentyp", "DATATYPE", fmt=lambda v: datatypes.get(v.upper(), v)),
            kw("Byte-Reihenfolge", "BYTEORD", fmt=lambda v: f"{byteorders.get(v.replace(' ', ''), v)} ({v})"),
            kw("Modus", "MODE", fmt=lambda v: "List-Mode" if v.upper() == "L" else v),
            kw("Zeichensatz", "CHARSET"),
            kw("GUID", "GUID"),
            val("Spillover-Matrix", f"{len(self.spill.channels)} × {len(self.spill.channels)} Kanäle"
                if self.spill else "keine", "$SPILLOVER / SPILL"),
        ]
        sections = [
            ("Messung", measurement),
            ("Probe", sample),
            ("Gerät & Setup", instrument),
            ("Datei", file_info),
        ]
        return [(title, [e for e in entries if e]) for title, entries in sections]


_PARAM_KEY_RE = re.compile(r"^\$?P(\d+)([A-Z]+)$")
_LASER_KEY_RE = re.compile(r"^\$?LASER(\d+)(NAME|DELAY|ASF)$")


def load_metadata(path: str) -> FcsMetadata:
    version, kw = read_keywords(path)
    try:
        n_par = int(float(_get(kw, "PAR") or 0))
    except ValueError:
        n_par = 0
    params: list[dict[str, str]] = [dict() for _ in range(n_par)]
    lasers: dict[int, Laser] = {}
    for key, value in kw.items():
        m = _PARAM_KEY_RE.match(key)
        if m:
            idx, suffix = int(m.group(1)) - 1, m.group(2)
            if idx >= len(params):  # file lies about $PAR — be lenient
                params.extend(dict() for _ in range(idx + 1 - len(params)))
            params[idx][suffix] = value
            continue
        m = _LASER_KEY_RE.match(key)
        if m:
            laser = lasers.setdefault(int(m.group(1)), Laser(int(m.group(1))))
            setattr(laser, {"NAME": "name", "DELAY": "delay", "ASF": "asf"}[m.group(2)], value)

    flowkit_style = {k.lstrip("$").lower(): v for k, v in kw.items()}
    spill = parse_spill(flowkit_style, source=os.path.basename(path))
    return FcsMetadata(
        path=path, version=version, keywords=kw, params=params,
        lasers=[lasers[i] for i in sorted(lasers)], spill=spill,
    )


# ---- formatting helpers ---------------------------------------------------------
def _parse_time(s: str | None) -> timedelta | None:
    """'hh:mm:ss', 'hh:mm:ss.cc' (FCS 3.1) or 'hh:mm:ss:tt' (FCS 2/3.0, 1/60 s)."""
    if not s:
        return None
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?(?:([.:])(\d+))?", s)
    if not m:
        return None
    h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    frac = 0.0
    if m.group(5):
        frac = int(m.group(5)) / 60.0 if m.group(4) == ":" else float("0." + m.group(5))
    return timedelta(hours=h, minutes=mi, seconds=sec + frac)


_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_MONTHS.update({"MAI": 5, "OKT": 10, "DEZ": 12, "MÄR": 3, "MRZ": 3})


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{1,2})[-/ ]([A-Za-zÄä]{3})[A-Za-z]*[-/ ](\d{2,4})$", s)
    if m and m.group(2).upper() in _MONTHS:
        year = int(m.group(3))
        year += 2000 if year < 100 else 0
        try:
            return datetime(year, _MONTHS[m.group(2).upper()], int(m.group(1)))
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _parse_datetime(date: str | None, time: str | None) -> datetime | None:
    d = _parse_date(date)
    if d is None:
        return None
    t = _parse_time(time)
    return d + t if t is not None else d


def _format_duration(d: timedelta) -> str:
    total = d.total_seconds()
    h, rem = divmod(int(total), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min {s:02d} s"
    if m:
        return f"{m} min {s:02d} s"
    return f"{total:.1f} s"


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"
