"""Database models: one table for members (User) and one for administrators (Admin)."""
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, String, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DB_PATH = os.environ.get("KARIBU_DB") or str(Path(__file__).resolve().parent / "karibu2.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Account(Base):
    """Columns shared by both tables. Not a table itself."""
    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254, collation="NOCASE"),default=None, unique=True)


class User(Account):
    """A registered member. The admin can suspend (is_active = False), reset or delete them."""
    __tablename__ = "users"
    is_active: Mapped[bool] = mapped_column(default=True)


class Admin(Account):
    """An administrator. Created with create_admin.py, never through the public form."""
    __tablename__ = "admins"

if __name__=='__main__':
    def init_db() -> None:
        Base.metadata.create_all(engine)


def get_db():
    """FastAPI dependency: one database session per request."""
    with SessionLocal() as db:
        yield db