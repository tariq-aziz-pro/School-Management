import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Backup the default database (SQLite file copy, or pg_dump for PostgreSQL)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--out-dir",
            default="",
            help="Output directory (default: BACKUP_DIR env or <project>/backups).",
        )

    def handle(self, *args, **options):
        raw_out = (options.get("out_dir") or "").strip()
        if raw_out:
            out_dir = Path(raw_out)
        else:
            env_dir = os.environ.get("BACKUP_DIR", "").strip()
            out_dir = Path(env_dir) if env_dir else Path(settings.BASE_DIR) / "backups"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        engine = settings.DATABASES["default"]["ENGINE"]

        if engine == "django.db.backends.sqlite3":
            src = settings.DATABASES["default"]["NAME"]
            src_path = Path(src)
            if not src_path.is_file():
                raise CommandError(f"SQLite database file not found: {src_path}")
            dest = out_dir / f"school_db_{ts}.sqlite3"
            shutil.copy2(src_path, dest)
            self.stdout.write(self.style.SUCCESS(f"Copied SQLite database to {dest}"))
            return

        if engine == "django.db.backends.postgresql":
            db = settings.DATABASES["default"]
            exe = shutil.which("pg_dump")
            if not exe:
                raise CommandError(
                    "pg_dump not found in PATH. Install PostgreSQL client tools, "
                    "or run a server-side backup."
                )
            dest = out_dir / f"school_db_{ts}.sql"
            env = os.environ.copy()
            env["PGPASSWORD"] = str(db.get("PASSWORD") or "")
            host = db.get("HOST") or "localhost"
            port = str(db.get("PORT") or "5432")
            cmd = [
                exe,
                "-h",
                host,
                "-p",
                port,
                "-U",
                db["USER"],
                "-d",
                db["NAME"],
                "-F",
                "p",
                "-f",
                str(dest),
            ]
            try:
                subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as e:
                detail = (e.stderr or e.stdout or "").strip()
                raise CommandError(f"pg_dump failed:\n{detail}") from e
            self.stdout.write(self.style.SUCCESS(f"Wrote PostgreSQL dump to {dest}"))
            return

        raise CommandError(f"Unsupported database ENGINE: {engine}")
