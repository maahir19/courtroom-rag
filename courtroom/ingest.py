"""Loading documents and cutting them into chunks.

Every chunk gets an id like E1, E2 ... ("E" for exhibit). Both lawyers and the
judge use the same ids, which is what makes the citation check possible later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path

from . import config

SUPPORTED = {".md", ".txt", ".pdf"}
NOT_EVIDENCE = {"claims.txt", "sources.md", "readme.md"}  # helper files that live next to the docs


@dataclass
class Chunk:
    id: str
    doc: str        # file name
    title: str      # document title (first heading or file name)
    section: str    # nearest heading above the chunk
    text: str

    def to_dict(self):
        return asdict(self)


def read_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader  # only import when needed, it's slow-ish

        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "") for p in reader.pages]
        # headers/footers repeat on every page ("Regd. Office: ...", "COMPANY | POLICY NAME").
        # a line that shows up on half the pages or more is page furniture, not content
        from collections import Counter
        seen = Counter(ln.strip() for pg in pages for ln in set(pg.split("\n")) if len(ln.strip()) > 15)
        limit = max(2, len(pages) // 2)
        repeated = {ln for ln, n in seen.items() if n >= limit} if len(pages) >= 3 else set()
        raw = "\n".join(ln for pg in pages for ln in pg.split("\n") if ln.strip() not in repeated)
        raw = re.sub(r"_{3,}", " ", raw)  # blank lines in forms: "Name: ________"
        title = path.stem.replace("_", " ").replace("-", " ").strip()
        title = re.sub(r"^\d+\s+", "", title)
        return f"# {title[:1].upper() + title[1:]}\n\n" + pdf_to_markdown(raw)
    return path.read_text(encoding="utf-8", errors="ignore")


_NUMBERED = re.compile(r"^(\d+(\.\d+)*\.?\s|[•●▪◦\-–]\s|\(?[a-z]\)\s|\(?[ivx]+\)\s)")


def pdf_to_markdown(raw: str) -> str:
    """Real PDFs break lines mid-sentence and have tables of contents full of dot
    leaders ("1. Introduction.......02"). This glues lines back into paragraphs and
    turns short ALL-CAPS lines into headings so chunks keep their section name.
    Found this the hard way when the Reliance policies came out as one giant blob."""
    lines = []
    for ln in raw.split("\n"):
        ln = re.sub(r"(\.\s?){4,}\s*\d*", " ", ln)          # dot leaders in contents pages
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln or re.fullmatch(r"(page\s*)?\d+(\s*of\s*\d+)?", ln, flags=re.I):
            continue
        lines.append(ln)

    out, para = [], []

    def flush():
        if para:
            out.append(" ".join(para))
            para.clear()

    for ln in lines:
        letters = [c for c in ln if c.isalpha()]
        is_heading = (len(ln.split()) <= 9 and len(letters) >= 4
                      and sum(c.isupper() for c in letters) / len(letters) > 0.8
                      and not ln.endswith((".", ",", ";")))
        if is_heading:
            flush()
            out.append("## " + re.sub(r"\d+$", "", ln).strip().title())
            continue
        starts_new = bool(_NUMBERED.match(ln)) or (para and para[-1].endswith((".", ":", ";")) and ln[:1].isupper())
        if starts_new:
            flush()
        # words split across lines with a hyphen: "employ-" + "ees"
        if para and para[-1].endswith("-") and ln[:1].islower():
            para[-1] = para[-1][:-1] + ln
        else:
            para.append(ln)
    flush()
    return "\n\n".join(out)


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _split_sections(text: str):
    """Yield (heading, body) pairs. Works for markdown; plain text becomes one section."""
    heading = ""
    buf: list[str] = []
    for line in text.split("\n"):
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*)", line)
        if m:
            if buf:
                yield heading, "\n".join(buf)
            heading = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if buf:
        yield heading, "\n".join(buf)


def chunk_text(text: str, max_words: int = config.CHUNK_WORDS):
    """Paragraph-aware chunking.

    First version split every N words blindly and it kept cutting sentences like
    "...is not permitted" right before the "not". So now: keep paragraphs together,
    merge small ones, and only hard-split a paragraph if it's huge.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur, cur_len = [], [], 0
    for p in paras:
        # skip the "synthetic sample" disclaimer lines, they're noise for retrieval
        if p.startswith(">"):
            continue
        words = p.split()
        if len(words) > max_words * 1.6:
            # giant paragraph -> split on sentences
            sents = re.split(r"(?<=[.!?])\s+", p)
            for s in sents:
                sl = len(s.split())
                if cur_len + sl > max_words and cur:
                    chunks.append(" ".join(cur))
                    cur, cur_len = [], 0
                cur.append(s)
                cur_len += sl
            continue
        if cur_len + len(words) > max_words and cur:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(p)
        cur_len += len(words)
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def load_collection(folder: Path) -> list[Chunk]:
    files = sorted(f for f in folder.rglob("*") if f.suffix.lower() in SUPPORTED and f.is_file())
    out: list[Chunk] = []
    n = 0
    for f in files:
        if f.name.lower() in NOT_EVIDENCE:  # sample claims / source notes, not evidence
            continue
        raw = _clean(read_file(f))
        if not raw:
            continue
        title_match = re.search(r"^#\s+(.+)$", raw, flags=re.M)
        title = title_match.group(1).strip() if title_match else f.stem.replace("_", " ")
        for heading, body in _split_sections(raw):
            if heading == title and not body.strip():
                continue
            for piece in chunk_text(body):
                if len(piece.split()) < 8:  # tiny leftovers like "Version 2.4" aren't useful
                    continue
                n += 1
                out.append(Chunk(id=f"E{n}", doc=f.name, title=title,
                                 section=heading if heading != title else "", text=piece))
    return out


def list_collections() -> list[dict]:
    cols = []
    if not config.DATA_DIR.exists():
        return cols
    for d in sorted(config.DATA_DIR.iterdir()):
        if not d.is_dir():
            continue
        if d.name == "uploads":
            # each upload batch is its own folder inside uploads/
            for u in sorted(d.iterdir()):
                if u.is_dir():
                    cols.append(_describe(u, f"uploads/{u.name}"))
            continue
        cols.append(_describe(d, d.name))
    return cols


def _describe(d: Path, key: str) -> dict:
    docs = [f.name for f in sorted(d.rglob("*"))
            if f.is_file() and f.suffix.lower() in SUPPORTED and f.name.lower() not in NOT_EVIDENCE]
    claims_file = d / "claims.txt"
    claims = []
    if claims_file.exists():
        claims = [c.strip() for c in claims_file.read_text(encoding="utf-8").splitlines() if c.strip()]
    pretty = key.split("/")[-1].replace("_", " ").strip().capitalize()
    return {"key": key, "name": pretty, "docs": docs, "claims": claims,
            "uploaded": key.startswith("uploads/")}
