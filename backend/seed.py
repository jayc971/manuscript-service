import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models.user import User, UserRole
from app.core.security import hash_password
from app.core.config import settings

async def seed():
    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        author = User(email="author@example.com", password_hash=hash_password("password123"), role=UserRole.author)
        editor = User(email="editor@example.com", password_hash=hash_password("password123"), role=UserRole.editor)
        db.add(author)
        db.add(editor)
        await db.commit()
        print("Seeded: author@example.com / password123 (author)")
        print("Seeded: editor@example.com / password123 (editor)")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed())
