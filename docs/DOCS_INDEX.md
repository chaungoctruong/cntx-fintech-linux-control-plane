# Documentation index — Spider AI Linux control plane

**Ghi chú (tiếng Việt):** Đây là **mục lục** tài liệu; một số cột bảng (`Document`, `Role`, `Notes`) giữ **tiếng Anh** cho ngắn gọn khi tra cứu nhanh. Nội dung các file được trỏ tới phần lớn là **tiếng Việt** (README gốc, DEPLOY, HEADSCALE, …). Nhân viên: đọc [README.md](../README.md) trước, rồi dùng bảng dưới để nhảy đúng file.

Use this file to find the right document without hunting the tree. **Do not paste secrets into docs, issues, or chat.**

---

## 1. Start here

| Document | Role |
|----------|------|
| [README.md](../README.md) | Main entry: overview, safety rules, compose flow, env map, troubleshooting |
| [DEPLOY_FRESH_VPS.md](../DEPLOY_FRESH_VPS.md) | Fresh Rocky/RHEL-style VPS: Docker, tunnel, compose, checks |
| [CLAUDE.md](../CLAUDE.md) | Deep monorepo context for AI assistants and senior engineers (canonical architecture + ops detail) |

---

## 2. Architecture & mesh

| Document | Notes |
|----------|-------|
| [CLAUDE.md](../CLAUDE.md) | Canonical high-level architecture, Redis/Postgres contracts, login lease, TradingView fan-out |
| [HEADSCALE_MESH_SETUP.md](HEADSCALE_MESH_SETUP.md) | Tailnet / Headscale-style private networking for runner ↔ Redis ↔ backend |

---

## 3. Backend (FastAPI control plane)

| Document | Notes |
|----------|-------|
| [backend_ai/README.md](../backend_ai/README.md) | Docker build context (`spider-app`) + pointer into `backend/` |
| [backend_ai/backend/README.md](../backend_ai/backend/README.md) | Backend-focused overview |
| [backend_ai/backend/app/README.md](../backend_ai/backend/app/README.md) | Map of `app/` packages (`api/`, `events/`, `services/`, …) |
| [backend_ai/backend/app/repositories/control_plane/sql/README.md](../backend_ai/backend/app/repositories/control_plane/sql/README.md) | Index of SQL domains + links to each `sql/<domain>/README.md` |
| [backend_ai/backend/migrations/README.md](../backend_ai/backend/migrations/README.md) | Alembic workflow — **read before new revisions** |
| [backend_ai/backend/scripts/README.md](../backend_ai/backend/scripts/README.md) | Operational scripts |
| [backend_ai/backend/app/runner/WINDOWS_RUNNER_INTEGRATION_PROMPT.md](../backend_ai/backend/app/runner/WINDOWS_RUNNER_INTEGRATION_PROMPT.md) | Hợp đồng runner (tiếng Việt) — copy cho team Windows |

---

## 4. Hubbot (Telegram)

| Document | Notes |
|----------|-------|
| [hubbot/README.md](../hubbot/README.md) | Bot package layout |
| [hubbot/app/README.md](../hubbot/app/README.md) | Application modules |
| [hubbot/app/api/README.md](../hubbot/app/api/README.md) | Backend HTTP client |
| [hubbot/app/commands/README.md](../hubbot/app/commands/README.md) | Slash commands |
| [hubbot/app/callback/README.md](../hubbot/app/callback/README.md) | CallbackQuery router |
| [hubbot/app/consumer/README.md](../hubbot/app/consumer/README.md) | RabbitMQ consumer (optional) |
| [hubbot/app/lifecycle/README.md](../hubbot/app/lifecycle/README.md) | Single-instance lock, shutdown, logging |

---

## 5. Frontend (Mini App)

