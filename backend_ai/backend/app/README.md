# Backend App - Hướng Dẫn Nhiệm Vụ và Hành Vi

## Mục tiêu của thư mục `app/`
- Đây là lõi backend của hệ thống CNTx labs.
- Chịu trách nhiệm nhận request API, điều phối runtime bot, ghi nhận sự kiện, quản lý trạng thái và trả dữ liệu cho Mini App.
- Đảm bảo hệ thống chạy ổn định, có khả năng quan sát, dễ điều tra lỗi và an toàn dữ liệu.

## Logic kiến trúc tổng quát
- **Control plane (Linux backend):**
  - Nhận lệnh từ API.
  - Chọn runner/slot phù hợp.
  - Phát command, nhận event/heartbeat.
  - Cập nhật trạng thái deployment/account/slot.
- **Execution plane (Windows runner):**
  - Thực thi bot và kết nối MT5.
  - Gửi event, log, heartbeat ngược về backend.
- Bot logic không nằm trong Linux backend.

## Luồng xử lý chính
1. User thao tác qua Telegram Mini App.
2. API `app/api/v2/` nhận request và xác thực.
3. Service/Orchestration áp policy và quyết định hành động.
4. Repository thao tác dữ liệu (PostgreSQL/Redis).
5. Worker nền xử lý command/event/reconcile/webhook.
6. API trả trạng thái mới nhất cho client.

## Cấu trúc thư mục và nhiệm vụ
- `main.py`: điểm vào FastAPI, middleware, health endpoints, bootstrap background workers.
- `api/`: định nghĩa endpoint HTTP theo domain (`v2/*`), chuẩn hóa request/response.
- `services/`: logic nghiệp vụ cấp ứng dụng, điều phối giữa API và repository.
- `orchestration/`: điều phối vòng đời deployment, verification, scheduling và policy thực thi.
- `repositories/`: truy cập dữ liệu; `control_plane/` là vùng quan trọng cho trạng thái runtime.
- `events/`: worker xử lý event stream, command reconciler, webhook delivery.
- `runner/`: client/protocol giao tiếp với runner.
- `monitoring/`: metrics và reconciler phục vụ vận hành.
- `risk/`: quota, circuit breaker và policy quản trị rủi ro.
- `ai/`: tính năng AI (route, context, memory, provider, queue).
- `core/`: hạ tầng dùng chung (auth, rate-limit, redis client, log hygiene).
- `models/`, `schemas/`: mô hình dữ liệu và schema giao tiếp.
- `infra/`: tiện ích tích hợp hạ tầng (ví dụ Redis streams).
- `providers/`: adapter tích hợp bên ngoài.
- `bot_catalog/`: nạp và quản lý dữ liệu catalog bot.

## Bản đồ nhiệm vụ theo từng mục trong `app/`
- `__init__.py`: đánh dấu package Python cho module `app`, hỗ trợ import chuẩn.
- `main.py`: entrypoint ứng dụng, khai báo FastAPI app, router, middleware, lifecycle startup/shutdown.
- `settings.py`: cấu hình tập trung bằng environment variables, default values và validation.
- `security.py`: tiện ích bảo mật ở cấp ứng dụng (token/signature/ràng buộc an toàn theo thiết kế hiện có).
- `store.py`: lớp truy cập DB/store dùng chung, quản lý kết nối và retry ở mức nền tảng.
- `README.md`: tài liệu định hướng kiến trúc, hành vi, checklist vận hành/đào tạo.

- `ai/`: toàn bộ năng lực AI (route, provider, memory, context, intent, policy trả lời, queue học liên tục).
- `api/`: tầng HTTP API; `api/v2/*` là các endpoint nghiệp vụ chính cho client/admin.
- `bot_catalog/`: nguồn dữ liệu và loader liên quan catalog bot giao dịch.
- `core/`: thành phần hạ tầng dùng chung (auth, rate limit, redis client, logging hygiene/filter).
- `events/`: worker và consumer xử lý event stream, command routing/reconcile, webhook delivery.
- `infra/`: helper tích hợp hạ tầng (ví dụ Redis streams constants/utilities).
- `models/`: model dữ liệu nội bộ, hằng trạng thái/lifecycle để thống nhất toàn hệ.
- `monitoring/`: metrics, reconciler, logic quan sát sức khỏe hệ thống.
- `orchestration/`: điều phối luồng nghiệp vụ nhiều bước (deployment, verification, scheduler, policy).
- `providers/`: adapter tích hợp hệ thống ngoài theo từng nhà cung cấp.
- `repositories/`: tầng truy cập dữ liệu; `control_plane/` là phần cốt lõi trạng thái runtime.
- `risk/`: quota, circuit breaker, policy rủi ro ở cấp tài khoản/deployment.
- `runner/`: giao thức và client giao tiếp với runner thực thi.
- `schemas/`: schema request/response, contract dữ liệu qua API.

## Hành vi bắt buộc khi phát triển
- Không đoán trạng thái runtime nếu chưa có dữ liệu từ source of truth (deployment/event/heartbeat/command).
- Không thay đổi lifecycle trạng thái khi chưa có migration và test phù hợp.
- Không chèn biến trực tiếp vào SQL tĩnh; luôn dùng tham số bind.
- Với luồng worker nền, ưu tiên thiết kế idempotent và chống poison message.
- Không log thông tin nhạy cảm (token, key, password, secret).

## Quy tắc an toàn vận hành
- Mọi thay đổi logic lớn phải có đường rollback rõ ràng.
- Endpoint health/readiness phải phản ánh trạng thái thật của dependency chính.
- Query ghi khối lượng lớn phải có điều kiện rõ ràng và giới hạn tác động.
- Cần phân biệt lỗi tạm thời (retryable) và lỗi dữ liệu (cần skip + cảnh báo).

## Mục tiêu đào tạo nhân viên mới
- **Giai đoạn 1:** nắm kiến trúc control-plane/execution-plane và cấu trúc thư mục.
- **Giai đoạn 2:** đọc được luồng API -> service -> repository -> event worker.
- **Giai đoạn 3:** xử lý lỗi theo checklist, biết xác định source of truth.
- **Giai đoạn 4:** theo dõi vận hành thực tế, điều tra sự cố với log/metrics/events.

## Checklist khi nhận task mới
- Task thuộc domain nào (API, service, repository, event, risk, monitoring)?
- Nguồn dữ liệu thật ở đâu?
- Có ảnh hưởng lifecycle trạng thái không?
- Có rủi ro race condition hoặc retry loop không?
- Có cần thêm logging/metrics để dễ debug không?
