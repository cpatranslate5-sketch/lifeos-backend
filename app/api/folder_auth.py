from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings

router = APIRouter()


class FolderAuthIn(BaseModel):
    folder: str
    password: str = ""


@router.post("/folder-auth")
def folder_auth(payload: FolderAuthIn):
    required = settings.FOLDER_PASSWORDS.get(payload.folder, "")
    if not required:
        # No password configured for this folder — open to anyone who
        # already has the main site token.
        return {"ok": True}
    if payload.password != required:
        raise HTTPException(401, "Неверный пароль")
    return {"ok": True}
