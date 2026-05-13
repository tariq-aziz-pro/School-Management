from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Optional local overrides: copy `.env.example` to `.env` in the project root.
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: tuple[str, ...]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return int(str(raw).strip())


# SECURITY: In production set DJANGO_SECRET_KEY to a long random value and DJANGO_DEBUG=0.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-not-for-production-change-via-env",
)

DEBUG = _env_bool("DJANGO_DEBUG", default=True)

ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", ("127.0.0.1", "localhost"))

# When you serve the site over HTTPS, set DJANGO_HTTPS=1 so cookies are marked Secure.
USE_HTTPS = _env_bool("DJANGO_HTTPS", default=False)

if not DEBUG:
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS must be non-empty when DJANGO_DEBUG=0. "
            "Set DJANGO_ALLOWED_HOSTS (comma-separated)."
        )
    if "insecure" in SECRET_KEY.lower() or len(SECRET_KEY) < 40:
        raise ImproperlyConfigured(
            "Set a strong DJANGO_SECRET_KEY (at least 40 random characters) when DJANGO_DEBUG=0."
        )


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'main_app',
    'import_export',
    'crispy_forms',
    'crispy_bootstrap5',
]


MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'main_app.middleware.SchoolAccessMiddleware',
]

ROOT_URLCONF = 'school_db.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.media',  # Add this to make MEDIA_URL available
                'main_app.context_processors.active_session',
                'main_app.context_processors.user_roles',
            ],
        },
    },
]

WSGI_APPLICATION = 'school_db.wsgi.application'


# Database: SQLite by default; set DJANGO_USE_POSTGRES=1 for PostgreSQL (see .env.example).
USE_POSTGRES = _env_bool("DJANGO_USE_POSTGRES", default=False)

if USE_POSTGRES:
    try:
        import psycopg  # noqa: F401
    except ImportError as exc:
        raise ImproperlyConfigured(
            'PostgreSQL is enabled (DJANGO_USE_POSTGRES=1). Install: pip install "psycopg[binary]"'
        ) from exc
    _pg_missing = []
    if not os.environ.get("POSTGRES_DB"):
        _pg_missing.append("POSTGRES_DB")
    if not os.environ.get("POSTGRES_USER"):
        _pg_missing.append("POSTGRES_USER")
    if _pg_missing:
        raise ImproperlyConfigured(
            "DJANGO_USE_POSTGRES=1 but these variables are missing: "
            + ", ".join(_pg_missing)
        )
    _conn_max_age = _env_int(
        "POSTGRES_CONN_MAX_AGE",
        60 if not DEBUG else 0,
    )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ["POSTGRES_USER"],
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": _conn_max_age,
        }
    }
    _sslmode = os.environ.get("POSTGRES_SSLMODE", "").strip()
    if _sslmode:
        DATABASES["default"]["OPTIONS"] = {"sslmode": _sslmode}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True




STATIC_URL = 'static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Add at the bottom of settings.py
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')


# Create a temp directory for temporary image storage
TEMP_DIR = os.path.join(MEDIA_ROOT, 'temp')
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# Custom user model
AUTH_USER_MODEL = 'main_app.CustomUser'

#for activations email
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'noreply@powerlink.local'

# Custom login redirect
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'


LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'main_app.views': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },
}


AUTHENTICATION_BACKENDS = ['main_app.views.CustomAuthBackend']


SESSION_COOKIE_SECURE = USE_HTTPS
CSRF_COOKIE_SECURE = USE_HTTPS
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'

if USE_HTTPS:
    CSRF_TRUSTED_ORIGINS = _env_list(
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        tuple(f"https://{h}" for h in ALLOWED_HOSTS if h not in ("127.0.0.1", "localhost", "testserver")),
    )
    if not CSRF_TRUSTED_ORIGINS:
        # e.g. set DJANGO_CSRF_TRUSTED_ORIGINS=https://your.school.edu
        CSRF_TRUSTED_ORIGINS = []