import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from typing import Annotated, Generator

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import JSON, Float, ForeignKey, Integer, String, UniqueConstraint, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from models import (
    AuthResponse,
    Credentials,
    CurrentUserResponse,
    HealthResponse,
    LatestSnapshotResponse,
    LogoutResponse,
    PlannerData,
    SnapshotResponse,
    SnapshotVersionResponse,
    SyncRequest,
    SyncResponse,
    VersionConflictDetail,
)


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./travel-planner.db")
SECRET_KEY = os.getenv("TRAVEL_PLANNER_SECRET_KEY", "local-development-key-change-before-deploying")
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
PASSWORD_ITERATIONS = 310_000
allowed_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173",
).split(",")

print(allowed_origins)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[float] = mapped_column(Float)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("user_id", "version", name="uq_snapshot_user_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


Base.metadata.create_all(engine)

app = FastAPI(title="EzMyGo Sync API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as db:
        yield db


DbSession = Annotated[Session, Depends(get_db)]


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        salt_hex, digest_hex = encoded.split(":", 1)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), PASSWORD_ITERATIONS)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def encode_token(session_id: str, user_id: int, expires_at: float) -> str:
    payload = json.dumps({"sid": session_id, "sub": user_id, "exp": int(expires_at)}, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    signature = hmac.new(SECRET_KEY.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def decode_token(token: str) -> dict:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(SECRET_KEY.encode(), encoded.encode(), hashlib.sha256).digest()
        provided = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, provided):
            raise ValueError("Invalid signature")
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        claims = json.loads(payload)
        if not isinstance(claims.get("sub"), int) or claims.get("exp", 0) <= time.time():
            raise ValueError("Expired token")
        return claims
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session") from error


def current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to sync your plans")
    claims = decode_token(authorization[7:])
    user_session = db.get(UserSession, claims["sid"])
    if (
        user_session is None
        or user_session.user_id != claims["sub"]
        or user_session.revoked_at is not None
        or user_session.expires_at <= time.time()
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session has expired")
    user = db.get(User, user_session.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session has expired")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def latest_snapshot(db: Session, user_id: int) -> Snapshot | None:
    return db.scalar(
        select(Snapshot).where(Snapshot.user_id == user_id).order_by(Snapshot.version.desc()).limit(1)
    )


def create_session(db: Session, user: User) -> AuthResponse:
    now = time.time()
    session_id = str(uuid.uuid4())
    expires_at = now + SESSION_TTL_SECONDS
    db.add(UserSession(id=session_id, user_id=user.id, expires_at=expires_at, created_at=now))
    db.commit()
    snapshot = latest_snapshot(db, user.id)
    return AuthResponse(
        access_token=encode_token(session_id, user.id, expires_at),
        token_type="bearer",
        email=user.email,
        version=snapshot.version if snapshot else 0,
        snapshot=PlannerData.model_validate(snapshot.data) if snapshot else None,
        created_at=snapshot.created_at if snapshot else None,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post(
    "/api/auth/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthResponse,
    response_model_exclude_unset=True,
)
def register(credentials: Credentials, db: DbSession) -> AuthResponse:
    email = str(credentials.email).strip().lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")
    user = User(email=email, password_hash=hash_password(credentials.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists") from error
    db.refresh(user)
    return create_session(db, user)


@app.post("/api/auth/login", response_model=AuthResponse, response_model_exclude_unset=True)
def login(credentials: Credentials, db: DbSession) -> AuthResponse:
    email = str(credentials.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect")
    return create_session(db, user)


@app.get("/api/auth/me", response_model=CurrentUserResponse, response_model_exclude_unset=True)
def me(user: CurrentUser, db: DbSession) -> CurrentUserResponse:
    snapshot = latest_snapshot(db, user.id)
    return CurrentUserResponse(
        email=user.email,
        version=snapshot.version if snapshot else 0,
        snapshot=PlannerData.model_validate(snapshot.data) if snapshot else None,
        created_at=snapshot.created_at if snapshot else None,
    )


@app.post("/api/auth/logout", response_model=LogoutResponse)
def logout(
    user: CurrentUser,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> LogoutResponse:
    if authorization:
        claims = decode_token(authorization[7:])
        user_session = db.get(UserSession, claims["sid"])
        if user_session is not None and user_session.user_id == user.id:
            user_session.revoked_at = time.time()
            db.commit()
    return LogoutResponse(status="signed out")


@app.get("/api/sync/latest", response_model=LatestSnapshotResponse, response_model_exclude_unset=True)
def get_latest(user: CurrentUser, db: DbSession) -> LatestSnapshotResponse:
    snapshot = latest_snapshot(db, user.id)
    if snapshot is None:
        return LatestSnapshotResponse(version=0, data=None, created_at=None)
    return LatestSnapshotResponse(
        version=snapshot.version,
        data=PlannerData.model_validate(snapshot.data),
        created_at=snapshot.created_at,
    )


@app.get("/api/sync/versions", response_model=list[SnapshotVersionResponse])
def get_versions(user: CurrentUser, db: DbSession) -> list[SnapshotVersionResponse]:
    snapshots = db.scalars(
        select(Snapshot).where(Snapshot.user_id == user.id).order_by(Snapshot.version.desc()).limit(100)
    )
    return [
        SnapshotVersionResponse(version=snapshot.version, created_at=snapshot.created_at)
        for snapshot in snapshots
    ]


@app.get("/api/sync/versions/{version}", response_model=SnapshotResponse, response_model_exclude_unset=True)
def get_version(version: int, user: CurrentUser, db: DbSession) -> SnapshotResponse:
    snapshot = db.scalar(
        select(Snapshot).where(Snapshot.user_id == user.id, Snapshot.version == version)
    )
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot version not found")
    return SnapshotResponse(
        version=snapshot.version,
        data=PlannerData.model_validate(snapshot.data),
        created_at=snapshot.created_at,
    )


@app.post("/api/sync", response_model=SyncResponse)
def save_snapshot(payload: SyncRequest, user: CurrentUser, db: DbSession) -> SyncResponse:
    current = latest_snapshot(db, user.id)
    current_version = current.version if current else 0
    if payload.base_version != current_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=VersionConflictDetail(
                message="A newer snapshot exists. Fetch the latest version before syncing.",
                current_version=current_version,
            ).model_dump(),
        )
    snapshot = Snapshot(
        user_id=user.id,
        version=current_version + 1,
        data=payload.data.model_dump(mode="json", exclude_unset=True),
        created_at=time.time(),
    )
    db.add(snapshot)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another device saved a newer version. Fetch the latest snapshot before syncing.",
        ) from error
    return SyncResponse(version=snapshot.version, created_at=snapshot.created_at)
