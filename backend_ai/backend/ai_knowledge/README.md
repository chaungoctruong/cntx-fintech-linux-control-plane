# `ai_knowledge/` — Tài liệu nghiệp vụ cho AI & đào tạo người

Markdown **không** được FastAPI import như module nghiệp vụ; dùng làm **nguồn tri thức** (ingest RAG), onboarding nhân viên, và chuẩn hóa câu trả lời support. **Không** thay cho log/DB khi xử sự cố production.

## Mục tiêu

- Giảm trả lời sai ngữ cảnh (hallucination) cho AI và nhân viên mới.
- Chuẩn hóa ranh giới: **Linux = control-plane**, **Windows = execution-plane (MT5)**.
- Checklist troubleshooting: ưu tiên bằng chứng (`request_id`, event, heartbeat, Postgres).

## Cấu trúc file (inventory thực tế)

| Đường dẫn | Nội dung |
|-----------|----------|
| **`cntx_labs_overview.md`** | Tổ quan sản phẩm, boundary hệ thống. |
| **`ai_answer_policy.md`** | Policy trả lời AI, guardrails. |
| **`bot_runtime.md`** | Runtime bot (khái niệm chung). |
| **`mt5_connect_flow.md`** | Luồng kết nối MT5 từ góc user/support. |
| **`common_mt5_errors.md`** | Lỗi MT5 thường gặp. |
| **`lot_margin_spread_swap.md`**, **`risk_management.md`** | Trading cơ bản / rủi ro. |
| **`runtime/linux_backend.md`** | Vai trò backend Linux. |
| **`runtime/windows_runner.md`** | Runner Windows, Redis queue, HTTP callback ngắn (**không** HTTP poll lệnh). |
| **`runtime/deployment_lifecycle.md`** | Trạng thái deployment. |
| **`runtime/sticky_slot.md`** | Sticky slot policy. |
| **`runtime/common_errors.md`** | Lỗi runtime chung. |
| **`broker/mt5_brokers.md`** | Broker / MT5 onboarding. |
| **`trading/*.md`** | Giải thích symbol, margin, funded rules, … |
| **`sales/sales_playbook.md`** | Sales playbook. |

*(Nếu thêm file `.md` mới, cập nhật bảng trên để đồng bộ đào tạo.)*

## Hành vi bắt buộc khi dùng làm “sự thật”

- Không suy đoán trạng thái bot nếu thiếu DB/event.
- Không cam kết lợi nhuận; không khuyên martingale / all-in.
- Không lộ secret (token, password, raw log nhạy cảm).

## Command / delivery (để nhân viên không nhầm)

- Trạng thái lệnh trong DB: `queued → dispatched → acknowledged/failed` (xem code + SQL `commands/`).
- **Transport lệnh tới runner:** Redis list `mt5:runner:{RUNNER_ID}:commands` — không lấy lệnh qua HTTP long-poll.

## Cập nhật nội dung

- Đổi lifecycle hoặc contract runner → sửa **`runtime/*.md`** trước khi lan truyền nội dung cũ.
- Giữ mỗi file: mục tiêu → source of truth → checklist → sai lầm thường gặp.

## Đào tạo

1. Tuần 1: `cntx_labs_overview.md` + `runtime/linux_backend.md` + `runtime/windows_runner.md`.
2. Tuần 2: `deployment_lifecycle.md` + `common_errors.md`.
3. Tuần 3: `ai_answer_policy.md` + shadow support có trích dẫn file.
4. Tuần 4: cập nhật chính tài liệu khi đổi sản phẩm.
