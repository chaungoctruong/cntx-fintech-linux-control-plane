import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

from .crypto import BotCipher
from .manifest import ManifestError, load_and_validate, public_summary


SKIP_DIR_PARTS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "var", "logs"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log"}
SKIP_NAMES = {".DS_Store"}


def _is_excluded(p: Path, root: Path) -> bool:
    rel = p.relative_to(root) if p != root else Path()
    if any(part in SKIP_DIR_PARTS for part in rel.parts):
        return True
    if p.suffix in SKIP_SUFFIXES:
        return True
    if p.name in SKIP_NAMES:
        return True
    return False


class BotRegistry:
    def __init__(self, source_dir: Path, encrypted_dir: Path, cipher: BotCipher):
        self.source_dir = Path(source_dir).resolve()
        self.encrypted_dir = Path(encrypted_dir).resolve()
        self.cipher = cipher
        self.encrypted_dir.mkdir(parents=True, exist_ok=True)

    def discover(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.source_dir.exists():
            return []
        packages: list[tuple[Path, dict[str, Any]]] = []
        seen: set[str] = set()
        for manifest_path in sorted(self.source_dir.rglob("bot_manifest.json")):
            if any(part in SKIP_DIR_PARTS for part in manifest_path.parts):
                continue
            pkg_dir = manifest_path.parent
            manifest = load_and_validate(manifest_path)
            bot_id = manifest["bot_id"]
            if pkg_dir.name != bot_id:
                raise ManifestError(
                    f"{manifest_path}: bot_id '{bot_id}' "
                    f"khác tên thư mục '{pkg_dir.name}'"
                )
            if bot_id in seen:
                raise ManifestError(f"bot_id '{bot_id}' xuất hiện nhiều lần trong source")
            seen.add(bot_id)
            packages.append((pkg_dir, manifest))
        return packages

    def _pack_to_tarball(self, pkg_dir: Path) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for p in sorted(pkg_dir.rglob("*")):
                if _is_excluded(p, pkg_dir):
                    continue
                if not (p.is_file() or p.is_dir()):
                    continue
                arcname = str(p.relative_to(pkg_dir))
                tf.add(p, arcname=arcname, recursive=False)
        return buf.getvalue()

    def encrypt_all(self) -> list[dict[str, Any]]:
        results = []
        for pkg_dir, manifest in self.discover():
            results.append(self._encrypt_one(pkg_dir, manifest))
        return results

    def _encrypt_one(self, pkg_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        bot_id = manifest["bot_id"]
        tarball = self._pack_to_tarball(pkg_dir)
        plain_sha = hashlib.sha256(tarball).hexdigest()
        blob = self.cipher.encrypt(tarball, aad=bot_id.encode())

        (self.encrypted_dir / f"{bot_id}.pkg.enc").write_bytes(blob)
        (self.encrypted_dir / f"{bot_id}.manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        meta = {
            "bot_id": bot_id,
            "version": manifest["version"],
            "plain_size": len(tarball),
            "plain_sha256": plain_sha,
            "encrypted_size": len(blob),
        }
        (self.encrypted_dir / f"{bot_id}.meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        return meta

    def list_encrypted(self) -> list[dict[str, Any]]:
        out = []
        for meta_path in sorted(self.encrypted_dir.glob("*.meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            bot_id = meta.get("bot_id")
            if not bot_id:
                continue
            mf_path = self.encrypted_dir / f"{bot_id}.manifest.json"
            try:
                manifest = json.loads(mf_path.read_text(encoding="utf-8"))
                summary = public_summary(manifest)
            except Exception:
                summary = None
            out.append({**meta, "summary": summary})
        return out

    def has(self, bot_id: str) -> bool:
        return (self.encrypted_dir / f"{bot_id}.pkg.enc").exists()

    def get_manifest(self, bot_id: str) -> dict[str, Any]:
        p = self.encrypted_dir / f"{bot_id}.manifest.json"
        if not p.exists():
            raise FileNotFoundError(bot_id)
        return json.loads(p.read_text(encoding="utf-8"))

    def decrypt_package(self, bot_id: str) -> bytes:
        p = self.encrypted_dir / f"{bot_id}.pkg.enc"
        if not p.exists():
            raise FileNotFoundError(bot_id)
        return self.cipher.decrypt(p.read_bytes(), aad=bot_id.encode())
