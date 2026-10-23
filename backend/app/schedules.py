"""Create the daily summary schedule. Run once: python -m app.schedules

Safe to run again. If the schedule exists, nothing changes. To change the time,
delete the schedule in the Temporal UI (or CLI) and run this again.
"""

import asyncio
import logging

from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)

from app.config import get_settings
from app.worker import connect_with_retry
from app.workflows.workflows import DailySummaryWorkflow

SCHEDULE_ID = "noteloop-daily-summary"


async def main() -> None:
    settings = get_settings()
    client = await connect_with_retry(settings.temporal_address, settings.temporal_namespace)
    try:
        await client.create_schedule(
            SCHEDULE_ID,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    DailySummaryWorkflow.run,
                    id="daily-summary",
                    task_queue=settings.temporal_task_queue,
                ),
                spec=ScheduleSpec(
                    cron_expressions=[settings.summary_cron],
                    time_zone_name=settings.summary_timezone,
                ),
                # If a run is still going when the next one is due, skip the new one.
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            ),
        )
    except ScheduleAlreadyRunningError:
        print(f"Schedule {SCHEDULE_ID} already exists. Nothing changed.")
        return
    print(f"Created schedule {SCHEDULE_ID}: {settings.summary_cron} ({settings.summary_timezone})")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
