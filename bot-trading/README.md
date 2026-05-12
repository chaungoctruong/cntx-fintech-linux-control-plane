# Bot Trading Registry

Thư mục này chứa bot package để Linux scan metadata/catalog. Đây không phải nơi chạy MT5 thật và không phải nơi chứa secret.

## Vai trò trong kiến trúc

- Linux đọc manifest để biết bot nào tồn tại, version nào, contract nào.
- Windows runner có registry riêng dạng `bot-runner/` để report bot đang có trên máy Windows.
- TradingView webhook thuộc Linux backend. Windows không tự expose webhook cho bot `gsalgovip`.
- Windows chỉ nhận command từ Linux ở các phase sau, ví dụ lifecycle dry-run hoặc batch execution.

## Package hiện tại

```text
bot-trading/
  gsalgovip/
    bot_manifest.json
    VERSION
    README.md
    requirements.txt
    config/
      schema.json
      default.json
    app/
      runner_impl.py
```

`gsalgovip` hiện là bot:

```text
bot_type=backend_webhook_signal
execution_owner=linux_backend
windows_role=mt5_executor_only
tradingview_webhook_owner=linux
requires_executor_slot=true
version=0.3.0
```

## Quy tắc bắt buộc

- Bot package không chứa MT5 password.
- Bot package không chứa login thật.
- Bot package không chứa terminal path thật.
- Bot package không tự gọi Redis.
- Bot package không tự ghi PostgreSQL.
- Bot package không tự kill process.
- Bot package không tự mở Windows webhook.
- Bot package chỉ khai báo contract và stub an toàn khi đang ở phase catalog/lifecycle.

## Khi thêm bot mới

1. Tạo thư mục bot mới dưới `bot-trading/<bot_id>/`.
2. Thêm `bot_manifest.json`, `VERSION`, `README.md`, `requirements.txt`, `config/schema.json`, `config/default.json`, `app/runner_impl.py`.
3. Đảm bảo manifest có `required_params`, `resource_hints`, `risk_contract` và các field ownership.
4. Chạy catalog sync/validation ở Linux trước khi để Windows runner nhận lifecycle.

Không copy credential hoặc file runtime production vào thư mục này.
