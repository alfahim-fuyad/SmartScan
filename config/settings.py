from pathlib import Path
import os

import dj_database_url
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_list(name: str, default: str = "") -> list:
    raw = os.environ.get(name)
    source = raw if raw is not None and raw.strip() else default
    return [item.strip() for item in source.split(",") if item.strip()]


SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-local-development-key-do-not-use-in-production",
)

DEBUG = env_bool("DEBUG", True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL", "")

CLOUDINARY = {
    "CLOUD_NAME": os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
    "API_KEY": os.environ.get("CLOUDINARY_API_KEY", ""),
    "API_SECRET": os.environ.get("CLOUDINARY_API_SECRET", ""),
}

_CLOUDINARY_READY = bool(CLOUDINARY_URL) or all(CLOUDINARY.values())

CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000",
)


INSTALLED_APPS = [
    "whitenoise.runserver_nostatic",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "scanner.apps.ScannerConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


DATABASE_URL = os.environ.get("DATABASE_URL", "")

_DB_SCHEMES = ("postgres://", "postgresql://")
if DATABASE_URL.lower().startswith(_DB_SCHEMES):


    _ssl_require = "sslmode=" not in DATABASE_URL
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=_ssl_require,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/scanner/"
LOGOUT_REDIRECT_URL = "/"


LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": (
            "scanner.services.media_storage.CloudinaryMediaStorage"
            if _CLOUDINARY_READY
            else "django.core.files.storage.FileSystemStorage"
        ),
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

WHITENOISE_ROOT = BASE_DIR / "static" / "images"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

FILE_UPLOAD_HANDLERS = ["django.core.files.uploadhandler.MemoryFileUploadHandler"]

DATA_UPLOAD_MAX_MEMORY_SIZE = env_int(
    "SCANNER_MAX_UPLOAD_SIZE", 12 * 1024 * 1024
) + (4 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024 * 1024

SCANNER_MAX_UPLOAD_SIZE = env_int("SCANNER_MAX_UPLOAD_SIZE", 12 * 1024 * 1024)
SCANNER_MAX_PIXELS = env_int("SCANNER_MAX_PIXELS", 25_000_000)
SCANNER_MAX_DIMENSION = env_int("SCANNER_MAX_DIMENSION", 8000)
SCANNER_MIN_DIMENSION = 50
SCANNER_MAX_PAGES = env_int("SCANNER_MAX_PAGES", 30)
SCANNER_MAX_PDF_PAGES = env_int("SCANNER_MAX_PDF_PAGES", 30)


SCANNER_DETECT_MAX_DIM = 720
SCANNER_PREVIEW_MAX_DIM = 1100
SCANNER_PROCESS_MAX_DIM = 2200
SCANNER_EXPORT_MAX_DIM = 4000
SCANNER_MULTIPDF_MAX_DIM = 3000
SCANNER_JPEG_QUALITY = 90
SCANNER_PDF_DPI = 150


SCANNER_QUALITY_THRESHOLDS = {
    "blur_variance_bad": 40,
    "blur_variance_warn": 110,
    "brightness_bad_low": 45,
    "brightness_warn_low": 75,
    "brightness_warn_high": 205,
    "contrast_warn_low": 28,
    "min_dim": 640,
}


SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 60 * 60 * 24 * 30)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_REFERRER_POLICY = "same-origin"


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[{levelname}] {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"level": "INFO", "propagate": True},
        "scanner": {"level": "INFO", "propagate": True},
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
