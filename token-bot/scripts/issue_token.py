import argparse
import base64
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bot_registry import BotRegistry
from app.config import Settings
from app.crypto import BotCipher
from app.db import make_engine, make_session_factory
from app.models import Base, Partner, Token
from app.token_service import TokenService


def main():
    ap = argparse.ArgumentParser(description="Cấp token cho partner")
    ap.add_argument("--partner", required=True, help="partner_id")
    ap.add_argument("--bot", action="append", required=True, help="bot_id (lặp lại được)")
    ap.add_argument("--ttl", type=int, default=None, help="TTL giây, mặc định lấy từ .env")
    args = ap.parse_args()

    settings = Settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)

    cipher = BotCipher(base64.b64decode(settings.master_key_b64))
    reg = BotRegistry(settings.source_bot_dir, settings.encrypted_bot_dir, cipher)

    unknown = [b for b in args.bot if not reg.has(b)]
    if unknown:
        raise SystemExit(f"Các bot_id sau chưa được mã hóa: {unknown}")

    with sf() as s:
        partner = s.get(Partner, args.partner)
        if partner is None or not partner.active:
            raise SystemExit(f"partner {args.partner} không tồn tại hoặc đã deactivate")

    ts = TokenService(settings.jwt_secret)
    ttl = args.ttl or settings.token_default_ttl_sec
    token, jti, exp = ts.issue(args.partner, args.bot, ttl)

    with sf() as s:
        s.add(
            Token(
                jti=jti,
                partner_id=args.partner,
                bot_ids_json=json.dumps(args.bot),
                issued_at=datetime.utcnow(),
                expires_at=exp.replace(tzinfo=None),
                revoked=False,
            )
        )
        s.commit()

    print(
        json.dumps(
            {
                "token": token,
                "jti": jti,
                "partner_id": args.partner,
                "bot_ids": args.bot,
                "expires_at": exp.isoformat(),
                "ttl_seconds": ttl,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
