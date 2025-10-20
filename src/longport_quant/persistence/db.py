"""Async database session manager for PostgreSQL."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class DatabaseSessionManager:
    def __init__(self, dsn: str, auto_init: bool = False) -> None:
        self._dsn = dsn
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._auto_init = auto_init

        # If auto_init is True, initialize immediately (for non-context manager usage)
        if auto_init:
            self._initialize()

    def _initialize(self) -> None:
        """Initialize engine and session factory."""
        if not self._engine:
            self._engine = create_async_engine(self._dsn, echo=False, pool_pre_ping=True)
            self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def __aenter__(self) -> "DatabaseSessionManager":
        self._initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._engine:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None

    async def close(self) -> None:
        """Explicitly close the database connection."""
        if self._engine:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if not self._session_factory:
            raise RuntimeError("Database session factory not initialised")
        session = self._session_factory()
        try:
            yield session
        finally:
            await session.close()
