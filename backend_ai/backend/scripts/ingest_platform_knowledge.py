from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.platform_knowledge import PlatformKnowledgeStore  # noqa: E402


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_stack: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        tag_l = str(tag or "").lower()
        if tag_l in {"script", "style", "noscript", "svg"}:
            self._skip_stack.append(tag_l)
        if tag_l in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        tag_l = str(tag or "").lower()
        if self._skip_stack and self._skip_stack[-1] == tag_l:
            self._skip_stack.pop()
        if tag_l in {"p", "div", "section", "article", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        text = str(data or "").strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        rendered = " ".join(self.parts)
        rendered = re.sub(r"\s+", " ", rendered)
        rendered = re.sub(r"\s+\n\s+", "\n", rendered)
        return rendered.strip()


def _source_key_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "web").replace(":", "_")
    path = re.sub(r"[^a-zA-Z0-9]+", "-", parsed.path or "root").strip("-")
    return f"web:{host}:{path or 'root'}"[:180]


def _fetch_url_text(url: str, *, timeout_sec: float) -> str:
    headers = {
        "User-Agent": "CNTxLabsKnowledgeIngest/1.0 (+manual curated ingest)",
        "Accept": "text/html, text/plain;q=0.9, */*;q=0.5",
    }
    with httpx.Client(timeout=timeout_sec, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        content_type = str(response.headers.get("content-type") or "").lower()
        if "html" in content_type:
            parser = _TextExtractor()
            parser.feed(response.text)
            return parser.text()
        return response.text.strip()


def _read_file_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _chunk_text(text: str, *, max_chars: int) -> list[str]:
    clean = re.sub(r"\n{3,}", "\n\n", str(text or "").strip())
    if len(clean) <= max_chars:
        return [clean] if clean else []

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", clean) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        current = paragraph[:max_chars]
    if current:
        chunks.append(current)
    return chunks


def ingest_source(
    *,
    url: str = "",
    file: str = "",
    source_key: str = "",
    title: str = "",
    source_type: str = "web",
    trust_level: int = 50,
    max_chars: int = 1800,
    timeout_sec: float = 20.0,
    metadata: dict | None = None,
    store: PlatformKnowledgeStore | None = None,
) -> tuple[str, int]:
    if bool(url) == bool(file):
        raise ValueError("exactly_one_source_required")

    if url:
        text = _fetch_url_text(url, timeout_sec=timeout_sec)
        resolved_source_key = source_key or _source_key_from_url(url)
        resolved_title = title or url
        resolved_url = url
    else:
        path = Path(file).resolve()
        text = _read_file_text(path)
        resolved_source_key = source_key or f"file:{path.name}"
        resolved_title = title or path.stem
        resolved_url = ""

    chunks = _chunk_text(text, max_chars=max(400, int(max_chars)))
    if not chunks:
        raise ValueError("no_content_to_ingest")

    knowledge_store = store or PlatformKnowledgeStore()
    for idx, chunk in enumerate(chunks, start=1):
        knowledge_store.upsert_chunk_sync(
            source_key=resolved_source_key,
            source_type=source_type,
            title=f"{resolved_title} #{idx}" if len(chunks) > 1 else resolved_title,
            url=resolved_url,
            content=chunk,
            trust_level=trust_level,
            metadata={
                **dict(metadata or {}),
                "ingest_tool": "scripts/ingest_platform_knowledge.py",
                "chunk_index": idx,
                "chunk_count": len(chunks),
            },
        )
    return resolved_source_key, len(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Curated ingest for CNTx platform AI knowledge.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Approved public URL to ingest.")
    source.add_argument("--file", help="Approved local text/markdown file to ingest.")
    parser.add_argument("--source-key", default="", help="Stable source key. Default derives from URL/file.")
    parser.add_argument("--title", default="", help="Human readable source title.")
    parser.add_argument("--source-type", default="web", choices=["web", "manual", "internal_doc", "rss", "partner_api"])
    parser.add_argument("--trust-level", type=int, default=50)
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--timeout-sec", type=float, default=20.0)
    args = parser.parse_args()

    source_key, chunk_count = ingest_source(
        url=args.url or "",
        file=args.file or "",
        source_key=args.source_key,
        title=args.title,
        source_type=args.source_type,
        trust_level=args.trust_level,
        max_chars=args.max_chars,
        timeout_sec=args.timeout_sec,
    )

    print(f"ingested source_key={source_key} chunks={chunk_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
