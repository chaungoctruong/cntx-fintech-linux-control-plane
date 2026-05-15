# `sql/` — Truy vấn Postgres theo domain (control-plane)

Mỗi thư mục con là một **bounded context**: chỉ chứa file `.sql` thuần. Python load qua `load_sql("domain/file.sql")` trong `app/repositories/control_plane/mixins/*.py` hoặc `repository.py`.

## Bản đồ thư mục → README

| Thư mục | README | Chủ đề |
|----------|--------|--------|
| `accounts/` | [accounts/README.md](accounts/README.md) | Account MT5, risk, PnL, scrub |
| `billing/` | [billing/README.md](billing/README.md) | Subscription user |
| `catalog/` | [catalog/README.md](catalog/README.md) | Bot catalog |
| `commands/` | [commands/README.md](commands/README.md) | `execution_commands`, events, replay |
| `dashboard/` | [dashboard/README.md](dashboard/README.md) | Tổng hợp dashboard |
| `deployments/` | [deployments/README.md](deployments/README.md) | Lifecycle deployment |
| `ops_summary/` | [ops_summary/README.md](ops_summary/README.md) | Snapshot ops |
| `reconcile/` | [reconcile/README.md](reconcile/README.md) | Job đối soát stale |
| `runner_bot_state/` | [runner_bot_state/README.md](runner_bot_state/README.md) | State GsAlgo / runner |
| `runners_slots/` | [runners_slots/README.md](runners_slots/README.md) | Runner node, slot, binding |
| `runtime_health/` | [runtime_health/README.md](runtime_health/README.md) | Health read paths |
| `snapshots/` | [snapshots/README.md](snapshots/README.md) | Snapshot account/position |
| `user_webhooks/` | [user_webhooks/README.md](user_webhooks/README.md) | Webhook user |
| `users/` | [users/README.md](users/README.md) | Metadata user |

## Quy tắc chung

1. Sửa SQL → tìm **mọi** chỗ `load_sql("domain/...")` gọi file đó.
2. Không nối chuỗi SQL từ input; luôn bind `%s`.
3. Thay đổi cột output của query dashboard/ops → kiểm tra FE/admin parse.

## Transport runner (nhắc ngắn)

- SQL trong `commands/` + publish Redis **không** thay cho nhau: DB = truth, Redis = vận chuyển lệnh tới Windows.
