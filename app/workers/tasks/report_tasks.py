from app.workers.celery_app import celery_app


@celery_app.task(name="reports.placeholder")
def placeholder_report_task() -> str:
    return "ok"
