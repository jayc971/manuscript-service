import os
import re
import uuid
import aiofiles
from fastapi import UploadFile, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.manuscript import Manuscript, ManuscriptStage, StageStatus
from app.core.config import settings

UPLOAD_DIR = "/app/uploads"
STAGE_NAMES = ["validated", "text_extracted", "similarity_checked", "preview_built", "author_notified"]

def sanitise_filename(name: str) -> str:
    name = os.path.basename(name)
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "upload.txt"

async def save_upload(file: UploadFile) -> tuple[str, str]:
    content = await file.read()

    if len(content) == 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File must not be empty")

    limit = settings.upload_size_limit_mb * 1024 * 1024
    if len(content) > limit:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"File exceeds the {settings.upload_size_limit_mb} MB size limit")

    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File must be a plain text file")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_name = sanitise_filename(file.filename or "upload.txt")
    unique_name = f"{uuid.uuid4()}_{safe_name}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    return file_path, safe_name

async def create_manuscript(
    db: AsyncSession,
    owner_id: str,
    title: str,
    author_email: str,
    priority: str,
    file_path: str,
    file_name: str,
) -> Manuscript:
    manuscript = Manuscript(
        owner_id=owner_id,
        title=title,
        author_email=author_email,
        priority=priority,
        file_path=file_path,
        file_name=file_name,
    )
    db.add(manuscript)
    await db.flush()

    for name in STAGE_NAMES:
        db.add(ManuscriptStage(manuscript_id=manuscript.id, name=name))

    await db.commit()
    await db.refresh(manuscript)
    return manuscript

async def get_manuscript(db: AsyncSession, manuscript_id: str, owner_id: str) -> Manuscript:
    result = await db.execute(
        select(Manuscript)
        .options(selectinload(Manuscript.stages))
        .where(Manuscript.id == manuscript_id, Manuscript.owner_id == owner_id)
    )
    manuscript = result.scalar_one_or_none()
    if not manuscript:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manuscript not found")
    return manuscript

async def list_manuscripts(db: AsyncSession, owner_id: str) -> list[Manuscript]:
    result = await db.execute(
        select(Manuscript)
        .options(selectinload(Manuscript.stages))
        .where(Manuscript.owner_id == owner_id)
        .order_by(Manuscript.created_at.desc())
    )
    return list(result.scalars().all())
