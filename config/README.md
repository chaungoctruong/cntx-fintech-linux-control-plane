# Quyền sở hữu cấu hình

Thư mục này chứa các file cấu hình handoff cho Linux control-plane.

## File Nginx chuẩn

- `config/nginx-spider.conf`
  - mẫu Nginx cho control-plane một node
  - dùng khi chuẩn bị release manifest hoặc bàn giao vận hành
- `ops/ha/nginx/control-plane-edge.conf`
  - mẫu edge/include cho HA control-plane

## File legacy/local giữ để tham khảo

- `../nginx.conf`
  - file local hoặc legacy ở host-level
  - không phải artifact release chuẩn
  - không dùng làm nguồn sự thật khi bàn giao sạch

## File env runtime

- `../.env`
  - runtime chính cho Docker Compose trên Linux
  - không commit, không dán secret ra ngoài
- `../backend_ai/backend/.env`
  - chỉ dùng khi chạy backend trực tiếp ngoài compose
- `../frontend-v2/.env`
  - chỉ dùng cho frontend khi build/chạy riêng

Các file `.env.example` ở root/backend/frontend đã được bỏ để tránh nhân viên sửa nhầm. Riêng bot package có thể vẫn có `.env.example` rỗng để document contract của package, không phải runtime secret.

## Quy tắc khi sửa cấu hình

- Sửa đúng file runtime đang được service đọc.
- Không chỉnh Nginx khi chỉ muốn đổi DB/Redis/API key.
- Không restart production nếu chưa kiểm `docker compose config --quiet`.
- Không để Windows runner dùng trực tiếp DB Linux; runner gọi backend/control-plane qua HTTP.
