# -*- coding: utf-8 -*-
"""RabbitMQ consumer entrypoints used by hubbot runtime."""

from app.consumer.rabbitmq_commands import consume_rabbitmq_commands

__all__ = ["consume_rabbitmq_commands"]
