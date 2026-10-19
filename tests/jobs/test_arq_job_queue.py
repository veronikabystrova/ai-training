from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import Settings
from app.jobs.queue import ArqJobQueue
from app.main import create_app


async def test_enqueue_puts_the_named_job_with_kwargs_on_the_arq_queue(redis_url: str) -> None:
    queue = ArqJobQueue(redis_url)
    await queue.enqueue("send_welcome_email", user_id="u-1")
    await queue.aclose()

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        [queued] = await pool.queued_jobs()
        assert (queued.function, queued.kwargs) == ("send_welcome_email", {"user_id": "u-1"})
    finally:
        await pool.aclose()


async def test_create_app_wires_the_arq_queue_without_connecting(settings: Settings) -> None:
    app = create_app(settings)
    assert isinstance(app.state.job_queue, ArqJobQueue)
