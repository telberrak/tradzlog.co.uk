from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe

from jose import JWTError, jwt
from passlib.context import CryptContext

from tradzlog_api.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return pwd_context.verify(password, hashed_password)


def create_access_token(user_id: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer)
    except JWTError:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None


def generate_one_time_token() -> str:
    return token_urlsafe(48)


def hash_one_time_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()
