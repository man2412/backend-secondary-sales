import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Serverless (Vercel) needs different connection handling than a long-lived
# server. Each function instance would otherwise build its own QueuePool and
# hold connections open, exhausting Supabase's pooler
# (asyncpg EMAXCONNSESSION "max clients reached in session mode"). NullPool
# opens a connection per checkout and closes it on release, so the
# *transaction-mode* Supabase pooler (port 6543) can multiplex across instances.
#
# Transaction-mode pgbouncer does not keep session state between statements, so
# we disable asyncpg's statement cache and SQLAlchemy's prepared-statement cache
# to avoid "prepared statement does not exist / already exists" errors.
#
# Vercel injects VERCEL=1 on every deployment, so the fix auto-engages there
# even if APP_ENV is not set; it also turns on for any non-development env.
_is_serverless = os.environ.get("VERCEL") == "1" or settings.APP_ENV != "development"

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_ENV == "development",
    poolclass=NullPool if _is_serverless else None,
    pool_pre_ping=True,
    connect_args=(
        {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
        if _is_serverless
        else {}
    ),
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
