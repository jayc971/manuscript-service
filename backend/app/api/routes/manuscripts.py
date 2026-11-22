import logging
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, status
from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_editor
from app.models.user import User
from app.models.manuscript import Manuscript, ManuscriptStatus, EditorStatus
from app.schemas.manuscript import ManuscriptResponse, ManuscriptSubmitResponse, StageResponse, EditorStatusUpdate
from app.services.manuscript_service import save_upload, create_manuscript, get_manuscript, list_manuscripts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/manuscripts", tags=["manuscripts"])

VALID_PRIORITIES = {"normal", "high"}
VALID_EDITOR_STATUSES = {s.value for s in EditorStatus}

@router.post("", response_model=ManuscriptSubmitResponse, status_code=202)
async def submit_manuscript(
    title: str = Form(...),
    author_email: EmailStr = Form(...),
    priority: str = Form("normal"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=422, detail="Priority must be normal or high")

    file_path, file_name = await save_upload(file)
    manuscript = await create_manuscript(
        db=db,
        owner_id=current_user.id,
        title=title,
        author_email=author_email,
        priority=priority,
        file_path=file_path,
        file_name=file_name,
    )

    from app.workers.tasks import process_manuscript
    from arq import create_pool
    from arq.connections import RedisSettings
    from app.core.config import settings
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    await pool.enqueue_job("process_manuscript", manuscript.id)
    await pool.aclose()

    logger.info(f"[{manuscript.id}] Submitted by user {current_user.id} priority={priority}")
    return ManuscriptSubmitResponse(id=manuscript.id, status=manuscript.status.value)

@router.get("", response_model=list[ManuscriptResponse])
async def list_my_manuscripts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    manuscripts = await list_manuscripts(db, current_user.id)
    return [_to_response(m) for m in manuscripts]

@router.get("/all", response_model=list[ManuscriptResponse])
async def list_all_manuscripts(
    editor: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Manuscript).options(selectinload(Manuscript.stages)).order_by(Manuscript.created_at.desc())
    )
    manuscripts = list(result.scalars().all())
    return [_to_response(m) for m in manuscripts]

@router.get("/{manuscript_id}", response_model=ManuscriptResponse)
async def get_my_manuscript(
    manuscript_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    manuscript = await get_manuscript(db, manuscript_id, current_user.id)
    return _to_response(manuscript)

@router.patch("/{manuscript_id}/status")
async def update_editor_status(
    manuscript_id: str,
    body: EditorStatusUpdate,
    editor: User = Depends(require_editor),
    db: AsyncSession = Depends(get_db),
):
    if body.status not in VALID_EDITOR_STATUSES:
        raise HTTPException(status_code=422, detail=f"Status must be one of {VALID_EDITOR_STATUSES}")

    result = await db.execute(
        select(Manuscript).options(selectinload(Manuscript.stages)).where(Manuscript.id == manuscript_id)
    )
    manuscript = result.scalar_one_or_none()
    if not manuscript:
        raise HTTPException(status_code=404, detail="Manuscript not found")

    manuscript.editor_status = body.status

    from app.workers.tasks import notify_editor_decision
    from arq import create_pool
    from arq.connections import RedisSettings
    from app.core.config import settings
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    await pool.enqueue_job("notify_editor_decision", manuscript.id, body.status)
    await pool.aclose()

    await db.commit()
    logger.info(f"[{manuscript.id}] Editor status updated to {body.status} by user {editor.id}")
    return _to_response(manuscript)

def _to_response(m: Manuscript) -> ManuscriptResponse:
    return ManuscriptResponse(
        id=m.id,
        title=m.title,
        author_email=m.author_email,
        status=m.status.value,
        editor_status=m.editor_status.value,
        priority=m.priority.value,
        needs_attention=m.needs_attention,
        word_count=m.word_count,
        preview=m.preview,
        similarity_score=m.similarity_score,
        created_at=m.created_at,
        stages=[StageResponse(
            name=s.name,
            status=s.status.value,
            started_at=s.started_at,
            finished_at=s.finished_at,
            attempts=s.attempts,
            last_error=s.last_error,
        ) for s in sorted(m.stages, key=lambda s: s.name)],
    )
