"""Policies PDF → one chunk per (sub)section → local embeddings → Chroma (SPEC §7).

Run with ``python -m emporio.rag.ingest [--force]``. The build is idempotent: the collection
stores a fingerprint of the PDF, the embedding model and the chunker version, and is only
rebuilt when one of them changes. The retriever calls :func:`ensure_index` on first use, so
running this module by hand is optional.

Chunking uses pypdf's *layout* mode, which keeps one line per visual line: section headings
("4.", "4.1") start at column 0, while numbered list items (manual §7.2) are indented. A
heading is only accepted when its number is the next one in sequence, which also guards
against stray numbers at the start of a line ("13.709/2018").
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import chromadb
from chromadb.config import Settings
from fastembed import TextEmbedding
from pypdf import PdfReader

from emporio import config

logger = logging.getLogger(__name__)

COLLECTION = "policies"
CHUNKER_VERSION = "1"

_RUNNING_HEADER = "Empório da Música Manual de Políticas e Procedimentos"
_FOOTER = re.compile(r"^Página \d+$")
_HEADING = re.compile(r"^(?P<major>\d+)(?:\.(?P<minor>\d+))?\.?\s+(?P<title>\S.*)$")
_CELL_GAP = re.compile(r"\s{3,}")
_LIST_START = re.compile(r"^(•|\d+\.\s)")
_WRAPPED_CELL_INDENT = 20


@dataclass(frozen=True)
class PolicyChunk:
    section: str  # "4.1" (or "4" for a top-level section)
    title: str  # "Direito de Arrependimento (Compras Online)"
    parent_title: str  # "Política de Trocas e Devoluções"; "" for top-level sections
    page: int  # page where the heading appears
    text: str  # body without the heading

    @property
    def chunk_id(self) -> str:
        return f"sec-{self.section}"

    @property
    def heading(self) -> str:
        own = f"{self.section} {self.title}"
        return f"{self.parent_title} — {own}" if self.parent_title else own

    def embedding_text(self) -> str:
        """Heading prefixed to the body, so short sections still retrieve by their topic."""
        return f"{self.heading}\n{self.text}"


# --------------------------------------------------------------------------- extraction


def read_layout_lines(pdf_path: Path) -> list[tuple[int, str]]:
    """(page number, raw line) for every content line; running header and footer dropped."""
    lines: list[tuple[int, str]] = []
    for page_number, page in enumerate(PdfReader(pdf_path).pages, start=1):
        text = page.extract_text(extraction_mode="layout") or ""
        for raw in text.splitlines():
            stripped = raw.strip()
            if stripped == _RUNNING_HEADER or _FOOTER.match(stripped):
                continue
            lines.append((page_number, raw.rstrip()))
    return lines


def clean_body(raw_lines: Iterable[str]) -> str:
    """Rejoin wrapped lines, keep bullets and table rows on their own line.

    Table cells (separated by wide gaps in layout mode) become ``a | b | c``; a cell that
    wrapped onto a heavily indented line is glued back to its row.
    """
    out: list[str] = []
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        indent = len(raw) - len(raw.lstrip())
        line = re.sub(r"^(•|\d+\.)\s+", r"\1 ", stripped)  # bullet gap is not a table cell
        line = _CELL_GAP.sub(" | ", line)
        is_row = " | " in line
        if out:
            prev = out[-1]
            prev_is_row = " | " in prev
            wrapped_cell = prev_is_row and not is_row and indent >= _WRAPPED_CELL_INDENT
            continuation = (
                not is_row
                and not prev_is_row
                and not _LIST_START.match(line)
                and not prev.endswith(".")
            )
            if wrapped_cell or continuation:
                out[-1] = f"{prev} {line}"
                continue
        out.append(line)
    return "\n".join(out)


def chunk_lines(lines: list[tuple[int, str]]) -> list[PolicyChunk]:
    """Split the manual into one chunk per subsection, plus the intro of each top-level section.

    A top-level section without subsections is a single chunk; the intro text of a section that
    has subsections becomes its own chunk only when it is not empty. Text before the first
    heading (the cover page) is dropped: contacts and hours live in the prompt (SPEC §7).
    """
    chunks: list[PolicyChunk] = []
    major, minor = 0, 0
    parent_title = ""
    current: dict | None = None
    body: list[str] = []

    def flush() -> None:
        if current is None:
            return
        text = clean_body(body)
        if text:
            chunks.append(PolicyChunk(text=text, **current))

    for page, raw in lines:
        match = _HEADING.match(raw)  # raw line: headings start at column 0
        if match:
            m_major = int(match["major"])
            m_minor = int(match["minor"]) if match["minor"] else None
            title = match["title"].strip()
            if m_minor is None and m_major == major + 1:
                flush()
                major, minor, parent_title = m_major, 0, title
                current = {"section": str(major), "title": title, "parent_title": "", "page": page}
                body = []
                continue
            if m_minor is not None and m_major == major and m_minor == minor + 1:
                flush()
                minor = m_minor
                current = {
                    "section": f"{major}.{minor}",
                    "title": title,
                    "parent_title": parent_title,
                    "page": page,
                }
                body = []
                continue
        if current is not None:
            body.append(raw)
    flush()
    return chunks


def load_chunks(pdf_path: Path = config.POLICIES_PDF) -> list[PolicyChunk]:
    return chunk_lines(read_layout_lines(pdf_path))


# --------------------------------------------------------------------------- embeddings + store


@lru_cache(maxsize=2)
def get_embedder(
    model_name: str = config.EMBEDDING_MODEL,
    cache_dir: Path = config.EMBEDDING_CACHE_DIR,
) -> TextEmbedding:
    """Local ONNX embedding model (downloaded once into ``storage/models``)."""
    return TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))


def embed(texts: list[str], model_name: str = config.EMBEDDING_MODEL) -> list[list[float]]:
    return [vector.tolist() for vector in get_embedder(model_name).embed(texts)]


def chroma_client(chroma_dir: Path = config.CHROMA_DIR) -> chromadb.ClientAPI:
    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(chroma_dir), settings=Settings(anonymized_telemetry=False)
    )


def fingerprint(pdf_path: Path, model_name: str) -> str:
    digest = hashlib.sha256(pdf_path.read_bytes())
    digest.update(f"|{model_name}|{CHUNKER_VERSION}".encode())
    return digest.hexdigest()[:16]


def _current_fingerprint(client: chromadb.ClientAPI) -> str | None:
    try:
        collection = client.get_collection(COLLECTION)
    except Exception:  # missing collection (exception type changed across chroma versions)
        return None
    return (collection.metadata or {}).get("fingerprint")


def build_index(
    pdf_path: Path = config.POLICIES_PDF,
    chroma_dir: Path = config.CHROMA_DIR,
    model_name: str = config.EMBEDDING_MODEL,
    force: bool = False,
) -> tuple[list[PolicyChunk], bool]:
    """Create the ``policies`` collection. Returns (chunks, rebuilt?)."""
    chunks = load_chunks(pdf_path)
    client = chroma_client(chroma_dir)
    expected = fingerprint(pdf_path, model_name)
    if not force and _current_fingerprint(client) == expected:
        return chunks, False

    if COLLECTION in _collection_names(client):
        client.delete_collection(COLLECTION)
    collection = client.create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine", "fingerprint": expected}
    )
    collection.add(
        ids=[c.chunk_id for c in chunks],
        embeddings=embed([c.embedding_text() for c in chunks], model_name),
        documents=[c.text for c in chunks],
        metadatas=[
            {"section": c.section, "title": c.title, "parent_title": c.parent_title, "page": c.page}
            for c in chunks
        ],
    )
    logger.info("indexed %d policy chunks into %s", len(chunks), chroma_dir)
    return chunks, True


def _collection_names(client: chromadb.ClientAPI) -> set[str]:
    return {getattr(c, "name", c) for c in client.list_collections()}


def ensure_index(
    pdf_path: Path = config.POLICIES_PDF,
    chroma_dir: Path = config.CHROMA_DIR,
    model_name: str = config.EMBEDDING_MODEL,
) -> None:
    """Build the index if it is missing or stale; no-op otherwise."""
    build_index(pdf_path, chroma_dir, model_name, force=False)


def main(argv: list[str] | None = None) -> None:
    from rich.console import Console
    from rich.table import Table

    parser = argparse.ArgumentParser(description="Indexa o manual de políticas no Chroma.")
    parser.add_argument("--force", action="store_true", help="recria o índice do zero")
    args = parser.parse_args(argv)

    chunks, rebuilt = build_index(force=args.force)
    console = Console()
    table = Table(title=f"Trechos do manual ({len(chunks)})")
    for column in ("seção", "título", "pág.", "caracteres"):
        table.add_column(column)
    for c in chunks:
        table.add_row(c.section, c.title, str(c.page), str(len(c.text)))
    console.print(table)
    status = "índice recriado" if rebuilt else "índice já atualizado (use --force para recriar)"
    console.print(f"{status}: {config.CHROMA_DIR}")


if __name__ == "__main__":
    main()
