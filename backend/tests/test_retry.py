"""
Test: when the similarity service always fails, the pipeline retries then dead-letters.

_call_with_retry attempts the call up to max_attempts (4) times with increasing
delays. After all attempts are exhausted, the stage is marked error with the last
error message, and the manuscript status becomes error.

asyncio.sleep is patched to avoid waiting real seconds.
"""
from unittest.mock import AsyncMock, MagicMock, patch, call
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from app.workers.tasks import process_manuscript
from app.models.manuscript import ManuscriptStatus, StageStatus, ManuscriptStage


async def test_similarity_failure_dead_letters_manuscript(manuscript_fixture):
    manuscript_id, fresh_session_factory, db_url = manuscript_fixture

    similarity_call_count = 0

    def make_mock_httpx_client(**kwargs):
        mock = MagicMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)

        async def mock_post(url, **kw):
            nonlocal similarity_call_count
            resp = MagicMock()
            if "similarity" in url:
                similarity_call_count += 1
                resp.status_code = 500
                resp.headers = {}
            else:
                resp.status_code = 200
                resp.json.return_value = {"score": 10}
                resp.headers = {}
            return resp

        mock.post = mock_post
        return mock

    with patch("app.workers.tasks._make_session", side_effect=fresh_session_factory), \
         patch("app.workers.tasks.httpx.AsyncClient", side_effect=make_mock_httpx_client), \
         patch("app.workers.tasks.asyncio.sleep", new_callable=AsyncMock):

        await process_manuscript({}, manuscript_id)

    engine = create_async_engine(db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        result = await db.execute(
            select(ManuscriptStage).where(
                ManuscriptStage.manuscript_id == manuscript_id,
                ManuscriptStage.name == "similarity_checked",
            )
        )
        sim_stage = result.scalar_one()

        result2 = await db.execute(
            select(ManuscriptStage).where(
                ManuscriptStage.manuscript_id == manuscript_id,
                ManuscriptStage.name == "validated",
            )
        )
        validated_stage = result2.scalar_one()

        from app.models.manuscript import Manuscript
        result3 = await db.execute(
            select(Manuscript).where(Manuscript.id == manuscript_id)
        )
        manuscript = result3.scalar_one()
    await engine.dispose()

    assert sim_stage.status == StageStatus.error, (
        f"similarity_checked stage should be 'error', got '{sim_stage.status}'"
    )
    assert sim_stage.last_error is not None, (
        "similarity_checked stage should record the error message"
    )
    assert manuscript.status == ManuscriptStatus.error, (
        f"Manuscript should be 'error' after stage failure, got '{manuscript.status}'"
    )
    assert validated_stage.status == StageStatus.done, (
        "Earlier stages (validated) should still show done"
    )
    assert similarity_call_count == 4, (
        f"Similarity service should be retried 4 times (max_attempts), got {similarity_call_count}"
    )
