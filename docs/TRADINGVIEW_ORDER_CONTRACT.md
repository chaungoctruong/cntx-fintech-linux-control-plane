# TradingView order contract

Tài liệu này là hợp đồng sản phẩm cho luồng TradingView -> backend -> Windows runner -> MT5. Mục tiêu là nhiều bot/chiến lược khác nhau vẫn đi qua cùng một hạ tầng mà không nhập nhằng SL/TP, DCA, symbol, hoặc routing user.

## 1. Nguyên tắc

- TradingView/Pine gửi **signal intent**: bot nào, chiến lược nào, side nào, entry/SL/TP trên chart là bao nhiêu.
- Backend là nơi normalize: validate, chống trùng, map symbol, chọn subscriber, tách ENTRY/DCA, rồi chuyển sang command contract cho runner.
- Windows runner chỉ thực thi command đã chuẩn hóa. Runner không nên tự đoán field nào là absolute price hay distance.
- Không tự khóa tài khoản vì thắng/thua. Risk guard hiện tại chỉ reject order lỗi contract, stale alert, volume quá lớn, hoặc khoảng SL/TP bất thường.

## 2. TradingView alert payload

Payload TradingView nên có các field chính:

```json
{
  "schema_version": 2,
  "contract_version": 2,
  "secret": "<webhook-secret>",
  "alert_id": "unique-per-signal",
  "signal_id": "gsalarm-xauusd",
  "bot_code": "gsalgovip",
  "strategy_code": "turtle-soup-v1",
  "action": "BUY",
  "symbol": "XAUUSD",
  "entry_price": 4522.265,
  "stop_loss": 4517.265,
  "take_profit": 4527.265,
  "dca_order_type": "limit",
  "dca_limit_price": 4519.765
}
```

`entry_price`, `stop_loss`, `take_profit`, `dca_limit_price` trong webhook TradingView là **giá absolute trên chart**. Nếu user đổi R SL/R TP trên TradingView rồi bấm OK/tạo lại alert, Pine sẽ gửi level mới và backend sẽ tính lại distance tương ứng.

## 3. Runner request contract v3

Backend gửi xuống Windows runner trong `payload.request`. Từ contract v3, mọi alias SL/TP legacy trong request đều là **price distance**, không phải absolute price:

```json
{
  "source": "tradingview",
  "runner_order_contract_version": 3,
  "entry_type": "market",
  "order_type": "MARKET",
  "pending_order": false,
  "entry_price": 4522.265,
  "stop_loss": 5.0,
  "take_profit": 5.0,
  "sl": 5.0,
  "tp": 5.0,
  "sl_price": 5.0,
  "tp_price": 5.0,
  "sl_distance": 5.0,
  "tp_distance": 5.0,
  "sl_tp_unit": "price_distance",
  "legacy_sltp_aliases_unit": "price_distance",
  "legacy_sltp_aliases_are_distances": true
}
```

Với DCA limit, `entry_price`, `price`, và `limit_price` là giá limit của pending order. SL/TP distance được tính lại từ giá limit đó:

```json
{
  "entry_type": "limit",
  "order_type": "BUY_LIMIT",
  "pending_order": true,
  "entry_price": 4519.765,
  "price": 4519.765,
  "limit_price": 4519.765,
  "stop_loss": 2.5,
  "take_profit": 7.5,
  "sl_tp_unit": "price_distance"
}
```

Absolute levels gốc từ TradingView được giữ ở `payload` metadata để audit/debug, không nằm trong request runner:

```json
{
  "tradingview_stop_loss_price": 4517.265,
  "tradingview_take_profit_price": 4527.265,
  "tradingview_price_level_unit": "absolute_price",
  "runner_order_contract": {
    "version": 3,
    "request_sl_tp_unit": "price_distance",
    "legacy_aliases_unit": "price_distance"
  }
}
```

## 4. Multi-bot và multi-strategy

- `signal_id`: route thô từ TradingView tới nhóm subscriber.
- `bot_code`: package/adapter bot cần chạy, ví dụ `gsalgovip`.
- `strategy_code`: logic chiến lược bên trong bot, ví dụ `turtle-soup-v1`, `breakout-v2`, `mean-reversion-v1`.

Subscriber có thể khai báo `allowed_strategy_codes` trong metadata/deployment config. Nếu alert gửi `strategy_code` không khớp, backend bỏ qua subscriber đó và trả lỗi route `tradingview_strategy_mismatch`, không gửi lệnh xuống runner.

## 5. Yêu cầu Windows runner

Runner chuẩn product nên:

- Ưu tiên đọc `runner_order_contract_version`, `sl_tp_unit`, `sl_distance`, `tp_distance`.
- Với contract v3, treat `stop_loss`, `take_profit`, `sl`, `tp`, `sl_price`, `tp_price` là distance.
- Không fallback limit sang market khi thiếu limit price; reject bằng lỗi rõ ràng.
- Log final MT5 request đã tính ra absolute SL/TP: `entry`, `limit_price`, `sl_abs`, `tp_abs`, `sl_tp_unit`, `retcode`.
- Test matrix tối thiểu: BUY, SELL, BUY_LIMIT, SELL_LIMIT, DCA limit, stale alert, wrong-side SL/TP, runner không support pending limit.

## 6. Checklist trước live test

- Backend container đã restart sau khi đổi contract.
- Queue Redis `mt5:runner:*:commands` trống trước khi bắn test.
- Runner heartbeat có capability `supports_pending_limit_orders=true` nếu bật DCA limit.
- Không còn position test cũ bị sai SL/TP từ contract cũ.
- Gửi một lệnh nhỏ, kiểm MT5 thực tế: SL/TP phải khớp chart level TradingView sau khi runner quy đổi distance.
