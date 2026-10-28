from django.contrib import admin

from .models import ScanHistory


@admin.register(ScanHistory)
class ScanHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "display_name",
        "owner_label",
        "status",
        "filter_used",
        "export_format",
        "original_dims",
        "created_at",
    )
    list_filter = ("status", "filter_used", "export_format", "created_at")
    search_fields = ("original_filename", "session_key", "user__username")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"

    @admin.display(description="Owner")
    def owner_label(self, obj):
        return obj.user.username if obj.user_id else f"session:{obj.session_key[:8]}"

    @admin.display(description="Original")
    def original_dims(self, obj):
        if obj.original_width and obj.original_height:
            return f"{obj.original_width}×{obj.original_height}"
        return "—"
