"""Embedded cron scheduler (APScheduler) — the in-process replacement for OS cron.

One BackgroundScheduler, one job ("sync_tick"). Frequency is either every N minutes
or a 5-field cron expression. Reschedule/pause/resume happen live from the UI.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger("agent")

JOB_ID = "sync_tick"
_scheduler: BackgroundScheduler | None = None


def _job():
    # Imported lazily so the scheduler module stays import-safe before Django is ready.
    from .engine import run_scheduled

    try:
        run_scheduled()
    except Exception as exc:  # never let a job kill the scheduler thread
        logger.error("scheduled sync errored: %s", exc)


def _trigger_from_settings(settings):
    if settings.cron_expr.strip():
        return CronTrigger.from_crontab(settings.cron_expr.strip())
    minutes = max(1, settings.sync_interval_minutes or 15)
    return IntervalTrigger(minutes=minutes)


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    from ..models import AgentSettings

    settings = AgentSettings.get_solo()
    sched = BackgroundScheduler(timezone="Asia/Kolkata")
    sched.add_job(
        _job,
        trigger=_trigger_from_settings(settings),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
        replace_existing=True,
    )
    sched.start()
    if settings.scheduler_paused:
        sched.pause_job(JOB_ID)
    _scheduler = sched
    logger.info("scheduler started (paused=%s)", settings.scheduler_paused)
    return sched


def reschedule(settings) -> None:
    sched = _scheduler
    if sched is None:
        return
    sched.reschedule_job(JOB_ID, trigger=_trigger_from_settings(settings))
    if settings.scheduler_paused:
        sched.pause_job(JOB_ID)
    else:
        sched.resume_job(JOB_ID)


def set_paused(paused: bool) -> None:
    sched = _scheduler
    if sched is None:
        return
    if paused:
        sched.pause_job(JOB_ID)
    else:
        sched.resume_job(JOB_ID)


def next_run_time():
    sched = _scheduler
    if sched is None:
        return None
    job = sched.get_job(JOB_ID)
    return getattr(job, "next_run_time", None) if job else None
