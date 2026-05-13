"""End-user (khách của partner) self-service module.

Auth = JWT cấp bởi token-bot. Không cần đăng ký user/email/password — khách chỉ
cần dán token là dùng được. Module này độc lập với luồng Mini App (Telegram
initData) hiện có, dùng `Authorization: Bearer <jwt>`.

Scale path:
- v1 (this): login + view bot + start/stop
- v2: deposit/subscription gates (insert check trong service.py)
- v3: usage metering, per-customer billing
"""
