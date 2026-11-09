"""Apply ordered, idempotent SQL migrations before starting background workers."""
from pathlib import Path

from . import db


def ensure_delivery_schema():
    directory = Path(__file__).resolve().parent.parent / "migrations"
    for path in sorted(directory.glob("*.sql")):
        lines = "\n".join(s for s in path.read_text(encoding="utf-8").splitlines()
                          if not s.strip().startswith("--"))
        with db.engine.begin() as conn:
            for statement in lines.split(";"):
                if statement.strip():
                    conn.execute(db.text(statement))

    from .equipment import seed_catalog
    seed_catalog()
