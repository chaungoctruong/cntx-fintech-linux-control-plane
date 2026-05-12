# Monitoring tối thiểu

Thư mục này chứa script kiểm tra nhanh để biết stack có sẵn sàng nhận test product hay không.

## Kiểm tra readiness

```bash
bash ops/monitoring/check_prod_readiness.sh
```

Script kiểm (theo thứ tự trong file):

- Docker service đang chạy (`docker compose ps`).
- Backend local `http://127.0.0.1:8001/ready`.
- Health public: `GET {PUBLIC_BASE_URL}/api/v2/system/healthz` (**bắt buộc** có `PUBLIC_BASE_URL` trong env hoặc trong root `.env` — script không dùng URL mặc định).
- Catalog public: `GET {PUBLIC_BASE_URL}/api/v2/bots` phải trả danh sách bot không rỗng.

Nếu muốn production thật, nên thêm alert định kỳ bằng cron/systemd timer hoặc dịch vụ uptime bên ngoài.
