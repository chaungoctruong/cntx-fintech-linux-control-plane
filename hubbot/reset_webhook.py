from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from telegram import Bot
from telegram.error import NetworkError, TimedOut


def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name, "1" if default else "0") or "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or default).strip()


async def _delete_webhook(bot: Bot, *, drop_pending_updates: bool, retries: int, timeout_sec: float) -> bool:
    last_err: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            # Đã truyền read_timeout để thông số timeout_sec thực sự có tác dụng
            ok = await bot.delete_webhook(
                drop_pending_updates=drop_pending_updates,
                read_timeout=timeout_sec
            )
            return bool(ok)
        except (TimedOut, NetworkError) as e:
            last_err = e
            if attempt < retries:
                await asyncio.sleep(0.8 * attempt)
                continue
            break
        except Exception as e:
            last_err = e
            break

    if last_err:
        print(f"[error] delete_webhook failed: {type(last_err).__name__}: {str(last_err)[:200]}")
    return False


async def main() -> None:
    load_dotenv()

    token = _env_str("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN in hubbot/.env")

    drop_pending = _env_bool("DROP_PENDING_UPDATES", True)
    retries = int(_env_str("TG_RETRIES", "2") or "2")
    timeout_sec = float(_env_str("TG_TIMEOUT_SEC", "12") or "12")

    bot = Bot(token=token)

    # Identify bot (nice debug)
    try:
        me = await bot.get_me(read_timeout=timeout_sec)
        print(f"[info] bot=@{me.username} id={me.id} drop_pending_updates={drop_pending}")
    except Exception:
        print(f"[info] bot loaded drop_pending_updates={drop_pending}")

    ok = await _delete_webhook(
        bot, 
        drop_pending_updates=drop_pending, 
        retries=retries,
        timeout_sec=timeout_sec
    )
    
    if ok:
        print("[ok] webhook deleted")
        raise SystemExit(0)

    raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())