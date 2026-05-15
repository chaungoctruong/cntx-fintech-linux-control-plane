from __future__ import annotations

import time
from typing import Any, Optional

from app.models.control_plane import ACTIVE_DEPLOYMENT_STATUSES
from app.repositories.control_plane.query_loader import load_sql
from app.repositories.control_plane.support import (
    RUNNER_ACTIVE_LIMIT_DEFAULT,
    RUNNER_NODE_SLOT_LIMIT_DEFAULT,
    RUNNER_MIN_HEALTHY_SLOTS_DEFAULT,
    _ACTIVE_RUNNER_DEPLOYMENT_STATUSES,
    _TERMINAL_DEPLOYMENT_STATUSES,
    _build_runner_heartbeat_metadata,
    _cap_runner_slots,
    _capacity_state_from_operational_status,
    _json_dict,
    _json_list,
    _json_payload,
    _metadata_flag,
    _norm,
    _norm_slot_id,
    _normalize_runner_slot_projection_status,
    _normalize_runner_status_for_db,
    _overlay_runner_slot_projection_metadata,
    _runner_heartbeat_allows_online_status,
    _runner_operational_status,
    _runner_readiness_bot_codes,
    _safe_int,
    _slot_inventory_projection_status,
    _slot_registration_should_update_projection,
    _slot_sequence_number,
)


def _slot_exceeds_node_slot_cap(slot_id: str | None) -> bool:
    seq = _slot_sequence_number(slot_id)
    return seq is not None and seq > RUNNER_NODE_SLOT_LIMIT_DEFAULT


_STALE_SLOT_ACCOUNT_METADATA_KEYS = (
    "account_id",
    "active_account_id",
    "deployment_id",
    "login_reservation_id",
    "login_reservation_status",
    "login_reservation_account_id",
    "login_slot_status",
    "login_slot_account_id",
    "reserved_account_id",
    "sticky_account_id",
)


