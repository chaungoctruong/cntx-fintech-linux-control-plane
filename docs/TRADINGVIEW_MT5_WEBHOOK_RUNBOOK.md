# TradingView -> MT5 Fan-Out Runbook

This runbook connects one TradingView signal to many MT5 user accounts:

```text
TradingView alert
-> POST /api/v2/public/tradingview/broadcast
-> backend selects tradingview_signal_subscriptions
-> backend writes execution_commands
-> Redis list mt5:runner:{runner_id}:commands
-> Windows runner executes MT5 order
```

## 1. Requirements

- Linux backend is running and healthy.
- Redis is reachable by Windows runner.
- Windows runner is online with `RUNNER_TRANSPORT=redis_queue`.
- The customer account has a bot deployment with `status=running`.
- The account is subscribed to the same `signal_id` used in TradingView.
- For multi-bot operation, use a distinct `signal_id` per bot and include the
  matching `bot_code` in the subscription/alert JSON.

If the deployment is not `running`, the broadcast endpoint will accept the alert
but it will not dispatch an MT5 order for that account.

## 2. Subscribe An Account To A Signal

Run inside the backend container:

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py subscribe \
  --account-id 9 \
  --signal-id gsalgovip-xauusd \
  --bot-code gsalgovip \
  --priority 60
```

Check readiness:

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py doctor \
  --signal-id gsalgovip-xauusd
```

`ready_for_live_signal` becomes `true` only when at least one subscribed account
has an active running deployment with runner and slot assigned.

## 3. Generate TradingView Alert JSON

Print the webhook URL and three messages:

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py alert-json \
  --signal-id gsalgovip-xauusd \
  --bot-code gsalgovip \
  --symbol XAUUSD
```

To print the configured webhook secret directly into the JSON:

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py alert-json \
  --signal-id gsalgovip-xauusd \
  --bot-code gsalgovip \
  --symbol XAUUSD \
  --include-secret
```

Use the same webhook URL for all three TradingView alerts:

```text
https://cntxlabs.vercel.app/api/v2/public/tradingview/broadcast
```

Create three alerts and paste one JSON body into each alert message:

- `BUY`
- `SELL`
- `CLOSE`

## 4. Test Without Sending A Real Order

Dry-run payload:

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py test-broadcast \
  --signal-id gsalgovip-xauusd \
  --bot-code gsalgovip \
  --action BUY \
  --symbol XAUUSD
```

Actually send the webhook:

```bash
docker compose exec spider-app python scripts/setup_tradingview_signal.py test-broadcast \
  --signal-id gsalgovip-xauusd \
  --bot-code gsalgovip \
  --action BUY \
  --symbol XAUUSD \
  --send
```

Only run `--send` when the account is intended to receive a live/test MT5 order.

## 5. TradingView Notes

- Enable 2FA on the TradingView account; webhooks require it.
- Keep the alert message valid JSON.
- `alert_id` is optional. If present, use a unique value per real signal; the
  backend dedupes retries by `alert_id`, account, and command kind. If omitted,
  backend generates an id automatically.
- Keep lot size small during first tests.
