# AI Knowledge Base - Hướng Dẫn Đào Tạo

## Mục tiêu thư mục
- Đây là kho tri thức nghiệp vụ và vận hành để AI/backend trả lời đúng ngữ cảnh CNTx labs.
- Dùng để đào tạo nhân viên mới: hiểu boundary hệ thống, quy trình vận hành và cách xử lý tình huống hỗ trợ user.
- Giảm trả lời sai sự thật (hallucination), giảm rủi ro compliance trong lĩnh vực trading.

## Nhiệm vụ chính
- Cung cấp “source of truth” cho câu hỏi sản phẩm, runtime, MT5, risk, trading basics và sales.
- Chuẩn hóa thông điệp trả lời: ngắn gọn, rõ ràng, chuyên nghiệp, không cam kết lợi nhuận.
- Hỗ trợ troubleshooting theo checklist, ưu tiên bằng chứng (status, event, heartbeat, logs).

## Hành vi bắt buộc khi sử dụng tri thức này
- Không đoán trạng thái bot/account nếu chưa có dữ liệu runtime.
- Không đưa số liệu kỹ thuật (swap/margin/spread) nếu thiếu specification broker.
- Không khuyên all-in, martingale nguy hiểm, hoặc cam kết “chắc thắng”.
- Không để lộ thông tin nhạy cảm (token, key, password, secret, raw logs nhạy cảm).
- Khi gặp lỗi: hỏi đúng thông tin cần thiết (account/deployment/thời điểm/log id), không yêu cầu thao tác rủi ro.

## Logic vận hành cần nắm
- Linux backend là control plane; Windows runner là execution plane.
- Bot logic không nằm trong Linux backend.
- Trạng thái deployment cần đọc theo lifecycle:
  - `start_requested -> starting -> running -> stop_requested -> stopped/failed`
- Trạng thái command cần đọc theo lifecycle:
  - `queued -> dispatched -> acknowledged/failed`
- Phân tích “bot có đang chạy không” phải dựa trên:
  - deployment status + desired_state + health_status + last_heartbeat_at
  - command gần nhất nếu đang kẹt start/stop
  - runner/slot status nếu có binding

## Cấu trúc thư mục (mapping)
- `cntx_labs_overview.md`: tổng quan sản phẩm và boundary hệ thống.
- `ai_answer_policy.md`: chính sách trả lời AI, guardrails, anti-hallucination.
- `runtime/`: lifecycle deployment, sticky slot, backend Linux, runner Windows, common errors.
- `trading/`: kiến thức trading cơ bản (lot, margin, swap, symbol), funded account rules.
- `broker/`: thông tin broker/MT5 liên quan onboarding và hỗ trợ.
- `sales/`: playbook trao đổi với khách hàng theo đúng định hướng sản phẩm.
- `risk_management.md`, `lot_margin_spread_swap.md`, `bot_runtime.md`, `mt5_connect_flow.md`, `common_mt5_errors.md`: tài liệu bổ trợ đa chủ đề.

## Mục tiêu đào tạo nhân viên mới
- Tuần 1: hiểu kiến trúc control-plane/execution-plane và flow user.
- Tuần 2: đọc và xử lý checklist runtime errors + MT5 connect issues.
- Tuần 3: thực hành trả lời theo policy, có trích dẫn source file liên quan.
- Tuần 4: shadow support ca trực, báo cáo tình huống “thiếu data cần xác minh”.

## Quy tắc cập nhật nội dung
- Mọi thay đổi phải ưu tiên tính đúng sự thật và tính nhất quán với runtime hiện tại.
- Nếu có thay đổi lifecycle, cập nhật tài liệu runtime trước.
- Khuyến nghị mỗi file tri thức giữ format:
  - Mục tiêu
  - Source of truth
  - Checklist thực thi
  - Sai lầm thường gặp
