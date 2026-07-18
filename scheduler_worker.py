"""Lightweight scheduler worker for v6.7.
Run with: python scheduler_worker.py
It checks scheduled message tasks, channel posts, and paused join jobs every SCHEDULER_INTERVAL_SECONDS seconds.
"""
from __future__ import annotations

import os
import time

from app import create_app
from app.extensions import db
from app.models import ChannelPost, ChannelSettings, MessageTask, JoinJob, utcnow
from worker_tasks import execute_message_task, publish_channel_post, execute_join_job


def run_once(app):
    with app.app_context():
        now = utcnow()
        tasks = MessageTask.query.filter(MessageTask.status == "scheduled", MessageTask.schedule_time <= now).limit(50).all()
        for task in tasks:
            task.status = "queued"
        posts = ChannelPost.query.filter(ChannelPost.status == "scheduled", ChannelPost.scheduled_at <= now).limit(50).all()
        for post in posts:
            post.status = "queued"
        db.session.commit()

        for task in tasks:
            execute_message_task(task.id)

        join_jobs = JoinJob.query.filter(
            JoinJob.status == "paused_rate_limit",
            JoinJob.auto_resume.is_(True),
            JoinJob.rate_limited_until.isnot(None),
            JoinJob.rate_limited_until <= now,
        ).limit(20).all()
        for job in join_jobs:
            job.status = "queued"
            job.stopped_reason = None
            job.rate_limited_until = None
        db.session.commit()
        for job in join_jobs:
            execute_join_job(job.id)

        for post in posts:
            settings = ChannelSettings.query.filter_by(owner_id=post.owner_id).first()
            if not settings or not settings.publisher_account_id or not settings.channel_ref:
                post.status = "failed"
                post.last_error = "إعدادات القناة أو حساب النشر غير مكتملة"
                db.session.commit()
                continue
            publish_channel_post(post.id, settings.publisher_account_id, settings.channel_ref)


def main():
    app = create_app()
    interval = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "60"))
    print(f"Scheduler started. interval={interval}s")
    while True:
        try:
            run_once(app)
        except Exception as exc:
            print("Scheduler error:", exc)
        time.sleep(interval)


if __name__ == "__main__":
    main()
