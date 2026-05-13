import json
import re
from pathlib import Path
from typing import Any


BOT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
REQUIRED_KEYS = (
    "manifest_version",
    "bot_id",
    "bot_name",
    "owner",
    "version",
    "description",
    "runtime_language",
    "entrypoint",
)


class ManifestError(ValueError):
    pass


def load_and_validate(manifest_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ManifestError(f"không đọc được manifest {manifest_path}: {e}") from e
    if not isinstance(data, dict):
        raise ManifestError(f"{manifest_path}: manifest phải là JSON object")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ManifestError(f"{manifest_path}: thiếu key bắt buộc {missing}")
    if data["manifest_version"] != 1:
        raise ManifestError(f"{manifest_path}: manifest_version phải = 1")
    if not BOT_ID_RE.match(str(data["bot_id"])):
        raise ManifestError(
            f"{manifest_path}: bot_id '{data['bot_id']}' không khớp regex {BOT_ID_RE.pattern}"
        )
    return data


def public_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "bot_id": manifest["bot_id"],
        "bot_name": manifest["bot_name"],
        "owner": manifest["owner"],
        "version": manifest["version"],
        "description": manifest["description"],
        "runtime_language": manifest["runtime_language"],
    }
