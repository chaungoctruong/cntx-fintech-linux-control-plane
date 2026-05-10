# Backup dữ liệu

Script trong thư mục này chỉ backup dữ liệu từ Docker Compose hiện tại.

## Postgres

```bash
bash ops/backup/backup_postgres.sh
```

Kết quả là file `.sql.gz` trong `ops/artifacts/backups/postgres/`.

## Redis

```bash
bash ops/backup/backup_redis.sh
```

Kết quả là file `.rdb` trong `ops/artifacts/backups/redis/`.

## Quy tắc an toàn

- Không in password ra màn hình.
- Không commit file backup.
- Trước migration hoặc deploy lớn, chạy backup Postgres trước.
- Với production thật, phải copy backup ra ngoài VPS.
