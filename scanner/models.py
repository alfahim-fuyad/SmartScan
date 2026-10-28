import os
import uuid
from datetime import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q

from .services.validators import sanitize_filename


def _scan_path(subfolder: str, filename: str) -> str:
    extension = os.path.splitext(filename)[1].lower() or ".jpg"
    stamp = datetime.now().strftime("%Y/%m")
    return f"scans/{subfolder}/{stamp}/{uuid.uuid4().hex}{extension}"


def original_upload_to(instance, filename: str) -> str:
    return _scan_path("originals", filename)


def processed_upload_to(instance, filename: str) -> str:
    return _scan_path("processed", filename)


def thumb_upload_to(instance, filename: str) -> str:
    return _scan_path("thumbs", filename)


class ScanHistory(models.Model):

    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="scans",
        help_text="Set when a signed-in user owns the scan.",
    )
    session_key = models.CharField(max_length=64, blank=True, default="", db_index=True)

    original_filename = models.CharField(max_length=255, blank=True, default="upload")
    original_image = models.FileField(upload_to=original_upload_to)
    original_width = models.PositiveIntegerField(null=True, blank=True)
    original_height = models.PositiveIntegerField(null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)

    processed_image = models.FileField(upload_to=processed_upload_to, blank=True)
    processed_width = models.PositiveIntegerField(null=True, blank=True)
    processed_height = models.PositiveIntegerField(null=True, blank=True)
    thumb_image = models.FileField(upload_to=thumb_upload_to, blank=True)

    filter_used = models.CharField(max_length=20, blank=True, default="")
    export_format = models.CharField(max_length=10, blank=True, default="")
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )


    corners_json = models.JSONField(null=True, blank=True)
    params_json = models.JSONField(null=True, blank=True)

    page_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Scan history entry"
        verbose_name_plural = "Scan history"
        ordering = ["page_order", "-created_at"]
        indexes = [
            models.Index(fields=["session_key", "page_order"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.display_name}"


    @classmethod
    def owned_filter(cls, user, session_key: str) -> Q:
        if user and user.is_authenticated:
            return Q(user=user)
        return Q(user__isnull=True, session_key=session_key or "__none__")

    @classmethod
    def owned(cls, request):
        return cls.objects.filter(
            cls.owned_filter(request.user, request.session.session_key or "")
        )


    @property
    def display_name(self) -> str:
        return sanitize_filename(self.original_filename or "upload", 40)

    @property
    def has_processed(self) -> bool:
        return bool(self.processed_image)

    @property
    def original_file_size(self) -> int:
        return self.file_size or 0

    def delete_files(self) -> None:
        for field_name in ("original_image", "processed_image", "thumb_image"):
            field = getattr(self, field_name)
            if field:
                try:
                    field.storage.delete(field.name)
                except (OSError, ValueError):
                    pass

    def delete(self, *args, **kwargs):
        self.delete_files()
        return super().delete(*args, **kwargs)
