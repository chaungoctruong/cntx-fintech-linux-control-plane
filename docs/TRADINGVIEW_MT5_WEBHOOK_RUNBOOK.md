# Runbook TradingView → MT5 (fan-out)

Runbook nối **một tín hiệu TradingView** với **nhiều tài khoản MT5**: TradingView gửi HTTP → backend đọc bảng `tradingview_signal_subscriptions` (Postgres) → ghi `execution_commands` → đẩy lệnh lên **Redis** (`mt5:runner:{runner_id}:commands`) → runner Windows thực thi lệnh trên MT5.

**Điều kiện:** backend Linux chạy ổn; Redis tới được từ runner Windows; runner bật `RUNNER_TRANSPORT=redis_queue`; deployment bot của khách ở trạng thái `running`; tài khoản đã **subscribe** đúng `signal_id` dùng trong TradingView.

**Công cụ:** `scripts/setup_tradingview_signal.py` (subscribe / doctor / alert-json / test-broadcast).

**Webhook công khai:** `https://<HOST_CONTROL_PLANE_PUBLIC>/api/v2/public/tradingview/broadcast` (thay host bằng domain/tunnel thật).

Luồng tóm tắt:

```text
Cảnh báo TradingView
  → POST /api/v2/public/tradingview/broadcast
  → backend đọc tradingview_signal_subscriptions
  → backend ghi execution_commands
  → Redis list mt5:runner:{runner_id}:commands
  → runner Windows thực thi lệnh MT5
```

---

## 1. Điều kiện cần có

- Backend Linux đang chạy và `/health` / `/ready` ổn.
- Runner Windows **tới được** Redis (mạng, mật khẩu, cổng).
- Runner online với `RUNNER_TRANSPORT=redis_queue`.
- Tài khoản khách có deployment bot với `status=running`.
- Tài khoản đã subscribe đúng `signal_id` như trong cảnh báo TradingView.
- Nếu nhiều bot: dùng `signal_id` **khác nhau** cho từng bot và khai báo đúng `bot_code` trong JSON subscribe / alert.

Nếu deployment **không** ở trạng thái `running`, endpoint broadcast vẫn có thể **nhận** cảnh báo nhưng **sẽ không** gửi lệnh MT5 cho tài khoản đó.

---

## 2. Gắn tài khoản với một tín hiệu (subscribe)

Chạy **trong container** backend:

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py subscribe \
  --account-id 9 \
  --signal-id gsalgovip-xauusd \
  --bot-code gsalgovip \
  --priority 60
```

Kiểm tra trạng thái “sẵn sàng”:

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py doctor \
  --signal-id gsalgovip-xauusd
```

`ready_for_live_signal` chỉ thành `true` khi có **ít nhất một** tài khoản đã subscribe đang có deployment **running**, đã gán runner và slot.

---

## 3. Sinh JSON cảnh báo cho TradingView

In URL webhook và nội dung ba loại cảnh báo (BUY / SELL / CLOSE):

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py alert-json \
  --signal-id gsalgovip-xauusd \
  --bot-code gsalgovip \
  --symbol XAUUSD
```

Nếu muốn script **nhúng luôn** `secret` webhook (đã cấu hình trên backend) vào JSON:

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py alert-json \
  --signal-id gsalgovip-xauusd \
  --bot-code gsalgovip \
  --symbol XAUUSD \
  --include-secret
```

Dùng **cùng một** URL webhook cho cả ba cảnh báo trên TradingView:

```text
https://<YOUR_PUBLIC_CONTROL_PLANE_HOST>/api/v2/public/tradingview/broadcast
```

Tạo **ba** alert trên TradingView, mỗi alert dán **một** JSON tương ứng:

- `BUY`
- `SELL`
- `CLOSE`

---

## 4. Thử nghiệm (không / có gửi lệnh thật)

**Chạy thử payload (dry-run, không gửi HTTP ra ngoài theo mặc định script):**

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py test-broadcast \
  --signal-id gsalgovip-xauusd \
  --bot-code gsalgovip \
  --action BUY \
  --symbol XAUUSD
```

**Gửi thật** webhook (cẩn thận — có thể tạo lệnh MT5):

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py test-broadcast \
  --signal-id gsalgovip-xauusd \
  --bot-code gsalgovip \
  --action BUY \
  --symbol XAUUSD \
  --send
```

Chỉ dùng `--send` khi tài khoản đích **được phép** nhận lệnh test / live.

---

## 5. Ghi chép khi cấu hình TradingView

- Bật **2FA** trên tài khoản TradingView (webhook thường yêu cầu).
- Nội dung alert phải là **JSON hợp lệ**.
- `alert_id` là **tuỳ chọn**. Nếu có: mỗi tín hiệu thật nên một giá trị **duy nhất**; backend dùng để **chống trùng** khi TradingView gửi lại. Nếu không có: backend tự sinh id.
- Lần đầu test nên để **khối lượng (lot) nhỏ**.
