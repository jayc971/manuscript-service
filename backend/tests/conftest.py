import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models.base import Base
from app.models.user import User, UserRole
from app.models.manuscript import Manuscript, ManuscriptStage, Priority
from app.core.security import hash_password


@pytest_asyncio.fixture
async def db_url(tmp_path):
    """File-based SQLite URL so multiple engines can share the same DB in one test."""
    return f"sqlite+aiosqlite:///{tmp_path}/test.db"


@pytest_asyncio.fixture
async def db_engine(db_url):
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    Session = async_sessionmaker(db_engine, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest_asyncio.fixture
async def author_user(db_session):
    user = User(
        id="uid-author-1",
        email="author1@test.com",
        password_hash=hash_password("pass"),
        role=UserRole.author,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def second_user(db_session):
    user = User(
        id="uid-author-2",
        email="author2@test.com",
        password_hash=hash_password("pass"),
        role=UserRole.author,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest_asyncio.fixture
async def manuscript_fixture(db_url, db_session, author_user, tmp_path):
    """
    Creates a text file on disk and a matching Manuscript + stages in the DB.
    Returns (manuscript_id, session_factory_fn) where session_factory_fn() gives
    a fresh (AsyncSessionFactory, engine) pair pointing to the same SQLite file —
    this lets process_manuscript dispose its engine without breaking further reads.
    """
    txt = tmp_path / "sample.txt"
    txt.write_text("The quick brown fox jumps over the lazy dog. " * 50)

    manuscript = Manuscript(
        id="m_test0001",
        owner_id=author_user.id,
        title="Sample Paper",
        author_email="author1@test.com",
        file_path=str(txt),
        file_name="sample.txt",
        priority=Priority.normal,
    )
    db_session.add(manuscript)
    await db_session.flush()

    for name in ["validated", "text_extracted", "similarity_checked", "preview_built", "author_notified"]:
        db_session.add(ManuscriptStage(manuscript_id=manuscript.id, name=name))
    await db_session.commit()

    def fresh_session_factory():
        """Each call returns a new (Session, engine) pair on the same DB file."""
        eng = create_async_engine(db_url)
        sess = async_sessionmaker(eng, expire_on_commit=False)
        return sess, eng

    return manuscript.id, fresh_session_factory, db_url
