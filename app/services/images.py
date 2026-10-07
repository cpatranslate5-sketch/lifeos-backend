"""
Сжатие картинок (обложки карточек и фото дневника), чтобы они не съедали
диск на сервере. Оригиналы с телефона весят по 3-8 МБ, а для показа в
приложении хватает картинки ~1200-2000 px по длинной стороне.

Всё сделано "безопасно": если файл не удаётся открыть (например, HEIC
или битая картинка), анимированный GIF, или сжатая версия не получилась
меньше — оставляем исходник как есть. Картинка никогда не теряется.
"""
import io
import os

from PIL import Image, ImageOps

COVER_MAX_SIDE = 1200   # обложки карточек
PHOTO_MAX_SIDE = 2000   # фото дневника — чуть крупнее, их открывают на весь экран
JPEG_QUALITY = 82

_FORMAT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


def _has_alpha(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA"):
        return img.getchannel("A").getextrema()[0] < 255
    return img.mode == "P" and "transparency" in img.info


def _encode(img: Image.Image, fmt: str) -> bytes:
    buf = io.BytesIO()
    if fmt == "JPEG":
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    elif fmt == "PNG":
        img.save(buf, "PNG", optimize=True)
    else:  # WEBP
        img.save(buf, "WEBP", quality=JPEG_QUALITY, method=4)
    return buf.getvalue()


def _open(content: bytes) -> Image.Image | None:
    try:
        img = Image.open(io.BytesIO(content))
        if getattr(img, "is_animated", False):
            return None  # анимацию не трогаем
        img.load()
        img = ImageOps.exif_transpose(img)  # чтобы фото с телефона не легли боком
        return img
    except Exception:
        return None


def shrink_upload(content: bytes, max_side: int) -> tuple[bytes, str] | None:
    """Для новых загрузок: уменьшает и сохраняет в JPEG (или PNG, если есть
    прозрачность). Возвращает (байты, расширение) или None — тогда сохраняем
    оригинал как был."""
    img = _open(content)
    if img is None:
        return None
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    fmt = "PNG" if _has_alpha(img) else "JPEG"
    try:
        out = _encode(img, fmt)
    except Exception:
        return None
    if len(out) >= len(content):
        return None
    return out, _FORMAT_EXT[fmt]


def shrink_file_in_place(path: str, max_side: int) -> tuple[int, int]:
    """Для уже лежащих файлов: уменьшает, сохраняя тот же формат и то же имя
    файла (на имена ссылаются карточки и записи дневника). Возвращает
    (размер до, размер после)."""
    before = os.path.getsize(path)
    try:
        with open(path, "rb") as f:
            content = f.read()
        img = _open(content)
        if img is None:
            return before, before
        fmt = (Image.open(io.BytesIO(content)).format or "").upper()
        if fmt not in _FORMAT_EXT:
            return before, before
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        if fmt == "JPEG" and img.mode != "RGB":
            img = img.convert("RGB")
        out = _encode(img, fmt)
        if len(out) >= before * 0.9:  # выигрыш меньше 10% — не трогаем
            return before, before
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(out)
        os.replace(tmp, path)  # подмена одним действием — файл не окажется "наполовину записан"
        return before, len(out)
    except Exception:
        try:
            os.remove(path + ".tmp")
        except OSError:
            pass
        return before, before
