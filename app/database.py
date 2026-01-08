from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

# ВАЖНО: check_same_thread=False нужен для SQLite в асинхронном режиме
engine = create_async_engine(
    settings.DATABASE_URL, 
    echo=True,
    connect_args={"check_same_thread": False} 
)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session