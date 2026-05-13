import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.db import make_engine, make_session_factory
from app.models import Base, Partner


def main():
    ap = argparse.ArgumentParser(description="Tạo partner mới")
    ap.add_argument("--id", default=None, help="partner_id, để trống = auto")
    ap.add_argument("--name", required=True)
    ap.add_argument("--contact", default=None)
    args = ap.parse_args()

    settings = Settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)

    pid = args.id or uuid.uuid4().hex[:12]
    with sf() as s:
        if s.get(Partner, pid) is not None:
            raise SystemExit(f"partner_id={pid} đã tồn tại")
        s.add(Partner(id=pid, name=args.name, contact=args.contact, active=True))
        s.commit()
    print(f"OK partner_id={pid} name={args.name}")


if __name__ == "__main__":
    main()
