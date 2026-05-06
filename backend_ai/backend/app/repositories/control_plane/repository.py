from __future__ import annotations

from typing import Any, Optional

from app.models.control_plane import ACTIVE_DEPLOYMENT_STATUSES
from app.store import Store

from app.repositories.control_plane.mixins.accounts import ControlPlaneAccountsMixin
from app.repositories.control_plane.mixins.commands import ControlPlaneCommandsMixin
from app.repositories.control_plane.mixins.deployments import ControlPlaneDeploymentsMixin
from app.repositories.control_plane.mixins.runners_slots import ControlPlaneRunnersSlotsMixin
from app.repositories.control_plane.mixins.users import ControlPlaneUserMixin
from app.repositories.control_plane.mixins.verification import ControlPlaneVerificationMixin
from app.repositories.control_plane.query_loader import load_sql
from app.repositories.control_plane.support import (
    _decorate_account_verification_projection,
    _epoch_now,
    _json_list,
    _json_loads_if_string,
    _json_payload,
    _norm,
    _norm_catalog_identity,
    _norm_slot_id,
    _safe_int,
)


class ControlPlaneRepository(
    ControlPlaneRunnersSlotsMixin,
    ControlPlaneCommandsMixin,
    ControlPlaneDeploymentsMixin,
    ControlPlaneVerificationMixin,
    ControlPlaneAccountsMixin,
    ControlPlaneUserMixin,
):
    _SQL_CLOSE_RUNNER_BOT_STATE_PENDING_ENTRY = load_sql(
        "runner_bot_state/close_runner_bot_state_pending_entry.sql"
    )
    _SQL_COMPUTE_REALIZED_PNL_TODAY_FOR_ACCOUNT = load_sql(
        "accounts/compute_realized_pnl_today_for_account.sql"
    )
    _SQL_COUNT_BROKER_ACCOUNTS_FOR_USER = load_sql("accounts/count_broker_accounts_for_user.sql")
    _SQL_COUNT_USER_ACCOUNTS_WITH_RISK_POLICY = load_sql("accounts/count_user_accounts_with_risk_policy.sql")
    _SQL_COUNT_USER_ACTIVE_DEPLOYMENTS_ALL_MODES = load_sql(
        "deployments/count_user_active_deployments_all_modes.sql"
    )
    _SQL_COUNT_USER_ACTIVE_DEPLOYMENTS_LIVE_ONLY = load_sql(
        "deployments/count_user_active_deployments_live_only.sql"
    )
    _SQL_CREATE_USER_WEBHOOK = load_sql("user_webhooks/insert_user_webhook.sql")
    _SQL_DELETE_USER_WEBHOOK = load_sql("user_webhooks/delete_user_webhook.sql")
    _SQL_GET_ACCOUNT_RISK_POLICY = load_sql("accounts/get_account_risk_policy.sql")
    _SQL_GET_ACCOUNT_STATE = load_sql("snapshots/get_account_state.sql")
    _SQL_GET_BOT_BY_NAME = load_sql("catalog/get_bot_by_name.sql")
    _SQL_GET_DASHBOARD_ACCOUNT_SUMMARY = load_sql("dashboard/get_dashboard_account_summary.sql")
    _SQL_GET_DASHBOARD_DEPLOYMENT_SUMMARY = load_sql("dashboard/get_dashboard_deployment_summary.sql")
    _SQL_GET_DASHBOARD_PNL_SUMMARY = load_sql("dashboard/get_dashboard_pnl_summary.sql")
    _SQL_GET_OPS_SUMMARY_BINDINGS_STICKY_MISMATCH = load_sql(
        "ops_summary/get_ops_summary_bindings_sticky_mismatch.sql"
    )
    _SQL_GET_OPS_SUMMARY_COMMANDS = load_sql("ops_summary/get_ops_summary_commands.sql")
    _SQL_GET_OPS_SUMMARY_DEPLOYMENTS = load_sql("ops_summary/get_ops_summary_deployments.sql")
    _SQL_GET_OPS_SUMMARY_EVENTS = load_sql("ops_summary/get_ops_summary_events.sql")
    _SQL_GET_OPS_SUMMARY_RUNNERS = load_sql("ops_summary/get_ops_summary_runners.sql")
    _SQL_GET_OPS_SUMMARY_SLOTS = load_sql("ops_summary/get_ops_summary_slots.sql")
    _SQL_GET_OPS_SUMMARY_VERIFICATION = load_sql("ops_summary/get_ops_summary_verification.sql")
    _SQL_GET_RUNTIME_HEALTH_ACCOUNTS = load_sql("runtime_health/get_runtime_health_accounts.sql")
    _SQL_GET_RUNTIME_HEALTH_DEPLOYMENTS = load_sql("runtime_health/get_runtime_health_deployments.sql")
    _SQL_GET_RUNTIME_HEALTH_EVENTS = load_sql("runtime_health/get_runtime_health_events.sql")
    _SQL_GET_RUNTIME_HEALTH_RUNNERS = load_sql("runtime_health/get_runtime_health_runners.sql")
    _SQL_GET_RUNTIME_HEALTH_SLOTS = load_sql("runtime_health/get_runtime_health_slots.sql")
    _SQL_GET_USER_ACTIVE_SUBSCRIPTION = load_sql("billing/get_user_active_subscription.sql")
    _SQL_GET_USER_METADATA = load_sql("users/get_user_metadata.sql")
    _SQL_LIST_ACCOUNTS_WITH_ACTIVE_CIRCUIT_BREAKER = load_sql(
        "accounts/list_accounts_with_active_circuit_breaker.sql"
    )
    _SQL_LIST_BOTS = load_sql("catalog/list_bots.sql")
    _SQL_LIST_DEPLOYMENT_ORDER_FILLED_EVENTS = load_sql(
        "deployments/list_deployment_order_filled_events.sql"
    )
    _SQL_LIST_DEPLOYMENT_ORDER_FILLED_EVENTS_SINCE = load_sql(
        "deployments/list_deployment_order_filled_events_since.sql"
    )
    _SQL_LIST_POSITION_SNAPSHOTS = load_sql("snapshots/list_position_snapshots.sql")
    _SQL_LIST_POSITION_SNAPSHOTS_BY_DEPLOYMENT = load_sql("snapshots/list_position_snapshots_by_deployment.sql")
    _SQL_LIST_RUNNER_IDS_ORDERED = load_sql("ops_summary/list_runner_ids_ordered.sql")
    _SQL_LIST_USER_WEBHOOKS = load_sql("user_webhooks/list_user_webhooks.sql")
    _SQL_LIST_USER_WEBHOOKS_WITH_SECRET = load_sql("user_webhooks/list_user_webhooks_with_secret.sql")
    _SQL_LOAD_ACTIVE_RUNNER_BOT_STATE_PENDING_ENTRY = load_sql(
        "runner_bot_state/load_active_runner_bot_state_pending_entry.sql"
    )
    _SQL_RECONCILE_MARK_ACCOUNT_STATE_SNAPSHOTS_STALE = load_sql(
        "reconcile/reconcile_mark_account_state_snapshots_stale.sql"
    )
    _SQL_RECONCILE_MARK_DEPLOYMENTS_HEALTH_STALE = load_sql(
        "reconcile/reconcile_mark_deployments_health_stale.sql"
    )
    _SQL_RECONCILE_MARK_RUNNER_NODES_OFFLINE = load_sql("reconcile/reconcile_mark_runner_nodes_offline.sql")
    _SQL_RECONCILE_REFRESH_RUNNER_SLOT_COUNTS_METADATA = load_sql(
        "reconcile/reconcile_refresh_runner_slot_counts_metadata.sql"
    )
    _SQL_RECONCILE_REFRESH_STICKY_SLOT_PROJECTION = load_sql(
        "reconcile/reconcile_refresh_sticky_slot_projection.sql"
    )
    _SQL_RECONCILE_SLOT_PROJECTION_FROM_EVENTS = load_sql(
        "reconcile/reconcile_slot_projection_from_events.sql"
    )
    _SQL_RECONCILE_STALE_STOP_REQUESTED_DEPLOYMENTS = load_sql(
        "reconcile/reconcile_stale_stop_requested_deployments.sql"
    )
    _SQL_RECONCILE_START_BOOTSTRAP_FAILURES = load_sql("reconcile/reconcile_start_bootstrap_failures.sql")
    _SQL_RETIRE_BOT_CATALOG_ENTRIES = load_sql("catalog/retire_bot_catalog_entries.sql")
    _SQL_RETIRE_MISSING_BOTS_ALL_NON_RUNNER = load_sql("catalog/retire_missing_bots_all_non_runner.sql")
    _SQL_RETIRE_STALE_RUNNER_BOT_CATALOG_NO_ACTIVE = load_sql(
        "catalog/retire_stale_runner_bot_catalog_no_active.sql"
    )
    _SQL_RETIRE_STALE_RUNNER_BOT_CATALOG_WHEN_ACTIVE = load_sql(
        "catalog/retire_stale_runner_bot_catalog_when_active.sql"
    )
    _SQL_RUNNER_BOT_STATE_RECORD_EXISTS = load_sql("runner_bot_state/runner_bot_state_record_exists.sql")
    _SQL_SCRUB_ACCOUNT_CREDENTIALS_FOR_USER = load_sql("accounts/scrub_account_credentials_for_user.sql")
    _SQL_SELECT_DEPLOYMENT_OWNED_BY_USER = load_sql("deployments/select_deployment_owned_by_user.sql")
    _SQL_SELECT_RUNNER_BOT_STATE_REALIZED_PNL_RECENT = load_sql(
        "runner_bot_state/select_runner_bot_state_realized_pnl_recent.sql"
    )
    _SQL_SOFT_DELETE_BROKER_ACCOUNTS_BY_USER = load_sql("accounts/soft_delete_broker_accounts_by_user.sql")
    _SQL_SUM_RUNNER_BOT_STATE_REALIZED_PNL = load_sql("runner_bot_state/sum_runner_bot_state_realized_pnl.sql")
    _SQL_UPDATE_ACCOUNT_RISK_POLICY = load_sql("accounts/update_account_risk_policy.sql")
    _SQL_UPDATE_USER_METADATA = load_sql("users/update_user_metadata.sql")
    _SQL_UPSERT_ACCOUNT_STATE_SNAPSHOT = load_sql("snapshots/upsert_account_state_snapshot.sql")
    _SQL_UPSERT_BOT_CATALOG_ENTRY = load_sql("catalog/upsert_bot_catalog_entry.sql")
    _SQL_UPSERT_BOT_VERSION = load_sql("catalog/upsert_bot_version.sql")
    _SQL_UPSERT_POSITION_SNAPSHOT = load_sql("snapshots/upsert_position_snapshot.sql")
    _SQL_UPSERT_RUNNER_BOT_STATE_RECORD = load_sql("runner_bot_state/upsert_runner_bot_state_record.sql")

    def __init__(self, store: Store) -> None:
        self._store = store

    def upsert_bot_catalog_entry(self, definition: dict[str, Any]) -> dict[str, Any]:
        bot_id = _norm(definition.get("bot_id") or definition.get("bot_code") or definition.get("bot_name"))
        bot_name = _norm(definition.get("bot_name") or bot_id)
        display_name = _norm(definition.get("display_name") or bot_name or bot_id)
        if not bot_id or not bot_name:
            raise ValueError("invalid_bot_definition")

        payload = {
            "display_name": display_name,
            "language": _norm(definition.get("language") or "other"),
            "version": _norm(definition.get("version") or "0.1.0"),
            "profile_class": _norm(definition.get("profile_class") or "normal"),
            "runtime_entry": _norm(definition.get("runtime_entry")),
            "required_params": list(definition.get("required_params") or []),
            "risk_profile": dict(definition.get("risk_profile") or {}),
            "indicator_requirements": list(definition.get("indicator_requirements") or []),
            "strategy_tags": list(definition.get("strategy_tags") or []),
            "resource_hints": dict(definition.get("resource_hints") or {}),
            "supports_demo": bool(definition.get("supports_demo", True)),
            "supports_live": bool(definition.get("supports_live", True)),
            "default_config_path": _norm(definition.get("default_config_path")) or None,
            "runtime_env": dict(definition.get("runtime_env") or {}),
            "checksum": _norm(definition.get("checksum")),
            "source_path": _norm(definition.get("source_path")),
            "metadata": dict(definition.get("metadata") or {}),
        }

        now = _epoch_now()

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_UPSERT_BOT_CATALOG_ENTRY,
                (
                    bot_id,
                    bot_name,
                    display_name,
                    _json_list(definition.get("strategy_tags") or []),
                    now,
                    now,
                    payload["display_name"],
                    payload["language"],
                    payload["version"],
                    payload["profile_class"],
                    payload["runtime_entry"],
                    _json_list(payload["required_params"]),
                    _json_payload(payload["risk_profile"]),
                    _json_list(payload["indicator_requirements"]),
                    _json_list(payload["strategy_tags"]),
                    _json_payload(payload["resource_hints"]),
                    payload["supports_demo"],
                    payload["supports_live"],
                    payload["default_config_path"],
                    _json_payload(payload["runtime_env"]),
                    payload["checksum"],
                    payload["source_path"],
                    _json_payload(payload),
                ),
            )
            return dict(cur.fetchone() or {})

        return self._store._with_retry_locked(_do)

    def upsert_bot_version(self, *, bot_id: str, version: str, checksum: str, source_path: str, metadata: dict[str, Any]) -> None:
        bot_id_s = _norm(bot_id)
        version_s = _norm(version) or "0.1.0"

        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                self._SQL_UPSERT_BOT_VERSION,
                (bot_id_s, version_s, _norm(checksum), _norm(source_path), _json_payload(metadata)),
            )

        self._store._with_retry_locked(_do)

    def retire_missing_bots(self, *, active_bot_ids: list[str]) -> None:
        active = sorted({_norm(item) for item in active_bot_ids if _norm(item)})
        now = _epoch_now()

        def _do(con: Any, cur: Any) -> None:
            if active:
                placeholders = ",".join(["%s"] * len(active))
                cur.execute(
                    f"""
                    UPDATE bot_catalog
                    SET enabled = FALSE,
                        status = 'RETIRED',
                        updated_at = %s
                    WHERE bot_code NOT IN ({placeholders})
                      AND COALESCE(source_path, '') NOT LIKE 'runner://%%'
                      AND COALESCE(metadata_json->>'catalog_origin', '') <> 'runner'
                    """,
                    (now, *tuple(active)),
                )
            else:
                cur.execute(
                    self._SQL_RETIRE_MISSING_BOTS_ALL_NON_RUNNER,
                    (now,),
                )

        self._store._with_retry_locked(_do)

    def retire_bot_catalog_entries(self, *, bot_identities: list[str]) -> dict[str, Any]:
        identities = sorted({_norm_catalog_identity(item) for item in bot_identities if _norm_catalog_identity(item)})
        if not identities:
            return {"retired_count": 0, "bot_codes": []}
        now = _epoch_now()

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_RETIRE_BOT_CATALOG_ENTRIES,
                (now, identities, identities, identities),
            )
            rows = [dict(row) for row in (cur.fetchall() or [])]
            return {
                "retired_count": len(rows),
                "bot_codes": [str(row.get("bot_code") or "") for row in rows if str(row.get("bot_code") or "")],
            }

        return self._store._with_retry_locked(_do)

    def retire_stale_runner_bot_catalog_entries(self, *, runner_id: str, active_bot_ids: list[str]) -> dict[str, Any]:
        runner_id_s = _norm(runner_id)
        if not runner_id_s:
            return {"retired_count": 0, "bot_codes": []}
        active = sorted({_norm_catalog_identity(item) for item in active_bot_ids if _norm_catalog_identity(item)})
        now = _epoch_now()

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            if active:
                cur.execute(
                    self._SQL_RETIRE_STALE_RUNNER_BOT_CATALOG_WHEN_ACTIVE,
                    (now, runner_id_s, f"runner://{runner_id_s}/%", active, active, active),
                )
            else:
                cur.execute(
                    self._SQL_RETIRE_STALE_RUNNER_BOT_CATALOG_NO_ACTIVE,
                    (now, runner_id_s, f"runner://{runner_id_s}/%"),
                )
            rows = [dict(row) for row in (cur.fetchall() or [])]
            return {
                "retired_count": len(rows),
                "bot_codes": [str(row.get("bot_code") or "") for row in rows if str(row.get("bot_code") or "")],
            }

        return self._store._with_retry_locked(_do)

    def list_bots(self) -> list[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(self._SQL_LIST_BOTS)
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def get_bot_by_name(self, *, bot_name: str) -> Optional[dict[str, Any]]:
        value = _norm(bot_name)
        if not value:
            return None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_BOT_BY_NAME,
                (value, value, value),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def upsert_account_state_snapshot(
        self,
        *,
        account_id: int,
        deployment_id: Optional[int],
        runner_id: Optional[str],
        slot_id: Optional[str],
        connection_status: str,
        pnl: Optional[float],
        balance: Optional[float],
        equity: Optional[float],
        free_margin: Optional[float],
        payload: dict[str, Any],
    ) -> None:
        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                self._SQL_UPSERT_ACCOUNT_STATE_SNAPSHOT,
                (
                    int(account_id),
                    int(deployment_id) if deployment_id is not None else None,
                    _norm(runner_id) or None,
                    _norm_slot_id(slot_id) or None,
                    _norm(connection_status) or "connected",
                    pnl,
                    balance,
                    equity,
                    free_margin,
                    _json_payload(payload),
                ),
            )

        self._store._with_retry_locked(_do)

    def upsert_position_snapshot(
        self,
        *,
        account_id: int,
        deployment_id: Optional[int],
        position_key: str,
        symbol: str,
        side: str,
        volume: Optional[float],
        entry_price: Optional[float],
        mark_price: Optional[float],
        pnl: Optional[float],
        payload: dict[str, Any],
    ) -> None:
        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                self._SQL_UPSERT_POSITION_SNAPSHOT,
                (
                    int(account_id),
                    int(deployment_id) if deployment_id is not None else None,
                    _norm(position_key),
                    _norm(symbol),
                    _norm(side),
                    volume,
                    entry_price,
                    mark_price,
                    pnl,
                    _json_payload(payload),
                ),
            )

        self._store._with_retry_locked(_do)

    def upsert_runner_bot_state_record(
        self,
        *,
        operation: str,
        record_type: str,
        context: dict[str, Any],
        record_key: str,
        payload: dict[str, Any],
        status: str,
        symbol: Optional[str],
        side: Optional[str],
        realized_pnl: Optional[float],
        occurred_at: Optional[str],
    ) -> dict[str, Any]:
        context_json = _json_payload(context)
        payload_json = _json_payload(payload)

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_UPSERT_RUNNER_BOT_STATE_RECORD,
                (
                    _norm(context.get("bot_id")) or "gsalgo_mt5_bot",
                    _norm(context.get("schema")) or "gsalgo_backend_state.v1",
                    _norm(operation),
                    _norm(record_type),
                    _norm(record_key),
                    int(context["account_id"]),
                    int(context["deployment_id"]),
                    _norm(context.get("runner_id")),
                    _norm_slot_id(context.get("slot_id")),
                    _norm(status) or "recorded",
                    _norm(symbol) or None,
                    _norm(side) or None,
                    realized_pnl,
                    occurred_at,
                    payload_json,
                    context_json,
                ),
            )
            return dict(cur.fetchone() or {})

        return self._store._with_retry_locked(_do)

    def runner_bot_state_record_exists(
        self,
        *,
        record_type: str,
        context: dict[str, Any],
        record_key: str,
    ) -> bool:
        def _do(con: Any, cur: Any) -> bool:
            cur.execute(
                self._SQL_RUNNER_BOT_STATE_RECORD_EXISTS,
                (
                    _norm(context.get("bot_id")) or "gsalgo_mt5_bot",
                    _norm(record_type),
                    int(context["account_id"]),
                    int(context["deployment_id"]),
                    _norm(record_key),
                ),
            )
            return bool(cur.fetchone())

        return self._store._with_retry_read(_do)

    def load_active_runner_bot_state_pending_entry(self, *, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_LOAD_ACTIVE_RUNNER_BOT_STATE_PENDING_ENTRY,
                (
                    _norm(context.get("bot_id")) or "gsalgo_mt5_bot",
                    int(context["account_id"]),
                    int(context["deployment_id"]),
                ),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def close_runner_bot_state_pending_entry(
        self,
        *,
        context: dict[str, Any],
        record_key: Optional[str],
        payload: dict[str, Any],
        closed_at: Optional[str],
    ) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_CLOSE_RUNNER_BOT_STATE_PENDING_ENTRY,
                (
                    _norm(context.get("bot_id")) or "gsalgo_mt5_bot",
                    int(context["account_id"]),
                    int(context["deployment_id"]),
                    _norm(record_key) or None,
                    _norm(record_key) or None,
                    closed_at,
                    _json_payload(payload),
                    _json_payload(context),
                ),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_locked(_do)

    def sum_runner_bot_state_realized_pnl(self, *, context: dict[str, Any], day: str) -> dict[str, Any]:
        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_SUM_RUNNER_BOT_STATE_REALIZED_PNL,
                (
                    _norm(context.get("bot_id")) or "gsalgo_mt5_bot",
                    int(context["account_id"]),
                    int(context["deployment_id"]),
                    _norm(day),
                ),
            )
            row = dict(cur.fetchone() or {})
            return {
                "date": _norm(day),
                "realized_pnl": float(row.get("realized_pnl") or 0.0),
                "record_count": _safe_int(row.get("record_count"), 0),
            }

        return self._store._with_retry_read(_do)

    def count_runner_bot_state_consecutive_losses(self, *, context: dict[str, Any], limit: int = 100) -> dict[str, Any]:
        limit_i = max(1, min(int(limit or 100), 1000))

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_SELECT_RUNNER_BOT_STATE_REALIZED_PNL_RECENT,
                (
                    _norm(context.get("bot_id")) or "gsalgo_mt5_bot",
                    int(context["account_id"]),
                    int(context["deployment_id"]),
                    limit_i,
                ),
            )
            count = 0
            scanned = 0
            for row in cur.fetchall() or []:
                scanned += 1
                try:
                    pnl = float(row.get("realized_pnl") or 0.0)
                except Exception:
                    break
                if pnl < 0:
                    count += 1
                    continue
                break
            return {"consecutive_losses": count, "scanned": scanned, "limit": limit_i}

        return self._store._with_retry_read(_do)

    def get_account_state(self, *, account_id: int, user_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_ACCOUNT_STATE,
                (int(account_id), int(user_id)),
            )
            row = cur.fetchone()
            return (
                _decorate_account_verification_projection(
                    dict(row),
                    account_status_key="connection_status",
                    job_status_key="verification_job_status",
                )
                if row
                else None
            )

        return self._store._with_retry_read(_do)

    def list_position_snapshots(
        self,
        *,
        account_id: int,
        user_id: int,
        deployment_id: Optional[int] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit_i = max(1, min(int(limit), 1000))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            if deployment_id is not None:
                cur.execute(
                    self._SQL_LIST_POSITION_SNAPSHOTS_BY_DEPLOYMENT,
                    (int(account_id), int(user_id), int(deployment_id), int(limit_i)),
                )
            else:
                cur.execute(
                    self._SQL_LIST_POSITION_SNAPSHOTS,
                    (int(account_id), int(user_id), int(limit_i)),
                )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def get_dashboard(self, *, user_id: int) -> dict[str, Any]:
        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_GET_DASHBOARD_ACCOUNT_SUMMARY,
                (int(user_id),),
            )
            account_summary = dict(cur.fetchone() or {})
            cur.execute(
                self._SQL_GET_DASHBOARD_DEPLOYMENT_SUMMARY,
                (int(user_id),),
            )
            deployment_summary = dict(cur.fetchone() or {})
            cur.execute(
                self._SQL_GET_DASHBOARD_PNL_SUMMARY,
                (int(user_id),),
            )
            pnl_summary = dict(cur.fetchone() or {})
            return {
                "accounts": account_summary,
                "deployments": deployment_summary,
                "pnl": pnl_summary,
            }

        return self._store._with_retry_read(_do)

    def reconcile_runtime_health(
        self,
        *,
        runner_stale_sec: int,
        deployment_stale_sec: int,
        stop_reconcile_sec: Optional[int] = None,
        slot_projection_lookback_sec: Optional[int] = None,
    ) -> dict[str, int]:
        runner_cutoff = max(30, int(runner_stale_sec))
        deployment_cutoff = max(30, int(deployment_stale_sec))
        stop_cutoff = max(10, int(stop_reconcile_sec if stop_reconcile_sec is not None else min(deployment_cutoff, 30)))
        slot_projection_lookback = max(300, int(slot_projection_lookback_sec or 21600))

        def _do(con: Any, cur: Any) -> dict[str, int]:
            cur.execute(
                self._SQL_RECONCILE_MARK_RUNNER_NODES_OFFLINE,
                (runner_cutoff,),
            )
            stale_runners = int(cur.rowcount or 0)

            cur.execute(
                self._SQL_RECONCILE_MARK_DEPLOYMENTS_HEALTH_STALE,
                (deployment_cutoff,),
            )
            stale_deployments = int(cur.rowcount or 0)

            cur.execute(
                self._SQL_RECONCILE_MARK_ACCOUNT_STATE_SNAPSHOTS_STALE,
                (deployment_cutoff,),
            )
            stale_accounts = int(cur.rowcount or 0)

            cur.execute(
                self._SQL_RECONCILE_STALE_STOP_REQUESTED_DEPLOYMENTS,
                (stop_cutoff, runner_cutoff, list(ACTIVE_DEPLOYMENT_STATUSES)),
            )
            row = dict(cur.fetchone() or {})
            reconciled_stop_requested_deployments = _safe_int(
                row.get("reconciled_stop_requested_deployments"),
                0,
            )
            failed_stale_start_commands = _safe_int(
                row.get("failed_stale_start_commands"),
                0,
            )
            acknowledged_stale_stop_commands = _safe_int(
                row.get("acknowledged_stale_stop_commands"),
                0,
            )

            cur.execute(
                self._SQL_RECONCILE_SLOT_PROJECTION_FROM_EVENTS,
                (slot_projection_lookback, slot_projection_lookback),
            )
            slot_projection_refreshed = int(cur.rowcount or 0)

            cur.execute(
                self._SQL_RECONCILE_REFRESH_STICKY_SLOT_PROJECTION,
            )
            sticky_projection_refreshed = int(cur.rowcount or 0)

            cur.execute(
                self._SQL_RECONCILE_REFRESH_RUNNER_SLOT_COUNTS_METADATA,
            )
            runner_projection_refreshed = int(cur.rowcount or 0)

            return {
                "stale_runners": stale_runners,
                "stale_deployments": stale_deployments,
                "stale_accounts": stale_accounts,
                "reconciled_stop_requested_deployments": reconciled_stop_requested_deployments,
                "failed_stale_start_commands": failed_stale_start_commands,
                "acknowledged_stale_stop_commands": acknowledged_stale_stop_commands,
                "slot_projection_refreshed": slot_projection_refreshed,
                "sticky_projection_refreshed": sticky_projection_refreshed,
                "runner_projection_refreshed": runner_projection_refreshed,
            }

        return self._store._with_retry_locked(_do)

    def reconcile_start_bootstrap_failures(self) -> dict[str, int]:
        """Close START deployments after runner reports fatal worker bootstrap failure."""

        def _do(con: Any, cur: Any) -> dict[str, int]:
            cur.execute(
                self._SQL_RECONCILE_START_BOOTSTRAP_FAILURES,
                (list(ACTIVE_DEPLOYMENT_STATUSES),),
            )
            row = dict(cur.fetchone() or {})
            return {
                "reconciled_start_bootstrap_failures": _safe_int(
                    row.get("reconciled_start_bootstrap_failures"),
                    0,
                ),
                "failed_bootstrap_start_commands": _safe_int(
                    row.get("failed_bootstrap_start_commands"),
                    0,
                ),
                "released_bootstrap_failure_slots": _safe_int(
                    row.get("released_bootstrap_failure_slots"),
                    0,
                ),
                "refreshed_bootstrap_failure_bindings": _safe_int(
                    row.get("refreshed_bootstrap_failure_bindings"),
                    0,
                ),
            }

        return self._store._with_retry_locked(_do)

    def get_runtime_health_summary(self, *, runner_stale_sec: int, deployment_stale_sec: int) -> dict[str, Any]:
        runner_cutoff = max(30, int(runner_stale_sec))
        deployment_cutoff = max(30, int(deployment_stale_sec))

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_GET_RUNTIME_HEALTH_RUNNERS,
                (runner_cutoff,),
            )
            runners = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_GET_RUNTIME_HEALTH_DEPLOYMENTS,
                (deployment_cutoff,),
            )
            deployments = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_GET_RUNTIME_HEALTH_SLOTS,
            )
            slots = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_GET_RUNTIME_HEALTH_ACCOUNTS,
            )
            accounts = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_GET_RUNTIME_HEALTH_EVENTS,
            )
            events = dict(cur.fetchone() or {})

            return {
                "runners": runners,
                "deployments": deployments,
                "slots": slots,
                "accounts": accounts,
                "events": events,
                "thresholds": {
                    "runner_stale_sec": runner_cutoff,
                    "deployment_stale_sec": deployment_cutoff,
                },
            }

        return self._store._with_retry_read(_do)

    def get_ops_summary_snapshot(self, *, runner_stale_sec: int, deployment_stale_sec: int) -> dict[str, Any]:
        runner_cutoff = max(30, int(runner_stale_sec))
        deployment_cutoff = max(30, int(deployment_stale_sec))

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_GET_OPS_SUMMARY_RUNNERS,
                (runner_cutoff,),
            )
            runners = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_GET_OPS_SUMMARY_SLOTS,
                (runner_cutoff,),
            )
            slots = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_GET_OPS_SUMMARY_VERIFICATION,
            )
            verification = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_GET_OPS_SUMMARY_COMMANDS,
            )
            commands = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_GET_OPS_SUMMARY_DEPLOYMENTS,
                (deployment_cutoff,),
            )
            deployments = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_GET_OPS_SUMMARY_EVENTS,
            )
            events = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_GET_OPS_SUMMARY_BINDINGS_STICKY_MISMATCH,
            )
            bindings = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_LIST_RUNNER_IDS_ORDERED,
            )
            runner_ids = [str(row["runner_id"]) for row in (cur.fetchall() or []) if str(row.get("runner_id") or "").strip()]

            return {
                "runner_ids": runner_ids,
                "runners": runners,
                "slots": slots,
                "verification": verification,
                "commands": commands,
                "deployments": deployments,
                "events": events,
                "bindings": bindings,
                "thresholds": {
                    "runner_stale_sec": runner_cutoff,
                    "deployment_stale_sec": deployment_cutoff,
                },
            }

        return self._store._with_retry_read(_do)

    def get_account_risk_policy(self, *, account_id: int, user_id: int) -> Optional[dict[str, Any]]:
        """Tra ve risk_policy_json + ownership check. None neu account khong thuoc user."""
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_ACCOUNT_RISK_POLICY,
                (int(account_id), int(user_id)),
            )
            row = cur.fetchone()
            if not row:
                return None
            row_d = dict(row)
            policy = _json_loads_if_string(row_d.get("risk_policy_json"), {})
            return dict(policy or {})

        return self._store._with_retry_read(_do)

    def update_account_risk_policy(
        self,
        *,
        account_id: int,
        user_id: int,
        policy: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Replace risk_policy_json (full overwrite). Tra None neu account khong thuoc user."""
        payload = _json_payload(policy or {})

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_UPDATE_ACCOUNT_RISK_POLICY,
                (payload, int(account_id), int(user_id)),
            )
            row = cur.fetchone()
            if not row:
                return None
            row_d = dict(row)
            stored = _json_loads_if_string(row_d.get("risk_policy_json"), {})
            return dict(stored or {})

        return self._store._with_retry_locked(_do)

    def compute_realized_pnl_today_for_account(
        self,
        *,
        account_id: int,
        timezone_offset_minutes: int = 0,
    ) -> dict[str, Any]:
        """Tinh realized PnL trong ngay (00:00 local) cho 1 account.

        Doc tu execution_events event_type='ORDER_FILLED', SUM payload_json->>'realized_pnl'.
        Thieu cot rieng -> coi nhu 0. Mot so runner co the dung key khac
        ('realized_pnl' / 'closed_pnl' / 'net_pnl'), ta cong don ca 3 (uu tien
        realized_pnl, fallback closed_pnl, fallback net_pnl).

        timezone_offset_minutes: cac runner Window thuong dung UTC+0 hoac local broker time;
            mac dinh 0 (UTC). FE/admin co the override neu can hien thi theo timezone khach.
        """
        offset_sec = int(timezone_offset_minutes) * 60

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_COMPUTE_REALIZED_PNL_TODAY_FOR_ACCOUNT,
                (offset_sec, offset_sec, int(account_id)),
            )
            row = dict(cur.fetchone() or {})
            try:
                pnl_val = float(row.get("pnl") or 0)
            except Exception:
                pnl_val = 0.0
            return {
                "account_id": int(account_id),
                "realized_pnl_today": pnl_val,
                "event_count": int(row.get("event_count") or 0),
                "today_start_ts": int(row.get("today_start_ts") or 0),
                "timezone_offset_minutes": int(timezone_offset_minutes),
            }

        return self._store._with_retry_read(_do)

    def soft_delete_user_accounts(self, *, user_id: int, reason: str = "") -> int:
        """GDPR soft-delete: mark accounts disconnected + scrub credentials blob.

        - Set broker_accounts.status='disconnected', is_active=FALSE, last_error=reason.
        - Scrub account_credentials_encrypted.password_encrypted -> '' (blob bi xoa).
        - Tra ve so account bi anh huong.
        Khong xoa hard execution_events/audit_logs (giu phap ly).
        """
        clean_reason = (reason or "user_self_delete")[:200]

        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                self._SQL_SOFT_DELETE_BROKER_ACCOUNTS_BY_USER,
                (clean_reason, int(user_id)),
            )
            affected = int(cur.rowcount or 0)
            cur.execute(
                self._SQL_SCRUB_ACCOUNT_CREDENTIALS_FOR_USER,
                (int(user_id),),
            )
            return affected

        return self._store._with_retry_locked(_do)

    def get_user_active_subscription(self, *, user_id: int) -> Optional[dict[str, Any]]:
        """Tra subscription `active` moi nhat cua user. None neu khong co (free tier)."""
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_USER_ACTIVE_SUBSCRIPTION,
                (int(user_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def count_user_active_deployments(self, *, user_id: int, include_paper: bool = False) -> int:
        """So deployment user dang chiem slot (start_requested/starting/running/stop_requested).

        Mac dinh CHI dem live mode (paper khong tinh vao quota).
        Truyen include_paper=True neu can dem ca paper cho policy 1 Telegram ID / 1 bot.
        """
        def _do(con: Any, cur: Any) -> int:
            if include_paper:
                cur.execute(
                    self._SQL_COUNT_USER_ACTIVE_DEPLOYMENTS_ALL_MODES,
                    (int(user_id),),
                )
            else:
                cur.execute(
                    self._SQL_COUNT_USER_ACTIVE_DEPLOYMENTS_LIVE_ONLY,
                    (int(user_id),),
                )
            row = cur.fetchone()
            return int((dict(row).get("n") if row else 0) or 0)

        return self._store._with_retry_read(_do)

    def list_deployment_order_filled_events(
        self,
        *,
        deployment_id: int,
        user_id: int,
        since_ts: Optional[int] = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Doc execution_events ORDER_FILLED cua 1 deployment de tinh performance.

        Owner check qua bot_deployments.user_id. Tra list[{created_at_ts, payload}].
        """
        limit_i = max(1, min(int(limit), 50000))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_SELECT_DEPLOYMENT_OWNED_BY_USER,
                (int(deployment_id), int(user_id)),
            )
            if not cur.fetchone():
                raise ValueError("deployment_not_found")
            if since_ts is not None and int(since_ts) > 0:
                cur.execute(
                    self._SQL_LIST_DEPLOYMENT_ORDER_FILLED_EVENTS_SINCE,
                    (int(deployment_id), int(since_ts), int(limit_i)),
                )
            else:
                cur.execute(
                    self._SQL_LIST_DEPLOYMENT_ORDER_FILLED_EVENTS,
                    (int(deployment_id), int(limit_i)),
                )
            rows = cur.fetchall() or []
            out: list[dict[str, Any]] = []
            for row in rows:
                d = dict(row)
                payload = _json_loads_if_string(d.get("payload_json"), {})
                out.append({"created_at_ts": int(d.get("created_at_ts") or 0), "payload": dict(payload or {})})
            return out

        return self._store._with_retry_read(_do)

    def count_user_accounts_with_risk_policy(self, *, user_id: int) -> int:
        """So broker_accounts cua user co daily_loss_limit_usd > 0 trong risk_policy_json."""
        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                self._SQL_COUNT_USER_ACCOUNTS_WITH_RISK_POLICY,
                (int(user_id),),
            )
            row = cur.fetchone()
            return int((dict(row).get("n") if row else 0) or 0)

        return self._store._with_retry_read(_do)

    def get_user_metadata(self, *, user_id: int) -> dict[str, Any]:
        """Tra users.metadata_json. {} neu user khong ton tai hoac chua co."""
        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_GET_USER_METADATA,
                (int(user_id),),
            )
            row = cur.fetchone()
            if not row:
                return {}
            value = _json_loads_if_string(dict(row).get("metadata_json"), {})
            return dict(value or {})

        return self._store._with_retry_read(_do)

    def update_user_metadata(self, *, user_id: int, metadata_patch: dict[str, Any]) -> dict[str, Any]:
        """Merge patch vao users.metadata_json (jsonb || patch). Tra value sau khi merge."""
        if not isinstance(metadata_patch, dict):
            raise ValueError("invalid_metadata_patch")
        patch_json = _json_payload(metadata_patch)

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_UPDATE_USER_METADATA,
                (patch_json, int(user_id)),
            )
            row = cur.fetchone()
            if not row:
                return {}
            value = _json_loads_if_string(dict(row).get("metadata_json"), {})
            return dict(value or {})

        return self._store._with_retry_locked(_do)

    # ------------------------------------------------------------------
    # User webhooks (Sprint 4)
    # ------------------------------------------------------------------
    def create_user_webhook(
        self,
        *,
        user_id: int,
        url: str,
        secret_hex: str,
        event_filter: list[str],
    ) -> dict[str, Any]:
        ef_json = _json_payload(list(event_filter or []))

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_CREATE_USER_WEBHOOK,
                (int(user_id), str(url), str(secret_hex), ef_json),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("webhook_create_failed")
            d = dict(row)
            if isinstance(d.get("event_filter"), str):
                d["event_filter"] = _json_loads_if_string(d.get("event_filter"), [])
            return d

        return self._store._with_retry_locked(_do)

    def list_user_webhooks(
        self,
        *,
        user_id: int,
        include_secret: bool = False,
    ) -> list[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            if include_secret:
                cur.execute(
                    self._SQL_LIST_USER_WEBHOOKS_WITH_SECRET,
                    (int(user_id),),
                )
            else:
                cur.execute(
                    self._SQL_LIST_USER_WEBHOOKS,
                    (int(user_id),),
                )
            rows = cur.fetchall() or []
            out: list[dict[str, Any]] = []
            for row in rows:
                d = dict(row)
                if isinstance(d.get("event_filter"), str):
                    d["event_filter"] = _json_loads_if_string(d.get("event_filter"), [])
                out.append(d)
            return out

        return self._store._with_retry_read(_do)

    def delete_user_webhook(self, *, user_id: int, webhook_id: int) -> bool:
        def _do(con: Any, cur: Any) -> bool:
            cur.execute(
                self._SQL_DELETE_USER_WEBHOOK,
                (int(webhook_id), int(user_id)),
            )
            return int(cur.rowcount or 0) > 0

        return self._store._with_retry_locked(_do)

    def count_user_accounts(self, *, user_id: int) -> int:
        """So broker_accounts user co (khong tinh disconnected)."""
        def _do(con: Any, cur: Any) -> int:
            cur.execute(
                self._SQL_COUNT_BROKER_ACCOUNTS_FOR_USER,
                (int(user_id),),
            )
            row = cur.fetchone()
            return int((dict(row).get("n") if row else 0) or 0)

        return self._store._with_retry_read(_do)

    def list_accounts_with_active_circuit_breaker(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Liet ke account opt-in circuit breaker (auto_stop_on_breach=true + limit > 0).

        Tra ve list[{user_id, account_id, policy}]. Dung cho background scheduler tick.
        """
        limit_i = max(1, min(int(limit), 5000))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_LIST_ACCOUNTS_WITH_ACTIVE_CIRCUIT_BREAKER,
                (limit_i,),
            )
            rows = cur.fetchall() or []
            out: list[dict[str, Any]] = []

            for row in rows:
                row_d = dict(row)
                policy = _json_loads_if_string(row_d.get("risk_policy_json"), {})
                out.append(
                    {
                        "account_id": int(row_d.get("account_id") or 0),
                        "user_id": int(row_d.get("user_id") or 0),
                        "policy": dict(policy or {}),
                    }
                )
            return out

        return self._store._with_retry_read(_do)

