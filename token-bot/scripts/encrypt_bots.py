import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bot_registry import BotRegistry
from app.config import Settings
from app.crypto import BotCipher


def main():
    settings = Settings()
    cipher = BotCipher(base64.b64decode(settings.master_key_b64))
    reg = BotRegistry(settings.source_bot_dir, settings.encrypted_bot_dir, cipher)

    if not reg.source_dir.exists():
        raise SystemExit(f"Source bot dir không tồn tại: {reg.source_dir}")

    results = reg.encrypt_all()
    print(f"Source dir : {reg.source_dir}")
    print(f"Output dir : {reg.encrypted_dir}")
    print(f"Đã mã hóa  : {len(results)} bot package")
    for r in results:
        print(
            f"  - {r['bot_id']}  v{r['version']}  "
            f"plain={r['plain_size']}b  enc={r['encrypted_size']}b  "
            f"sha256={r['plain_sha256'][:16]}…"
        )


if __name__ == "__main__":
    main()
