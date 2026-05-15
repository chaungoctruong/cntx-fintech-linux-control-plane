from __future__ import annotations

from typing import Any, Optional

from app.core.redis_client import get_redis_write
from app.runner.protocol import QueueEnvelope, decode_queue_payload


class MT5RunnerRedisQueueConsumer:
    def __init__(self, *, runner_id: str, redis_client: Optional[Any] = None) -> None:
        self._runner_id = str(runner_id or "").strip()
        if not self._runner_id:
            raise ValueError("runner_id_required")
        self._redis_client = redis_client
        self._command_queue = f"mt5:runner:{self._runner_id}:commands"
        self._command_processing_queue = f"{self._command_queue}:processing"

    @property
    def command_queue(self) -> str:
        return self._command_queue

    @property
    def command_processing_queue(self) -> str:
        return self._command_processing_queue

    async def _redis(self) -> Any:
        if self._redis_client is not None:
            return self._redis_client
        redis = await get_redis_write(decode_responses=True)
        if redis is None:
            raise RuntimeError("redis_unavailable")
        return redis

    async def _blocking_dequeue_to_processing(
        self,
        *,
        source_queue: str,
        processing_queue: str,
        timeout_sec: int,
    ) -> Optional[QueueEnvelope]:
        redis = await self._redis()
        raw = await redis.brpoplpush(
            source_queue,
            processing_queue,
            timeout=max(1, int(timeout_sec)),
        )
        if raw is None:
            return None
        envelope = decode_queue_payload(source_queue, str(raw or ""))
        return envelope.model_copy(update={"processing_queue_name": processing_queue})

    async def pop_next_command(self, *, timeout_sec: int = 5) -> Optional[QueueEnvelope]:
        return await self._blocking_dequeue_to_processing(
            source_queue=self._command_queue,
            processing_queue=self._command_processing_queue,
            timeout_sec=timeout_sec,
        )

    async def pop_next(self, *, timeout_sec: int = 5) -> Optional[QueueEnvelope]:
        return await self.pop_next_command(timeout_sec=timeout_sec)

    async def ack(self, envelope: QueueEnvelope) -> None:
        processing_queue = str(envelope.processing_queue_name or "").strip()
        if not processing_queue:
            return
        redis = await self._redis()
        await redis.lrem(processing_queue, 1, envelope.raw)

    async def requeue(self, envelope: QueueEnvelope) -> None:
        processing_queue = str(envelope.processing_queue_name or "").strip()
        source_queue = str(envelope.queue_name or "").strip()
        if not processing_queue or not source_queue:
            return
        redis = await self._redis()
        await redis.lrem(processing_queue, 1, envelope.raw)
        await redis.rpush(source_queue, envelope.raw)

    async def recover_inflight(self) -> dict[str, int]:
        redis = await self._redis()
        recovered = {"command": 0}
        while True:
            raw = await redis.rpoplpush(self._command_processing_queue, self._command_queue)
            if raw is None:
                break
            recovered["command"] += 1
        return recovered
