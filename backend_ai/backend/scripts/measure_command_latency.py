#!/usr/bin/env python3
"""Đo độ trễ thực tế của lệnh START_BOT / STOP_BOT.

Đọc structured logs (`logs/backend/api*.jsonl`) và bắt cặp:
  - dispatch `event=runner.command.dispatch.queued`  → BOT_STARTED event
  - dispatch `event=runner.command.dispatch.queued`  → BOT_STOPPED event

Cùng dispatch + drop cũng được đếm để biết tỉ lệ command bị drop bởi guard
desired_state / intent_seq (sau khi deploy bản fix start_replacement).

Cách dùng
---------
    # Mặc định đọc logs/backend/api*.jsonl từ project root
    python3 backend_ai/backend/scripts/measure_command_latency.py

    # Chỉ định file/khoảng thời gian (ISO, mặc định toàn bộ file)
    python3 measure_command_latency.py --log logs/backend/api.jsonl \\
        --since 2026-05-11T00:00:00 --until 2026-05-12T00:00:00

    # Filter theo deployment hoặc account
    python3 measure_command_latency.py --account 7 --deployment 142

Đầu ra: bảng pairing + thống kê min / p50 / avg / p95 / max + tỉ lệ
sent vs dropped vs orphan-dispatch (không có event ack tương ứng).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT_LOG_GLOB = os.path.join(PROJECT_ROOT, "logs", "backend", "api*.jsonl")
EVENT_TYPE_RE = re.compile(r"type=(\w+) .*? deployment=(\d+)")


@dataclass
class Dispatch:
    ts: datetime
    deployment_id: int
    account_id: int | None
    command_id: str
    command_type: str
    trace_id: str | None
    dispatch_decision: str  # "sent" or "dropped"
    drop_reason: str | None
    intent_seq: int | None


@dataclass
class RunnerAck:
    ts: datetime
    deployment_id: int
    event_type: str


@dataclass
class PairBucket:
    deltas_sec: list[float] = field(default_factory=list)
    rows: list[tuple[Dispatch, RunnerAck]] = field(default_factory=list)


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def load_dispatches_and_acks(
    log_paths: list[str],
    *,
    since: datetime | None,
    until: datetime | None,
    account_id: int | None,
    deployment_id: int | None,
) -> tuple[list[Dispatch], list[RunnerAck]]:
    dispatches: list[Dispatch] = []
    acks: list[RunnerAck] = []
    for path in log_paths:
        try:
            with open(path, "r") as fh:
                for line in fh:
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    ts = parse_iso(record.get("iso", "") or "")
                    if ts is None:
                        continue
                    if since and ts < since:
                        continue
                    if until and ts > until:
                        continue
                    logger = record.get("logger") or ""
                    event = record.get("event") or ""
                    dep = record.get("deployment_id")
                    acct = record.get("account_id")
                    if deployment_id is not None and dep != deployment_id:
                        continue
                    if account_id is not None and acct != account_id:
                        continue
                    if logger == "runner.command.dispatch" and event in {
                        "runner.command.dispatch.queued",
                        "runner.command.dispatch.dropped",
                    }:
                        ct = str(record.get("command_type") or "").upper()
                        if ct not in {"START_BOT", "STOP_BOT"}:
                            continue
                        try:
                            dep_i = int(dep) if dep is not None else 0
                        except Exception:
                            dep_i = 0
                        try:
                            acct_i = int(acct) if acct is not None else None
                        except Exception:
                            acct_i = None
                        intent_seq_raw = record.get("intent_seq")
                        try:
                            intent_seq_i = int(intent_seq_raw) if intent_seq_raw is not None else None
                        except Exception:
                            intent_seq_i = None
                        dispatches.append(
                            Dispatch(
                                ts=ts,
                                deployment_id=dep_i,
                                account_id=acct_i,
                                command_id=str(record.get("command_id") or ""),
                                command_type=ct,
                                trace_id=record.get("trace_id"),
                                dispatch_decision=str(record.get("dispatch_decision") or ("dropped" if event.endswith("dropped") else "sent")),
                                drop_reason=record.get("drop_reason"),
                                intent_seq=intent_seq_i,
                            )
                        )
                    elif logger == "runner.event.ingest":
                        msg = record.get("msg") or ""
                        match = EVENT_TYPE_RE.search(msg)
                        if not match:
                            continue
                        etype = match.group(1)
                        dep_i = int(match.group(2))
                        if deployment_id is not None and dep_i != deployment_id:
                            continue
                        if etype not in {
                            "BOT_STARTED",
                            "BOT_STOPPED",
                            "SIGNAL_EXECUTOR_PREPARING",
                            "SIGNAL_EXECUTOR_READY",
                            "SIGNAL_EXECUTOR_STOPPED",
                        }:
                            continue
                        acks.append(RunnerAck(ts=ts, deployment_id=dep_i, event_type=etype))
        except FileNotFoundError:
            continue
    return dispatches, acks


def pair_dispatch_to_ack(
    dispatches: list[Dispatch],
    acks: list[RunnerAck],
    *,
    dispatch_type: str,
    target_event: str,
    verbose: bool,
) -> PairBucket:
    bucket = PairBucket()
    available = sorted(
        (a for a in acks if a.event_type == target_event), key=lambda x: x.ts
    )
    used: set[int] = set()
    for disp in sorted(
        (d for d in dispatches if d.command_type == dispatch_type and d.dispatch_decision == "sent"),
        key=lambda x: x.ts,
    ):
        for idx, ack in enumerate(available):
            if idx in used:
                continue
            if ack.deployment_id != disp.deployment_id:
                continue
            if ack.ts < disp.ts:
                continue
            used.add(idx)
            delta = (ack.ts - disp.ts).total_seconds()
            bucket.deltas_sec.append(delta)
            bucket.rows.append((disp, ack))
            break
    return bucket


def percentile(sorted_arr: list[float], pct: float) -> float:
    if not sorted_arr:
        return float("nan")
    if len(sorted_arr) == 1:
        return sorted_arr[0]
    idx = int(round(pct * (len(sorted_arr) - 1)))
    return sorted_arr[idx]


def print_pair_stats(label: str, bucket: PairBucket, *, verbose: bool) -> None:
    print(f"\n=== {label} (n={len(bucket.deltas_sec)}) ===")
    if not bucket.deltas_sec:
        print("  (no matched pairs)")
        return
    arr = sorted(bucket.deltas_sec)
    n = len(arr)
    print(
        f"  min={arr[0]:6.2f}s  p50={percentile(arr,0.5):6.2f}s  "
        f"avg={statistics.fmean(arr):6.2f}s  p95={percentile(arr,0.95):6.2f}s  max={arr[-1]:6.2f}s"
    )
    if verbose:
        for disp, ack in bucket.rows:
            print(
                f"  dep={disp.deployment_id:>4}  "
                f"{disp.ts.strftime('%Y-%m-%d %H:%M:%S')} → {ack.ts.strftime('%H:%M:%S')}  "
                f"Δ={(ack.ts - disp.ts).total_seconds():6.2f}s  "
                f"cid={disp.command_id[:8]}  trace={(disp.trace_id or '?')[:24]}"
            )


def summarize_decisions(dispatches: list[Dispatch]) -> None:
    print("\n=== dispatch_decision tally ===")
    counts: dict[tuple[str, str, str | None], int] = {}
    for d in dispatches:
        key = (d.command_type, d.dispatch_decision, d.drop_reason)
        counts[key] = counts.get(key, 0) + 1
    for (ct, decision, reason), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        suffix = f" drop_reason={reason}" if decision == "dropped" else ""
        print(f"  {n:5d}  {ct:9s} {decision:7s}{suffix}")


def summarize_orphans(dispatches: list[Dispatch], acks: list[RunnerAck]) -> None:
    ack_by_dep: dict[int, list[RunnerAck]] = {}
    for ack in acks:
        ack_by_dep.setdefault(ack.deployment_id, []).append(ack)
    sent_starts = [d for d in dispatches if d.command_type == "START_BOT" and d.dispatch_decision == "sent"]
    sent_stops = [d for d in dispatches if d.command_type == "STOP_BOT" and d.dispatch_decision == "sent"]

    def count_orphans(cmds: list[Dispatch], target_event: str) -> int:
        orphans = 0
        for d in cmds:
            relevant = ack_by_dep.get(d.deployment_id, [])
            matched = any(a.event_type == target_event and a.ts >= d.ts for a in relevant)
            if not matched:
                orphans += 1
        return orphans

    print("\n=== orphan dispatches (sent but no matching ack) ===")
    if sent_starts:
        n = count_orphans(sent_starts, "BOT_STARTED")
        print(f"  START_BOT: {n}/{len(sent_starts)} ({100*n/len(sent_starts):.1f}%)")
    if sent_stops:
        n = count_orphans(sent_stops, "BOT_STOPPED")
        print(f"  STOP_BOT : {n}/{len(sent_stops)} ({100*n/len(sent_stops):.1f}%)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        action="append",
        help="Đường dẫn file JSONL (có thể truyền nhiều lần). Mặc định: logs/backend/api*.jsonl",
    )
    parser.add_argument("--since", help="ISO timestamp (vd 2026-05-11T00:00:00)")
    parser.add_argument("--until", help="ISO timestamp")
    parser.add_argument("--account", type=int, help="Filter account_id")
    parser.add_argument("--deployment", type=int, help="Filter deployment_id")
    parser.add_argument("-v", "--verbose", action="store_true", help="In từng cặp dispatch→ack")
    args = parser.parse_args()

    log_paths: list[str] = []
    if args.log:
        for entry in args.log:
            log_paths.extend(glob.glob(entry) if any(ch in entry for ch in "*?[") else [entry])
    else:
        log_paths = sorted(glob.glob(DEFAULT_LOG_GLOB))
    if not log_paths:
        print(f"No log files matched. Searched: {DEFAULT_LOG_GLOB}", file=sys.stderr)
        return 1
    print(f"Reading {len(log_paths)} file(s):")
    for p in log_paths:
        print(f"  - {p}")

    since = parse_iso(args.since) if args.since else None
    until = parse_iso(args.until) if args.until else None

    dispatches, acks = load_dispatches_and_acks(
        log_paths,
        since=since,
        until=until,
        account_id=args.account,
        deployment_id=args.deployment,
    )
    print(f"\nLoaded {len(dispatches)} dispatches, {len(acks)} runner ack events.")

    summarize_decisions(dispatches)

    pair_start = pair_dispatch_to_ack(
        dispatches, acks, dispatch_type="START_BOT", target_event="BOT_STARTED", verbose=args.verbose
    )
    pair_stop = pair_dispatch_to_ack(
        dispatches, acks, dispatch_type="STOP_BOT", target_event="BOT_STOPPED", verbose=args.verbose
    )
    print_pair_stats("START_BOT → BOT_STARTED", pair_start, verbose=args.verbose)
    print_pair_stats("STOP_BOT  → BOT_STOPPED", pair_stop, verbose=args.verbose)

    # Intermediate sub-states (signal executor preparing/ready/stopped) help
    # benchmark MT5 cold start phases vs total dispatch->run time.
    pair_prep = pair_dispatch_to_ack(
        dispatches, acks, dispatch_type="START_BOT", target_event="SIGNAL_EXECUTOR_PREPARING", verbose=False
    )
    pair_ready = pair_dispatch_to_ack(
        dispatches, acks, dispatch_type="START_BOT", target_event="SIGNAL_EXECUTOR_READY", verbose=False
    )
    print_pair_stats("START_BOT → SIGNAL_EXECUTOR_PREPARING", pair_prep, verbose=False)
    print_pair_stats("START_BOT → SIGNAL_EXECUTOR_READY", pair_ready, verbose=False)

    summarize_orphans(dispatches, acks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
