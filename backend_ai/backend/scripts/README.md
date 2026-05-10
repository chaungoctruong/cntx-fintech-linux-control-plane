# Scripts - Hướng Dẫn Nhiệm Vụ và Vận Hành

## Mục tiêu thư mục
- Chứa các script vận hành/offline dùng cho backend CNTx labs.
- Hỗ trợ triển khai, bảo trì dữ liệu, huấn luyện AI, và chạy tác vụ nền theo ngữ cảnh môi trường.
- Giúp đội vận hành và nhân viên mới thực thi công việc lặp lại theo chuẩn an toàn.

## Nhóm nhiệm vụ theo script
- `run_api.py`:
  - Chạy API backend ở chế độ script/local.
- `start_backend_cluster.sh`:
  - Khởi động backend theo cụm/tiến trình phục vụ môi trường vận hành.
- `run_runner_event_consumer.py`:
  - Chạy consumer nhận event từ runner.
- `run_mt5_runner_stub.py`:
  - Giả lập runner MT5 cho mục đích test tích hợp.
- `apply_control_plane_scale_indexes.py`:
  - Áp dụng index tối ưu hiệu năng cho control-plane.
- `setup_tradingview_signal.py`:
  - Gắn account vào `signal_id`, kiểm tra trạng thái fan-out, sinh JSON alert
    TradingView BUY/SELL/CLOSE và gửi thử webhook khi cần.

- `export_ai_training_dataset.py`:
  - Xuất dữ liệu huấn luyện AI từ nguồn nội bộ.
- `review_ai_training_examples.py`:
  - Rà soát mẫu dữ liệu training trước khi huấn luyện.
- `evaluate_ai_training_dataset.py`:
  - Đánh giá chất lượng dataset training.
- `build_lora_training_job.py`:
  - Tạo job huấn luyện LoRA theo cấu hình.
- `register_ai_model_version.py`:
  - Đăng ký phiên bản model sau huấn luyện/đánh giá.

- `ingest_platform_knowledge.py`:
  - Nạp tri thức nền tảng vào kho dữ liệu tri thức.
- `ingest_platform_sources.py`:
  - Nạp nguồn tri thức thô/phụ trợ.
- `backfill_platform_knowledge_embeddings.py`:
  - Backfill embedding cho dữ liệu tri thức đã có.

- `ops/zingserver_probe.py`, `ops/zingserver_plan_create_vps.py`:
  - Script hỗ trợ vận hành hạ tầng liên quan ZingServer.

## Hành vi bắt buộc khi chạy script
- Luôn xác nhận môi trường (`dev/staging/prod`) trước khi chạy.
- Không chạy script có tác động ghi dữ liệu production nếu chưa có backup/kế hoạch rollback.
- Không sửa dữ liệu production bằng tay nếu đã có script chuẩn cho thao tác đó.
- Không hard-code secret/token trong script; luôn đọc từ env/config an toàn.
- Luôn ghi lại log chạy script và kết quả đầu ra để phục vụ audit/điều tra.

## Logic vận hành an toàn
- Script thay đổi schema/index:
  - Chạy ngoài giờ cao điểm nếu có thể.
  - Theo dõi lock/thời gian chạy.
- Script xử lý dữ liệu lớn:
  - Ưu tiên batch.
  - Có checkpoint hoặc khả năng chạy lại idempotent.
- Script AI pipeline:
  - Dữ liệu đầu vào phải qua bước review/evaluate.
  - Chỉ đăng ký model khi đạt tiêu chí chất lượng nội bộ.

## Quy trình chuẩn khi nhận task script
1. Xác định script thuộc nhóm nào (runtime, DB/index, AI data, hạ tầng).
2. Kiểm tra tác động đọc/ghi và phạm vi môi trường.
3. Chạy thử trên local hoặc staging trước.
4. Chạy chính thức với log đầy đủ.
5. Xác minh hậu kiểm (DB/API/metrics) và lưu biên bản ngắn.

## Mục tiêu đào tạo nhân viên mới
- Tuần 1: nhận diện từng script và mục đích sử dụng.
- Tuần 2: thực hành chạy script read-only trên môi trường dev.
- Tuần 3: thực hành quy trình staging với checklist an toàn.
- Tuần 4: phối hợp vận hành script production dưới giám sát, có hậu kiểm đầy đủ.
