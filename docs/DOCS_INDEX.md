# Documentation index — Spider AI Linux control plane

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
| [backend_ai/backend/README.md](../backend_ai/backend/README.md) | Backend-focused overview |
| [backend_ai/backend/migrations/README.md](../backend_ai/backend/migrations/README.md) | Alembic workflow — **read before new revisions** |
| [backend_ai/backend/scripts/README.md](../backend_ai/backend/scripts/README.md) | Operational scripts |
| [backend_ai/backend/app/runner/WINDOWS_RUNNER_INTEGRATION_PROMPT.md](../backend_ai/backend/app/runner/WINDOWS_RUNNER_INTEGRATION_PROMPT.md) | Contract prompt for Windows runner implementers |

---

## 4. Hubbot (Telegram)

| Document | Notes |
|----------|-------|
| [hubbot/README.md](../hubbot/README.md) | Bot package layout |
| [hubbot/app/README.md](../hubbot/app/README.md) | Application modules |

---

## 5. Frontend (Mini App)

| Document | Notes |
|----------|-------|
| [frontend-v2/README.md](../frontend-v2/README.md) | Next.js 14 static export, `NEXT_PUBLIC_*` build-time vars |

---

## 6. Runner / Windows integration

| Document | Notes |
|----------|-------|
| [runner/README.md](../runner/README.md) | **Stub/reference** in this repo — production Windows runner lives in a separate repo |
| [WINDOWS_RUNNER_HANDOFF_runner-win-01.md](../WINDOWS_RUNNER_HANDOFF_runner-win-01.md) | Handoff for a specific runner node — **verify paths/IDs against current deployment** |
| [docs/WINDOWS_RUNNER_HANDOFF_PROMPT.md](WINDOWS_RUNNER_HANDOFF_PROMPT.md) | Prompt-style handoff — **historical / verify before use** |
| [WINDOWS_RUNNER_INTEGRATION_PROMPT.md](../backend_ai/backend/app/runner/WINDOWS_RUNNER_INTEGRATION_PROMPT.md) | **Canonical** wire-format and API expectations for runner authors |

---

## 7. Ops, webhooks, trading

| Document | Notes |
|----------|-------|
| [ops/README.md](../ops/README.md) | Ops layout |
| [ops/monitoring/README.md](../ops/monitoring/README.md) | Monitoring notes |
| [TRADINGVIEW_MT5_WEBHOOK_RUNBOOK.md](TRADINGVIEW_MT5_WEBHOOK_RUNBOOK.md) | TradingView → `POST /api/v2/public/tradingview/broadcast` → Redis → runner |

---

## 8. Bot registry (Linux packages)

| Document | Notes |
|----------|-------|
| [bot-trading/README.md](../bot-trading/README.md) | Bot registry on Linux, `gsalgovip` contract, no secrets in packages |
| [bot-trading/PACKAGE_STANDARD.md](../bot-trading/PACKAGE_STANDARD.md) | Package standard |
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
| [vercel.json](../vercel.json) | Vercel metadata if used — Mini App static export is normally served by the backend |
