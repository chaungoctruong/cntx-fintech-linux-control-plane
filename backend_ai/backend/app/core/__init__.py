# -*- coding: utf-8 -*-
"""
Core shared infrastructure: Redis client helpers.
No business logic; used by routes, services, and event processor.
"""
from app.core.redis_client import get_redis, close_redis, is_readonly_redis_error

__all__ = [
    "get_redis",
    "close_redis",
    "is_readonly_redis_error",
]