| Document | Notes |
|----------|-------|
| [frontend-v2/.env.example](../frontend-v2/.env.example) | Mẫu `NEXT_PUBLIC_*` (commit) — `cp` → `.env` trước build |
| [frontend-v2/app/README.md](../frontend-v2/app/README.md) | App Router — map `page.tsx` theo URL |
| [frontend-v2/components/README.md](../frontend-v2/components/README.md) | UI tái sử dụng |
| [frontend-v2/hooks/README.md](../frontend-v2/hooks/README.md) | Custom hooks |
| [frontend-v2/lib/README.md](../frontend-v2/lib/README.md) | API client, Telegram adapter, telemetry |
| [frontend-v2/public/README.md](../frontend-v2/public/README.md) | Asset tĩnh (`public/`) |

---

## 6. Runner / Windows integration

| Document | Notes |
|----------|-------|
| [runner/README.md](../runner/README.md) | **Stub/reference** in this repo — production Windows runner lives in a separate repo |
| [WINDOWS_RUNNER_HANDOFF_runner-win-01.md](../WINDOWS_RUNNER_HANDOFF_runner-win-01.md) | Handoff for a specific runner node — **verify paths/IDs against current deployment** |
| [docs/WINDOWS_RUNNER_HANDOFF_PROMPT.md](WINDOWS_RUNNER_HANDOFF_PROMPT.md) | Prompt-style handoff — **historical / verify before use** |
| [WINDOWS_RUNNER_INTEGRATION_PROMPT.md](../backend_ai/backend/app/runner/WINDOWS_RUNNER_INTEGRATION_PROMPT.md) | **Canonical** — định dạng wire + API runner (nội dung tiếng Việt, ví dụ JSON giữ nguyên) |

---

## 7. Ops, webhooks, trading

| Document | Notes |
|----------|-------|
| [ops/README.md](../ops/README.md) | Ops layout |
| [ops/monitoring/README.md](../ops/monitoring/README.md) | Monitoring notes |
| [TRADINGVIEW_MT5_WEBHOOK_RUNBOOK.md](TRADINGVIEW_MT5_WEBHOOK_RUNBOOK.md) | Runbook tiếng Việt: TradingView → broadcast → Redis → runner |
| [TRADINGVIEW_ORDER_CONTRACT.md](TRADINGVIEW_ORDER_CONTRACT.md) | Contract chuẩn product: TradingView absolute levels → backend normalized distance request → Windows runner |

---

## 8. Bot registry (Linux packages)

Các đường dẫn dưới đây **có thể không tồn tại** trên clone tối giản (registry chỉ có trên máy build / submodule). Khi 404, lấy package + doc từ kênh release nội bộ.

| Document | Notes |
|----------|-------|
| [bot-trading/README.md](../bot-trading/README.md) | Bot registry on Linux — optional in minimal clone |
| [bot-trading/PACKAGE_STANDARD.md](../bot-trading/PACKAGE_STANDARD.md) | Package standard — optional |
| [bot-trading/PLATFORM_INTEGRATION_PLAN.md](../bot-trading/PLATFORM_INTEGRATION_PLAN.md) | **Historical / verify before use** — planning doc, not execution runbook |

---

## 9. Config & nginx

| Document | Notes |
|----------|-------|
| [config/README.md](../config/README.md) | Nginx / config ownership |

---

## 10. AI knowledge & deprecated candidates

| Path | Notes |
|------|-------|
| [backend_ai/backend/ai_knowledge/README.md](../backend_ai/backend/ai_knowledge/README.md) | Knowledge base index for AI assistant — **not** API contract |
| Various `ai_knowledge/**/*.md` | Domain text for RAG / support — **historical / verify before use** for live trading decisions |

---

## 11. Repository root config (reference only)

| File | Notes |
|------|-------|
| [nginx.conf](../nginx.conf) | Sample/baseline — production may differ |
| [docker-compose.yml](../docker-compose.yml) | Local/dev compose — see comments inside file |
| [ecosystem.config.js](../ecosystem.config.js) | PM2 example with **hard-coded host paths** — adjust for your server layout |
| [vercel.json](../vercel.json) | Vercel build Mini App; `rewrites` → backend Linux (IP/host + cổng 8001/8081) phải khớp hạ tầng thật; static `out/` thường do backend Linux serve. |
