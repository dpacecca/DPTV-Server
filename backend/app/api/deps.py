from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def require_admin(token: Annotated[str | None, Depends(oauth2_scheme)]) -> str:
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    username = decode_access_token(token)
    if username is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return username


AdminUser = Annotated[str, Depends(require_admin)]
