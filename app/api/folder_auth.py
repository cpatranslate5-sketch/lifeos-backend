import hashlib
import json
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

FOLDER_PW_FILE = "/app/data/folder_passwords.json"


def _load() -> dict:
    if not os.path.exists(FOLDER_PW_FILE):
        return {}
    with open(FOLDER_PW_FILE) as f:
        return json.load(f)


def _save(data: dict):
    os.makedirs(os.path.dirname(FOLDER_PW_FILE), exist_ok=True)
    with open(FOLDER_PW_FILE, "w") as f:
        json.dump(data, f)


def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


class FolderAuthIn(BaseModel):
    folder: str
    password: str = ""


@router.post("/folder-auth")
def folder_auth(payload: FolderAuthIn):
    passwords = _load()
    stored_hash = passwords.get(payload.folder)
    if not stored_hash:
        # No password set for this folder yet — open to anyone with the
        # main site token.
        return {"ok": True}
    if _hash(payload.password) != stored_hash:
        raise HTTPException(401, "Неверный пароль")
    return {"ok": True}


class SetFolderPasswordIn(BaseModel):
    folder: str
    password: str


@router.post("/folder-set-password")
def set_folder_password(payload: SetFolderPasswordIn):
    passwords = _load()
    passwords[payload.folder] = _hash(payload.password)
    _save(passwords)
    return {"ok": True}


@router.get("/folder-has-password")
def folder_has_password(folder: str):
    passwords = _load()
    return {"has_password": folder in passwords}
