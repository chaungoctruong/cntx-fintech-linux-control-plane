from __future__ import annotations

import threading
import time
from typing import Any

from app.repositories.control_plane_repository import ControlPlaneRepository
from app.services.store_service import get_process_store
from app.settings import settings

_HEALTH_STATUS_VALUES = {
    "error": 0,
    "degraded": 1,
    "ok": 2,
}
_STARTUP_STATES = ("created", "booting", "ready", "failed", "shutting_down")
_BACKGROUND_SINGLETON_STATES = (
    "created",
    "starting",
    "owner",
    "busy",
    "disabled",
    "error",
    "stopping",
    "released",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _age_sec(ts: Any, *, now: int) -> int | None:
    value = _safe_int(ts, 0)
    if value <= 0:
        return None
    return max(0, now - value)


def _bool_gauge(value: Any) -> float:
    return 1.0 if bool(value) else 0.0


def _escape_label(value: Any) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_metric_value(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _format_sample(metric: str, value: float, labels: dict[str, Any] | None = None) -> str:
    if labels:
        rendered_labels = ",".join(
            f'{key}="{_escape_label(label_value)}"'
            for key, label_value in sorted(labels.items())
        )
        return f"{metric}{{{rendered_labels}}} {_format_metric_value(value)}"
    return f"{metric} {_format_metric_value(value)}"


def _append_metric_family(
    lines: list[str],
    *,
    metric: str,
    help_text: str,
    samples: list[tuple[dict[str, Any] | None, float]],
) -> None:
    lines.append(f"# HELP {metric} {help_text}")
    lines.append(f"# TYPE {metric} gauge")
    for labels, value in samples:
        lines.append(_format_sample(metric, value, labels))


def _build_check(ok: bool, *, critical: bool, detail: str, age_sec: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": bool(ok),
        "critical": bool(critical),
        "detail": detail,
    }
    if age_sec is not None:
        payload["age_sec"] = int(age_sec)
    return payload


def _runtime_degraded_reasons(summary: dict[str, Any], service_health: dict[str, Any]) -> list[str]:
    runners = dict(summary.get("runners") or {})
    deployments = dict(summary.get("deployments") or {})
    slots = dict(summary.get("slots") or {})

    reasons: list[str] = []
    if _safe_int(runners.get("stale_runners")) > 0:
        reasons.append("stale_runners")
    if _safe_int(runners.get("offline_runners")) > 0:
        reasons.append("offline_runners")
    if _safe_int(deployments.get("stale_deployments")) > 0:
        reasons.append("stale_deployments")
    if _safe_int(deployments.get("failed_deployments")) > 0:
        reasons.append("failed_deployments")
    if _safe_int(slots.get("broken_slots")) > 0:
        reasons.append("broken_slots")

    desired_running = _safe_int(deployments.get("desired_running_deployments"))
    if (
        desired_running > 0
        and not bool(service_health.get("service_capacity_available"))
        and _safe_int(runners.get("online_runners")) <= 0
        and _safe_int(slots.get("ready_slots")) <= 0
    ):
        reasons.append("runtime_capacity_unavailable")

    return reasons


class ControlPlaneMetricsService:
    def __init__(self, repo: ControlPlaneRepository | None = None) -> None:
        self._repo = repo or ControlPlaneRepository(get_process_store())
        self._cache_ttl_sec = max(
            1,
            int(getattr(settings, "CONTROL_PLANE_METRICS_CACHE_TTL_SEC", 5) or 5),
        )
        self._cache_lock = threading.Lock()
        self._summary_cache: dict[str, Any] | None = None
        self._summary_cached_at = 0

    def user_dashboard(self, *, user_id: int) -> dict[str, Any]:
        return self._repo.get_dashboard(user_id=user_id)

    def _fetch_runtime_health_summary(self) -> dict[str, Any]:
        return self._repo.get_runtime_health_summary(
            runner_stale_sec=int(getattr(settings, "CONTROL_PLANE_RUNNER_STALE_SEC", 180) or 180),
            deployment_stale_sec=int(getattr(settings, "CONTROL_PLANE_DEPLOYMENT_STALE_SEC", 180) or 180),
        )

    def observe_runtime_health(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = int(time.time())
        with self._cache_lock:
            cached_summary = dict(self._summary_cache or {}) if self._summary_cache else None
            cached_at = int(self._summary_cached_at or 0)

        cached_age = _age_sec(cached_at, now=now)
        if not force_refresh and cached_summary and cached_age is not None and cached_age <= self._cache_ttl_sec:
            return {
                "ok": True,
                "source": "cache",
                "summary": cached_summary,
                "collected_at": cached_at,
                "age_sec": cached_age,
                "error": None,
            }

        try:
            summary = self._fetch_runtime_health_summary()
        except Exception as exc:
            if cached_summary:
                return {
                    "ok": False,
                    "source": "stale-cache",
                    "summary": cached_summary,
                    "collected_at": cached_at,
                    "age_sec": cached_age,
                    "error": str(exc),
                }
            return {
                "ok": False,
                "source": "error",
                "summary": {},
                "collected_at": 0,
                "age_sec": None,
                "error": str(exc),
            }

        collected_at = int(time.time())
        with self._cache_lock:
            self._summary_cache = dict(summary)
            self._summary_cached_at = collected_at

        return {
            "ok": True,
            "source": "db",
            "summary": dict(summary),
            "collected_at": collected_at,
            "age_sec": 0,
            "error": None,
        }

    def runtime_health_summary(self) -> dict[str, Any]:
        return dict(self.observe_runtime_health().get("summary") or {})

    def build_observability_snapshot(
        self,
        *,
        app_state: Any,
        started_at: float,
        version: str,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        now = int(time.time())
        service_health = dict(getattr(app_state, "service_health_status", {}) or {})
        startup_state = str(getattr(app_state, "startup_state", "created") or "created")
        startup_started_at = _safe_int(
            getattr(app_state, "startup_started_at", 0) or int(started_at),
            int(started_at),
        )
        startup_completed_at = _safe_int(getattr(app_state, "startup_completed_at", 0))
        startup_error = getattr(app_state, "startup_error", None)

        background_state = str(getattr(app_state, "background_singleton_state", "created") or "created")
        background_enabled = bool(getattr(app_state, "background_singleton_enabled", False))
        background_updated_at = _safe_int(getattr(app_state, "background_singleton_updated_at", 0))

        health_updated_at = _safe_int(service_health.get("updated_at"))
        health_age_sec = _age_sec(health_updated_at, now=now)
        health_stale_sec = max(
            30,
            int(getattr(settings, "CONTROL_PLANE_HEALTH_STALE_SEC", 120) or 120),
        )

        observation = self.observe_runtime_health(force_refresh=force_refresh)
        runtime_summary = dict(observation.get("summary") or {})
        runtime_age_sec = observation.get("age_sec")
        runtime_stale_sec = max(
            self._cache_ttl_sec,
            int(getattr(settings, "CONTROL_PLANE_RUNTIME_SUMMARY_STALE_SEC", 90) or 90),
        )

        reconciler_snapshot: dict[str, Any] = {}
        reconciler = getattr(app_state, "control_plane_reconciler", None)
        if reconciler is not None and hasattr(reconciler, "snapshot"):
            try:
                reconciler_snapshot = dict(reconciler.snapshot() or {})
            except Exception as exc:
                reconciler_snapshot = {"ok": False, "error": str(exc)}
        command_delivery_snapshot: dict[str, Any] = {}
        command_delivery = getattr(app_state, "command_delivery_reconciler", None)
        if command_delivery is not None and hasattr(command_delivery, "snapshot"):
            try:
                command_delivery_snapshot = dict(command_delivery.snapshot() or {})
            except Exception as exc:
                command_delivery_snapshot = {"ok": False, "error": str(exc)}
        command_delivery_backlog_count = 0
        command_delivery_backlog_error = None
        counter = getattr(self._repo, "count_command_delivery_replay_backlog", None)
        if callable(counter):
            try:
                command_delivery_backlog_count = _safe_int(counter())
            except Exception as exc:
                command_delivery_backlog_count = -1
                command_delivery_backlog_error = str(exc)
        if command_delivery_snapshot:
            command_delivery_snapshot["backlog_count"] = command_delivery_backlog_count
            if command_delivery_backlog_error:
                command_delivery_snapshot["backlog_error"] = command_delivery_backlog_error

        reconciler_expected = (not background_enabled) or background_state in {"owner", "disabled"}
        reconcile_interval_sec = max(
            10,
            int(getattr(settings, "CONTROL_PLANE_RECONCILE_INTERVAL_SEC", 30) or 30),
        )
        reconciler_stale_sec = max(reconcile_interval_sec * 3, 60)
        reconciler_last_success_age = _age_sec(reconciler_snapshot.get("last_success_at"), now=now)
        reconciler_ok = True
        reconciler_detail = "not_required_on_this_worker"
        if reconciler_expected:
            reconciler_ok = (
                bool(reconciler_snapshot)
                and _safe_int(reconciler_snapshot.get("run_count")) > 0
                and reconciler_last_success_age is not None
                and reconciler_last_success_age <= reconciler_stale_sec
                and not reconciler_snapshot.get("last_error")
            )
            if reconciler_snapshot:
                reconciler_detail = (
                    f"last_success_age_sec={reconciler_last_success_age}, "
                    f"run_count={_safe_int(reconciler_snapshot.get('run_count'))}, "
                    f"last_error={reconciler_snapshot.get('last_error') or 'none'}"
                )
            else:
                reconciler_detail = "reconciler_snapshot_missing"

        service_online = bool(service_health.get("service_online", getattr(app_state, "is_service_online", False)))
        runtime_degraded_reasons = _runtime_degraded_reasons(runtime_summary, service_health)

        checks = {
            "startup_bootstrap": _build_check(
                startup_state == "ready" and startup_completed_at > 0,
                critical=True,
                detail=f"startup_state={startup_state}",
            ),
            "service_online": _build_check(
                service_online,
                critical=True,
                detail=f"service_online={service_online}",
            ),
            "service_health_fresh": _build_check(
                health_age_sec is not None and health_age_sec <= health_stale_sec,
                critical=True,
                detail=f"updated_at={health_updated_at}, stale_after_sec={health_stale_sec}",
                age_sec=health_age_sec,
            ),
            "runtime_summary_available": _build_check(
                bool(runtime_summary),
                critical=True,
                detail=f"source={observation.get('source')}",
            ),
            "runtime_summary_fresh": _build_check(
                runtime_age_sec is not None and runtime_age_sec <= runtime_stale_sec,
                critical=True,
                detail=f"age_sec={runtime_age_sec}, stale_after_sec={runtime_stale_sec}",
                age_sec=runtime_age_sec,
            ),
            "runtime_summary_collection": _build_check(
                bool(observation.get("ok")),
                critical=False,
                detail=f"source={observation.get('source')}, error={observation.get('error') or 'none'}",
                age_sec=runtime_age_sec,
            ),
            "background_singleton_coordination": _build_check(
                (not background_enabled) or background_state in {"owner", "busy", "disabled"},
                critical=False,
                detail=f"enabled={background_enabled}, state={background_state}",
                age_sec=_age_sec(background_updated_at, now=now),
            ),
            "reconciler_loop_fresh": _build_check(
                reconciler_ok,
                critical=False,
                detail=reconciler_detail,
                age_sec=reconciler_last_success_age,
            ),
        }

        critical_failures = [name for name, check in checks.items() if check["critical"] and not check["ok"]]
        warning_failures = [name for name, check in checks.items() if not check["critical"] and not check["ok"]]
        ready = not critical_failures
        if not ready:
            status = "error"
        elif runtime_degraded_reasons or warning_failures:
            status = "degraded"
        else:
            status = "ok"

        return {
            "status": status,
            "live": True,
            "ready": ready,
            "critical_failures": critical_failures,
            "warning_failures": warning_failures,
            "process": {
                "version": version,
                "env": settings.APP_ENV,
                "db_mode": settings.DB_MODE,
                "started_at": int(started_at),
                "uptime_sec": max(0, now - int(started_at)),
            },
            "startup": {
                "state": startup_state,
                "started_at": startup_started_at,
                "completed_at": startup_completed_at,
                "duration_sec": max(0, startup_completed_at - startup_started_at) if startup_completed_at > 0 else None,
                "error": str(startup_error) if startup_error else None,
            },
            "background_singleton": {
                "enabled": background_enabled,
                "state": background_state,
                "updated_at": background_updated_at,
                "age_sec": _age_sec(background_updated_at, now=now),
            },
            "service_health_status": service_health,
            "runtime": {
                "healthy": ready and not runtime_degraded_reasons and bool(observation.get("ok")),
                "degraded_reasons": runtime_degraded_reasons,
                "summary": runtime_summary,
                "observation": {
                    "ok": bool(observation.get("ok")),
                    "source": observation.get("source"),
                    "collected_at": _safe_int(observation.get("collected_at")),
                    "age_sec": runtime_age_sec,
                    "error": observation.get("error"),
                },
            },
            "checks": checks,
            "reconciler": reconciler_snapshot,
            "command_delivery_reconciler": command_delivery_snapshot,
            "command_delivery_backlog_count": command_delivery_backlog_count,
            "command_delivery_backlog_error": command_delivery_backlog_error,
            "status_code": 200 if ready else 503,
        }

    def render_prometheus(
        self,
        *,
        app_state: Any,
        started_at: float,
        version: str,
        force_refresh: bool = False,
    ) -> str:
        snapshot = self.build_observability_snapshot(
            app_state=app_state,
            started_at=started_at,
            version=version,
            force_refresh=force_refresh,
        )
        process = dict(snapshot.get("process") or {})
        startup = dict(snapshot.get("startup") or {})
        background = dict(snapshot.get("background_singleton") or {})
        runtime = dict(snapshot.get("runtime") or {})
        summary = dict(runtime.get("summary") or {})
        service_health = dict(snapshot.get("service_health_status") or {})
        observation = dict(runtime.get("observation") or {})
        checks = dict(snapshot.get("checks") or {})
        reconciler = dict(snapshot.get("reconciler") or {})
        command_delivery = dict(snapshot.get("command_delivery_reconciler") or {})
        command_delivery_backlog_count = _safe_float(snapshot.get("command_delivery_backlog_count"))

        lines: list[str] = []

        _append_metric_family(
            lines,
            metric="spider_backend_info",
            help_text="Static metadata for the CNTx labs MT5 control-plane backend.",
            samples=[
                (
                    {
                        "version": process.get("version") or version,
                        "env": process.get("env") or settings.APP_ENV,
                        "db_mode": process.get("db_mode") or settings.DB_MODE,
                    },
                    1.0,
                )
            ],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_uptime_seconds",
            help_text="Backend process uptime in seconds.",
            samples=[(None, _safe_float(process.get("uptime_sec")))],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_live",
            help_text="Whether the backend process is live.",
            samples=[(None, _bool_gauge(snapshot.get("live")))],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_ready",
            help_text="Whether the backend instance is ready to serve control-plane traffic.",
            samples=[(None, _bool_gauge(snapshot.get("ready")))],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_health_status",
            help_text="Overall health status encoded as 0=error, 1=degraded, 2=ok.",
            samples=[(None, float(_HEALTH_STATUS_VALUES.get(str(snapshot.get("status")), 0)))],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_startup_state",
            help_text="Current startup state for this backend worker.",
            samples=[
                ({"state": state}, 1.0 if state == str(startup.get("state") or "") else 0.0)
                for state in _STARTUP_STATES
            ],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_background_singleton_state",
            help_text="Current background singleton coordination state for this worker.",
            samples=[
                ({"state": state}, 1.0 if state == str(background.get("state") or "") else 0.0)
                for state in _BACKGROUND_SINGLETON_STATES
            ],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_service_online",
            help_text="Whether watchdog-derived service state reports the control plane online.",
            samples=[(None, _bool_gauge(service_health.get("service_online")))],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_service_capacity_available",
            help_text="Whether watchdog-derived runtime capacity is currently available.",
            samples=[(None, _bool_gauge(service_health.get("service_capacity_available")))],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_service_health_age_seconds",
            help_text="Age of the last watchdog service health snapshot in seconds.",
            samples=[(None, _safe_float(_age_sec(service_health.get("updated_at"), now=int(time.time())) or 0))],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_runtime_summary_collection_success",
            help_text="Whether the latest runtime summary collection succeeded without falling back to stale cache.",
            samples=[(None, _bool_gauge(observation.get("ok")))],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_runtime_summary_age_seconds",
            help_text="Age of the runtime health summary used for this scrape in seconds.",
            samples=[(None, _safe_float(observation.get("age_sec")))],
        )
        _append_metric_family(
            lines,
            metric="spider_backend_runtime_summary_source",
            help_text="Source used for the runtime health summary in this scrape.",
            samples=[
                (
                    {"source": source},
                    1.0 if source == str(observation.get("source") or "") else 0.0,
                )
                for source in ("db", "cache", "stale-cache", "error")
            ],
        )

        health_fields = (
            ("linked_accounts", "Connected MT5 accounts seen by watchdog."),
            ("active_bot_runs", "Desired running bot deployments according to watchdog."),
            ("running_bot_runs", "Currently running bot deployments according to watchdog."),
            ("waiting_bot_runs", "Transitional bot deployments according to watchdog."),
            ("error_bot_runs", "Failed bot deployments according to watchdog."),
            ("stale_heartbeat_runs", "Deployments with stale heartbeats according to watchdog."),
            ("recent_event_count", "Recent execution event count according to watchdog."),
            ("runtime_heartbeat_grace_sec", "Grace period used by watchdog for runtime heartbeat freshness."),
        )
        for field_name, help_text in health_fields:
            _append_metric_family(
                lines,
                metric=f"spider_backend_{field_name}",
                help_text=help_text,
                samples=[(None, _safe_float(service_health.get(field_name)))],
            )

        summary_sections = (
            (
                "spider_control_plane_runner_nodes",
                "Counts of runner nodes by status.",
                summary.get("runners"),
                ("total_runners", "online_runners", "degraded_runners", "offline_runners", "stale_runners"),
            ),
            (
                "spider_control_plane_bot_deployments",
                "Counts of bot deployments by runtime state.",
                summary.get("deployments"),
                (
                    "total_deployments",
                    "running_deployments",
                    "desired_running_deployments",
                    "failed_deployments",
                    "transitional_deployments",
                    "stale_deployments",
                ),
            ),
            (
                "spider_control_plane_runner_slots",
                "Counts of runner slots by state.",
                summary.get("slots"),
                ("total_slots", "ready_slots", "allocated_slots", "degraded_slots", "broken_slots"),
            ),
            (
                "spider_control_plane_broker_accounts",
                "Counts of broker accounts by connectivity state.",
                summary.get("accounts"),
                ("total_accounts", "connected_accounts", "pending_accounts"),
            ),
        )
        for metric_name, help_text, payload, fields in summary_sections:
            payload_dict = dict(payload or {})
            _append_metric_family(
                lines,
                metric=metric_name,
                help_text=help_text,
                samples=[
                    ({"state": field_name}, _safe_float(payload_dict.get(field_name)))
                    for field_name in fields
                ],
            )

        events = dict(summary.get("events") or {})
        thresholds = dict(summary.get("thresholds") or {})
        _append_metric_family(
            lines,
            metric="spider_control_plane_recent_execution_events",
            help_text="Recent execution events projected into the control plane over the last 30 minutes.",
            samples=[(None, _safe_float(events.get("recent_event_count")))],
        )
        _append_metric_family(
            lines,
            metric="spider_control_plane_last_runtime_activity_timestamp_seconds",
            help_text="Unix timestamp of the most recent control-plane runtime activity.",
            samples=[(None, _safe_float(events.get("last_runtime_activity_ts")))],
        )
        _append_metric_family(
            lines,
            metric="spider_control_plane_stale_threshold_seconds",
            help_text="Configured stale thresholds used by the control plane.",
            samples=[
                ({"kind": "runner"}, _safe_float(thresholds.get("runner_stale_sec"))),
                ({"kind": "deployment"}, _safe_float(thresholds.get("deployment_stale_sec"))),
            ],
        )

        if reconciler:
            _append_metric_family(
                lines,
                metric="spider_control_plane_reconciler_runs_total",
                help_text="Number of reconcile iterations completed by the local control-plane reconciler.",
                samples=[(None, _safe_float(reconciler.get("run_count")))],
            )
            _append_metric_family(
                lines,
                metric="spider_control_plane_reconciler_last_success_timestamp_seconds",
                help_text="Unix timestamp of the most recent successful reconcile iteration on this worker.",
                samples=[(None, _safe_float(reconciler.get("last_success_at")))],
            )
            _append_metric_family(
                lines,
                metric="spider_control_plane_reconciler_last_error",
                help_text="Whether the local control-plane reconciler recorded a recent error.",
                samples=[(None, _bool_gauge(reconciler.get("last_error")))],
            )
            last_result = dict(reconciler.get("last_result") or {})
            if last_result:
                _append_metric_family(
                    lines,
                    metric="spider_control_plane_reconciler_last_result",
                    help_text="Counts updated during the most recent reconcile iteration on this worker.",
                    samples=[
                        ({"kind": key}, _safe_float(value))
                        for key, value in sorted(last_result.items())
                    ],
                )

        if command_delivery:
            command_delivery_lag_sec = 0.0
            last_success_at = _safe_int(command_delivery.get("last_success_at"))
            if last_success_at > 0:
                command_delivery_lag_sec = max(0.0, float(int(time.time()) - last_success_at))
            _append_metric_family(
                lines,
                metric="spider_command_delivery_replay_runs_total",
                help_text="Number of command delivery replay iterations completed by this worker.",
                samples=[(None, _safe_float(command_delivery.get("run_count")))],
            )
            _append_metric_family(
                lines,
                metric="spider_command_delivery_replay_last_success_timestamp_seconds",
                help_text="Unix timestamp of the most recent successful command delivery replay iteration.",
                samples=[(None, _safe_float(command_delivery.get("last_success_at")))],
            )
            _append_metric_family(
                lines,
                metric="spider_command_delivery_replay_last_error",
                help_text="Whether the command delivery replay loop recorded a recent error.",
                samples=[(None, _bool_gauge(command_delivery.get("last_error")))],
            )
            _append_metric_family(
                lines,
                metric="spider_command_delivery_reconciler_last_success_age_seconds",
                help_text="Age in seconds since the command delivery reconciler last succeeded.",
                samples=[(None, command_delivery_lag_sec)],
            )
            _append_metric_family(
                lines,
                metric="spider_command_delivery_backlog_count",
                help_text="Pending or queued START/STOP commands missing redis_stream_id.",
                samples=[(None, command_delivery_backlog_count)],
            )
            _append_metric_family(
                lines,
                metric="cntx_command_delivery_backlog_count",
                help_text="CNTx alias: pending or queued START/STOP commands missing redis_stream_id.",
                samples=[(None, command_delivery_backlog_count)],
            )
            last_result = dict(command_delivery.get("last_result") or {})
            if last_result:
                _append_metric_family(
                    lines,
                    metric="spider_command_delivery_replay_last_result",
                    help_text="Counts from the most recent command delivery replay iteration.",
                    samples=[
                        ({"kind": key}, _safe_float(value))
                        for key, value in sorted(last_result.items())
                    ],
                )
                _append_metric_family(
                    lines,
                    metric="cntx_command_delivery_replay_last_result",
                    help_text="CNTx alias: counts from the most recent command delivery replay iteration.",
                    samples=[
                        ({"kind": key}, _safe_float(value))
                        for key, value in sorted(last_result.items())
                    ],
                )

        _append_metric_family(
            lines,
            metric="spider_backend_check_status",
            help_text="Health/readiness check results for this backend worker.",
            samples=[
                (
                    {
                        "check": check_name,
                        "critical": str(bool(check_payload.get("critical"))).lower(),
                    },
                    _bool_gauge(check_payload.get("ok")),
                )
                for check_name, check_payload in sorted(checks.items())
            ],
        )

        return "\n".join(lines) + "\n"
