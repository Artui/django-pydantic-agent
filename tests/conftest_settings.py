from __future__ import annotations

SECRET_KEY = "test"
DEBUG = False
ALLOWED_HOSTS = ["*"]
USE_TZ = True

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    # Opt-in reference store app — exercises the shipped models + migration.
    "django_pydantic_agent.contrib.store",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

MIDDLEWARE: list[str] = []

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# In-memory file storage so the reference attachment store's FileField never
# touches disk during tests.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
