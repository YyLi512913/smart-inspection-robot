"""Independent command delivery and task timeout loops."""
import asyncio
import logging

from . import delivery, state_machine, operations

logger = logging.getLogger(__name__)


async def delivery_loop():
    while True:
        try:
            await asyncio.to_thread(delivery.flush_pending)
            from .alerts import settle_rechecks
            await asyncio.to_thread(settle_rechecks)
            operations.beat("delivery")
        except Exception:
            operations.beat("delivery",False)
            logger.exception("Command outbox scan failed")
        await asyncio.sleep(1)


async def watchdog_loop():
    operations.beat("watchdog")
    while True:
        await asyncio.sleep(10)
        try:
            await asyncio.to_thread(state_machine.check_timeouts)
            operations.beat("watchdog")
        except Exception:
            operations.beat("watchdog",False)
            logger.exception("Task timeout scan failed")
