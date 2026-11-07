"""WB-owned XLSX reader.

Streams the worksheet XML directly, so a wrong declared dimension (e.g. ``A1``)
never truncates rows or columns. Numbers come back as Decimal (no float
rounding), shared/inline strings as str. Works on a file path or on an XLSX
member inside a ZIP archive (read in memory, the archive is never modified).
"""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, Iterator

from lxml import etree as E

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_COL = re.compile(r"[A-Z]+")


def col_index(ref: str) -> int | None:
    m = _COL.match(ref or "")
    if not m:
        return None
    idx = 0
    for ch in m.group(0):
        idx = idx * 26 + ord(ch) - 64
    return idx - 1


def col_letter(idx: int) -> str:
    s, n = "", idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class Workbook:
    """Open workbook (path or bytes). Use as a context manager."""

    def __init__(self, source: str | Path | bytes):
        self._buf = io.BytesIO(source) if isinstance(source, bytes) else None
        self._zip = zipfile.ZipFile(self._buf if self._buf is not None else source)
        names = set(self._zip.namelist())
        self._shared: list[str] | None = None
        self._has_shared = "xl/sharedStrings.xml" in names
        book = E.fromstring(self._zip.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in E.fromstring(self._zip.read("xl/_rels/workbook.xml.rels"))}
        self.sheets: list[tuple[str, str]] = []
        for s in book.findall("m:sheets/m:sheet", NS):
            target = rels[s.get(REL_ID)]
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            self.sheets.append((s.get("name"), target))
        props = book.find("m:workbookPr", NS)
        self.date_system = "1904" if props is not None and props.get("date1904", "0") in ("1", "true") else "1900"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self._zip.close()

    @property
    def sheet_names(self) -> list[str]:
        return [n for n, _ in self.sheets]

    def _strings(self) -> list[str]:
        if self._shared is None:
            self._shared = []
            if self._has_shared:
                with self._zip.open("xl/sharedStrings.xml") as f:
                    for _, el in E.iterparse(f, events=("end",), tag="{*}si"):
                        self._shared.append("".join(el.xpath(".//m:t/text()", namespaces=NS)))
                        el.clear()
        return self._shared

    def declared_dimension(self, sheet: str) -> str | None:
        target = dict(self.sheets)[sheet]
        with self._zip.open(target) as f:
            for _, el in E.iterparse(f, events=("start",), tag="{*}dimension"):
                return el.get("ref")
                # dimension is near the top; stop at the first sheetData
        return None

    def rows(self, sheet: str | None = None) -> Iterator[tuple[int, list]]:
        """Yield (excel_row_number, values) for every physical row."""
        name = sheet or self.sheets[0][0]
        target = dict(self.sheets).get(name)
        if target is None:
            raise ValueError(f"Unknown sheet {name!r}")
        ss = self._strings()
        with self._zip.open(target) as f:
            row_no = 0
            for _, el in E.iterparse(f, events=("end",), tag="{*}row"):
                row_no = int(el.get("r", row_no + 1))
                out: list = []
                for c in el:
                    idx = col_index(c.get("r", ""))
                    if idx is None:
                        idx = len(out)
                    while len(out) <= idx:
                        out.append(None)
                    t = c.get("t")
                    v = c.find("m:v", NS)
                    if t == "inlineStr":
                        is_ = c.find("m:is", NS)
                        val = "".join(is_.itertext()) if is_ is not None else None
                    elif v is None or v.text is None:
                        val = None
                    elif t == "s":
                        val = ss[int(v.text)]
                    elif t in ("str", "e", "d"):
                        val = v.text
                    elif t == "b":
                        val = bool(int(v.text))
                    else:
                        val = Decimal(v.text)
                    out[idx] = val
                yield row_no, out
                el.clear()
                while el.getprevious() is not None:
                    del el.getparent()[0]


def open_zip_member(zip_path: str | Path) -> tuple[str, bytes]:
    """Return (member_name, bytes) of the single XLSX inside a ZIP export."""
    with zipfile.ZipFile(zip_path) as z:
        members = [i for i in z.infolist() if not i.is_dir()]
        xlsx = [i for i in members if i.filename.lower().endswith(".xlsx")]
        if len(xlsx) != 1:
            raise ValueError(f"{Path(zip_path).name}: expected exactly one .xlsx inside, found {[i.filename for i in members]}")
        info = xlsx[0]
        name = info.filename
        if not (info.flag_bits & 0x800):  # not flagged UTF-8 -> WB uses cp866 names
            try:
                name = info.filename.encode("cp437").decode("cp866")
            except UnicodeError:
                pass
        return name, z.read(info)


def find_header(rows: Iterator[tuple[int, list]], required: list[str], scan: int = 30) -> tuple[int, dict[str, int], list]:
    """Find the header row containing every required title (whitespace-normalised).

    Returns (header_row_number, {title: column_index}, header_values). Titles must be unique.
    """
    want = {norm(r): r for r in required}
    for i, (rn, vals) in enumerate(rows):
        titles = [norm(v) for v in vals]
        if all(w in titles for w in want):
            mapping = {}
            for w, orig in want.items():
                if titles.count(w) != 1:
                    raise ValueError(f"Header {orig!r} is not unique in row {rn}")
                mapping[orig] = titles.index(w)
            return rn, mapping, vals
        if i >= scan:
            break
    raise ValueError(f"Header with {required} not found in the first {scan} rows")


def norm(v) -> str:
    return "" if v is None else " ".join(str(v).replace("\xa0", " ").split())
