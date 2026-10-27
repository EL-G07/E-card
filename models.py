"""Database models: one table for members (User) and one for administrators (Admin)."""
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, String, create_engine, func,ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DB_PATH = os.environ.get("KARIBU_DB") or str(Path(__file__).resolve().parent / "karibu.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Account(Base):
    """Columns shared by both tables. Not a table itself."""
    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254, collation="NOCASE"), unique=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True)
    username: Mapped[str] = mapped_column(String(30, collation="NOCASE"), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)


class User(Account):
    """A registered member. The admin can suspend (is_active = False), reset or delete them."""
    __tablename__ = "users"
    is_active: Mapped[bool] = mapped_column(default=True)


class Admin(Account):
    """An administrator. Created with create_admin.py, never through the public form."""
    __tablename__ = "admins"

class Event(Base):
    """A registered event."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(50))
    type: Mapped[str]= mapped_column(String(50))
    date_time:Mapped[datetime] = mapped_column(DateTime)
    venue: Mapped[str] = mapped_column(String(90))
    description: Mapped[str| None] = mapped_column(String(500))
    created_at: Mapped[datetime]=mapped_column(DateTime, server_default=func.now())
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    event_key:Mapped[str]=mapped_column(String(255))

class Event_payment(Base):
    __tablename__ = "events_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(ForeignKey("events.title"))
    full_name: Mapped[str] = mapped_column(ForeignKey("users.full_name"))
    email: Mapped[str] = mapped_column(ForeignKey("users.email"))
    phone: Mapped[str] = mapped_column(ForeignKey("users.phone"))
    user:Mapped[int]=mapped_column(String(30))
    kind:Mapped[str]=mapped_column(String(30), default="Single")
    payment:Mapped[bool]=mapped_column(default=False)

class Attendance(Base):
    __tablename__ = "attendances"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    username: Mapped[str] = mapped_column(String(30))
    full_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str] = mapped_column(String(20))
    attended: Mapped[bool] = mapped_column(default=False)
    paid: Mapped[bool] = mapped_column(default=False)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    scanned_by: Mapped[str] = mapped_column(String(30))  # username of the scanner (event owner)

    # ... etc

def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db():
    """FastAPI dependency: one database session per request."""
    with SessionLocal() as db:
        yield db

init_db()