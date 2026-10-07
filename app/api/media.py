"""
Обслуживание картинок: сколько места занято на диске и разовое сжатие
уже загруженных обложек и фото. Требует токен (как и всё остальное).
"""
import os
import shutil

from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.api.entities import COVERS_DIR
from app.api.diary import PHOTOS_DIR
from app.services.images import COVER_MAX_SIDE, PHOTO_MAX_SIDE, shrink_file_in_place

router = APIRouter(dependencies=[Depends(require_auth)])

DATA_DIR = "/app/data"


def _mb(n: int) -> float:
    return round(n / (1024 * 1024), 1)


def _dir_size(path: str) -> tuple[int, int]:
    total, count = 0, 0
    if os.path.isdir(path):
        for name in os.listdir(path):
            p = os.path.join(path, name)
            if os.path.isfile(p):
                total += os.path.getsize(p)
                count += 1
    return total, count


@router.get("/maintenance/disk")
def disk_usage():
    du = shutil.disk_usage(DATA_DIR) if os.path.isdir(DATA_DIR) else None
    covers, n_covers = _dir_size(COVERS_DIR)
    photos, n_photos = _dir_size(PHOTOS_DIR)
    db_size = sum(os.path.getsize(os.path.join(DATA_DIR, f)) for f in os.listdir(DATA_DIR)
                  if f.startswith("lifeos.db")) if os.path.isdir(DATA_DIR) else 0
    return {
        "disk_total_mb": _mb(du.total) if du else None,
        "disk_used_mb": _mb(du.used) if du else None,
        "disk_free_mb": _mb(du.free) if du else None,
        "covers_mb": _mb(covers), "covers_count": n_covers,
        "photos_mb": _mb(photos), "photos_count": n_photos,
        "database_mb": _mb(db_size),
    }


@router.post("/maintenance/compress-images")
def compress_images():
    """Разово ужимает все уже лежащие картинки. Можно запускать повторно —
    уже сжатые файлы просто пропускаются."""
    result = {}
    for label, folder, max_side in (("covers", COVERS_DIR, COVER_MAX_SIDE), ("photos", PHOTOS_DIR, PHOTO_MAX_SIDE)):
        before_total = after_total = changed = 0
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                p = os.path.join(folder, name)
                if not os.path.isfile(p) or name.endswith(".tmp"):
                    continue
                b, a = shrink_file_in_place(p, max_side)
                before_total += b
                after_total += a
                if a < b:
                    changed += 1
        result[label] = {"files_compressed": changed, "before_mb": _mb(before_total),
                         "after_mb": _mb(after_total), "saved_mb": _mb(before_total - after_total)}
    result["disk"] = disk_usage()
    return result
