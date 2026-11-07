"""Celery worker. Start with:  celery -A app.tasks worker -l info -c 4"""
from celery import Celery, Task

from .core.config import settings
from .core.errors import TransientError

celery_app = Celery("docintel", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, task_track_started=True,
                       task_time_limit=3600, task_soft_time_limit=3300)


class _Base(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):  # retries exhausted or non-retryable
        from . import services
        services.mark_failed(args[0], f"{type(exc).__name__}: {exc}")


@celery_app.task(bind=True, base=_Base, autoretry_for=(TransientError,), retry_backoff=5, retry_backoff_max=120,
                 retry_jitter=True, max_retries=4)
def process_document_task(self, doc_id: str):
    from . import services
    services.process_document(doc_id, attempt=self.request.retries)
