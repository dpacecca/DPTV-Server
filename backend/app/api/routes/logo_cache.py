from fastapi import APIRouter, Query

from app.api.deps import AdminUser
from app.services.iptvboss_logos import search_logos

router = APIRouter(prefix="/api/logo-cache", tags=["logo-cache"])


@router.get("/search")
async def search(_admin: AdminUser, q: str = Query(..., min_length=1)) -> list[dict]:
    return search_logos(q)
