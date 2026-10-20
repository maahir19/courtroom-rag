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
        return "\n\n".join((p.extract_text() or "") for p in reader.pages)
    return path.read_text(encoding="utf-8", errors="ignore")


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
        if f.name == "claims.txt":  # sample claims, not evidence
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
            if f.is_file() and f.suffix.lower() in SUPPORTED and f.name != "claims.txt"]
    claims_file = d / "claims.txt"
    claims = []
    if claims_file.exists():
        claims = [c.strip() for c in claims_file.read_text(encoding="utf-8").splitlines() if c.strip()]
    pretty = key.split("/")[-1].replace("_", " ").strip().capitalize()
    return {"key": key, "name": pretty, "docs": docs, "claims": claims,
            "uploaded": key.startswith("uploads/")}
