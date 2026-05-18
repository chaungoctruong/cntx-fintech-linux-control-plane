import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.tg_bot import build_application


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    # Telegram Bot API URLs contain the bot token. Keep noisy HTTP client logs
    # out of production logs so secrets do not leak through getUpdates lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings()
    if not settings.telegram_bot_token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN chưa được cấu hình trong .env. "
            "Lấy token từ @BotFather rồi điền vào .env."
        )

    admins = settings.admin_id_set()
    if not admins:
        logging.warning(
            "TG_ADMIN_USER_IDS đang rỗng. Sau khi bot chạy, gửi /whoami "
            "trong chat với bot để biết Telegram ID của bạn, rồi điền vào .env "
            "và restart."
        )

    app = build_application(settings)
    print(f"token-bot Telegram polling started. Admins: {sorted(admins) or 'none'}")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
