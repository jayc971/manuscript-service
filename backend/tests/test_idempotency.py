"""
Test: running the pipeline twice on the same manuscript sends exactly one notification.

The idempotency guarantee lives in _idempotent_notify: before calling the notify
service it checks notification_keys for the key "{manuscript_id}:received". On the
first run the key doesn't exist, so the service is called and the key is stored.
On the second run the key already exists, so the service is skipped.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from app.workers.tasks import process_manuscript
from app.models.manuscript import NotificationKey, ManuscriptStatus


async def test_pipeline_twice_notifies_once(manuscript_fixture):
    manuscript_id, fresh_session_factory, db_url = manuscript_fixture

    notify_call_count = 0

    def make_mock_httpx_client(**kwargs):
        """Returns a mock that behaves as an async context manager."""
        mock = MagicMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        async def mock_post(url, **kw):
            nonlocal notify_call_count
            if "notify" in url:
                notify_call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"score": 42}
            resp.headers = {}
            return resp

        mock.post = mock_post
        return mock

    with patch("app.workers.tasks._make_session", side_effect=fresh_session_factory), \
         patch("app.workers.tasks.httpx.AsyncClient", side_effect=make_mock_httpx_client):

        await process_manuscript({}, manuscript_id)  # first run: all stages execute
        await process_manuscript({}, manuscript_id)  # second run: all stages already done

    # Verify: only one notification key was stored
    engine = create_async_engine(db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        result = await db.execute(select(NotificationKey))
        keys = result.scalars().all()
        result2 = await db.execute(
            select(NotificationKey).where(NotificationKey.key == f"{manuscript_id}:received")
        )
        received_key = result2.scalar_one_or_none()
    await engine.dispose()

    assert notify_call_count == 1, (
        f"Notify service was called {notify_call_count} time(s); expected exactly 1. "
        "The idempotency key check is not working correctly."
    )
    assert len(keys) == 1, (
        f"Found {len(keys)} notification key row(s); expected exactly 1."
    )
    assert received_key is not None, (
        f"No notification key stored for '{manuscript_id}:received'."
    )
