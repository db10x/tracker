import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
BACKUP_DIR = PROJECT_DIR / "backups"

load_dotenv(PROJECT_DIR / ".env")


def build_connection_url(database_url):
    try:
        parsed_url = urlsplit(database_url)
        host = parsed_url.hostname
        parsed_url.port
    except ValueError as error:
        raise ValueError("DATABASE_URL non è un URI PostgreSQL valido.") from error

    if parsed_url.scheme not in {"postgres", "postgresql"}:
        raise ValueError(
            "DATABASE_URL deve usare lo schema postgres:// o postgresql://."
        )
    if not host or not parsed_url.path.strip("/"):
        raise ValueError("DATABASE_URL deve includere host e nome del database.")
    if any(key.lower() == "password" for key, _ in parse_qsl(parsed_url.query)):
        raise ValueError(
            "Inserisci la password nella sezione user:password di DATABASE_URL, "
            "non come parametro query."
        )

    user_info, separator, host_info = parsed_url.netloc.rpartition("@")
    if separator:
        username = user_info.partition(":")[0]
        netloc = f"{username}@{host_info}"
        password = (
            unquote(parsed_url.password)
            if parsed_url.password is not None
            else None
        )
    else:
        netloc = parsed_url.netloc
        password = None

    safe_url = urlunsplit(
        (parsed_url.scheme, netloc, parsed_url.path, parsed_url.query, "")
    )
    return safe_url, password


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "Errore: DATABASE_URL non è impostata nell'ambiente o nel file .env.",
            file=sys.stderr,
        )
        return 1

    configured_pg_dump = os.environ.get("PG_DUMP_PATH", "").strip()
    if configured_pg_dump:
        pg_dump_path = Path(configured_pg_dump)
        if not pg_dump_path.is_absolute():
            pg_dump_path = PROJECT_DIR / pg_dump_path
        if not pg_dump_path.is_file():
            print(
                f"Errore: PG_DUMP_PATH non indica un file esistente: {pg_dump_path}",
                file=sys.stderr,
            )
            return 1
        pg_dump = str(pg_dump_path.resolve())
    else:
        pg_dump = shutil.which("pg_dump")
        if pg_dump is None:
            print(
                "Errore: pg_dump non è installato o non è disponibile nel PATH. "
                "Imposta PG_DUMP_PATH nel file .env oppure installa gli strumenti "
                "client PostgreSQL.",
                file=sys.stderr,
            )
            return 1

    try:
        safe_url, password = build_connection_url(database_url)
    except ValueError as error:
        print(f"Errore: {error}", file=sys.stderr)
        return 1

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = BACKUP_DIR / f"supabase_backup_{timestamp}.dump"
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=BACKUP_DIR,
            prefix=".supabase_backup_",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        process_environment = os.environ.copy()
        process_environment.pop("DATABASE_URL", None)
        if password is not None:
            process_environment["PGPASSWORD"] = password

        subprocess.run(
            [
                pg_dump,
                "--format=custom",
                "--no-password",
                "--file",
                str(temporary_path),
                "--dbname",
                safe_url,
            ],
            check=True,
            env=process_environment,
        )

        if temporary_path.stat().st_size == 0:
            raise OSError("pg_dump ha prodotto un file vuoto.")

        os.replace(temporary_path, backup_path)
        print(f"Backup completato: {backup_path}")
        return 0
    except subprocess.CalledProcessError as error:
        print(
            f"Errore: pg_dump è terminato con codice {error.returncode}; "
            "il backup non è stato completato.",
            file=sys.stderr,
        )
        return 1
    except OSError as error:
        print(f"Errore durante il backup: {error}", file=sys.stderr)
        return 1
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError as error:
                print(
                    f"Avviso: impossibile rimuovere il file temporaneo "
                    f"{temporary_path}: {error}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    raise SystemExit(main())
