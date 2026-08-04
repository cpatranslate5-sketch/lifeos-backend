import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings

_bearer = HTTPBearer(auto_error=False)


def require_auth(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    """
    Single-user system: one shared secret token, set via APP_AUTH_TOKEN
    in the environment (see config.py), not hardcoded anywhere in code.
    Compares with hmac.compare_digest to avoid timing attacks.
    """
    if creds is None or not hmac.compare_digest(creds.credentials, settings.AUTH_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )
