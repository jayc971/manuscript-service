"""
Test: a user cannot read another user's manuscript.

get_manuscript in manuscript_service.py filters by BOTH manuscript_id AND owner_id.
If User B requests a manuscript that belongs to User A, the service returns a 404 —
the same response as if the manuscript didn't exist at all, to avoid leaking IDs.
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.core.database import get_db
from app.core.security import create_access_token
from app.models.manuscript import Manuscript, ManuscriptStage, Priority


@pytest_asyncio.fixture
async def api_client(db_engine, db_session, author_user, second_user, tmp_path):
    """
    FastAPI test client backed by the in-memory SQLite DB.
    Overrides the get_db dependency so the app uses the test DB.
    """
    Session = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # Create a manuscript owned by author_user
    txt = tmp_path / "paper.txt"
    txt.write_text("Some manuscript content " * 20)

    manuscript = Manuscript(
        id="m_owned001",
        owner_id=author_user.id,
        title="Author One's Paper",
        author_email=author_user.email,
        file_path=str(txt),
        file_name="paper.txt",
        priority=Priority.normal,
    )
    db_session.add(manuscript)
    await db_session.flush()
    for name in ["validated", "text_extracted", "similarity_checked", "preview_built", "author_notified"]:
        db_session.add(ManuscriptStage(manuscript_id=manuscript.id, name=name))
    await db_session.commit()

    # Tokens for both users
    token_a = create_access_token(author_user.id, author_user.role.value)
    token_b = create_access_token(second_user.id, second_user.role.value)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, token_a, token_b, manuscript.id

    app.dependency_overrides.clear()


async def test_owner_can_read_own_manuscript(api_client):
    client, token_a, _, manuscript_id = api_client
    response = await client.get(
        f"/manuscripts/{manuscript_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == manuscript_id


async def test_other_user_gets_404(api_client):
    client, _, token_b, manuscript_id = api_client
    response = await client.get(
        f"/manuscripts/{manuscript_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404, (
        f"Expected 404 for a manuscript belonging to another user, got {response.status_code}. "
        "Users must not be able to discover or read each other's manuscripts."
    )
