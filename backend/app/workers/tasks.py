import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import httpx
from app.core.config import settings
from app.models.user import User  # noqa: F401 — must import to register users table for FK resolution
from app.models.manuscript import Manuscript, ManuscriptStage, ManuscriptStatus, StageStatus, NotificationKey

logger = logging.getLogger(__name__)

similarity_semaphore = asyncio.Semaphore(4)

def _make_session():
    engine = create_async_engine(settings.database_url)
    return async_sessionmaker(engine, expire_on_commit=False), engine

async def _get_stage(db, manuscript_id: str, name: str) -> ManuscriptStage:
    result = await db.execute(
        select(ManuscriptStage).where(
            ManuscriptStage.manuscript_id == manuscript_id,
            ManuscriptStage.name == name,
        )
    )
    return result.scalar_one()

async def _mark_stage(db, stage: ManuscriptStage, status: StageStatus, error: str = None):
    stage.status = status
    if status == StageStatus.running:
        stage.started_at = datetime.now(timezone.utc)
        stage.attempts += 1
    elif status in (StageStatus.done, StageStatus.error):
        stage.finished_at = datetime.now(timezone.utc)
    if error:
        stage.last_error = error
    await db.commit()

async def _call_with_retry(fn, max_attempts=4):
    delay = 2
    for attempt in range(max_attempts):
        try:
            return await fn()
        except Exception as e:
            if attempt == max_attempts - 1:
                raise
            logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

async def _idempotent_notify(db, key: str, payload: dict):
    result = await db.execute(select(NotificationKey).where(NotificationKey.key == key))
    if result.scalar_one_or_none():
        logger.info(f"Notification already sent for key {key}, skipping")
        return

    async def do_notify():
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                settings.notify_service_url,
                json=payload,
                headers={"Idempotency-Key": key},
            )
            if response.status_code >= 500:
                raise Exception(f"Notify service error: {response.status_code}")

    await _call_with_retry(do_notify)
    db.add(NotificationKey(key=key))
    await db.commit()

