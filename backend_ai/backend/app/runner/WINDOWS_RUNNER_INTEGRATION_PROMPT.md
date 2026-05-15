# Windows Runner Integration Contract

Production contract after removing the old standalone MT5 account-check lane.

## Transport

- Commands are delivered through Redis list: `mt5:runner:{RUNNER_ID}:commands`.
- HTTP remains for registration, heartbeat, events, delivery ack, and package fetch.
- There is no independent account-check queue.

## Login Slot Flow

1. Mini App sends broker, server, login, password to backend.
2. Backend stores the account credential and dispatches `RESERVE_OR_LOGIN_SLOT`.
3. Windows runner selects the assigned free slot and opens the real MT5 terminal with that credential.
4. If login succeeds, runner emits `LOGIN_SLOT_VERIFIED` with `login_reservation_id`, `account_id`, `runner_id`, and `slot_id`.
5. Backend keeps that slot for up to `login_slot_ttl_sec` seconds, default 300.
6. If user starts bot before expiry, backend sends `START_BOT` for the same runner/slot with `reuse_login_slot=true`.
7. If user does not start in time, backend releases the reservation and marks the slot ready.
8. If login fails, server is wrong, or MT5 cannot start, runner emits `LOGIN_SLOT_FAILED` and backend releases the slot immediately.

## Required Runner Events

- `LOGIN_SLOT_VERIFIED`
- `LOGIN_SLOT_FAILED`
- `LOGIN_SLOT_RELEASED`
- Existing bot lifecycle events for `START_BOT` and `STOP_BOT`

## Required Command Support

- `RESERVE_OR_LOGIN_SLOT`
- `START_BOT`
- `STOP_BOT`
- Existing trade/order commands

## Slot Capacity

Each Windows node may expose many local MT5 templates, but backend production routing only accepts slot numbers up to 10 per node. When a node has 10 active/login-reserved slots, backend schedules the next user onto another online node.
