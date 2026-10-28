from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "scanner"

urlpatterns = [

    path("", views.home, name="home"),
    path("scanner/", views.scanner_view, name="scanner_view"),
    path("enhancer/", views.enhancer_view, name="enhancer"),
    path("history/", views.history_view, name="history"),
    path("about/", views.about_view, name="about"),
    path("health/", views.health, name="health"),

    path("scanner/upload/", views.upload_view, name="upload"),

    path("scanner/api/<int:scan_id>/detect/", views.api_detect, name="api_detect"),
    path("scanner/api/<int:scan_id>/process/", views.api_process, name="api_process"),
    path("scanner/api/<int:scan_id>/export/", views.api_export, name="api_export"),
    path("scanner/api/<int:scan_id>/enhance/", views.api_enhance, name="api_enhance"),
    path("scanner/api/<int:scan_id>/download/", views.processed_download, name="processed_download"),
    path("scanner/api/<int:scan_id>/delete/", views.api_scan_delete, name="api_scan_delete"),
    path("scanner/api/pages/", views.api_pages, name="api_pages"),
    path("scanner/api/pages/reorder/", views.api_pages_reorder, name="api_pages_reorder"),
    path("scanner/api/export/pdf-multi/", views.api_pdf_multi, name="api_pdf_multi"),

    path("history/<int:scan_id>/delete/", views.history_delete, name="history_delete"),
    path("history/clear-all/", views.history_clear_all, name="history_clear_all"),

    path("accounts/register/", views.register_view, name="register"),
    path("accounts/login/", views.LoginView.as_view(), name="login"),
    path(
        "accounts/logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
]