async def process_manuscript(ctx, manuscript_id: str):
    logger.info(f"[{manuscript_id}] Starting pipeline")
    Session, engine = _make_session()

    try:
        async with Session() as db:
            result = await db.execute(
                select(Manuscript).options(selectinload(Manuscript.stages)).where(Manuscript.id == manuscript_id)
            )
            manuscript = result.scalar_one()
            manuscript.status = ManuscriptStatus.processing
            await db.commit()

        # Stage 1: validate
        async with Session() as db:
            stage = await _get_stage(db, manuscript_id, "validated")
            if stage.status == StageStatus.done:
                pass
            else:
                await _mark_stage(db, stage, StageStatus.running)
                try:
                    with open(manuscript.file_path, "rb") as f:
                        content = f.read()
                    if len(content) == 0:
                        raise ValueError("File is empty")
                    content.decode("utf-8")
                    await _mark_stage(db, stage, StageStatus.done)
                except Exception as e:
                    await _mark_stage(db, stage, StageStatus.error, str(e))
                    await _fail_manuscript(db, manuscript_id, str(e))
                    return

        # Stage 2: extract text
        async with Session() as db:
            stage = await _get_stage(db, manuscript_id, "text_extracted")
            if stage.status != StageStatus.done:
                await _mark_stage(db, stage, StageStatus.running)
                try:
                    with open(manuscript.file_path, "r", encoding="utf-8") as f:
                        text = f.read()
                    words = text.split()
                    word_count = len(words)
                    result = await db.execute(select(Manuscript).where(Manuscript.id == manuscript_id))
                    m = result.scalar_one()
                    m.word_count = word_count
                    await _mark_stage(db, stage, StageStatus.done)
                except Exception as e:
                    await _mark_stage(db, stage, StageStatus.error, str(e))
                    await _fail_manuscript(db, manuscript_id, str(e))
                    return

        # Stage 3: similarity check
        async with Session() as db:
            stage = await _get_stage(db, manuscript_id, "similarity_checked")
            if stage.status != StageStatus.done:
                await _mark_stage(db, stage, StageStatus.running)
                try:
                    with open(manuscript.file_path, "r", encoding="utf-8") as f:
                        text = f.read()

                    async def do_similarity():
                        async with similarity_semaphore:
                            async with httpx.AsyncClient(timeout=15) as client:
                                response = await client.post(
                                    settings.similarity_service_url,
                                    json={"manuscript_id": manuscript_id, "text": text},
                                )
                                if response.status_code == 429:
                                    retry_after = int(response.headers.get("Retry-After", "5"))
                                    await asyncio.sleep(retry_after)
                                    raise Exception("Rate limited, will retry")
                                if response.status_code >= 500:
                                    raise Exception(f"Similarity service error: {response.status_code}")
                                return response.json()["score"]

                    score = await _call_with_retry(do_similarity)
                    result = await db.execute(select(Manuscript).where(Manuscript.id == manuscript_id))
                    m = result.scalar_one()
                    m.similarity_score = score
                    m.needs_attention = score >= 70
                    await _mark_stage(db, stage, StageStatus.done)
                except Exception as e:
                    await _mark_stage(db, stage, StageStatus.error, str(e))
                    await _fail_manuscript(db, manuscript_id, str(e))
                    return

        # Stage 4: build preview
        async with Session() as db:
            stage = await _get_stage(db, manuscript_id, "preview_built")
            if stage.status != StageStatus.done:
                await _mark_stage(db, stage, StageStatus.running)
                try:
                    with open(manuscript.file_path, "r", encoding="utf-8") as f:
                        text = f.read()
                    preview = " ".join(text.split()[:200])
                    result = await db.execute(select(Manuscript).where(Manuscript.id == manuscript_id))
                    m = result.scalar_one()
                    m.preview = preview
                    await _mark_stage(db, stage, StageStatus.done)
                except Exception as e:
                    await _mark_stage(db, stage, StageStatus.error, str(e))
                    await _fail_manuscript(db, manuscript_id, str(e))
                    return

        # Stage 5: notify author
        async with Session() as db:
            stage = await _get_stage(db, manuscript_id, "author_notified")
            if stage.status != StageStatus.done:
                await _mark_stage(db, stage, StageStatus.running)
                try:
                    result = await db.execute(select(Manuscript).where(Manuscript.id == manuscript_id))
                    m = result.scalar_one()
                    await _idempotent_notify(db, f"{manuscript_id}:received", {
                        "to": m.author_email,
                        "subject": "We received your manuscript",
                        "body": f"Thank you for submitting '{m.title}'. We will be in touch soon.",
                    })
                    await _mark_stage(db, stage, StageStatus.done)
                except Exception as e:
                    await _mark_stage(db, stage, StageStatus.error, str(e))
                    await _fail_manuscript(db, manuscript_id, str(e))
                    return

        # All done
        async with Session() as db:
            result = await db.execute(select(Manuscript).where(Manuscript.id == manuscript_id))
            m = result.scalar_one()
            m.status = ManuscriptStatus.done
            await db.commit()
            logger.info(f"[{manuscript_id}] Pipeline complete")

    finally:
        await engine.dispose()

async def _fail_manuscript(db, manuscript_id: str, reason: str):
    result = await db.execute(select(Manuscript).where(Manuscript.id == manuscript_id))
    m = result.scalar_one()
    m.status = ManuscriptStatus.error
    await db.commit()
    logger.error(f"[{manuscript_id}] Pipeline failed: {reason}")

async def notify_editor_decision(ctx, manuscript_id: str, decision: str):
    Session, engine = _make_session()
    try:
        async with Session() as db:
            result = await db.execute(select(Manuscript).where(Manuscript.id == manuscript_id))
            m = result.scalar_one()
            await _idempotent_notify(db, f"{manuscript_id}:editor_{decision}", {
                "to": m.author_email,
                "subject": f"Your manuscript status: {decision}",
                "body": f"The editor has marked your manuscript '{m.title}' as: {decision}.",
            })
    finally:
        await engine.dispose()
