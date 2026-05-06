from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.ingest_platform_knowledge import ingest_source  # noqa: E402


def _load_manifest(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest_must_be_object")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ValueError("manifest_sources_must_be_list")
    return payload


def _host_allowed(url: str, allowed_domains: list[str]) -> bool:
    host = str(urlparse(url).hostname or "").lower()
    if not host:
        return False
    normalized = [str(domain or "").strip().lower().lstrip(".") for domain in allowed_domains if str(domain or "").strip()]
    if not normalized:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in normalized)


def _source_enabled(source: dict) -> bool:
    return bool(source.get("enabled", True))


def _validate_source(source: dict, *, manifest_path: Path, allowed_domains: list[str]) -> tuple[str, str]:
    if not isinstance(source, dict):
        raise ValueError("source_must_be_object")
    url = str(source.get("url") or "").strip()
    file_value = str(source.get("file") or "").strip()
    if bool(url) == bool(file_value):
        raise ValueError("source_must_have_exactly_one_url_or_file")
    if url and not _host_allowed(url, allowed_domains):
        raise ValueError(f"url_host_not_allowed:{urlparse(url).hostname or ''}")
    if file_value:
        path = (manifest_path.parent / file_value).resolve() if not Path(file_value).is_absolute() else Path(file_value).resolve()
        if not path.is_file():
            raise ValueError(f"source_file_missing:{path}")
        return "", str(path)
    return url, ""


def ingest_manifest(path: Path, *, dry_run: bool = False) -> dict:
    manifest = _load_manifest(path)
    allowed_domains = [str(item or "").strip() for item in manifest.get("allowed_domains") or []]
    defaults = manifest.get("defaults") if isinstance(manifest.get("defaults"), dict) else {}

    summary = {"ingested": 0, "skipped": 0, "failed": 0, "items": []}
    for idx, source in enumerate(manifest["sources"], start=1):
        item_result = {"index": idx, "status": "", "source_key": "", "chunks": 0, "error": ""}
        try:
            if not _source_enabled(source):
                item_result["status"] = "skipped_disabled"
                summary["skipped"] += 1
                summary["items"].append(item_result)
                continue

            url, file_value = _validate_source(source, manifest_path=path, allowed_domains=allowed_domains)
            source_key = str(source.get("source_key") or "").strip()
            title = str(source.get("title") or "").strip()
            source_type = str(source.get("source_type") or defaults.get("source_type") or ("web" if url else "internal_doc")).strip()
            trust_level = int(source.get("trust_level") or defaults.get("trust_level") or 50)
            max_chars = int(source.get("max_chars") or defaults.get("max_chars") or 1800)
            timeout_sec = float(source.get("timeout_sec") or defaults.get("timeout_sec") or 20.0)
            metadata = {
                "manifest": str(path),
                "tags": source.get("tags") or [],
                "refresh_interval_sec": source.get("refresh_interval_sec") or defaults.get("refresh_interval_sec"),
                "license": source.get("license") or defaults.get("license"),
                "repository": source.get("repository") or defaults.get("repository"),
                "training_use": source.get("training_use") or defaults.get("training_use"),
            }

            if dry_run:
                item_result["status"] = "dry_run"
                item_result["source_key"] = source_key
                summary["skipped"] += 1
                summary["items"].append(item_result)
                continue

            resolved_source_key, chunks = ingest_source(
                url=url,
                file=file_value,
                source_key=source_key,
                title=title,
                source_type=source_type,
                trust_level=trust_level,
                max_chars=max_chars,
                timeout_sec=timeout_sec,
                metadata=metadata,
            )
            item_result["status"] = "ingested"
            item_result["source_key"] = resolved_source_key
            item_result["chunks"] = chunks
            summary["ingested"] += 1
        except Exception as exc:
            item_result["status"] = "failed"
            item_result["error"] = str(exc)[:240]
            summary["failed"] += 1
        summary["items"].append(item_result)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch ingest approved CNTx platform AI knowledge sources.")
    parser.add_argument("--manifest", required=True, help="JSON manifest with approved sources.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    summary = ingest_manifest(Path(args.manifest).resolve(), dry_run=bool(args.dry_run))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if int(summary.get("failed") or 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
