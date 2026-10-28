import base64
import os
import urllib.request

import cloudinary
import cloudinary.api
import cloudinary.uploader
import cloudinary.utils
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import Storage

_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


def _apply_config() -> None:
    if os.environ.get("CLOUDINARY_URL"):
        cloudinary.config(secure=True)
        return
    from django.conf import settings

    config = settings.CLOUDINARY
    if not (config["CLOUD_NAME"] and config["API_KEY"] and config["API_SECRET"]):
        raise ImproperlyConfigured(
            "Cloudinary credentials are missing. Set CLOUDINARY_URL or "
            "CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET."
        )
    cloudinary.config(
        cloud_name=config["CLOUD_NAME"],
        api_key=config["API_KEY"],
        api_secret=config["API_SECRET"],
        secure=True,
    )


class CloudinaryMediaStorage(Storage):

    def __init__(self) -> None:
        self._configured = False

    def _ensure_config(self) -> None:
        if not self._configured:
            _apply_config()
            self._configured = True

    @staticmethod
    def _split_name(name: str) -> tuple[str, str]:
        root, extension = os.path.splitext(name.replace("\\", "/").lstrip("/"))
        return root, extension.lstrip(".").lower() or "jpg"

    def _delivery_url(self, name: str) -> str:
        self._ensure_config()
        public_id, extension = self._split_name(name)
        url, _options = cloudinary.utils.cloudinary_url(
            public_id, format=extension, secure=True
        )
        return url

    def _open(self, name, mode="rb"):
        with urllib.request.urlopen(self._delivery_url(name), timeout=30) as response:
            data = response.read()
        return ContentFile(data, name=name)

    def _save(self, name, content):
        self._ensure_config()
        content.seek(0)
        data = content.read()
        public_id, extension = self._split_name(name)
        mime = _MIME.get(extension, "image/jpeg")
        encoded = base64.b64encode(data).decode("ascii")
        cloudinary.uploader.upload(
            f"data:{mime};base64,{encoded}",
            public_id=public_id,
            overwrite=True,
            invalidate=True,
            resource_type="image",
        )
        return name

    def exists(self, name: str) -> bool:
        self._ensure_config()
        public_id, _extension = self._split_name(name)
        try:
            cloudinary.api.resource(public_id, resource_type="image")
        except cloudinary.api.NotFound:
            return False
        return True

    def size(self, name: str) -> int:
        self._ensure_config()
        public_id, _extension = self._split_name(name)
        info = cloudinary.api.resource(public_id, resource_type="image")
        return int(info.get("bytes", 0))

    def url(self, name: str) -> str:
        if not name:
            raise ValueError("This file has no stored name.")
        return self._delivery_url(name)

    def delete(self, name: str) -> None:
        self._ensure_config()
        public_id, _extension = self._split_name(name)
        cloudinary.uploader.destroy(public_id, invalidate=True, resource_type="image")

    def get_available_name(self, name, max_length=None):
        return name
