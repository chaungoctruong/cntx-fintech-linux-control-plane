# Scale / new Linux control-plane server — runbook

**Tiếng Việt:** Runbook khi **clone server Linux control-plane mới** — đúng tag, venv, env keys, preflight, health. Không chứa secret trong file.

Mục tiêu: server mới **giống production** về version, env keys, và layout — không đoán mò path hay branch.

**Không** chứa secret. Mọi giá trị nhạy cảm điền trong file env ngoài git.

---

## 0. Chuẩn bị

1. Điền [CONFIG_MANIFEST_TEMPLATE.md](CONFIG_MANIFEST_TEMPLATE.md) cho server đích (trước khi cắm traffic).
2. Đọc [RELEASE_FREEZE_CHECKLIST.md](RELEASE_FREEZE_CHECKLIST.md) — chỉ scale khi checklist pass.

---

## 1. Clone đúng tag / branch release

```bash
cd /root   # hoặc thư mục chuẩn của team
git clone <REPO_URL> linux-root-backend-hubot-v1
cd linux-root-backend-hubot-v1
git fetch --tags origin
git checkout <release/vX.Y.Z>   # hoặc tag cụ thể
git rev-parse HEAD
```

**CHECK:** `git describe --tags --always` khớp manifest.

---

## 2. Python venv (host / PM2 — không dùng trong luồng Docker image)

Chỉ khi chạy backend/hubbot **trên host** (không chỉ `docker compose`):

```bash
cd /path/to/linux-root-backend-hubot-v1/backend_ai/backend
python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Hubbot (venv riêng):

```bash
cd /path/to/linux-root-backend-hubot-v1/hubbot
python3.11 -m venv venv_hub
./venv_hub/bin/pip install -r requirements.txt
```

**CHECK:** `python3.11 --version` khớp manifest và [README.md](../README.md).

---

## 3. Copy env template (không commit)

- Sao chép từ **backup nội bộ** hoặc từ file mẫu team (không có trong repo public nếu chứa secret).
- Đặt tại:
  - `docker compose`: `.env` ở root repo (hoặc file `ENV_FILE` trỏ tới).
  - PM2 backend: `backend_ai/backend/.env`.
- `chmod 600` mọi file env.

**Không** paste secret vào chat/issue.

---

## 4. Biến bắt buộc (control plane)

Đối chiếu `docker-compose.yml`, [README.md](../README.md) mục biến môi trường, và `backend_ai/backend/app/settings.py` (tên field). Tối thiểu thường gồm:

| Nhóm | Keys (tên — không value) |
|------|----------------------------|
| Backend | `BACKEND_HOST`, `BACKEND_API_KEY`, `PUBLIC_BASE_URL`, `POSTGRES_*` hoặc URL tương đương, `REDIS_URL` / `REDIS_WRITE_URL` |
| Compose | `LOCAL_REDIS_PASSWORD`, `LOCAL_POSTGRES_*` nếu dùng service `db`/`redis` của compose |
| Hubbot | `TELEGRAM_BOT_TOKEN`, `BACKEND_URL` (nội bộ compose: `http://spider-app:8001`) |
| Runner-facing | `RUNNER_CONTROL_PLANE_URL` (HTTPS hoặc tailnet) |

Frontend build-time: `NEXT_PUBLIC_BACKEND_URL`, `NEXT_PUBLIC_API_URL` — **rebuild** sau khi đổi.

---

## 5. Preflight read-only

Từ root repo:

```bash
bash ops/preflight_linux_control_plane.sh
```

Hoặc chỉ định file env backend:

```bash
ENV_FILE=/path/to/.env bash ops/preflight_linux_control_plane.sh
```

**Kỳ vọng:** không `FAIL` trước khi go-live. `WARN` (vd. `/ready` không trả 200 khi service chưa bật) — xử lý theo context.

---

## 6. Health sau khi được phép chạy dịch vụ (maintenance window)

Chỉ chạy khi team vận hành đã **start** stack theo quy trình nội bộ:

```bash
curl -fsS http://127.0.0.1:8001/ready
curl -fsS http://127.0.0.1:8001/health
```

Public (sau TLS/nginx):

```bash
curl -fsS https://<domain>/ready
```

---

## 7. Onboard runner mới (Windows — repo khác)

- Linux chỉ cần URL control plane + `BACKEND_API_KEY` khớp runner.
- Runner join tailnet / firewall theo [HEADSCALE_MESH_SETUP.md](HEADSCALE_MESH_SETUP.md) (nếu dùng mesh).
- Đăng ký/heartbeat theo [WINDOWS_RUNNER_INTEGRATION_PROMPT.md](../backend_ai/backend/app/runner/WINDOWS_RUNNER_INTEGRATION_PROMPT.md).
- Cập nhật manifest fleet (runner_id) ở tài liệu Windows — không nhồi secret vào manifest Linux.

---

## 8. Thử nghiệm 1 account nhỏ (canary)

1. Tạo / dùng account test, **không** traffic user thật.
2. `START` deployment test — xác nhận command xuống đúng `runner_id`.
3. Xác nhận event `BOT_STOPPED` / heartbeat sau `STOP`.
4. Chỉ mở traffic rộng sau khi log + metric ổn.

---

## 9. Lỗi thường gặp khi cấu hình sai

| Triệu chứng | Nguyên nhân hay gặp |
|-------------|---------------------|
| Hubbot không gọi được backend | `BACKEND_URL` trỏ tunnel thay vì `http://spider-app:8001` trong compose |
| `/ready` false | Postgres/Redis URL sai; password; firewall |
| Mini App trắng | Chưa build `frontend-v2/out` hoặc `NEXT_PUBLIC_*` sai tag |
| Runner không nhận lệnh từ Redis | `REDIS_URL`/password; `RUNNER_TRANSPORT=redis_queue`; queue `mt5:runner:{RUNNER_ID}:commands` |
| 401 runner | `BACKEND_API_KEY` lệch giữa backend và runner |
| Hai bot Conflict | Hai hubbot cùng `TELEGRAM_BOT_TOKEN` |

Chi tiết: [README.md](../README.md) troubleshooting, [CLAUDE.md](../CLAUDE.md).

---

## Rollback

- Git: quay về tag trước + redeploy có kế hoạch.
- **Không** xoá/restore DB từ runbook này mà không có DBA.
