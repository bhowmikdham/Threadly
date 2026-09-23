"""Test env. DB tests run against a real postgres 16 and SKIP with a clear
message when it isn't reachable (CI provides one; locally: `make dev` or
export THREADLY_TEST_DB).
"""

import asyncio
import os

# Settings are cached at import — pin the test env BEFORE app imports.
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef-32b!")
os.environ.setdefault("FERNET_KEY", "F3EhFuo7yg2vcXW615VjV0zmVUCTe9h8O_dcHZZk_NM=")
os.environ.setdefault("OPENROUTER_API_KEY", "test-or-key")
os.environ["GMAIL_SOURCE_MODE"] = "legacy_sync"
os.environ["MAILBOX_BACKGROUND_SYNC_ENABLED"] = "true"
os.environ["INFERENCE_PROVIDER"] = "legacy"  # individual adapter tests explicitly opt into fakes
# Never inherit the API's DATABASE_URL: test fixtures below drop/truncate tables.
os.environ["DATABASE_URL"] = os.environ.get(
    "THREADLY_TEST_DB",
    "postgresql+asyncpg://threadly:change-me@localhost:5432/threadly_test",
)

import jwt as pyjwt  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import Base  # noqa: E402


def _pg_available() -> bool:
    async def probe() -> bool:
        try:
            engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
            async with engine.connect():
                pass
            await engine.dispose()
            return True
        except Exception:
            return False

    return asyncio.run(probe())


PG_UP = _pg_available()
if os.environ.get("THREADLY_REQUIRE_TEST_DB") == "1" and not PG_UP:
    raise RuntimeError("PostgreSQL is required for this test run but is not reachable")
needs_pg = pytest.mark.skipif(
    not PG_UP, reason="postgres not reachable — run `make dev` or set THREADLY_TEST_DB"
)


@pytest.fixture(scope="session")
def db_engine():
    if not PG_UP:
        pytest.skip("postgres not reachable")
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture()
def db_sessionmaker(db_engine):
    async def truncate():
        async with db_engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                from sqlalchemy import text

                await conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))

    asyncio.run(truncate())
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture()
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def db_client(db_sessionmaker) -> TestClient:
    """App wired to the test database."""
    from app.db.engine import get_session
    from app.main import create_app

    app = create_app()

    async def override():
        async with db_sessionmaker() as session:
            yield session

    app.dependency_overrides[get_session] = override
    return TestClient(app, raise_server_exceptions=False)


def make_jwt(user_id: int) -> str:
    s = get_settings()
    return pyjwt.encode({"sub": str(user_id)}, s.secret_key, algorithm=s.jwt_algorithm)


@pytest.fixture()
def auth_headers():
    def _for(user_id: int) -> dict:
        return {"Authorization": f"Bearer {make_jwt(user_id)}"}

    return _for