def _drop_stale_slot_account_metadata(payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    metadata = dict(payload or {})
    for key in _STALE_SLOT_ACCOUNT_METADATA_KEYS:
        metadata.pop(key, None)
    inventory = metadata.get("slot_inventory_entry")
    if isinstance(inventory, dict):
        clean_inventory = dict(inventory)
        for key in _STALE_SLOT_ACCOUNT_METADATA_KEYS:
            clean_inventory.pop(key, None)
        metadata["slot_inventory_entry"] = clean_inventory
    return metadata


def _slot_cap_disabled_metadata(extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    metadata = _drop_stale_slot_account_metadata(extra)
    metadata.update(
        {
            "disabled_by_node_slot_cap": True,
            "node_slot_cap": RUNNER_NODE_SLOT_LIMIT_DEFAULT,
            "disabled_reason": "node_slot_cap_10",
            "available_for_new_account": False,
            "control_plane_state": "disabled",
            "current_control_plane_state": "disabled",
        }
    )
    return metadata


class ControlPlaneRunnersSlotsMixin:
    _SQL_COUNT_RUNNER_SLOTS_BY_STATUS_FOR_HEARTBEAT = load_sql(
        "runners_slots/count_runner_slots_by_status_for_heartbeat.sql"
    )
    _SQL_GET_CURRENT_ACCOUNT_SLOT_BINDING = load_sql("runners_slots/get_current_account_slot_binding.sql")
    _SQL_GET_RUNNER_HEALTH_SLOTS_DETAIL = load_sql("runners_slots/get_runner_health_slots_detail.sql")
    _SQL_GET_RUNNER_HEALTH_SUMMARY = load_sql("runners_slots/get_runner_health_summary.sql")
    _SQL_GET_RUNNER_NODE = load_sql("runners_slots/get_runner_node.sql")
    _SQL_INSERT_ACCOUNT_SLOT_BINDING = load_sql("runners_slots/insert_account_slot_binding.sql")
    _SQL_INSERT_RUNNER_SLOT_ON_REGISTER = load_sql("runners_slots/insert_runner_slot_on_register.sql")
    _SQL_LIST_RUNNER_SLOTS_FOR_RUNNER = load_sql("runners_slots/list_runner_slots_for_runner.sql")
    _SQL_LIST_RUNNERS = load_sql("runners_slots/list_runners.sql")
    _SQL_LIST_RUNNERS_HEALTH = load_sql("runners_slots/list_runners_health.sql")
    _SQL_PREPARE_ORPHANED_HANDOFF_BREAK_BINDING = load_sql(
        "runners_slots/prepare_orphaned_handoff_break_binding.sql"
    )
    _SQL_PREPARE_ORPHANED_HANDOFF_BREAK_RUNNER_SLOT = load_sql(
        "runners_slots/prepare_orphaned_handoff_break_runner_slot.sql"
    )
    _SQL_PREPARE_ORPHANED_HANDOFF_DEGRADE_RUNNER_NODE = load_sql(
        "runners_slots/prepare_orphaned_handoff_degrade_runner_node.sql"
    )
    _SQL_PREPARE_ORPHANED_HANDOFF_FAIL_ACTIVE_DEPLOYMENTS = load_sql(
        "runners_slots/prepare_orphaned_handoff_fail_active_deployments.sql"
    )
    _SQL_PREPARE_ORPHANED_HANDOFF_SELECT_CURRENT_BINDING = load_sql(
        "runners_slots/prepare_orphaned_handoff_select_current_binding.sql"
    )
    _SQL_PREPARE_ORPHANED_HANDOFF_SELECT_SLOT_FOR_UPDATE = load_sql(
        "runners_slots/prepare_orphaned_handoff_select_slot_for_update.sql"
    )
    _SQL_RELEASE_OTHER_CURRENT_SLOT_BINDINGS_FOR_ACCOUNT = load_sql(
        "runners_slots/release_other_current_slot_bindings_for_account.sql"
    )
    _SQL_RESERVE_SLOT_BINDING_UPDATE_RUNNER_SLOT = load_sql(
        "runners_slots/reserve_slot_binding_update_runner_slot.sql"
    )
    _SQL_SELECT_LATEST_ACCOUNT_SLOT_BINDING = load_sql("runners_slots/select_latest_account_slot_binding.sql")
    _SQL_SELECT_RUNNER_NODE_MAX_SLOTS_FOR_UPDATE = load_sql(
        "runners_slots/select_runner_node_max_slots_for_update.sql"
    )
    _SQL_SELECT_RUNNER_SLOT_METADATA_FOR_UPDATE = load_sql(
        "runners_slots/select_runner_slot_metadata_for_update.sql"
    )
    _SQL_SET_RUNNER_MAINTENANCE_CLEAR_RUNNER_NODE = load_sql(
        "runners_slots/set_runner_maintenance_clear_runner_node.sql"
    )
    _SQL_SET_RUNNER_MAINTENANCE_DISABLE_READY_SLOTS = load_sql(
        "runners_slots/set_runner_maintenance_disable_ready_slots.sql"
    )
    _SQL_SET_RUNNER_MAINTENANCE_DRAIN_RUNNER_NODE = load_sql(
        "runners_slots/set_runner_maintenance_drain_runner_node.sql"
    )
    _SQL_SET_RUNNER_MAINTENANCE_ENABLE_DISABLED_SLOTS = load_sql(
        "runners_slots/set_runner_maintenance_enable_disabled_slots.sql"
    )
    _SQL_UPDATE_ACCOUNT_SLOT_BINDING_REACTIVATE = load_sql(
        "runners_slots/update_account_slot_binding_reactivate.sql"
    )
    _SQL_UPDATE_RUNNER_NODE_HEARTBEAT = load_sql("runners_slots/update_runner_node_heartbeat.sql")
    _SQL_UPDATE_RUNNER_SLOT_HEARTBEAT_INVENTORY_READY = load_sql(
        "runners_slots/update_runner_slot_heartbeat_inventory_ready.sql"
    )
    _SQL_UPDATE_RUNNER_SLOT_ON_REGISTER_ALLOWED_ONLY = load_sql(
        "runners_slots/update_runner_slot_on_register_allowed_only.sql"
    )
    _SQL_UPDATE_RUNNER_SLOT_ON_REGISTER_WITH_PROJECTION = load_sql(
        "runners_slots/update_runner_slot_on_register_with_projection.sql"
    )
    _SQL_UPDATE_RUNNER_SLOT_TOUCH_HEARTBEAT = load_sql("runners_slots/update_runner_slot_touch_heartbeat.sql")
    _SQL_UPSERT_RUNNER_NODE_ON_REGISTER = load_sql("runners_slots/upsert_runner_node_on_register.sql")

    def _reserve_slot_binding_locked(
        self,
        cur: Any,
        *,
        account_id: int,
        runner_id: str,
        slot_id: str,
        sticky: bool,
    ) -> Optional[dict[str, Any]]:
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id)
        if not runner_id_s or not slot_id_s:
            return None

        cur.execute(
            self._SQL_RESERVE_SLOT_BINDING_UPDATE_RUNNER_SLOT,
            (
                int(account_id),
                runner_id_s,
                slot_id_s,
                int(account_id),
                RUNNER_NODE_SLOT_LIMIT_DEFAULT,
                RUNNER_ACTIVE_LIMIT_DEFAULT,
                int(account_id),
                str(int(account_id)),
                str(int(account_id)),
                str(int(account_id)),
                str(int(account_id)),
                int(account_id),
            ),
        )
        reserved_row = cur.fetchone()
        if not reserved_row:
            return None

        cur.execute(
            self._SQL_RELEASE_OTHER_CURRENT_SLOT_BINDINGS_FOR_ACCOUNT,
            (int(account_id), runner_id_s, slot_id_s),
        )
        cur.execute(
            self._SQL_SELECT_LATEST_ACCOUNT_SLOT_BINDING,
            (int(account_id), runner_id_s, slot_id_s),
        )
        row = cur.fetchone()
        if row:
            binding_id = int(row.get("id"))
            cur.execute(
                self._SQL_UPDATE_ACCOUNT_SLOT_BINDING_REACTIVATE,
                (bool(sticky), binding_id),
            )
        else:
            cur.execute(
                self._SQL_INSERT_ACCOUNT_SLOT_BINDING,
                (int(account_id), runner_id_s, slot_id_s, bool(sticky)),
            )
        return dict(cur.fetchone() or {})

    def register_runner(
        self,
        *,
        runner_id: str,
        label: Optional[str],
        host: Optional[str],
        status: str,
        supported_profiles: list[str],
        capability_tags: list[str],
        capabilities: dict[str, Any],
        max_slots: int,
        slots: list[dict[str, Any]],
    ) -> dict[str, Any]:
        runner_id_s = _norm(runner_id)
        if not runner_id_s:
            raise ValueError("runner_id_required")
        label_s = _norm(label) or runner_id_s
        host_s = _norm(host) or None
        status_s = _normalize_runner_status_for_db(status)
        profiles = [str(item).strip().lower() for item in supported_profiles if str(item).strip()]
        tags = [str(item).strip().lower() for item in capability_tags if str(item).strip()]
        max_slots_i = _cap_runner_slots(max_slots)
        slot_payload = slots or [
            {
                "slot_id": f"slot-{idx:03d}",
                "status": "ready",
                "allowed_profile_classes": profiles or ["light", "normal", "heavy"],
                "metadata": {},
            }
            for idx in range(1, max_slots_i + 1)
        ]

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                self._SQL_UPSERT_RUNNER_NODE_ON_REGISTER,
                (
                    runner_id_s,
                    label_s,
                    host_s,
                    status_s,
                    _json_list(profiles),
                    _json_list(tags),
                    _json_payload(capabilities),
                    max_slots_i,
                ),
            )
            runner = dict(cur.fetchone() or {})

            for slot in slot_payload:
                slot_id = _norm_slot_id(slot.get("slot_id"))
                if not slot_id:
                    continue
                allowed = [str(item).strip().lower() for item in (slot.get("allowed_profile_classes") or []) if str(item).strip()]
                slot_status = _norm(slot.get("status")) or "ready"
                slot_metadata = dict(slot.get("metadata") if isinstance(slot.get("metadata"), dict) else {})
                slot_over_cap = _slot_exceeds_node_slot_cap(slot_id)
                if slot_over_cap:
                    slot_status = "disabled"
                    slot_metadata = _slot_cap_disabled_metadata(slot_metadata)
                cur.execute(
                    self._SQL_SELECT_RUNNER_SLOT_METADATA_FOR_UPDATE,
                    (runner_id_s, slot_id),
                )
                existing_slot = cur.fetchone()
                if existing_slot is None:
                    cur.execute(
                        self._SQL_INSERT_RUNNER_SLOT_ON_REGISTER,
                        (
                            runner_id_s,
                            slot_id,
                            slot_status,
                            _json_list(allowed or profiles or ["light", "normal", "heavy"]),
                            _json_payload(slot_metadata),
                        ),
                    )
                    continue

                existing_metadata = (
                    existing_slot.get("metadata_json")
                    if isinstance(existing_slot.get("metadata_json"), dict)
                    else {}
                )
                if slot_over_cap or _slot_registration_should_update_projection(
                    existing_metadata=existing_metadata,
                    incoming_metadata=slot_metadata,
                ):
                    cur.execute(
                        self._SQL_UPDATE_RUNNER_SLOT_ON_REGISTER_WITH_PROJECTION,
                        (
                            slot_status,
                            _json_list(allowed or profiles or ["light", "normal", "heavy"]),
                            _json_payload(slot_metadata),
                            runner_id_s,
                            slot_id,
                        ),
                    )
                else:
                    cur.execute(
                        self._SQL_UPDATE_RUNNER_SLOT_ON_REGISTER_ALLOWED_ONLY,
                        (
                            _json_list(allowed or profiles or ["light", "normal", "heavy"]),
                            runner_id_s,
                            slot_id,
                        ),
                    )
            cur.execute(
                """
                UPDATE runner_slots
                SET status = 'disabled',
                    current_account_id = NULL,
                    metadata_json = jsonb_strip_nulls(
                        (
                            COALESCE(metadata_json, '{}'::jsonb)
                                - 'account_id'
                                - 'active_account_id'
                                - 'deployment_id'
                                - 'login_reservation_id'
                                - 'login_reservation_status'
                                - 'login_reservation_account_id'
                                - 'login_slot_status'
                                - 'login_slot_account_id'
                                - 'reserved_account_id'
                                - 'sticky_account_id'
                                - 'slot_inventory_entry'
                        )
                        || jsonb_build_object(
                            'disabled_by_node_slot_cap', TRUE,
                            'node_slot_cap', %s,
                            'disabled_reason', 'node_slot_cap_10',
                            'available_for_new_account', FALSE,
                            'control_plane_state', 'disabled',
                            'current_control_plane_state', 'disabled'
                        )
                    ),
                    updated_at = NOW()
                WHERE runner_id = %s
                  AND COALESCE(NULLIF(SUBSTRING(slot_id FROM '([0-9]+)$'), ''), '') <> ''
                  AND CAST(SUBSTRING(slot_id FROM '([0-9]+)$') AS INTEGER) > %s
                """,
                (RUNNER_NODE_SLOT_LIMIT_DEFAULT, runner_id_s, max_slots_i),
            )

            return runner

        return self._store._with_retry_locked(_do)

    def touch_runner_heartbeat(
        self,
        *,
        runner_id: str,
        slot_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id) or None

        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                self._SQL_SELECT_RUNNER_NODE_MAX_SLOTS_FOR_UPDATE,
                (runner_id_s,),
            )
            runner_row = cur.fetchone() or {}
            current_max_slots = _cap_runner_slots(_safe_int(runner_row.get("max_slots"), 1) or 1)
            metadata = _build_runner_heartbeat_metadata(
                current_max_slots=current_max_slots,
                existing_metadata=runner_row.get("metadata_json") if isinstance(runner_row.get("metadata_json"), dict) else {},
                payload=payload,
            )
            effective_slots = _cap_runner_slots(_safe_int(metadata.get("effective_slots"), current_max_slots) or current_max_slots)
            slot_inventory = []
            if isinstance(payload, dict) and isinstance(payload.get("slot_inventory"), list):
                slot_inventory = [item for item in payload.get("slot_inventory") or [] if isinstance(item, dict)]
            for entry in slot_inventory:
                inventory_slot_id = _norm_slot_id(entry.get("slot_id") or entry.get("storage_slot_id"))
                if not inventory_slot_id:
                    continue
                seq = _slot_sequence_number(inventory_slot_id)
                if seq is not None and seq > effective_slots:
                    continue
                slot_status = _slot_inventory_projection_status(entry)
                if slot_status != "ready":
                    continue
                incoming_metadata = dict(entry)
                for stale_key in (
                    "account_id",
                    "active_account_id",
                    "deployment_id",
                    "login_reservation_id",
                    "login_reservation_status",
                    "login_reservation_account_id",
                    "login_slot_status",
                    "login_slot_account_id",
                    "reserved_account_id",
                    "sticky_account_id",
                ):
                    incoming_metadata.pop(stale_key, None)
                incoming_inventory_entry = dict(entry)
                for stale_key in (
                    "account_id",
                    "active_account_id",
                    "deployment_id",
                    "login_reservation_id",
                    "login_reservation_status",
                    "login_reservation_account_id",
                    "login_slot_status",
                    "login_slot_account_id",
                    "reserved_account_id",
                    "sticky_account_id",
                ):
                    incoming_inventory_entry.pop(stale_key, None)
                incoming_metadata["slot_inventory_entry"] = incoming_inventory_entry
                incoming_metadata["control_plane_state"] = "ready"
                incoming_metadata["current_control_plane_state"] = "ready"
                incoming_metadata["heartbeat_projection"] = True
                incoming_metadata["heartbeat_received_at"] = time.time()
                cur.execute(
                    self._SQL_SELECT_RUNNER_SLOT_METADATA_FOR_UPDATE,
                    (runner_id_s, inventory_slot_id),
                )
                existing_slot = cur.fetchone()
                existing_metadata = (
                    existing_slot.get("metadata_json")
                    if existing_slot and isinstance(existing_slot.get("metadata_json"), dict)
                    else {}
                )
                if existing_slot is not None and not _slot_registration_should_update_projection(
                    existing_metadata=existing_metadata,
                    incoming_metadata=incoming_metadata,
                ):
                    continue
                cur.execute(
                    self._SQL_UPDATE_RUNNER_SLOT_HEARTBEAT_INVENTORY_READY,
                    (_json_payload(incoming_metadata), runner_id_s, inventory_slot_id),
                )
            cur.execute(
                self._SQL_COUNT_RUNNER_SLOTS_BY_STATUS_FOR_HEARTBEAT,
                (runner_id_s, runner_id_s, effective_slots),
            )
            metadata = _overlay_runner_slot_projection_metadata(metadata, dict(cur.fetchone() or {}))
            heartbeat_allows_online = _runner_heartbeat_allows_online_status(metadata)
            cur.execute(
                self._SQL_UPDATE_RUNNER_NODE_HEARTBEAT,
                (effective_slots, _json_payload(metadata), bool(heartbeat_allows_online), runner_id_s),
            )
            if slot_id_s and _slot_exceeds_node_slot_cap(slot_id_s):
                cur.execute(
                    """
                    UPDATE runner_slots
                    SET status = 'disabled',
                        current_account_id = NULL,
                        metadata_json = jsonb_strip_nulls(
                            (
                                COALESCE(metadata_json, '{}'::jsonb)
                                    - 'account_id'
                                    - 'active_account_id'
                                    - 'deployment_id'
                                    - 'login_reservation_id'
                                    - 'login_reservation_status'
                                    - 'login_reservation_account_id'
                                    - 'login_slot_status'
                                    - 'login_slot_account_id'
                                    - 'reserved_account_id'
                                    - 'sticky_account_id'
                                    - 'slot_inventory_entry'
                            )
                            || %s::jsonb
                        ),
                        updated_at = NOW()
                    WHERE runner_id = %s AND slot_id = %s
                    """,
                    (_json_payload(_slot_cap_disabled_metadata()), runner_id_s, slot_id_s),
                )
            elif slot_id_s:
                cur.execute(
                    self._SQL_UPDATE_RUNNER_SLOT_TOUCH_HEARTBEAT,
                    (runner_id_s, slot_id_s),
                )

        self._store._with_retry_locked(_do)

    def list_runners(self) -> list[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(self._SQL_LIST_RUNNERS)
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def get_runner(self, *, runner_id: str) -> Optional[dict[str, Any]]:
        runner_id_s = _norm(runner_id)
        if not runner_id_s:
            return None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_RUNNER_NODE,
                (runner_id_s,),
            )
            row = cur.fetchone()
            if not row:
                return None
            runner = dict(row)
            cur.execute(
                self._SQL_LIST_RUNNER_SLOTS_FOR_RUNNER,
                (runner_id_s,),
            )
            runner["slots"] = [dict(item) for item in (cur.fetchall() or [])]
            return runner

        return self._store._with_retry_read(_do)

    def list_runners_health(self, *, stale_sec: int) -> list[dict[str, Any]]:
        stale_cutoff = max(30, int(stale_sec))

        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                self._SQL_LIST_RUNNERS_HEALTH,
                (stale_cutoff, stale_cutoff, stale_cutoff),
            )
            rows = [dict(row) for row in (cur.fetchall() or [])]
            out: list[dict[str, Any]] = []
            for row in rows:
                metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
                operational_status = _runner_operational_status(row)
                row["operational_status"] = operational_status
                row["capacity_state"] = _capacity_state_from_operational_status(operational_status)
                row["active_limit"] = _cap_runner_slots(
                    _safe_int(metadata.get("active_limit"), RUNNER_ACTIVE_LIMIT_DEFAULT) or RUNNER_ACTIVE_LIMIT_DEFAULT
                )
                row["min_healthy_slots"] = _safe_int(metadata.get("min_healthy_slots"), RUNNER_MIN_HEALTHY_SLOTS_DEFAULT) or RUNNER_MIN_HEALTHY_SLOTS_DEFAULT
                row["maintenance_mode"] = operational_status == "MAINTENANCE"
                row["paused"] = _metadata_flag(
                    metadata,
                    "paused",
                    "pause",
                    "frozen",
                    "freeze",
                    "dispatch_paused",
                    "login_paused",
                    "warm_guard_paused",
                    "warm_guard_pause",
                    "warm_pool_paused",
                )
                row["accepts_new_work"] = bool(
                    operational_status in {"ONLINE_AVAILABLE", "ONLINE_NEAR_FULL"}
                    and int(row.get("available_slots") or 0) > 0
                )
                row["last_error"] = metadata.get("last_error") or metadata.get("runtime_error") or row.get("last_login_reservation_error")
                row["last_login_reservation"] = {
                    "reservation_id": row.pop("last_login_reservation_id", None),
                    "status": row.pop("last_login_reservation_status", None),
                    "error": row.pop("last_login_reservation_error", None),
                    "completed_at": row.pop("last_login_reservation_completed_at", None),
                }
                out.append(row)
            return out

        return self._store._with_retry_read(_do)

    def get_runner_health(self, *, runner_id: str, stale_sec: int) -> Optional[dict[str, Any]]:
        runner_id_s = _norm(runner_id)
        if not runner_id_s:
            return None
        stale_cutoff = max(30, int(stale_sec))

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_RUNNER_HEALTH_SUMMARY,
                (stale_cutoff, stale_cutoff, stale_cutoff, runner_id_s),
            )
            row = cur.fetchone()
            if not row:
                return None

            runner = dict(row)
            metadata = runner.get("metadata_json") if isinstance(runner.get("metadata_json"), dict) else {}
            operational_status = _runner_operational_status(runner)
            runner["operational_status"] = operational_status
            runner["capacity_state"] = _capacity_state_from_operational_status(operational_status)
            runner["active_limit"] = _cap_runner_slots(
                _safe_int(metadata.get("active_limit"), RUNNER_ACTIVE_LIMIT_DEFAULT) or RUNNER_ACTIVE_LIMIT_DEFAULT
            )
            runner["min_healthy_slots"] = _safe_int(metadata.get("min_healthy_slots"), RUNNER_MIN_HEALTHY_SLOTS_DEFAULT) or RUNNER_MIN_HEALTHY_SLOTS_DEFAULT
            runner["maintenance_mode"] = operational_status == "MAINTENANCE"
            runner["paused"] = _metadata_flag(
                metadata,
                "paused",
                "pause",
                "frozen",
                "freeze",
                "dispatch_paused",
                "login_paused",
                "warm_guard_paused",
                "warm_guard_pause",
                "warm_pool_paused",
            )
            runner["accepts_new_work"] = bool(
                operational_status in {"ONLINE_AVAILABLE", "ONLINE_NEAR_FULL"}
                and int(runner.get("available_slots") or 0) > 0
            )
            runner["last_error"] = metadata.get("last_error") or metadata.get("runtime_error") or runner.get("last_login_reservation_error")
            runner["last_login_reservation"] = {
                "reservation_id": runner.pop("last_login_reservation_id", None),
                "status": runner.pop("last_login_reservation_status", None),
                "error": runner.pop("last_login_reservation_error", None),
                "completed_at": runner.pop("last_login_reservation_completed_at", None),
            }

            cur.execute(
                self._SQL_GET_RUNNER_HEALTH_SLOTS_DETAIL,
                (stale_cutoff, runner_id_s),
            )
            slots = [dict(item) for item in (cur.fetchall() or [])]
            for slot in slots:
                slot_status = str(slot.get("status") or "").strip().lower()
                slot_is_stale = bool(slot.get("is_stale"))
                if slot_status == "broken":
                    slot["health_state"] = "broken"
                elif slot_is_stale:
                    slot["health_state"] = "stale"
                elif slot_status == "ready":
                    slot["health_state"] = "ready"
                elif slot_status == "allocated":
                    slot["health_state"] = "allocated"
                elif slot_status == "degraded":
                    slot["health_state"] = "degraded"
                else:
                    slot["health_state"] = slot_status or "unknown"
            runner["slots"] = slots
            return runner

        return self._store._with_retry_read(_do)

    def set_runner_maintenance(
        self,
        *,
        runner_id: str,
        draining: bool,
        reason: Optional[str] = None,
        actor: Optional[str] = None,
        disable_ready_slots: bool = True,
        enable_disabled_slots: bool = True,
    ) -> Optional[dict[str, Any]]:
        runner_id_s = _norm(runner_id)
        if not runner_id_s:
            raise ValueError("runner_not_found")
        reason_s = _norm(reason) or None
        actor_s = _norm(actor) or None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            if draining:
                cur.execute(
                    self._SQL_SET_RUNNER_MAINTENANCE_DRAIN_RUNNER_NODE,
                    (reason_s, actor_s, runner_id_s),
                )
                row = cur.fetchone()
                if not row:
                    return None
                disabled_slots = 0
                if disable_ready_slots:
                    cur.execute(
                        self._SQL_SET_RUNNER_MAINTENANCE_DISABLE_READY_SLOTS,
                        (reason_s, actor_s, runner_id_s),
                    )
                    disabled_slots = int(cur.rowcount or 0)
                result = dict(row)
                result["disabled_slots"] = disabled_slots
                result["maintenance_mode"] = True
                return result

            cur.execute(
                self._SQL_SET_RUNNER_MAINTENANCE_CLEAR_RUNNER_NODE,
                (runner_id_s,),
            )
            row = cur.fetchone()
            if not row:
                return None
            enabled_slots = 0
            if enable_disabled_slots:
                cur.execute(
                    self._SQL_SET_RUNNER_MAINTENANCE_ENABLE_DISABLED_SLOTS,
                    (runner_id_s,),
                )
                enabled_slots = int(cur.rowcount or 0)
            result = dict(row)
            result["enabled_slots"] = enabled_slots
            result["maintenance_mode"] = False
            if reason_s or actor_s:
                result["resume_note"] = {"reason": reason_s, "actor": actor_s}
            return result

        return self._store._with_retry_locked(_do)

    def prepare_orphaned_slot_handoff(
        self,
        *,
        runner_id: str,
        slot_id: str,
        reason: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id)
        reason_s = _norm(reason) or "orphaned_runtime_confirmed_dead"
        actor_s = _norm(actor) or None
        if not runner_id_s or not slot_id_s:
            return None

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_PREPARE_ORPHANED_HANDOFF_SELECT_SLOT_FOR_UPDATE,
                (runner_id_s, slot_id_s),
            )
            row = cur.fetchone()
            if not row:
                return None
            slot_row = dict(row)

            cur.execute(
                self._SQL_PREPARE_ORPHANED_HANDOFF_SELECT_CURRENT_BINDING,
                (runner_id_s, slot_id_s),
            )
            binding = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_PREPARE_ORPHANED_HANDOFF_FAIL_ACTIVE_DEPLOYMENTS,
                (reason_s, runner_id_s, slot_id_s, list(ACTIVE_DEPLOYMENT_STATUSES)),
            )
            broken_deployments = [dict(item) for item in (cur.fetchall() or [])]

            account_id = binding.get("account_id") or slot_row.get("current_account_id")
            if not account_id and broken_deployments:
                account_id = broken_deployments[0].get("account_id")

            cur.execute(
                self._SQL_PREPARE_ORPHANED_HANDOFF_BREAK_RUNNER_SLOT,
                (reason_s, actor_s, account_id, runner_id_s, slot_id_s),
            )
            slot_after = dict(cur.fetchone() or {})

            cur.execute(
                self._SQL_PREPARE_ORPHANED_HANDOFF_DEGRADE_RUNNER_NODE,
                (runner_id_s,),
            )
            runner_after = dict(cur.fetchone() or {})

            broken_bindings = 0
            if binding:
                cur.execute(
                    self._SQL_PREPARE_ORPHANED_HANDOFF_BREAK_BINDING,
                    (int(binding["id"]),),
                )
                broken_bindings = int(cur.rowcount or 0)

            return {
                "runner_id": runner_id_s,
                "slot_id": slot_id_s,
                "slot_status_before": slot_row.get("status"),
                "runner_status_before": slot_row.get("runner_status"),
                "runner_status_after": runner_after.get("status"),
                "account_id": account_id,
                "binding_state_before": binding.get("binding_state"),
                "broken_bindings": broken_bindings,
                "broken_deployments": broken_deployments,
                "broken_deployment_count": len(broken_deployments),
                "slot": slot_after,
                "reason": reason_s,
                "actor": actor_s,
            }

        return self._store._with_retry_locked(_do)

    def list_slots(self) -> list[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> list[dict[str, Any]]:
            cur.execute(
                """
                SELECT
                    s.runner_id,
                    s.slot_id,
                    s.status,
                    s.allowed_profile_classes,
                    s.current_account_id,
                    s.metadata_json,
                    s.last_heartbeat_at,
                    n.status AS runner_status,
                    n.capabilities_json AS runner_capabilities_json,
                    n.metadata_json AS runner_metadata_json,
                    n.last_heartbeat_at AS runner_last_heartbeat_at,
                    n.supported_profiles,
                    n.capability_tags,
                    n.max_slots,
                    COALESCE(slot_stats.total_slots, 0) AS runner_total_slots,
                    COALESCE(slot_stats.healthy_slots, 0) AS runner_healthy_slots,
                    COALESCE(slot_stats.ready_slots, 0) AS runner_ready_slots,
                    COALESCE(slot_stats.allocated_slots, 0) AS runner_allocated_slots,
                    COALESCE(slot_stats.degraded_slots, 0) AS runner_degraded_slots,
                    COALESCE(slot_stats.broken_slots, 0) AS runner_broken_slots,
                    COALESCE(dep_stats.running_deployments, 0) AS runner_active_count,
                    active_dep.id AS active_deployment_id,
                    active_dep.account_id AS active_deployment_account_id,
                    active_dep.status AS active_deployment_status,
                    active_dep.health_status AS active_deployment_health_status,
                    b.account_id AS sticky_account_id
                FROM runner_slots s
                JOIN runner_nodes n ON n.runner_id = s.runner_id
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) AS total_slots,
                        COUNT(*) FILTER (WHERE rs.status IN ('ready', 'allocated')) AS healthy_slots,
                        COUNT(*) FILTER (WHERE rs.status = 'ready') AS ready_slots,
                        COUNT(*) FILTER (WHERE rs.status = 'allocated' OR rs.current_account_id IS NOT NULL) AS allocated_slots,
                        COUNT(*) FILTER (WHERE rs.status = 'degraded') AS degraded_slots,
                        COUNT(*) FILTER (WHERE rs.status = 'broken') AS broken_slots
                    FROM runner_slots rs
                    WHERE rs.runner_id = n.runner_id
                      AND (
                          COALESCE(NULLIF(SUBSTRING(rs.slot_id FROM '([0-9]+)$'), ''), '') = ''
                          OR CAST(SUBSTRING(rs.slot_id FROM '([0-9]+)$') AS INTEGER) <= LEAST(10, GREATEST(1, n.max_slots))
                      )
                ) slot_stats ON TRUE
                LEFT JOIN LATERAL (
                    SELECT
                        COUNT(*) FILTER (
                            WHERE d.desired_state = 'running'
                              AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
                        ) AS running_deployments
                    FROM bot_deployments d
                    WHERE d.runner_id = n.runner_id
                ) dep_stats ON TRUE
                LEFT JOIN LATERAL (
                    SELECT d.id, d.account_id, d.status, d.health_status
                    FROM bot_deployments d
                    WHERE d.runner_id = s.runner_id
                      AND d.slot_id = s.slot_id
                      AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
                      AND (COALESCE(d.is_active, FALSE) = TRUE OR d.desired_state = 'running')
                    ORDER BY d.updated_at DESC, d.id DESC
                    LIMIT 1
                ) active_dep ON TRUE
                LEFT JOIN account_slot_bindings b
                  ON b.runner_id = s.runner_id
                 AND b.slot_id = s.slot_id
                 AND b.is_current = TRUE
                 AND b.binding_state = 'active'
                WHERE (
                    COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
                    OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= LEAST(10, GREATEST(1, n.max_slots))
                )
                ORDER BY s.runner_id ASC, s.slot_id ASC
                """
            )
            return [dict(row) for row in (cur.fetchall() or [])]

        return self._store._with_retry_read(_do)

    def get_current_binding(self, *, account_id: int) -> Optional[dict[str, Any]]:
        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                self._SQL_GET_CURRENT_ACCOUNT_SLOT_BINDING,
                (int(account_id),),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_read(_do)

    def allocate_slot_binding(
        self,
        *,
        account_id: int,
        runner_id: str,
        slot_id: str,
        sticky: bool,
    ) -> dict[str, Any]:
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id)

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                UPDATE runner_slots AS s
                SET current_account_id = %s,
                    status = CASE
                        WHEN s.status = 'broken' THEN s.status
                        ELSE 'allocated'
                    END,
                    updated_at = NOW()
                WHERE s.runner_id = %s
                  AND s.slot_id = %s
                  AND s.status IN ('ready', 'allocated')
                  AND (s.current_account_id IS NULL OR s.current_account_id = %s)
                  AND EXISTS (
                      SELECT 1
                      FROM runner_nodes n
                      WHERE n.runner_id = s.runner_id
                        AND n.status = 'online'
                        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'maintenance_mode'), '')), 'false')
                            NOT IN ('true', '1', 'yes', 'y', 'on')
                        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'maintenance'), '')), 'false')
                            NOT IN ('true', '1', 'yes', 'y', 'on')
                        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'paused'), '')), 'false')
                            NOT IN ('true', '1', 'yes', 'y', 'on')
                        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'frozen'), '')), 'false')
                            NOT IN ('true', '1', 'yes', 'y', 'on')
                        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'dispatch_paused'), '')), 'false')
                            NOT IN ('true', '1', 'yes', 'y', 'on')
                        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'login_paused'), '')), 'false')
                            NOT IN ('true', '1', 'yes', 'y', 'on')
                        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'warm_guard_paused'), '')), 'false')
                            NOT IN ('true', '1', 'yes', 'y', 'on')
                        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'warm_pool_paused'), '')), 'false')
                            NOT IN ('true', '1', 'yes', 'y', 'on')
                        AND COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'runner_state'), '')), 'online')
                            NOT IN ('draining', 'frozen', 'maintenance', 'paused', 'login_paused', 'warm_guard_paused')
                        AND (
                            COALESCE(LOWER(NULLIF(BTRIM(n.metadata_json->>'accepting_new_accounts'), '')), 'false')
                                IN ('true', '1', 'yes', 'y', 'on')
                            OR NOT EXISTS (
                                SELECT 1
                                FROM runner_slots ds
                                WHERE ds.runner_id = n.runner_id
                                  AND ds.status = 'degraded'
                            )
                        )
                        AND (
                            SELECT COUNT(*)
                            FROM bot_deployments d
                            WHERE d.runner_id = n.runner_id
                              AND d.desired_state = 'running'
                              AND d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
                        ) < CASE
                            WHEN COALESCE(n.metadata_json->>'active_limit', '') ~ '^[0-9]+$'
                                THEN LEAST(%s, GREATEST(1, (n.metadata_json->>'active_limit')::INTEGER))
                            ELSE %s
                        END
                        AND EXISTS (
                            SELECT 1
                            FROM runner_slots hs
                            WHERE hs.runner_id = n.runner_id
                              AND hs.status IN ('ready', 'allocated')
                        )
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM account_slot_bindings b
                      WHERE b.runner_id = s.runner_id
                        AND b.slot_id = s.slot_id
                        AND b.is_current = TRUE
                        AND b.binding_state = 'active'
                        AND b.account_id <> %s
                  )
                  AND (
                      %s IS NOT NULL
                  )
                  AND (
                      NULLIF(BTRIM(COALESCE(s.metadata_json->>'reserved_account_id', '')), '') IS NULL
                      OR LOWER(NULLIF(BTRIM(COALESCE(s.metadata_json->>'reserved_account_id', '')), '')) IN ('null', 'none', '0')
                      OR NULLIF(BTRIM(COALESCE(s.metadata_json->>'reserved_account_id', '')), '') = %s
                  )
	                  AND (
	                      COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'available_for_new_account'), '')), 'true')
	                            NOT IN ('false', '0', 'no', 'n', 'off')
	                      OR %s IS NULL
	                      OR NULLIF(BTRIM(COALESCE(s.metadata_json->>'reserved_account_id', '')), '') = %s
	                      OR (
	                          NULLIF(BTRIM(COALESCE(s.metadata_json->>'sticky_account_id', '')), '') IS NOT NULL
	                          AND LOWER(NULLIF(BTRIM(COALESCE(s.metadata_json->>'sticky_account_id', '')), '')) NOT IN ('null', 'none', '0')
	                      )
	                  )
                  AND (
                      EXISTS (
                          SELECT 1
                          FROM account_login_reservations v
                          WHERE v.account_id = %s
                            AND v.runner_id = s.runner_id
                            AND v.slot_id = s.slot_id
                            AND v.status = 'claimed'
                            AND (v.expires_at IS NULL OR v.expires_at > NOW())
                      )
                      OR (
                          COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'control_plane_state'), '')), 'ready')
                              NOT IN ('allocated', 'broken', 'degraded', 'disabled', 'offline', 'running', 'starting', 'stopping', 'verifying')
                          AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'current_control_plane_state'), '')), 'ready')
                              NOT IN ('allocated', 'broken', 'degraded', 'disabled', 'offline', 'running', 'starting', 'stopping', 'verifying')
                          AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'runner_state'), '')), 'ready')
                              NOT IN ('allocated', 'broken', 'degraded', 'disabled', 'offline', 'running', 'starting', 'stopping', 'verifying')
                          AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'current_runner_state'), '')), 'ready')
                              NOT IN ('allocated', 'broken', 'degraded', 'disabled', 'offline', 'running', 'starting', 'stopping', 'verifying')
                      )
                  )
                  AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'mt5_liveness_state'), '')), 'ready')
                        NOT IN ('broken', 'dead', 'degraded', 'disabled', 'failed', 'offline', 'stale')
                  AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'login_slot_status'), '')), '')
                        NOT IN ('dispatched', 'pending', 'queued', 'running', 'verifying')
                RETURNING runner_id, slot_id, status, current_account_id
                """,
                (
                    int(account_id),
                    runner_id_s,
                    slot_id_s,
                    int(account_id),
                    RUNNER_NODE_SLOT_LIMIT_DEFAULT,
                    RUNNER_ACTIVE_LIMIT_DEFAULT,
                    int(account_id),
                    str(int(account_id)),
                    str(int(account_id)),
                    str(int(account_id)),
                    str(int(account_id)),
                    int(account_id),
                ),
            )
            if not cur.fetchone():
                raise ValueError("no_available_unreserved_slot")

            cur.execute(
                """
                UPDATE account_slot_bindings
                SET is_current = FALSE,
                    binding_state = CASE
                        WHEN binding_state = 'broken' THEN binding_state
                        ELSE 'released'
                    END,
                    updated_at = NOW()
                WHERE runner_id = %s
                  AND slot_id = %s
                  AND is_current = TRUE
                  AND account_id <> %s
                  AND binding_state <> 'active'
                """,
                (runner_id_s, slot_id_s, int(account_id)),
            )
            cur.execute(
                """
                UPDATE account_slot_bindings
                SET is_current = FALSE,
                    binding_state = CASE
                        WHEN binding_state = 'broken' THEN binding_state
                        ELSE 'released'
                    END,
                    updated_at = NOW()
                WHERE account_id = %s
                  AND is_current = TRUE
                  AND NOT (runner_id = %s AND slot_id = %s)
                """,
                (int(account_id), runner_id_s, slot_id_s),
            )
            cur.execute(
                """
                SELECT id
                FROM account_slot_bindings
                WHERE account_id = %s
                  AND runner_id = %s
                  AND slot_id = %s
                ORDER BY is_current DESC, updated_at DESC, id DESC
                LIMIT 1
                """,
                (int(account_id), runner_id_s, slot_id_s),
            )
            row = cur.fetchone()
            if row:
                binding_id = int(row.get("id"))
                cur.execute(
                    """
                    UPDATE account_slot_bindings
                    SET binding_state = 'active',
                        is_sticky = %s,
                        is_current = TRUE,
                        last_used_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, account_id, runner_id, slot_id, binding_state, is_sticky, is_current, last_used_at, created_at, updated_at
                    """,
                    (bool(sticky), binding_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO account_slot_bindings(
                        account_id, runner_id, slot_id, binding_state,
                        is_sticky, is_current, last_used_at, created_at, updated_at
                    )
                    VALUES(%s, %s, %s, 'active', %s, TRUE, NOW(), NOW(), NOW())
                    RETURNING id, account_id, runner_id, slot_id, binding_state, is_sticky, is_current, last_used_at, created_at, updated_at
                    """,
                    (int(account_id), runner_id_s, slot_id_s, bool(sticky)),
            )
            binding = dict(cur.fetchone() or {})
            return binding

        return self._store._with_retry_locked(_do)

    def release_account_slot_binding(
        self,
        *,
        account_id: int,
        runner_id: str,
        slot_id: str,
        keep_sticky: bool,
    ) -> None:
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id)
        if not runner_id_s or not slot_id_s:
            return

        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                """
                UPDATE runner_slots
                SET current_account_id = NULL,
                    status = CASE WHEN status = 'broken' THEN status ELSE 'ready' END,
                    metadata_json = jsonb_strip_nulls(
                        (
                            COALESCE(metadata_json, '{}'::jsonb)
                                - 'account_id'
                                - 'active_account_id'
                                - 'deployment_id'
                                - 'login_reservation_id'
                                - 'login_slot_status'
                                - 'login_slot_account_id'
                                - 'sticky_account_id'
                                - 'reserved_account_id'
                                - 'current_control_plane_state'
                                - 'previous_control_plane_state'
                                - 'runner_state'
                                - 'current_runner_state'
                                - 'previous_runner_state'
                                - 'mt5_liveness_state'
                                - 'mt5_liveness_reason'
                                - 'current_state'
                                - 'previous_state'
                                - 'reason'
                                - 'last_error'
                        ) || jsonb_build_object(
                            'sticky_account_id', CASE WHEN %s THEN %s ELSE NULL END,
                            'available_for_new_account', TRUE,
                            'control_plane_state', 'ready',
                            'current_control_plane_state', 'ready',
                            'runner_state', 'ready',
                            'current_runner_state', 'ready',
                            'mt5_liveness_state', '',
                            'last_reason', 'account_slot_binding_released',
                            'last_error', ''
                        )
                    ),
                    updated_at = NOW()
                WHERE runner_id = %s AND slot_id = %s
                  AND (current_account_id IS NULL OR current_account_id = %s)
                """,
                (
                    bool(keep_sticky),
                    str(int(account_id)),
                    runner_id_s,
                    slot_id_s,
                    int(account_id),
                ),
            )
            cur.execute(
                """
                UPDATE account_slot_bindings
                SET binding_state = CASE
                        WHEN %s THEN 'sticky'
                        WHEN binding_state = 'broken' THEN binding_state
                        ELSE 'released'
                    END,
                    is_sticky = %s,
                    is_current = %s,
                    last_used_at = NOW(),
                    updated_at = NOW()
                WHERE account_id = %s
                  AND runner_id = %s
                  AND slot_id = %s
                  AND is_current = TRUE
                """,
                (
                    bool(keep_sticky),
                    bool(keep_sticky),
                    bool(keep_sticky),
                    int(account_id),
                    runner_id_s,
                    slot_id_s,
                ),
            )

        self._store._with_retry_locked(_do)

    def release_expired_sticky_slot_bindings(
        self,
        *,
        timezone_name: str,
        batch_size: int = 500,
    ) -> dict[str, int]:
        tz_name = _norm(timezone_name) or "Asia/Ho_Chi_Minh"
        try:
            batch_i = int(batch_size)
        except Exception:
            batch_i = 500
        batch_i = max(1, min(batch_i, 5000))

        def _do(con: Any, cur: Any) -> dict[str, int]:
            cur.execute(
                """
                WITH cutoff AS (
                    SELECT (date_trunc('day', NOW() AT TIME ZONE %s) AT TIME ZONE %s) AS ts
                ),
                expired AS (
                    SELECT b.id, b.account_id, b.runner_id, b.slot_id
                    FROM account_slot_bindings b
                    JOIN runner_slots s
                      ON s.runner_id = b.runner_id
                     AND s.slot_id = b.slot_id
                    CROSS JOIN cutoff
                    WHERE b.is_current = TRUE
                      AND b.is_sticky = TRUE
                      AND b.binding_state = 'sticky'
                      AND COALESCE(b.last_used_at, b.updated_at, b.created_at) < cutoff.ts
                      AND s.current_account_id IS NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM bot_deployments d
                          WHERE d.account_id = b.account_id
                            AND d.status = ANY(%s)
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM account_login_reservations v
                          WHERE v.status IN ('pending', 'dispatched', 'verified')
                            AND (v.expires_at IS NULL OR v.expires_at > NOW())
                            AND (
                                v.account_id = b.account_id
                                OR (v.runner_id = b.runner_id AND v.slot_id = b.slot_id)
                            )
                      )
                    ORDER BY COALESCE(b.last_used_at, b.updated_at, b.created_at) ASC, b.id ASC
                    LIMIT %s
                    FOR UPDATE OF b SKIP LOCKED
                ),
                released AS (
                    UPDATE account_slot_bindings b
                    SET binding_state = 'released',
                        is_sticky = FALSE,
                        is_current = FALSE,
                        updated_at = NOW()
                    FROM expired e
                    WHERE b.id = e.id
                    RETURNING b.account_id, b.runner_id, b.slot_id
                ),
                released_slots AS (
                    UPDATE runner_slots s
                    SET current_account_id = NULL,
                        status = CASE
                            WHEN s.status = 'allocated' THEN 'ready'
                            ELSE s.status
                        END,
                        metadata_json = jsonb_strip_nulls(
                            (
                                COALESCE(s.metadata_json, '{}'::jsonb)
                                    - 'sticky_account_id'
                                    - 'reserved_account_id'
                                    - 'account_id'
                                    - 'active_account_id'
                                    - 'deployment_id'
                            ) || jsonb_build_object(
                                'available_for_new_account', TRUE,
                                'sticky_release_reason', 'midnight_idle_expiry',
                                'sticky_released_account_id', r.account_id::TEXT,
                                'sticky_released_at', NOW()
                            )
                        ),
                        updated_at = NOW()
                    FROM released r
                    WHERE s.runner_id = r.runner_id
                      AND s.slot_id = r.slot_id
                      AND s.current_account_id IS NULL
                    RETURNING s.runner_id, s.slot_id
                )
                SELECT
                    (SELECT COUNT(*) FROM released) AS released_bindings,
                    (SELECT COUNT(*) FROM released_slots) AS released_slots
                """,
                (
                    tz_name,
                    tz_name,
                    list(_ACTIVE_RUNNER_DEPLOYMENT_STATUSES),
                    batch_i,
                ),
            )
            row = dict(cur.fetchone() or {})
            return {
                "expired_sticky_bindings": _safe_int(row.get("released_bindings"), 0),
                "expired_sticky_slots": _safe_int(row.get("released_slots"), 0),
            }

        return self._store._with_retry_locked(_do)

    def prepare_sticky_slot_for_reuse(self, *, account_id: int) -> Optional[dict[str, Any]]:
        """Normalize a same-account sticky slot before START scheduling.

        This is intentionally narrow: it only touches the current binding for
        the requested account, and only when DB state proves there is no active
        deployment, active login-slot reservation, or in-flight START/STOP command.
        Foreign sticky slots and unhealthy slots are left untouched.
        """

        def _do(con: Any, cur: Any) -> Optional[dict[str, Any]]:
            cur.execute(
                """
                WITH safe_account AS (
                    SELECT a.id
                    FROM broker_accounts a
                    WHERE a.id = %s
                      AND COALESCE(a.is_active, TRUE) = TRUE
                      AND COALESCE(LOWER(a.status), '') <> 'disconnected'
                      AND (
                          LOWER(a.status) IN ('connected', 'verified')
                          OR a.verified_at IS NOT NULL
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM bot_deployments d
                          WHERE d.account_id = a.id
                            AND (
                                COALESCE(d.is_active, FALSE) = TRUE
                                OR d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
                            )
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM account_login_reservations v
                          WHERE v.account_id = a.id
                            AND v.status IN ('pending', 'dispatched', 'verified')
                            AND (v.expires_at IS NULL OR v.expires_at > NOW())
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM execution_commands c
                          LEFT JOIN bot_deployments d ON d.id = c.deployment_id
                          WHERE c.account_id = a.id
                            AND c.command_type IN ('START_BOT', 'STOP_BOT')
                            AND c.delivery_status IN ('pending', 'queued', 'dispatched')
                            AND NOT (
                                d.id IS NOT NULL
                                AND d.desired_state = 'stopped'
                                AND d.status = ANY(%s)
                                AND COALESCE(d.is_active, FALSE) = FALSE
                            )
                      )
                ),
                existing_binding AS (
                    SELECT
                        b.id,
                        b.account_id,
                        b.runner_id,
                        b.slot_id,
                        b.binding_state,
                        b.is_sticky,
                        b.is_current
                    FROM account_slot_bindings b
                    WHERE b.account_id = %s
                      AND b.is_current = TRUE
                    ORDER BY b.updated_at DESC, b.id DESC
                    LIMIT 1
                    FOR UPDATE
                ),
                heartbeat_sticky_slot AS (
                    SELECT
                        a.id AS account_id,
                        s.runner_id,
                        s.slot_id
                    FROM safe_account a
                    JOIN runner_slots s
                      ON COALESCE(s.metadata_json->>'sticky_account_id', '') = a.id::TEXT
                    JOIN runner_nodes n
                      ON n.runner_id = s.runner_id
                    WHERE NOT EXISTS (SELECT 1 FROM existing_binding)
                      AND n.status = 'online'
                      AND s.status NOT IN ('broken', 'degraded', 'disabled', 'offline', 'verifying')
                      AND (
                          s.current_account_id IS NULL
                          OR s.current_account_id = a.id
                      )
                      AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'mt5_liveness_state'), '')), 'ready')
                            NOT IN ('broken', 'dead', 'degraded', 'disabled', 'failed', 'offline', 'stale')
                      AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'login_slot_status'), '')), '')
                            NOT IN ('dispatched', 'pending', 'queued', 'running', 'verifying')
                      AND NOT EXISTS (
                          SELECT 1
                          FROM account_slot_bindings b
                          WHERE b.is_current = TRUE
                            AND (
                                b.account_id = a.id
                                OR (
                                    b.runner_id = s.runner_id
                                    AND b.slot_id = s.slot_id
                                )
                            )
                      )
                    ORDER BY s.updated_at DESC, s.slot_id ASC
                    LIMIT 1
                ),
                recovered_binding AS (
                    INSERT INTO account_slot_bindings(
                        account_id, runner_id, slot_id, binding_state,
                        is_sticky, is_current, last_used_at, created_at, updated_at
                    )
                    SELECT
                        account_id, runner_id, slot_id, 'sticky',
                        TRUE, TRUE, NOW(), NOW(), NOW()
                    FROM heartbeat_sticky_slot
                    RETURNING
                        id,
                        account_id,
                        runner_id,
                        slot_id,
                        binding_state,
                        is_sticky,
                        is_current
                ),
                current_binding AS (
                    SELECT * FROM existing_binding
                    UNION ALL
                    SELECT * FROM recovered_binding
                ),
                eligible AS (
                    SELECT
                        b.id AS binding_id,
                        b.account_id,
                        b.runner_id,
                        b.slot_id
                    FROM current_binding b
                    JOIN runner_slots s
                      ON s.runner_id = b.runner_id
                     AND s.slot_id = b.slot_id
                    JOIN runner_nodes n
                      ON n.runner_id = s.runner_id
                    WHERE n.status = 'online'
                      AND s.status NOT IN ('broken', 'degraded', 'disabled', 'offline', 'verifying')
                      AND (
                          s.current_account_id IS NULL
                          OR s.current_account_id = b.account_id
                      )
                      AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'mt5_liveness_state'), '')), 'ready')
                            NOT IN ('broken', 'dead', 'degraded', 'disabled', 'failed', 'offline', 'stale')
                      AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'login_slot_status'), '')), '')
                            NOT IN ('dispatched', 'pending', 'queued', 'running', 'verifying')
                      AND NOT EXISTS (
                          SELECT 1
                          FROM bot_deployments d
                          WHERE d.account_id = b.account_id
                            AND (
                                COALESCE(d.is_active, FALSE) = TRUE
                                OR d.status IN ('start_requested', 'starting', 'running', 'stop_requested')
                            )
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM account_login_reservations v
                          WHERE v.account_id = b.account_id
                            AND v.status IN ('pending', 'dispatched', 'verified')
                            AND (v.expires_at IS NULL OR v.expires_at > NOW())
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM execution_commands c
                          WHERE c.account_id = b.account_id
                            AND c.command_type IN ('START_BOT', 'STOP_BOT')
                            AND c.delivery_status IN ('pending', 'queued', 'dispatched')
                      )
                ),
                updated_binding AS (
                    UPDATE account_slot_bindings b
                    SET binding_state = 'sticky',
                        is_sticky = TRUE,
                        is_current = TRUE,
                        updated_at = NOW()
                    FROM eligible e
                    WHERE b.id = e.binding_id
                      AND (
                          b.binding_state <> 'sticky'
                          OR b.is_sticky IS DISTINCT FROM TRUE
                          OR b.is_current IS DISTINCT FROM TRUE
                      )
                    RETURNING b.id
                ),
                updated_slot AS (
                    UPDATE runner_slots s
                    SET current_account_id = NULL,
                        status = 'ready',
                        metadata_json = jsonb_strip_nulls(
                            (
                                COALESCE(s.metadata_json, '{}'::jsonb)
                                - 'account_id'
                                - 'active_account_id'
                                - 'deployment_id'
                                - 'login_reservation_id'
                                - 'login_slot_account_id'
                                - 'login_slot_status'
                                - 'current_state'
                                - 'previous_state'
                                - 'runner_state'
                                - 'current_runner_state'
                                - 'previous_runner_state'
                                - 'mt5_liveness_state'
                                - 'mt5_liveness_reason'
                                - 'current_control_plane_state'
                                - 'previous_control_plane_state'
                                - 'last_error'
                            ) || jsonb_build_object(
                                'sticky_account_id', e.account_id::TEXT,
                                'available_for_new_account', FALSE,
                                'control_plane_state', 'ready',
                                'current_control_plane_state', 'ready',
                                'runner_state', 'ready',
                                'current_runner_state', 'ready',
                                'mt5_liveness_state', '',
                                'last_reason', 'sticky_slot_reuse_prepared',
                                'last_error', ''
                            )
                        ),
                        updated_at = NOW()
                    FROM eligible e
                    WHERE s.runner_id = e.runner_id
                      AND s.slot_id = e.slot_id
                      AND (
                          s.status <> 'ready'
                          OR s.current_account_id IS NOT NULL
                          OR COALESCE(s.metadata_json->>'control_plane_state', '') <> 'ready'
                          OR COALESCE(s.metadata_json->>'current_control_plane_state', '') <> 'ready'
                          OR COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'runner_state'), '')), 'ready') <> 'ready'
                          OR COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'current_runner_state'), '')), 'ready') <> 'ready'
                          OR COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'mt5_liveness_state'), '')), '') NOT IN ('', 'ready')
                          OR COALESCE(s.metadata_json->>'account_id', '') <> ''
                          OR COALESCE(s.metadata_json->>'active_account_id', '') <> ''
                          OR COALESCE(s.metadata_json->>'deployment_id', '') <> ''
                          OR COALESCE(s.metadata_json->>'last_error', '') <> ''
                          OR COALESCE(s.metadata_json->>'sticky_account_id', '') <> e.account_id::TEXT
                      )
                    RETURNING s.runner_id, s.slot_id
                )
                SELECT
                    e.binding_id,
                    e.account_id,
                    e.runner_id,
                    e.slot_id,
                    EXISTS(SELECT 1 FROM updated_binding) AS binding_updated,
                    EXISTS(SELECT 1 FROM updated_slot) AS slot_updated
                FROM eligible e
                LIMIT 1
                """,
                (int(account_id), list(_TERMINAL_DEPLOYMENT_STATUSES), int(account_id)),
            )
            row = cur.fetchone()
            return dict(row) if row else None

        return self._store._with_retry_locked(_do)

    def mark_slot_health(self, *, runner_id: str, slot_id: str, status: str) -> None:
        slot_id_s = _norm_slot_id(slot_id)
        status_s = "disabled" if _slot_exceeds_node_slot_cap(slot_id_s) else _norm(status)

        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                """
                UPDATE runner_slots
                SET status = %s,
                    current_account_id = CASE WHEN %s = 'disabled' THEN NULL ELSE current_account_id END,
                    updated_at = NOW()
                WHERE runner_id = %s AND slot_id = %s
                """,
                (status_s, status_s, _norm(runner_id), slot_id_s),
            )
            if status_s in {"degraded", "broken"}:
                cur.execute(
                    """
                    UPDATE runner_nodes
                    SET status = CASE WHEN status = 'offline' THEN status ELSE 'degraded' END,
                        updated_at = NOW()
                    WHERE runner_id = %s
                    """,
                    (_norm(runner_id),),
                )
            if status_s == "broken":
                cur.execute(
                    """
                    UPDATE account_slot_bindings
                    SET binding_state = 'broken',
                        is_current = FALSE,
                        updated_at = NOW()
                    WHERE runner_id = %s AND slot_id = %s AND is_current = TRUE
                    """,
                    (_norm(runner_id), slot_id_s),
                )

        self._store._with_retry_locked(_do)

    def update_runner_slot_state(self, *, runner_id: str, slot_id: str, status: str, metadata: Optional[dict[str, Any]] = None) -> None:
        status_s = _normalize_runner_slot_projection_status(status)
        slot_id_s = _norm_slot_id(slot_id)
        if not slot_id_s:
            return
        metadata_payload = dict(metadata or {})
        force_slot_cap_disabled = _slot_exceeds_node_slot_cap(slot_id_s)
        if force_slot_cap_disabled:
            status_s = "disabled"
            metadata_payload = _slot_cap_disabled_metadata(metadata_payload)
        metadata_json = _json_payload(metadata_payload)

        def _do(con: Any, cur: Any) -> None:
            if metadata_payload and not force_slot_cap_disabled:
                cur.execute(
                    """
                    SELECT metadata_json
                    FROM runner_slots
                    WHERE runner_id = %s AND slot_id = %s
                    FOR UPDATE
                    """,
                    (_norm(runner_id), slot_id_s),
                )
                existing = cur.fetchone()
                existing_metadata = (
                    existing.get("metadata_json")
                    if existing and isinstance(existing.get("metadata_json"), dict)
                    else {}
                )
                if existing is not None and not _slot_registration_should_update_projection(
                    existing_metadata=existing_metadata,
                    incoming_metadata=metadata_payload,
                ):
                    return
            cur.execute(
                """
                UPDATE runner_slots
                SET status = %s,
                    current_account_id = CASE
                        WHEN %s IN ('ready', 'disabled') THEN NULL
                        ELSE current_account_id
                    END,
                    metadata_json = CASE
                        WHEN %s = 'ready' THEN
                            jsonb_strip_nulls(
                                (
                                    COALESCE(metadata_json, '{}'::jsonb)
                                        - 'account_id'
                                        - 'active_account_id'
                                        - 'deployment_id'
                                        - 'login_reservation_id'
                                        - 'login_reservation_status'
                                        - 'login_reservation_account_id'
                                        - 'login_slot_status'
                                        - 'login_slot_account_id'
                                        - 'reserved_account_id'
                                        - 'sticky_account_id'
                                        - 'current_control_plane_state'
                                        - 'previous_control_plane_state'
                                ) || CASE
                                        WHEN %s::jsonb = '{}'::jsonb THEN '{}'::jsonb
                                        ELSE (
                                            %s::jsonb
                                                - 'account_id'
                                                - 'active_account_id'
                                                - 'deployment_id'
                                                - 'login_reservation_id'
                                                - 'login_reservation_status'
                                                - 'login_reservation_account_id'
                                                - 'login_slot_status'
                                                - 'login_slot_account_id'
                                                - 'reserved_account_id'
                                                - 'sticky_account_id'
                                        )
                                     END || jsonb_build_object(
                                        'control_plane_state', 'ready',
                                        'available_for_new_account', TRUE
                                     )
                            )
                        WHEN %s = 'disabled' THEN
                            jsonb_strip_nulls(
                                (
                                    COALESCE(metadata_json, '{}'::jsonb)
                                        - 'account_id'
                                        - 'active_account_id'
                                        - 'deployment_id'
                                        - 'login_reservation_id'
                                        - 'login_reservation_status'
                                        - 'login_reservation_account_id'
                                        - 'login_slot_status'
                                        - 'login_slot_account_id'
                                        - 'reserved_account_id'
                                        - 'sticky_account_id'
                                        - 'slot_inventory_entry'
                                ) || %s::jsonb
                            )
                        WHEN %s::jsonb = '{}'::jsonb THEN metadata_json
                        ELSE metadata_json || %s::jsonb
                    END,
                    updated_at = NOW()
                WHERE runner_id = %s AND slot_id = %s
                """,
                (
                    status_s,
                    status_s,
                    status_s,
                    metadata_json,
                    metadata_json,
                    status_s,
                    metadata_json,
                    metadata_json,
                    metadata_json,
                    _norm(runner_id),
                    slot_id_s,
                ),
            )
            if status_s in {"degraded", "broken"}:
                cur.execute(
                    """
                    UPDATE runner_nodes
                    SET status = CASE WHEN status = 'offline' THEN status ELSE 'degraded' END,
                        updated_at = NOW()
                    WHERE runner_id = %s
                    """,
                    (_norm(runner_id),),
                )
            elif status_s in {"ready", "allocated"}:
                cur.execute(
                    """
                    UPDATE runner_nodes
                    SET status = CASE WHEN status = 'offline' THEN status ELSE 'online' END,
                        updated_at = NOW()
                    WHERE runner_id = %s
                    """,
                    (_norm(runner_id),),
                )

        self._store._with_retry_locked(_do)

    def quarantine_runner_slot_for_start_failure(
        self,
        *,
        runner_id: str,
        slot_id: str,
        account_id: int,
        deployment_id: int,
        command_id: str | None,
        reason: str,
        quarantine_sec: int,
        runner_failure_window_sec: int,
        runner_throttle_threshold: int,
        runner_throttle_sec: int,
    ) -> dict[str, Any]:
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id)
        reason_s = (_norm(reason) or "start_slot_failure")[:300]
        command_id_s = _norm(command_id) or ""
        quarantine_sec_i = max(30, int(quarantine_sec or 0))
        failure_window_i = max(60, int(runner_failure_window_sec or 0))
        throttle_threshold_i = max(1, int(runner_throttle_threshold or 0))
        throttle_sec_i = max(30, int(runner_throttle_sec or 0))

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                UPDATE runner_slots
                SET status = CASE WHEN status = 'broken' THEN status ELSE 'degraded' END,
                    current_account_id = CASE
                        WHEN current_account_id = %s THEN NULL
                        ELSE current_account_id
                    END,
                    metadata_json = jsonb_strip_nulls(
                        COALESCE(metadata_json, '{}'::jsonb)
                        || jsonb_build_object(
                            'control_plane_state', 'degraded',
                            'current_control_plane_state', 'degraded',
                            'mt5_liveness_state', 'degraded',
                            'available_for_new_account', FALSE,
                            'auto_quarantine', TRUE,
                            'auto_quarantine_until', NOW() + (%s * INTERVAL '1 second'),
                            'auto_quarantine_reason', %s,
                            'auto_quarantine_account_id', %s,
                            'auto_quarantine_deployment_id', %s,
                            'auto_quarantine_command_id', NULLIF(%s, ''),
                            'last_reason', %s,
                            'last_error', %s
                        )
                    ),
                    updated_at = NOW()
                WHERE runner_id = %s AND slot_id = %s
                """,
                (
                    int(account_id),
                    quarantine_sec_i,
                    reason_s,
                    str(int(account_id)),
                    str(int(deployment_id)),
                    command_id_s,
                    reason_s,
                    reason_s,
                    runner_id_s,
                    slot_id_s,
                ),
            )
            slot_updated = int(cur.rowcount or 0)

            cur.execute(
                """
                SELECT COUNT(*) AS failed_count
                FROM execution_commands
                WHERE runner_id = %s
                  AND command_type = 'START_BOT'
                  AND delivery_status = 'failed'
                  AND updated_at >= NOW() - (%s * INTERVAL '1 second')
                """,
                (runner_id_s, failure_window_i),
            )
            failed_count = _safe_int((cur.fetchone() or {}).get("failed_count"), 0)
            throttled = failed_count >= throttle_threshold_i
            cur.execute(
                """
                UPDATE runner_nodes
                SET metadata_json = jsonb_strip_nulls(
                    COALESCE(metadata_json, '{}'::jsonb)
                    || jsonb_build_object(
                        'start_failure_recent_count', %s,
                        'last_start_failure_at', NOW(),
                        'last_start_failure_reason', %s,
                        'dispatch_penalty_until',
                            CASE WHEN %s THEN NOW() + (%s * INTERVAL '1 second') ELSE NULL END
                    )
                ),
                    updated_at = NOW()
                WHERE runner_id = %s
                """,
                (failed_count, reason_s, bool(throttled), throttle_sec_i, runner_id_s),
            )
            return {
                "slot_updated": slot_updated,
                "runner_recent_start_failures": failed_count,
                "runner_throttled": bool(throttled),
                "quarantine_sec": quarantine_sec_i,
                "runner_throttle_sec": throttle_sec_i if throttled else 0,
            }

        return self._store._with_retry_locked(_do)

    def release_stale_runtime_for_ready_slot(self, *, runner_id: str, slot_id: str) -> None:
        runner_id_s = _norm(runner_id)
        slot_id_s = _norm_slot_id(slot_id)
        if not runner_id_s or not slot_id_s:
            return

        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                """
                UPDATE bot_deployments
                SET status = CASE WHEN status IN ('start_requested', 'starting', 'running', 'stop_requested') THEN 'stopped' ELSE status END,
                    desired_state = 'stopped',
                    is_active = FALSE,
                    health_status = 'runner_reported_slot_ready',
                    stopped_at = COALESCE(stopped_at, NOW()),
                    updated_at = NOW()
                WHERE runner_id = %s
                  AND slot_id = %s
                  AND (
                      COALESCE(is_active, FALSE) = TRUE
                      OR desired_state = 'running'
                      OR status IN ('start_requested', 'starting', 'running', 'stop_requested')
                  )
                """,
                (runner_id_s, slot_id_s),
            )
            cur.execute(
                """
                UPDATE account_slot_bindings
                SET binding_state = 'released',
                    is_current = FALSE,
                    updated_at = NOW()
                WHERE runner_id = %s
                  AND slot_id = %s
                  AND is_current = TRUE
                  AND binding_state IN ('active', 'reserved')
                """,
                (runner_id_s, slot_id_s),
            )
            cur.execute(
                """
                UPDATE account_login_reservations
                SET status = 'released',
                    last_error = 'runner_reported_slot_ready',
                    completed_at = COALESCE(completed_at, NOW()),
                    updated_at = NOW()
                WHERE runner_id = %s
                  AND slot_id = %s
                  AND status IN ('pending', 'dispatched', 'verified')
                """,
                (runner_id_s, slot_id_s),
            )
            cur.execute(
                """
                UPDATE runner_slots
                SET current_account_id = NULL,
                    status = CASE WHEN status IN ('allocated', 'ready') THEN 'ready' ELSE status END,
                    metadata_json = COALESCE(metadata_json, '{}'::jsonb)
                        - 'account_id'
                        - 'active_account_id'
                        - 'deployment_id'
                        - 'active_deployment_id'
                        - 'login_slot_account_id'
                        || jsonb_build_object(
                            'start_eligible', true,
                            'available_for_new_account', true,
                            'requires_ipc_ready_before_start', false,
                            'stale_runtime_released_at', extract(epoch from NOW())::bigint
                        ),
                    updated_at = NOW()
                WHERE runner_id = %s
                  AND slot_id = %s
                """,
                (runner_id_s, slot_id_s),
            )

        self._store._with_retry_locked(_do)

    def reconcile_ready_runner_slots(self, *, runner_id: str) -> None:
        runner_id_s = _norm(runner_id)
        if not runner_id_s:
            return

        def _do(con: Any, cur: Any) -> list[str]:
            cur.execute(
                """
                SELECT slot_id
                FROM runner_slots
                WHERE runner_id = %s
                  AND status = 'ready'
                  AND (
                      LOWER(COALESCE(metadata_json->>'requires_ipc_ready_before_start', 'false')) IN ('false', '0', 'no', 'off')
                      OR LOWER(COALESCE(metadata_json->>'start_mode', metadata_json->>'runtime_start_mode', '')) IN ('cold_start', 'cold-start', 'ondemand', 'on_demand')
                      OR LOWER(COALESCE(metadata_json->>'start_eligible', 'false')) IN ('true', '1', 'yes', 'on')
                  )
                """,
                (runner_id_s,),
            )
            return [_norm_slot_id(row.get("slot_id")) for row in (cur.fetchall() or []) if _norm_slot_id(row.get("slot_id"))]

        for slot_id_s in self._store._with_retry_read(_do):
            self.release_stale_runtime_for_ready_slot(runner_id=runner_id_s, slot_id=slot_id_s)

    def release_deployment_slot(self, *, deployment_id: int, keep_sticky: bool) -> None:
        def _do(con: Any, cur: Any) -> None:
            cur.execute(
                """
                SELECT
                    d.id,
                    d.account_id,
                    d.runner_id,
                    d.slot_id,
                    d.binding_id,
                    a.status AS account_status,
                    a.is_active AS account_is_active
                FROM bot_deployments d
                LEFT JOIN broker_accounts a ON a.id = d.account_id
                WHERE d.id = %s
                FOR UPDATE OF d
                """,
                (int(deployment_id),),
            )
            row = cur.fetchone() or {}
            if not row:
                return
            account_id = row.get("account_id")
            runner_id = _norm(row.get("runner_id"))
            slot_id = _norm_slot_id(row.get("slot_id"))
            binding_id = row.get("binding_id")
            account_status = _norm(row.get("account_status")).lower()
            account_is_active = row.get("account_is_active")
            effective_keep_sticky = bool(keep_sticky)
            if account_status == "disconnected" or account_is_active is False:
                effective_keep_sticky = False
            if runner_id and slot_id:
                cur.execute(
                    """
                    UPDATE runner_slots
                    SET current_account_id = NULL,
                        status = CASE WHEN status = 'broken' THEN status ELSE 'ready' END,
                        metadata_json = jsonb_strip_nulls(
                            (
                                COALESCE(metadata_json, '{}'::jsonb)
                                    - 'account_id'
                                    - 'active_account_id'
                                    - 'deployment_id'
                                    - 'sticky_account_id'
                                    - 'reserved_account_id'
                                    - 'current_control_plane_state'
                                    - 'previous_control_plane_state'
                                    - 'runner_state'
                                    - 'current_runner_state'
                                    - 'previous_runner_state'
                                    - 'mt5_liveness_state'
                                    - 'mt5_liveness_reason'
                                    - 'current_state'
                                    - 'previous_state'
                                    - 'reason'
                                    - 'last_error'
                            ) || jsonb_build_object(
                                'sticky_account_id', CASE WHEN %s THEN %s ELSE NULL END,
                                'available_for_new_account', TRUE,
                                'control_plane_state', 'ready',
                                'current_control_plane_state', 'ready',
                                'runner_state', 'ready',
                                'current_runner_state', 'ready',
                                'mt5_liveness_state', '',
                                'last_reason', 'deployment_slot_released',
                                'last_error', ''
                            )
                        ),
                        updated_at = NOW()
                    WHERE runner_id = %s AND slot_id = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM bot_deployments other
                          WHERE other.runner_id = %s
                            AND other.slot_id = %s
                            AND other.id <> %s
                            AND other.status = ANY(%s)
                      )
                    """,
                    (
                        effective_keep_sticky,
                        str(int(account_id)) if account_id is not None else None,
                        runner_id,
                        slot_id,
                        runner_id,
                        slot_id,
                        int(deployment_id),
                        list(ACTIVE_DEPLOYMENT_STATUSES),
                    ),
                )
            if binding_id:
                cur.execute(
                    """
                    UPDATE account_slot_bindings
                    SET binding_state = %s,
                        is_sticky = %s,
                        is_current = %s,
                        last_used_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        "sticky" if effective_keep_sticky else "released",
                        effective_keep_sticky,
                        effective_keep_sticky,
                        int(binding_id),
                    ),
                )
            elif account_id and runner_id and slot_id and not effective_keep_sticky:
                cur.execute(
                    """
                    UPDATE account_slot_bindings
                    SET binding_state = 'released',
                        is_current = FALSE,
                        updated_at = NOW()
                    WHERE account_id = %s
                      AND runner_id = %s
                      AND slot_id = %s
                      AND is_current = TRUE
                    """,
                    (int(account_id), runner_id, slot_id),
                )

        self._store._with_retry_locked(_do)

    def get_runner_readiness_snapshot(self, *, runner_id: str, runner_stale_sec: int) -> dict[str, Any]:
        self.reconcile_ready_runner_slots(runner_id=runner_id)
        runner_id_s = _norm(runner_id)
        stale_cutoff = max(30, int(runner_stale_sec))
        zero_slots = {
            "total": 0,
            "expected": 0,
            "ready": 0,
            "available": 0,
            "ipc_ready": 0,
            "start_eligible": 0,
            "start_available": 0,
            "active": 0,
            "login_reserved": 0,
            "reserved": 0,
            "degraded": 0,
            "broken": 0,
        }
        if not runner_id_s:
            return {
                "runner_id": "",
                "registered": False,
                "runner": {},
                "slots": zero_slots,
                "thresholds": {"runner_stale_sec": stale_cutoff},
            }

        def _do(con: Any, cur: Any) -> dict[str, Any]:
            cur.execute(
                """
                SELECT
                    runner_id,
                    status,
                    supported_profiles,
                    capability_tags,
                    capabilities_json,
                    metadata_json,
                    max_slots,
                    last_registered_at,
                    last_heartbeat_at,
                    (
                        last_heartbeat_at IS NULL
                        OR last_heartbeat_at < (NOW() - (%s * INTERVAL '1 second'))
                    ) AS is_stale
                FROM runner_nodes
                WHERE runner_id = %s
                """,
                (stale_cutoff, runner_id_s),
            )
            runner_row = cur.fetchone()
            if not runner_row:
                return {
                    "runner_id": runner_id_s,
                    "registered": False,
                    "runner": {},
                    "slots": zero_slots,
                    "thresholds": {"runner_stale_sec": stale_cutoff},
                }

            runner = dict(runner_row)
            max_slot_number_i = _cap_runner_slots(_safe_int(runner.get("max_slots"), 1))
            runner_metadata = _json_dict(runner.get("metadata_json"))
            expected_slots_i = max(
                1,
                min(
                    max_slot_number_i,
                    _safe_int(
                        runner_metadata.get("effective_slots")
                        or runner_metadata.get("requested_slots")
                        or runner_metadata.get("max_slots"),
                        max_slot_number_i,
                    ),
                ),
            )
            cur.execute(
                """
                WITH active_login_reservations AS (
                    SELECT runner_id, slot_id, COUNT(*) AS active_count
                    FROM account_login_reservations
                    WHERE status IN ('pending', 'dispatched', 'verified')
                      AND runner_id = %s
                      AND slot_id IS NOT NULL
                    GROUP BY runner_id, slot_id
                ),
                current_bindings AS (
                    SELECT
                        runner_id,
                        slot_id,
                        COUNT(*) AS current_count,
                        COUNT(*) FILTER (WHERE binding_state = 'active') AS active_count
                    FROM account_slot_bindings
                    WHERE is_current = TRUE
                      AND binding_state = 'active'
                      AND runner_id = %s
                    GROUP BY runner_id, slot_id
                ),
                counted_slots AS (
                    SELECT s.*
                    FROM runner_slots s
                    WHERE s.runner_id = %s
                      AND (
                          COALESCE(NULLIF(SUBSTRING(s.slot_id FROM '([0-9]+)$'), ''), '') = ''
                          OR CAST(SUBSTRING(s.slot_id FROM '([0-9]+)$') AS INTEGER) <= GREATEST(1, %s)
                      )
                      AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'manual_disabled'), '')), 'false')
                            NOT IN ('true', '1', 'yes', 'y', 'on')
                      AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'disabled'), '')), 'false')
                            NOT IN ('true', '1', 'yes', 'y', 'on')
                      AND LOWER(COALESCE(
                            NULLIF(BTRIM(s.metadata_json->>'registration_status'), ''),
                            NULLIF(BTRIM(s.metadata_json->>'runner_state'), ''),
                            NULLIF(BTRIM(s.metadata_json->>'current_runner_state'), ''),
                            ''
                          )) NOT IN ('disabled', 'manual_disabled', 'degraded')
                )
                SELECT
                    COUNT(*) AS total,
                    %s::INTEGER AS expected,
                    COUNT(*) FILTER (WHERE s.status = 'ready') AS ready,
                    COUNT(*) FILTER (
                        WHERE s.status = 'allocated'
                           OR s.current_account_id IS NOT NULL
                           OR COALESCE(b.active_count, 0) > 0
                    ) AS active,
                    COUNT(*) FILTER (
                        WHERE COALESCE(v.active_count, 0) > 0
                    ) AS login_reserved,
                    COUNT(*) FILTER (WHERE COALESCE(b.current_count, 0) > 0) AS reserved,
                    COUNT(*) FILTER (WHERE s.status = 'degraded') AS degraded,
                    COUNT(*) FILTER (WHERE s.status = 'broken') AS broken,
                    COUNT(*) FILTER (
                        WHERE s.status = 'ready'
                          AND s.current_account_id IS NULL
                          AND COALESCE(b.current_count, 0) = 0
                          AND COALESCE(v.active_count, 0) = 0
                    ) AS available,
                    COUNT(*) FILTER (
                        WHERE COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'ipc_ready'), '')), 'false')
                              IN ('true', '1', 'yes', 'y', 'on')
                    ) AS ipc_ready,
                    COUNT(*) FILTER (
                        WHERE COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'start_eligible'), '')), 'false')
                              IN ('true', '1', 'yes', 'y', 'on')
                    ) AS start_eligible,
                    COUNT(*) FILTER (
                        WHERE s.status = 'ready'
                          AND s.current_account_id IS NULL
                          AND COALESCE(b.current_count, 0) = 0
                          AND COALESCE(v.active_count, 0) = 0
                          AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'ipc_ready'), '')), 'false')
                              IN ('true', '1', 'yes', 'y', 'on')
                          AND COALESCE(LOWER(NULLIF(BTRIM(s.metadata_json->>'start_eligible'), '')), 'false')
                              IN ('true', '1', 'yes', 'y', 'on')
                    ) AS start_available
                FROM counted_slots s
                LEFT JOIN active_login_reservations v
                  ON v.runner_id = s.runner_id
                 AND v.slot_id = s.slot_id
                LEFT JOIN current_bindings b
                  ON b.runner_id = s.runner_id
                 AND b.slot_id = s.slot_id
                """,
                (
                    runner_id_s,
                    runner_id_s,
                    runner_id_s,
                    max_slot_number_i,
                    expected_slots_i,
                ),
            )
            slots = dict(cur.fetchone() or {})
            session0_metadata: dict[str, Any] = {}
            nested_session0 = runner_metadata.get("session0")
            if isinstance(nested_session0, dict):
                session0_metadata.update(
                    {
                        key: nested_session0.get(key)
                        for key in (
                            "runner_owned_session0_terminals",
                            "runner_owned_session0_terminal_count",
                            "runner_owned_session0_count",
                            "foreign_session0_terminals",
                            "foreign_session0_terminal_count",
                            "foreign_session0_count",
                            "foreign_session0_classifications",
                        )
                        if key in nested_session0
                    }
                )
            for key in (
                "runner_owned_session0_terminals",
                "runner_owned_session0_terminal_count",
                "runner_owned_session0_count",
                "foreign_session0_terminals",
                "foreign_session0_terminal_count",
                "foreign_session0_count",
                "foreign_session0_classifications",
            ):
                if key in runner_metadata:
                    session0_metadata[key] = runner_metadata.get(key)
            runner_payload = {
                "status": runner.get("status"),
                "max_slots": expected_slots_i,
                "last_registered_at": runner.get("last_registered_at"),
                "last_heartbeat_at": runner.get("last_heartbeat_at"),
                "is_stale": bool(runner.get("is_stale")),
                "bot_codes": _runner_readiness_bot_codes(
                    runner.get("capabilities_json"),
                    runner.get("metadata_json"),
                ),
                "session0": session0_metadata,
            }
            return {
                "runner_id": runner_id_s,
                "registered": True,
                "runner": runner_payload,
                "slots": slots,
                "thresholds": {"runner_stale_sec": stale_cutoff},
            }

        return self._store._with_retry_read(_do)

    # -----------------------------------------------------------------
    # Risk policy + daily PnL (Sprint 2 - circuit breaker)
    # -----------------------------------------------------------------
