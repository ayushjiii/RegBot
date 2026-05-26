import os
from celery import Celery
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

app = Celery(
    "agent_tasks",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=300,
    task_time_limit=360,
)


@app.task(bind=True, name="tasks.fill_form", max_retries=2, default_retry_delay=15)
def fill_form_task(self, profile_id: int, target_url: str) -> dict:
    from ai_agent_test import execute_form_filler_agent

    try:
        result = execute_form_filler_agent(
            profile_id=profile_id,
            target_url=target_url,
            headless=True,
            submit_form=False,
        )

        if result["status"] == "FAILED" and self.request.retries < self.max_retries:
            raise self.retry(exc=RuntimeError(result.get("error_detail", "Task failed internally.")))

        return result

    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

        return {
            "session_id": None,
            "status": "FAILED",
            "error_detail": str(exc),
            "fields_found": 0,
            "fields_filled": 0,
            "fields_skipped": 0,
        }