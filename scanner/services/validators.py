import io
import posixpath
from typing import Dict

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.core.exceptions import ValidationError


Image.MAX_IMAGE_PIXELS = 80_000_000

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


_MAGIC = (
    (0, b"\xff\xd8\xff", "JPEG"),
    (0, b"\x89PNG\r\n\x1a\n", "PNG"),
    (0, b"RIFF", "WebP"),
)


class ImageValidationError(ValidationError):
    pass


def sanitize_filename(name: str, max_length: int = 120) -> str:
    name = (name or "").strip()
    name = posixpath.basename(name.replace("\\", "/"))
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_. ()"
    cleaned = "".join(ch if ch in allowed else "_" for ch in name).strip(". ")
    cleaned = " ".join(cleaned.split())
    return (cleaned[:max_length] or "upload")


def _sniff_format(head: bytes) -> str:
    for offset, signature, label in _MAGIC:
        if head[offset : offset + len(signature)] == signature:
            if label == "WebP":
                if head[8:12] == b"WEBP":
                    return label
                continue
            return label
    raise ImageValidationError(
        "Unsupported file type. Please upload a JPG, PNG or WebP image."
    )


def _check_size(size_bytes: int) -> None:
    limit = settings.SCANNER_MAX_UPLOAD_SIZE
    if size_bytes <= 0:
        raise ImageValidationError("The uploaded file is empty.")
    if size_bytes > limit:
        raise ImageValidationError(
            f"Image is too large ({size_bytes / (1024 * 1024):.1f} MB). "
            f"The maximum allowed size is {limit / (1024 * 1024):.0f} MB."
        )


def _check_dimensions(width: int, height: int) -> None:
    max_side = settings.SCANNER_MAX_DIMENSION
    min_side = settings.SCANNER_MIN_DIMENSION
    max_pixels = settings.SCANNER_MAX_PIXELS
    if width < min_side or height < min_side:
        raise ImageValidationError(
            f"Image is too small ({width}×{height}px). "
            f"Minimum dimension is {min_side}px."
        )
    if width > max_side or height > max_side:
        raise ImageValidationError(
            f"Image is too large ({width}×{height}px). "
            f"Maximum dimension is {max_side}px."
        )
    if width * height > max_pixels:
        raise ImageValidationError(
            "Image resolution is too high "
            f"({width}×{height}px ≈ {width * height / 1_000_000:.0f} MP). "
            f"The maximum is {max_pixels / 1_000_000:.0f} MP."
        )


def decode_image(data: bytes) -> Dict:
    if not data:
        raise ImageValidationError("The uploaded file is empty.")

    _check_size(len(data))
    detected_format = _sniff_format(data[:16])

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
            pil_format = probe.format
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError(
            "This file could not be read as an image. It may be corrupted."
        ) from exc

    if pil_format not in {"JPEG", "PNG", "WEBP"}:
        raise ImageValidationError(
            "Unsupported image format. Please upload a JPG, PNG or WebP image."
        )
    if pil_format != detected_format:
        raise ImageValidationError(
            "File content does not match its type. Please upload a genuine "
            "JPG, PNG or WebP image."
        )

    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ImageValidationError(
            "This image could not be decoded. It may be corrupted or use an "
            "unsupported colour profile."
        )

    height, width = image.shape[:2]
    _check_dimensions(int(width), int(height))
    return {
        "image": image,
        "format": detected_format,
        "width": int(width),
        "height": int(height),
        "size_bytes": len(data),
    }


def validate_upload_file(django_file) -> Dict:
    name = getattr(django_file, "name", "") or ""
    extension = posixpath.splitext(name)[1].lower()
    if extension and extension not in ALLOWED_EXTENSIONS:
        raise ImageValidationError(
            "Unsupported file extension “%s”. Allowed formats: JPG, JPEG, PNG, WebP."
            % extension
        )

    try:
        data = django_file.read()
    except (OSError, ValueError) as exc:
        raise ImageValidationError(
            "The uploaded file could not be read. Please try again."
        ) from exc
    finally:
        try:
            django_file.seek(0)
        except (OSError, ValueError):
            pass

    return decode_image(data)
