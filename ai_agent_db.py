import os
from contextlib import contextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv, find_dotenv
from sqlalchemy import (
    create_engine,
    Column, Integer, String, Text, DateTime,
    ForeignKey, Index, event
)
from sqlalchemy.orm import (
    declarative_base,
    sessionmaker,
    scoped_session,
    relationship
)
from sqlalchemy.pool import QueuePool

load_dotenv(find_dotenv())

_DATABASE_URL = os.getenv("DATABASE_URL")

if _DATABASE_URL:
    # Production PostgreSQL setup
    engine = create_engine(
        _DATABASE_URL,
        poolclass=QueuePool,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=1800,
        echo=False,
    )
    DB_BACKEND = "postgresql"
else:
    # Local SQLite fallback
    _DB_PATH = os.path.join(os.path.dirname(__file__), "agent_vault.db")
    engine = create_engine(
        f"sqlite:///{_DB_PATH}",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    DB_BACKEND = "sqlite"


    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.close()

print(f"Database initialized. Backend: {DB_BACKEND}")

_SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
ScopedSession = scoped_session(_SessionFactory)
session = ScopedSession()


@contextmanager
def get_db():
    db = ScopedSession()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        ScopedSession.remove()


Base = declarative_base()


class AnonymousProfile(Base):
    __tablename__ = "anonymous_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    first_name = Column(String(50))
    last_name = Column(String(50))
    email = Column(String(100), unique=True, nullable=False)
    contact_no = Column(String(20))
    address = Column(Text)
    city = Column(String(50))
    state = Column(String(50))
    country = Column(String(50))

    education = relationship(
        "EducationRecord",
        back_populates="profile",
        cascade="all, delete-orphan",
        lazy="joined",
    )
    experience = relationship(
        "ExperienceRecord",
        back_populates="profile",
        cascade="all, delete-orphan",
        lazy="joined",
    )

    def to_dynamic_dict(self) -> dict:
        data = {f"profile_{k}": v for k, v in self.__dict__.items() if not k.startswith("_")}

        if self.education:
            edu = self.education[0]
            for k, v in edu.__dict__.items():
                if not k.startswith("_") and k not in ("profile_id", "id"):
                    data[f"education_{k}"] = str(v) if v is not None else ""

        if self.experience:
            exp = self.experience[0]
            for k, v in exp.__dict__.items():
                if not k.startswith("_") and k not in ("profile_id", "id"):
                    data[f"experience_{k}"] = v if v is not None else ""

        return data


class EducationRecord(Base):
    __tablename__ = "education_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("anonymous_profiles.id"), nullable=False)
    degree = Column(String(100))
    year = Column(Integer)
    profile = relationship("AnonymousProfile", back_populates="education")


class ExperienceRecord(Base):
    __tablename__ = "experience_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("anonymous_profiles.id"), nullable=False)
    role_title = Column(String(100))
    company_name = Column(String(100))
    description = Column(Text)
    profile = relationship("AnonymousProfile", back_populates="experience")


class FieldMappingCache(Base):
    __tablename__ = "field_mappings_cache"
    id = Column(Integer, primary_key=True, autoincrement=True)
    website_domain = Column(String(255), nullable=False)
    html_placeholder = Column(String(255), nullable=False)
    mapped_db_key = Column(String(100), nullable=False)

    __table_args__ = (
        Index("ix_fmc_domain_placeholder", "website_domain", "html_placeholder"),
    )


class FormSession(Base):
    __tablename__ = "form_sessions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("anonymous_profiles.id"))
    target_url = Column(Text, nullable=False)
    domain = Column(String(255))
    status = Column(String(30), nullable=False, default="PENDING")
    fields_found = Column(Integer, default=0)
    fields_filled = Column(Integer, default=0)
    fields_skipped = Column(Integer, default=0)
    error_detail = Column(Text)
    screenshot_path = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_fs_domain_status", "domain", "status"),
        Index("ix_fs_created_at", "created_at"),
    )

class BlacklistedDomain(Base):
    __tablename__ = "blacklisted_domains"
    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(255), unique=True, nullable=False)
    reason = Column(Text)

    def to_dict(self) -> dict:
        return {
            "session_id": self.id,
            "profile_id": self.profile_id,
            "target_url": self.target_url,
            "domain": self.domain,
            "status": self.status,
            "fields_found": self.fields_found,
            "fields_filled": self.fields_filled,
            "fields_skipped": self.fields_skipped,
            "error_detail": self.error_detail,
            "screenshot_path": self.screenshot_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


Base.metadata.create_all(engine)


def _seed_default_profile() -> None:
    with get_db() as db:
        if db.query(AnonymousProfile).count() > 0:
            return

        test_user = AnonymousProfile(
            first_name="Robin",
            last_name="Stealth",
            email="robin.dev.test@example.com",
            contact_no="+919876543210",
            address="102 Corporate Hub, Ring Road",
            city="Surat",
            state="Gujarat",
            country="India",
        )
        db.add(test_user)
        db.flush()

        db.add(EducationRecord(profile_id=test_user.id, degree="BCA", year=2026))
        db.add(ExperienceRecord(
            profile_id=test_user.id,
            role_title="Python Intern",
            company_name="Atlysbridge Solutions",
            description="Backend agent R&D"
        ))
        print("Default profile seeded.")


_seed_default_profile()