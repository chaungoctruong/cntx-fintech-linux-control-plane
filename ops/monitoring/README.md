# Monitoring tối thiểu

Thư mục này chứa script kiểm tra nhanh để biết stack có sẵn sàng nhận test product hay không.

## Kiểm tra readiness

```bash
bash ops/monitoring/check_prod_readiness.sh
```

Script kiểm:

- Docker service đang chạy.
- Backend local `/ready`.
- Public Vercel health endpoint.
- Public bot catalog có bot.

Nếu muốn production thật, nên thêm alert định kỳ bằng cron/systemd timer hoặc dịch vụ uptime bên ngoài.
