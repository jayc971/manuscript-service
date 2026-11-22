import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models.user import User, UserRole
from app.core.security import hash_password
from app.core.config import settings

SEED_USERS = [
    ("author@example.com", "password123", UserRole.author),
    ("editor@example.com", "password123", UserRole.editor),
]

async def seed():
    engine = create_async_engine(settings.database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        for email, password, role in SEED_USERS:
            exists = await db.scalar(select(User).where(User.email == email))
            if not exists:
                db.add(User(email=email, password_hash=hash_password(password), role=role))
                print(f"Seeded: {email} ({role.value})")
            else:
                print(f"Already exists: {email} — skipping")
        await db.commit()

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed())
