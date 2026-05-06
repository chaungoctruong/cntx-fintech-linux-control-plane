# Config Ownership

This directory holds the handoff-facing config artifacts for the active Linux control-plane workstream.

## Canonical Nginx Files

- `config/nginx-spider.conf`
  - canonical single-node control-plane nginx sample for release manifests and handoff
- `ops/ha/nginx/control-plane-edge.conf`
  - canonical HA edge/include sample for the shared control-plane endpoint

## Legacy / Local File Kept For Reference

- `../nginx.conf`
  - local or legacy host-level example kept for operator context
  - not the canonical release artifact
  - do not treat it as the source of truth when preparing a clean handoff

## Environment Baselines

- `../.env.linux.example`
  - local `docker-compose.yml` compatibility baseline only
- `../backend_ai/backend/.env.control-plane.example`
  - active control-plane baseline for PM2/systemd style deployment
- `../backend_ai/backend/.env.connect.example`
  - frozen legacy broker/API adapter reference only
