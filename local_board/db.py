"""Domain layer: one repository, one board, category-typed statuses, free transitions."""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator


SCHEMA_VERSION = 4

# Statements are executed one by one inside a single BEGIN IMMEDIATE transaction,
# so concurrent processes cannot race the migration (executescript would commit early).
SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS board (
        id INTEGER PRIMARY KEY CHECK(id=1),
        prefix TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        -- Allocator for public issue numbers (APP-12): SQLite has no sequences,
        -- and MAX(number)+1 would recycle a number after an admin deletion.
        next_issue_number INTEGER NOT NULL DEFAULT 1,
        defaults_json TEXT NOT NULL DEFAULT '{}',
        agent_policy_json TEXT NOT NULL DEFAULT '{}',
        -- sha256 of the applied project.toml; doctor compares it against the
        -- file on disk to detect unapplied configuration drift.
        config_digest TEXT,
        -- Prefixes this project used before renames. Identifiers are derived
        -- (prefix + number), so textual references like "APP-12" written into
        -- comments before a rename keep resolving through this list.
        former_prefixes_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS actors (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL CHECK(kind IN ('agent','human')),
        role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('admin','member','viewer')),
        token_hash TEXT UNIQUE,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS statuses (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        category TEXT NOT NULL CHECK(category IN ('backlog','unstarted','started','completed','canceled')),
        position INTEGER NOT NULL DEFAULT 0,
        -- 'config' rows are reconciled from project.toml; 'manual' rows were
        -- created ad hoc. Doctor uses this to report drift precisely.
        managed_by TEXT NOT NULL DEFAULT 'manual'
    )""",
    """CREATE TABLE IF NOT EXISTS milestones (
        id INTEGER PRIMARY KEY,
        key TEXT UNIQUE,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        due_at TEXT,
        managed_by TEXT NOT NULL DEFAULT 'manual',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS labels (
        id INTEGER PRIMARY KEY,
        key TEXT UNIQUE,
        name TEXT NOT NULL UNIQUE,
        color TEXT NOT NULL DEFAULT '#64748b',
        managed_by TEXT NOT NULL DEFAULT 'manual'
    )""",
    """CREATE TABLE IF NOT EXISTS issues (
        id INTEGER PRIMARY KEY,
        number INTEGER NOT NULL UNIQUE,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT 'medium',
        assignee_id INTEGER REFERENCES actors(id) ON DELETE SET NULL,
        milestone_id INTEGER REFERENCES milestones(id) ON DELETE SET NULL,
        parent_id INTEGER REFERENCES issues(id) ON DELETE SET NULL,
        -- Ordering inside a status column (kanban order), fed by state_counters.
        position REAL NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL DEFAULT 1,
        -- The claim lease. assignee_id doubles as the lease holder: claiming
        -- sets the assignee, so a separate claimed_by column would only drift.
        -- Datetimes are ISO-8601 UTC TEXT throughout: SQLite has no datetime
        -- type, and ISO text sorts correctly and stays human-readable.
        claimed_at TEXT,
        claim_expires_at TEXT,
        created_by INTEGER NOT NULL REFERENCES actors(id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS issue_labels (
        issue_id INTEGER REFERENCES issues(id) ON DELETE CASCADE,
        label_id INTEGER REFERENCES labels(id) ON DELETE CASCADE,
        PRIMARY KEY(issue_id,label_id)
    )""",
    """CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY,
        issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
        author_id INTEGER NOT NULL REFERENCES actors(id),
        body TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS dependencies (
        issue_id INTEGER REFERENCES issues(id) ON DELETE CASCADE,
        depends_on_id INTEGER REFERENCES issues(id) ON DELETE CASCADE,
        PRIMARY KEY(issue_id,depends_on_id),
        CHECK(issue_id != depends_on_id)
    )""",
    """CREATE TABLE IF NOT EXISTS git_links (
        id INTEGER PRIMARY KEY,
        issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        ref TEXT NOT NULL,
        url TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(issue_id,kind,ref)
    )""",
    # The audit journal and the status history: every status change, claim,
    # release, and edit lands here as an append-only row.
    """CREATE TABLE IF NOT EXISTS activity (
        id INTEGER PRIMARY KEY,
        actor_id INTEGER REFERENCES actors(id) ON DELETE RESTRICT,
        entity_type TEXT NOT NULL,
        entity_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        data_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )""",
    # Per-status allocator for issues.position: appending to a column takes the
    # next integer instead of scanning MAX(position), keeping appends O(1) and
    # race-free inside the write transaction.
    """CREATE TABLE IF NOT EXISTS state_counters (
        status TEXT PRIMARY KEY,
        next_position INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status,position)",
    "CREATE INDEX IF NOT EXISTS idx_activity_entity ON activity(entity_type,entity_id,id DESC)",
    """CREATE TRIGGER IF NOT EXISTS activity_no_update BEFORE UPDATE ON activity
       BEGIN SELECT RAISE(ABORT, 'activity is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS activity_no_delete BEFORE DELETE ON activity
       BEGIN SELECT RAISE(ABORT, 'activity is append-only'); END""",
]

CATEGORIES = ("backlog", "unstarted", "started", "completed", "canceled")
DONE_CATEGORIES = ("completed", "canceled")
PRIORITIES = ("none", "low", "medium", "high", "urgent")
# Branches are deliberately not linkable: the issue identifier in the branch name
# already carries that association inside git itself, and branch refs go stale.
GIT_LINK_KINDS = ("commit", "pr", "mr")

DEFAULT_STATUSES = [
    {"name": "Backlog", "category": "backlog"},
    {"name": "Todo", "category": "unstarted"},
    {"name": "In Progress", "category": "started"},
    {"name": "In Review", "category": "started"},
    {"name": "Done", "category": "completed"},
    {"name": "Canceled", "category": "canceled"},
]


class ConflictError(ValueError):
    """A write was based on stale state or lost an atomic claim race."""


class DatabaseBusyError(RuntimeError):
    """SQLite remained locked after the configured bounded retry window."""


class AuthorizationError(PermissionError):
    """The authenticated actor does not have permission for an operation."""


def now() -> str:
    return datetime.now(UTC).isoformat()


class Board:
    def __init__(
        self,
        path: str | Path = ".local-board/state/board.db",
        *,
        busy_timeout_ms: int = 1000,
        max_lock_retries: int = 6,
        retry_base_seconds: float = 0.01,
    ):
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.max_lock_retries = max_lock_retries
        self.retry_base_seconds = retry_base_seconds

    # -- connections and transactions ------------------------------------------------

    def _open_connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        return db

    @staticmethod
    def _is_lock_error(error: sqlite3.OperationalError) -> bool:
        text = str(error).lower()
        return "locked" in text or "busy" in text

    def _retry_lock(self, operation: Callable[[], Any]) -> Any:
        for attempt in range(self.max_lock_retries + 1):
            try:
                return operation()
            except sqlite3.OperationalError as error:
                if not self._is_lock_error(error):
                    raise
                if attempt == self.max_lock_retries:
                    raise DatabaseBusyError(
                        f"database remained locked after {attempt + 1} attempts "
                        f"(busy_timeout={self.busy_timeout_ms}ms)"
                    ) from error
                delay = self.retry_base_seconds * (2**attempt)
                time.sleep(delay + random.random() * delay)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = self._open_connection()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a write transaction after bounded retries to acquire its lock."""
        db = self._open_connection()
        try:
            self._retry_lock(lambda: db.execute("BEGIN IMMEDIATE"))
            yield db
            self._retry_lock(db.commit)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def init(self) -> None:
        """Create the schema. Safe under concurrent callers: the version is re-read
        inside BEGIN IMMEDIATE, so only one process applies the statements."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version in (1, 2, 3):
                raise RuntimeError(
                    "this database predates the 0.1.0 board format and cannot be upgraded; "
                    "back it up and re-run `local-board init` on a fresh state directory"
                )
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {version} is newer than supported schema {SCHEMA_VERSION}"
                )
            if version == 0:
                for statement in SCHEMA_STATEMENTS:
                    db.execute(statement)
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            if not db.execute("SELECT 1 FROM statuses LIMIT 1").fetchone():
                for index, status in enumerate(DEFAULT_STATUSES):
                    db.execute(
                        "INSERT INTO statuses(name,category,position) VALUES(?,?,?)",
                        (status["name"], status["category"], index),
                    )
        self._enable_wal()

    def _enable_wal(self) -> None:
        """Switch to WAL with bounded retries; concurrent initializers all attempt this.

        WAL (write-ahead logging) appends changes to a side log instead of
        rewriting pages in place, so readers never block the writer and vice
        versa — the property that lets the server answer reads while a write
        transaction is open. The switch itself needs an exclusive lock, hence
        the retry: whichever process wins converts the database for everyone.
        """
        db = self._open_connection()
        try:
            try:
                self._retry_lock(lambda: db.execute("PRAGMA journal_mode=WAL"))
            except DatabaseBusyError:
                mode = db.execute("PRAGMA journal_mode").fetchone()[0]
                if str(mode).lower() != "wal":
                    raise
            db.commit()
        finally:
            db.close()

    def schema_version(self) -> int:
        with self.connect() as db:
            return int(db.execute("PRAGMA user_version").fetchone()[0])

    # -- actors ----------------------------------------------------------------------

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_actor(self, name: str, kind: str = "agent", role: str | None = None) -> dict[str, Any]:
        if kind not in ("agent", "human"):
            raise ValueError("kind must be agent or human")
        if role not in (None, "admin", "member", "viewer"):
            raise ValueError("role must be admin, member, or viewer")
        token = secrets.token_urlsafe(32)
        with self.transaction() as db:
            first = not db.execute("SELECT 1 FROM actors LIMIT 1").fetchone()
            role = role or ("admin" if first else "member")
            cur = db.execute(
                "INSERT INTO actors(name,kind,role,token_hash,created_at) VALUES(?,?,?,?,?)",
                (name, kind, role, self._hash(token), now()),
            )
            self._activity(db, cur.lastrowid, "actor", cur.lastrowid, "created", {"kind": kind, "role": role})
            return {"id": cur.lastrowid, "name": name, "kind": kind, "role": role, "token": token}

    def provision_actor(self, actor: int, name: str, kind: str = "agent", role: str = "member") -> dict[str, Any]:
        """Create an identity through an authenticated administrator, atomically with its audit entry."""
        self.require_role(actor, "admin")
        if kind not in ("agent", "human"):
            raise ValueError("kind must be agent or human")
        if role not in ("admin", "member", "viewer"):
            raise ValueError("role must be admin, member, or viewer")
        token = secrets.token_urlsafe(32)
        with self.transaction() as db:
            cur = db.execute(
                "INSERT INTO actors(name,kind,role,token_hash,created_at) VALUES(?,?,?,?,?)",
                (name, kind, role, self._hash(token), now()),
            )
            self._activity(db, actor, "actor", cur.lastrowid, "created", {"kind": kind, "role": role})
            return {"id": cur.lastrowid, "name": name, "kind": kind, "role": role, "token": token}

    def rotate_actor_token(self, actor: int, target: int | str) -> dict[str, Any]:
        """Invalidate an actor's current token and return its one-time replacement."""
        self.require_role(actor, "admin")
        token = secrets.token_urlsafe(32)
        with self.transaction() as db:
            target_id = self._actor_id(db, target)
            db.execute("UPDATE actors SET token_hash=? WHERE id=?", (self._hash(token), target_id))
            self._activity(db, actor, "actor", target_id, "token_rotated")
        return {**self.get_actor(target_id), "token": token}

    def authenticate(self, token: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT id,name,kind,role FROM actors WHERE token_hash=?", (self._hash(token),)
            ).fetchone()
            return dict(row) if row else None

    def _actor_id(self, db: sqlite3.Connection, actor: int | str) -> int:
        query = "SELECT id FROM actors WHERE id=?" if isinstance(actor, int) else "SELECT id FROM actors WHERE name=?"
        row = db.execute(query, (actor,)).fetchone()
        if not row:
            raise KeyError("actor not found")
        return int(row[0])

    def get_actor(self, actor: int | str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT id,name,kind,role,created_at FROM actors WHERE id=?"
                if isinstance(actor, int)
                else "SELECT id,name,kind,role,created_at FROM actors WHERE name=?",
                (actor,),
            ).fetchone()
            if not row:
                raise KeyError("actor not found")
            return dict(row)

    def list_actors(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT id,name,kind,role,created_at FROM actors ORDER BY name")
            return [dict(row) for row in rows]

    def require_role(self, actor: int, *roles: str) -> dict[str, Any]:
        value = self.get_actor(actor)
        if value["role"] not in roles:
            raise AuthorizationError(f"{value['role']} role cannot perform this operation")
        return value

    def set_actor_role(self, actor: int, target: int | str, role: str) -> dict[str, Any]:
        self.require_role(actor, "admin")
        if role not in ("admin", "member", "viewer"):
            raise ValueError("role must be admin, member, or viewer")
        with self.transaction() as db:
            target_id = self._actor_id(db, target)
            current = db.execute("SELECT role FROM actors WHERE id=?", (target_id,)).fetchone()[0]
            if role != "admin" and current == "admin":
                admins = db.execute("SELECT count(*) FROM actors WHERE role='admin'").fetchone()[0]
                if admins == 1:
                    raise ValueError("cannot remove the last admin")
            db.execute("UPDATE actors SET role=? WHERE id=?", (role, target_id))
            self._activity(db, actor, "actor", target_id, "role_changed", {"role": role})
        return self.get_actor(target_id)

    # -- board and catalog -----------------------------------------------------------

    def configure_board(
        self,
        prefix: str,
        name: str,
        description: str = "",
        *,
        defaults: dict[str, Any] | None = None,
        agent_policy: dict[str, Any] | None = None,
        config_digest: str | None = None,
        db: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        prefix = prefix.upper()
        if not prefix.isalnum() or not 2 <= len(prefix) <= 10:
            raise ValueError("board prefix must be 2-10 alphanumeric characters")
        if db is None:
            with self.transaction() as conn:
                return self.configure_board(
                    prefix, name, description,
                    defaults=defaults, agent_policy=agent_policy, config_digest=config_digest, db=conn,
                )
        stamp = now()
        current = db.execute("SELECT prefix, former_prefixes_json FROM board WHERE id=1").fetchone()
        former: list[str] = json.loads(current["former_prefixes_json"]) if current else []
        if current and current["prefix"] != prefix and current["prefix"] not in former:
            # Remember the outgoing prefix so old textual references keep resolving.
            former.append(current["prefix"])
        former = [item for item in former if item != prefix]
        db.execute(
            "INSERT INTO board(id,prefix,name,description,defaults_json,agent_policy_json,config_digest,"
            "created_at,updated_at) VALUES(1,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET prefix=excluded.prefix,name=excluded.name,"
            "description=excluded.description,defaults_json=excluded.defaults_json,"
            "agent_policy_json=excluded.agent_policy_json,config_digest=excluded.config_digest,"
            "updated_at=excluded.updated_at",
            (
                prefix, name, description,
                json.dumps(defaults or {}), json.dumps(agent_policy or {}), config_digest,
                stamp, stamp,
            ),
        )
        db.execute("UPDATE board SET former_prefixes_json=? WHERE id=1", (json.dumps(former),))
        return self.get_board(db)

    def get_board(self, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        if db is None:
            with self.connect() as conn:
                return self.get_board(conn)
        row = db.execute("SELECT * FROM board WHERE id=1").fetchone()
        if not row:
            raise KeyError("board is not configured; run `local-board init`")
        result = dict(row)
        result["defaults"] = json.loads(result.pop("defaults_json"))
        result["agent_policy"] = json.loads(result.pop("agent_policy_json"))
        result["former_prefixes"] = json.loads(result.pop("former_prefixes_json"))
        return result

    def board_context(self) -> dict[str, Any]:
        """The full board snapshot an agent needs once, at connection time."""
        with self.connect() as db:
            result = self.get_board(db)
            result["statuses"] = [
                dict(row)
                for row in db.execute("SELECT name,category,position FROM statuses ORDER BY position,id")
            ]
            result["labels"] = [
                dict(row) for row in db.execute("SELECT id,key,name,color FROM labels ORDER BY name")
            ]
            result["milestones"] = [
                dict(row)
                for row in db.execute("SELECT id,key,name,description,due_at,managed_by FROM milestones ORDER BY id")
            ]
            result["priorities"] = list(PRIORITIES)
            return result

    def _status(self, db: sqlite3.Connection, name: str) -> dict[str, Any]:
        row = db.execute("SELECT name,category,position FROM statuses WHERE name=?", (name,)).fetchone()
        if not row:
            raise ValueError(f"unknown status: {name}")
        return dict(row)

    def _initial_status(self, db: sqlite3.Connection) -> str:
        for category in ("backlog", "unstarted", "started"):
            row = db.execute(
                "SELECT name FROM statuses WHERE category=? ORDER BY position,id LIMIT 1", (category,)
            ).fetchone()
            if row:
                return row[0]
        raise ValueError("no active statuses are configured")

    def replace_statuses(self, desired: list[dict[str, Any]], *, managed_by: str = "config",
                         db: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        """Reconcile the status catalog; refuses to drop a status still used by issues."""
        if db is None:
            with self.transaction() as conn:
                return self.replace_statuses(desired, managed_by=managed_by, db=conn)
        names = {status["name"] for status in desired}
        used = {row[0] for row in db.execute("SELECT DISTINCT status FROM issues")}
        removed = used - names
        if removed:
            raise ValueError(f"cannot remove statuses used by issues: {', '.join(sorted(removed))}")
        db.execute("DELETE FROM statuses")
        for index, status in enumerate(desired):
            if status["category"] not in CATEGORIES:
                raise ValueError(f"invalid status category: {status['category']}")
            db.execute(
                "INSERT INTO statuses(name,category,position,managed_by) VALUES(?,?,?,?)",
                (status["name"], status["category"], index, managed_by),
            )
        return [dict(row) for row in db.execute("SELECT name,category,position FROM statuses ORDER BY position")]

    # -- milestones and labels -------------------------------------------------------

    def create_milestone(self, actor: int, name: str, description: str = "", due_at: str | None = None,
                         key: str | None = None) -> dict[str, Any]:
        with self.transaction() as db:
            cur = db.execute(
                "INSERT INTO milestones(key,name,description,due_at,created_at) VALUES(?,?,?,?,?)",
                (key, name, description, due_at, now()),
            )
            self._activity(db, actor, "milestone", cur.lastrowid, "created", {"name": name})
            return dict(db.execute("SELECT * FROM milestones WHERE id=?", (cur.lastrowid,)).fetchone())

    def update_milestone(self, actor: int, milestone: int | str, **changes: Any) -> dict[str, Any]:
        allowed = {field: value for field, value in changes.items() if field in {"name", "description", "due_at"}}
        if not allowed:
            raise ValueError("at least one milestone field is required")
        with self.transaction() as db:
            row = self._milestone_row(db, milestone)
            if row["managed_by"] == "config":
                raise ValueError("milestone is managed by .local-board/project.toml")
            assignments = ",".join(f"{field}=?" for field in allowed)
            db.execute(f"UPDATE milestones SET {assignments} WHERE id=?", (*allowed.values(), row["id"]))
            self._activity(db, actor, "milestone", row["id"], "updated", {"fields": sorted(allowed)})
            return dict(db.execute("SELECT * FROM milestones WHERE id=?", (row["id"],)).fetchone())

    def delete_milestone(self, actor: int, milestone: int | str) -> dict[str, Any]:
        with self.transaction() as db:
            row = self._milestone_row(db, milestone)
            if row["managed_by"] == "config":
                raise ValueError("milestone is managed by .local-board/project.toml")
            db.execute("DELETE FROM milestones WHERE id=?", (row["id"],))
            self._activity(db, actor, "milestone", row["id"], "deleted", {"name": row["name"]})
            return {"deleted": True, "id": row["id"]}

    @staticmethod
    def _milestone_row(db: sqlite3.Connection, milestone: int | str) -> sqlite3.Row:
        query = (
            "SELECT * FROM milestones WHERE id=?"
            if isinstance(milestone, int)
            else "SELECT * FROM milestones WHERE key=? OR name=?"
        )
        args = (milestone,) if isinstance(milestone, int) else (milestone, milestone)
        row = db.execute(query, args).fetchone()
        if not row:
            raise KeyError("milestone not found")
        return row

    def create_label(self, actor: int, name: str, color: str = "#64748b", key: str | None = None) -> dict[str, Any]:
        with self.transaction() as db:
            cur = db.execute("INSERT INTO labels(key,name,color) VALUES(?,?,?)", (key, name, color))
            self._activity(db, actor, "label", cur.lastrowid, "created", {"name": name})
            return dict(db.execute("SELECT * FROM labels WHERE id=?", (cur.lastrowid,)).fetchone())

    def update_label(self, actor: int, label: int | str, **changes: Any) -> dict[str, Any]:
        allowed = {field: value for field, value in changes.items() if field in {"name", "color"}}
        if not allowed:
            raise ValueError("at least one label field is required")
        with self.transaction() as db:
            row = self._label_row(db, label)
            if row["managed_by"] == "config":
                raise ValueError("label is managed by .local-board/project.toml")
            assignments = ",".join(f"{field}=?" for field in allowed)
            db.execute(f"UPDATE labels SET {assignments} WHERE id=?", (*allowed.values(), row["id"]))
            self._activity(db, actor, "label", row["id"], "updated", {"fields": sorted(allowed)})
            return dict(db.execute("SELECT * FROM labels WHERE id=?", (row["id"],)).fetchone())

    def delete_label(self, actor: int, label: int | str) -> dict[str, Any]:
        with self.transaction() as db:
            row = self._label_row(db, label)
            if row["managed_by"] == "config":
                raise ValueError("label is managed by .local-board/project.toml")
            db.execute("DELETE FROM labels WHERE id=?", (row["id"],))
            self._activity(db, actor, "label", row["id"], "deleted", {"name": row["name"]})
            return {"deleted": True, "id": row["id"]}

    @staticmethod
    def _label_row(db: sqlite3.Connection, label: int | str) -> sqlite3.Row:
        query = (
            "SELECT * FROM labels WHERE id=?"
            if isinstance(label, int)
            else "SELECT * FROM labels WHERE key=? OR name=?"
        )
        args = (label,) if isinstance(label, int) else (label, label)
        row = db.execute(query, args).fetchone()
        if not row:
            raise KeyError("label not found")
        return row

    def replace_labels(self, desired: list[dict[str, Any]], *, db: sqlite3.Connection) -> None:
        """Config-managed upsert; entities omitted from configuration are kept."""
        for label in desired:
            db.execute(
                "INSERT INTO labels(key,name,color,managed_by) VALUES(?,?,?,'config') "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name,color=excluded.color,managed_by='config'",
                (label["key"], label["name"], label.get("color", "#64748b")),
            )

    def replace_milestones(self, desired: list[dict[str, Any]], *, db: sqlite3.Connection) -> None:
        stamp = now()
        for milestone in desired:
            db.execute(
                "INSERT INTO milestones(key,name,description,due_at,managed_by,created_at) "
                "VALUES(?,?,?,?,'config',?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name,description=excluded.description,"
                "due_at=excluded.due_at,managed_by='config'",
                (milestone["key"], milestone["name"], milestone.get("description", ""),
                 milestone.get("due_at"), stamp),
            )

    # -- issues ----------------------------------------------------------------------

    def resolve_issue(self, issue: int | str, db: sqlite3.Connection | None = None) -> int:
        if db is None:
            with self.connect() as conn:
                return self.resolve_issue(issue, conn)
        if isinstance(issue, int):
            row = db.execute("SELECT id FROM issues WHERE id=?", (issue,)).fetchone()
        else:
            try:
                prefix, raw_number = issue.upper().rsplit("-", 1)
                number = int(raw_number)
            except (ValueError, AttributeError) as exc:
                raise ValueError("issue identifier must look like APP-12") from exc
            board = self.get_board(db)
            # Former prefixes stay valid so references written before a rename
            # (in comments, descriptions, branch names) keep resolving.
            if prefix != board["prefix"] and prefix not in board.get("former_prefixes", []):
                raise KeyError("issue not found")
            row = db.execute("SELECT id FROM issues WHERE number=?", (number,)).fetchone()
        if not row:
            raise KeyError("issue not found")
        return int(row[0])

    def _activity(self, db: sqlite3.Connection, actor: int | None, entity: str, entity_id: int,
                  action: str, data: dict[str, Any] | None = None) -> None:
        db.execute(
            "INSERT INTO activity(actor_id,entity_type,entity_id,action,data_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (actor, entity, entity_id, action, json.dumps(data or {}), now()),
        )

    @staticmethod
    def _next_position(db: sqlite3.Connection, status: str) -> int:
        return int(
            db.execute(
                "INSERT INTO state_counters(status,next_position) VALUES(?,2) "
                "ON CONFLICT(status) DO UPDATE SET next_position=next_position+1 "
                "RETURNING next_position-1",
                (status,),
            ).fetchone()[0]
        )

    def _set_labels(self, db: sqlite3.Connection, issue_id: int, labels: list[str | int]) -> None:
        label_ids = [int(self._label_row(db, label)["id"]) for label in labels]
        db.execute("DELETE FROM issue_labels WHERE issue_id=?", (issue_id,))
        for label_id in label_ids:
            db.execute("INSERT OR IGNORE INTO issue_labels(issue_id,label_id) VALUES(?,?)", (issue_id, label_id))

    def _assert_parent(self, db: sqlite3.Connection, issue_id: int | None, parent_id: int) -> None:
        if not db.execute("SELECT 1 FROM issues WHERE id=?", (parent_id,)).fetchone():
            raise ValueError("parent issue does not exist")
        if issue_id is None:
            return
        if parent_id == issue_id:
            raise ValueError("an issue cannot be its own parent")
        ancestor: int | None = parent_id
        while ancestor is not None:
            if ancestor == issue_id:
                raise ValueError("parent assignment would create a cycle")
            row = db.execute("SELECT parent_id FROM issues WHERE id=?", (ancestor,)).fetchone()
            ancestor = row[0] if row else None

    def create_issue(self, actor: int, title: str, description: str = "", *,
                     priority: str | None = None, status: str | None = None,
                     milestone_id: int | None = None, parent_id: int | None = None,
                     assignee_id: int | None = None, labels: list[str | int] | None = None) -> dict[str, Any]:
        with self.transaction() as db:
            board = self.get_board(db)
            priority = priority or board["defaults"].get("priority", "medium")
            if priority not in PRIORITIES:
                raise ValueError("invalid priority")
            status = status or self._initial_status(db)
            status_row = self._status(db, status)
            if milestone_id is not None:
                self._milestone_row(db, milestone_id)
            if parent_id is not None:
                self._assert_parent(db, None, parent_id)
            if status_row["category"] == "started":
                self._assert_start_allowed(board, assignee_id)
            number = db.execute(
                "UPDATE board SET next_issue_number=next_issue_number+1 WHERE id=1 "
                "RETURNING next_issue_number-1"
            ).fetchone()[0]
            position = self._next_position(db, status)
            stamp = now()
            cur = db.execute(
                "INSERT INTO issues(number,title,description,status,priority,assignee_id,milestone_id,"
                "parent_id,position,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (number, title, description, status, priority, assignee_id, milestone_id,
                 parent_id, position, actor, stamp, stamp),
            )
            if labels:
                self._set_labels(db, cur.lastrowid, labels)
            self._activity(db, actor, "issue", cur.lastrowid, "created",
                           {"identifier": f"{board['prefix']}-{number}"})
            return self.get_issue(cur.lastrowid, db)

    @staticmethod
    def _assert_start_allowed(board: dict[str, Any], assignee_id: int | None) -> None:
        policy = board["agent_policy"]
        if policy.get("require_assignee_before_start", True) and assignee_id is None:
            raise ValueError("issue must be claimed or assigned before it starts")

    _BLOCKED_SQL = (
        "EXISTS(SELECT 1 FROM dependencies d JOIN issues b ON b.id=d.depends_on_id "
        "JOIN statuses bs ON bs.name=b.status "
        "WHERE d.issue_id=i.id AND bs.category NOT IN ('completed','canceled'))"
    )

    def get_issue(self, issue_id: int, db: sqlite3.Connection | None = None, *,
                  comments_limit: int | None = None) -> dict[str, Any]:
        """comments_limit: None = all comments, 0 = none, N > 0 = the last N.
        comments_total is always present, so a trimmed thread is visible as trimmed."""
        if db is None:
            with self.connect() as conn:
                return self.get_issue(issue_id, conn, comments_limit=comments_limit)
        board = self.get_board(db)
        row = db.execute(
            f"SELECT i.*, s.category, {self._BLOCKED_SQL} AS blocked, a.name AS assignee "
            "FROM issues i JOIN statuses s ON s.name=i.status "
            "LEFT JOIN actors a ON a.id=i.assignee_id WHERE i.id=?",
            (issue_id,),
        ).fetchone()
        if not row:
            raise KeyError("issue not found")
        result = dict(row)
        result["blocked"] = bool(result["blocked"])
        result["identifier"] = f"{board['prefix']}-{result['number']}"
        result["labels"] = [
            dict(item)
            for item in db.execute(
                "SELECT l.id,l.key,l.name,l.color FROM labels l "
                "JOIN issue_labels il ON il.label_id=l.id WHERE il.issue_id=? ORDER BY l.name",
                (issue_id,),
            )
        ]
        result["comments_total"] = int(
            db.execute("SELECT count(*) FROM comments WHERE issue_id=?", (issue_id,)).fetchone()[0]
        )
        if comments_limit == 0:
            result["comments"] = []
        else:
            comments_sql = (
                "SELECT c.id,c.author_id,a.name AS author,c.body,c.created_at,c.updated_at "
                "FROM comments c JOIN actors a ON a.id=c.author_id WHERE c.issue_id=?"
            )
            if comments_limit and comments_limit > 0:
                window = db.execute(
                    comments_sql + " ORDER BY c.id DESC LIMIT ?", (issue_id, comments_limit)
                ).fetchall()
                result["comments"] = [dict(item) for item in reversed(window)]
            else:
                result["comments"] = [
                    dict(item) for item in db.execute(comments_sql + " ORDER BY c.id", (issue_id,))
                ]
        result["blocked_by"] = [
            {**dict(item), "identifier": f"{board['prefix']}-{item['number']}",
             "completed": item["category"] in DONE_CATEGORIES}
            for item in db.execute(
                "SELECT b.id,b.number,b.title,b.status,bs.category FROM dependencies d "
                "JOIN issues b ON b.id=d.depends_on_id JOIN statuses bs ON bs.name=b.status "
                "WHERE d.issue_id=? ORDER BY b.number",
                (issue_id,),
            )
        ]
        result["blocks"] = [
            {**dict(item), "identifier": f"{board['prefix']}-{item['number']}"}
            for item in db.execute(
                "SELECT source.id,source.number,source.title,source.status FROM dependencies d "
                "JOIN issues source ON source.id=d.issue_id WHERE d.depends_on_id=? ORDER BY source.number",
                (issue_id,),
            )
        ]
        result["children"] = [
            {**dict(item), "identifier": f"{board['prefix']}-{item['number']}"}
            for item in db.execute(
                "SELECT id,number,title,status FROM issues WHERE parent_id=? ORDER BY number", (issue_id,)
            )
        ]
        result["git_links"] = [
            dict(item)
            for item in db.execute("SELECT * FROM git_links WHERE issue_id=? ORDER BY id", (issue_id,))
        ]
        return result

    def list_issues(self, *, status: str | None = None, milestone_id: int | None = None,
                    assignee_id: int | None = None, label: str | int | None = None,
                    parent_id: int | None = None, query: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT i.id,i.number,i.title,i.status,s.category,i.priority,i.assignee_id,"
            "actor.name AS assignee,i.milestone_id,"
            f"i.parent_id,i.position,i.revision,i.claimed_at,i.claim_expires_at,{self._BLOCKED_SQL} AS blocked "
            "FROM issues i JOIN statuses s ON s.name=i.status "
            "LEFT JOIN actors actor ON actor.id=i.assignee_id WHERE 1=1"
        )
        args: list[Any] = []
        if status:
            sql += " AND i.status=?"
            args.append(status)
        if milestone_id is not None:
            sql += " AND i.milestone_id=?"
            args.append(milestone_id)
        if assignee_id is not None:
            sql += " AND i.assignee_id=?"
            args.append(assignee_id)
        if parent_id is not None:
            sql += " AND i.parent_id=?"
            args.append(parent_id)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sql += " AND (i.title LIKE ? ESCAPE '\\' OR i.description LIKE ? ESCAPE '\\')"
            args.extend([f"%{escaped}%", f"%{escaped}%"])
        with self.connect() as db:
            board = self.get_board(db)
            if label is not None:
                label_id = self._label_row(db, label)["id"]
                sql += " AND EXISTS(SELECT 1 FROM issue_labels il WHERE il.issue_id=i.id AND il.label_id=?)"
                args.append(label_id)
            sql += " ORDER BY s.position,i.position,i.id"
            rows = []
            for row in db.execute(sql, args):
                item = dict(row)
                item["blocked"] = bool(item["blocked"])
                item["identifier"] = f"{board['prefix']}-{item['number']}"
                item["labels"] = []
                rows.append(item)
            if rows:
                by_id = {item["id"]: item for item in rows}
                marks = ",".join("?" for _ in by_id)
                label_rows = db.execute(
                    "SELECT il.issue_id, COALESCE(l.key, l.name) AS label FROM issue_labels il "
                    f"JOIN labels l ON l.id=il.label_id WHERE il.issue_id IN ({marks}) ORDER BY label",
                    tuple(by_id),
                )
                for label_row in label_rows:
                    by_id[label_row["issue_id"]]["labels"].append(label_row["label"])
            return rows

    def update_issue(self, actor: int, issue_id: int, **fields: Any) -> dict[str, Any]:
        expected_revision = fields.pop("expected_revision", None)
        labels = fields.pop("labels", None)
        allowed = {"title", "description", "priority", "status", "assignee_id", "milestone_id",
                   "parent_id", "position"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown issue fields: {', '.join(sorted(unknown))}")
        changes = dict(fields)
        if not changes and labels is None:
            return self.get_issue(issue_id)
        if "priority" in changes and changes["priority"] not in PRIORITIES:
            raise ValueError("invalid priority")
        with self.transaction() as db:
            board = self.get_board(db)
            current = self.get_issue(issue_id, db)
            data: dict[str, Any] = {"fields": sorted(changes)}
            if changes.get("milestone_id") is not None:
                self._milestone_row(db, changes["milestone_id"])
            if changes.get("parent_id") is not None:
                self._assert_parent(db, issue_id, changes["parent_id"])
            if "status" in changes and changes["status"] != current["status"]:
                target = self._status(db, changes["status"])
                assignee = changes.get("assignee_id", current["assignee_id"])
                if target["category"] == "started" and current["category"] != "started":
                    self._assert_start_allowed(board, assignee)
                changes["position"] = self._next_position(db, changes["status"])
                data["status"] = {"from": current["status"], "to": changes["status"]}
                if target["category"] in DONE_CATEGORIES:
                    # The lease guards work in progress; finished work has nothing to lease.
                    # The assignee stays as attribution of who did the work.
                    changes["claimed_at"] = None
                    changes["claim_expires_at"] = None
            if "assignee_id" in changes:
                changes["claimed_at"] = None
                changes["claim_expires_at"] = None
            revoked_from = None
            clears_lease = "claimed_at" in changes and changes["claimed_at"] is None
            if (clears_lease and current["assignee_id"] not in (None, actor)
                    and current["claim_expires_at"] and current["claim_expires_at"] > now()):
                revoked_from = current["assignee"]
                data["lease_revoked_from"] = revoked_from
            expected_revision = current["revision"] if expected_revision is None else expected_revision
            if changes:
                changes["updated_at"] = now()
                changes["revision"] = current["revision"] + 1
                assignments = ",".join(f"{key}=?" for key in changes)
                cur = db.execute(
                    f"UPDATE issues SET {assignments} WHERE id=? AND revision=?",
                    [*changes.values(), issue_id, expected_revision],
                )
                if not cur.rowcount:
                    raise ConflictError(
                        f"issue revision conflict: expected {expected_revision}, "
                        f"current {current['revision']}"
                    )
            elif current["revision"] != expected_revision:
                raise ConflictError(
                    f"issue revision conflict: expected {expected_revision}, "
                    f"current {current['revision']}"
                )
            if labels is not None:
                self._set_labels(db, issue_id, labels)
                data["fields"] = sorted({*data["fields"], "labels"})
            self._activity(db, actor, "issue", issue_id, "updated", data)
            result = self.get_issue(issue_id, db)
            if revoked_from:
                result["lease_revoked_from"] = revoked_from
            return result

    def claim_issue(self, actor: int, issue_id: int, expected_revision: int,
                    lease_seconds: int = 1800, status: str | None = None) -> dict[str, Any]:
        """Claim the issue and, optionally, move it to a status in the same transaction."""
        if not 60 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be between 60 and 86400")
        stamp = datetime.now(UTC)
        expires = stamp + timedelta(seconds=lease_seconds)
        with self.transaction() as db:
            cur = db.execute(
                "UPDATE issues SET assignee_id=?,claimed_at=?,claim_expires_at=?,revision=revision+1,"
                "updated_at=? WHERE id=? AND revision=? AND (assignee_id IS NULL OR assignee_id=? "
                "OR (claim_expires_at IS NOT NULL AND claim_expires_at<=?))",
                (actor, stamp.isoformat(), expires.isoformat(), stamp.isoformat(),
                 issue_id, expected_revision, actor, stamp.isoformat()),
            )
            if not cur.rowcount:
                row = db.execute(
                    "SELECT i.revision, i.claim_expires_at, a.name AS holder FROM issues i "
                    "LEFT JOIN actors a ON a.id=i.assignee_id WHERE i.id=?",
                    (issue_id,),
                ).fetchone()
                if not row:
                    raise KeyError("issue not found")
                message = (
                    f"issue claim conflict: expected revision {expected_revision}, "
                    f"current {row['revision']}"
                )
                if row["holder"] and row["claim_expires_at"] and row["claim_expires_at"] > stamp.isoformat():
                    message += f"; held by {row['holder']} until {row['claim_expires_at']}"
                raise ConflictError(message)
            data: dict[str, Any] = {"lease_seconds": lease_seconds}
            if status is not None:
                current = db.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()[0]
                if status != current:
                    target = self._status(db, status)
                    position = self._next_position(db, status)
                    lease_reset = (
                        ",claimed_at=NULL,claim_expires_at=NULL"
                        if target["category"] in DONE_CATEGORIES
                        else ""
                    )
                    # The claim above already advanced the revision; claim-with-status is one
                    # logical mutation, so the status write must not advance it again.
                    db.execute(
                        f"UPDATE issues SET status=?,position=?,updated_at=?{lease_reset} WHERE id=?",
                        (status, position, stamp.isoformat(), issue_id),
                    )
                    data["status"] = {"from": current, "to": status}
            self._activity(db, actor, "issue", issue_id, "claimed", data)
            return self.get_issue(issue_id, db)

    def release_issue(self, actor: int, issue_id: int, expected_revision: int) -> dict[str, Any]:
        with self.transaction() as db:
            cur = db.execute(
                "UPDATE issues SET assignee_id=NULL,claimed_at=NULL,claim_expires_at=NULL,"
                "revision=revision+1,updated_at=? WHERE id=? AND revision=? AND assignee_id=?",
                (now(), issue_id, expected_revision, actor),
            )
            if not cur.rowcount:
                raise ConflictError(f"issue release conflict: expected revision {expected_revision}")
            self._activity(db, actor, "issue", issue_id, "released")
            return self.get_issue(issue_id, db)

    # -- comments --------------------------------------------------------------------

    def add_comment(self, actor: int, issue_id: int, body: str) -> dict[str, Any]:
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM issues WHERE id=?", (issue_id,)).fetchone():
                raise KeyError("issue not found")
            stamp = now()
            cur = db.execute(
                "INSERT INTO comments(issue_id,author_id,body,created_at,updated_at) VALUES(?,?,?,?,?)",
                (issue_id, actor, body, stamp, stamp),
            )
            self._activity(db, actor, "issue", issue_id, "comment_added", {"comment_id": cur.lastrowid})
            revision = db.execute("SELECT revision FROM issues WHERE id=?", (issue_id,)).fetchone()[0]
            comment = dict(db.execute("SELECT * FROM comments WHERE id=?", (cur.lastrowid,)).fetchone())
            return {**comment, "issue_revision": int(revision)}

    def _comment_row(self, db: sqlite3.Connection, actor: int, comment_id: int) -> sqlite3.Row:
        row = db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not row:
            raise KeyError("comment not found")
        caller = db.execute("SELECT role FROM actors WHERE id=?", (actor,)).fetchone()
        if row["author_id"] != actor and (not caller or caller["role"] != "admin"):
            raise AuthorizationError("only the author or an admin can modify this comment")
        return row

    def update_comment(self, actor: int, comment_id: int, body: str) -> dict[str, Any]:
        with self.transaction() as db:
            row = self._comment_row(db, actor, comment_id)
            db.execute("UPDATE comments SET body=?,updated_at=? WHERE id=?", (body, now(), comment_id))
            self._activity(db, actor, "issue", row["issue_id"], "comment_updated", {"comment_id": comment_id})
            return dict(db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone())

    def delete_comment(self, actor: int, comment_id: int) -> dict[str, Any]:
        with self.transaction() as db:
            row = self._comment_row(db, actor, comment_id)
            db.execute("DELETE FROM comments WHERE id=?", (comment_id,))
            self._activity(db, actor, "issue", row["issue_id"], "comment_deleted", {"comment_id": comment_id})
            return {"deleted": True, "id": comment_id}

    # -- dependencies and git links --------------------------------------------------

    def add_dependency(self, actor: int, issue_id: int, depends_on_id: int) -> dict[str, Any]:
        if issue_id == depends_on_id:
            raise ValueError("an issue cannot block itself")
        with self.transaction() as db:
            for candidate in (issue_id, depends_on_id):
                if not db.execute("SELECT 1 FROM issues WHERE id=?", (candidate,)).fetchone():
                    raise KeyError("issue not found")
            cycle = db.execute(
                "WITH RECURSIVE reach(id) AS ("
                "  SELECT ? UNION SELECT d.depends_on_id FROM dependencies d JOIN reach r ON d.issue_id=r.id"
                ") SELECT 1 FROM reach WHERE id=?",
                (depends_on_id, issue_id),
            ).fetchone()
            if cycle:
                raise ValueError("dependency would create a cycle")
            db.execute(
                "INSERT OR IGNORE INTO dependencies(issue_id,depends_on_id) VALUES(?,?)",
                (issue_id, depends_on_id),
            )
            self._activity(db, actor, "issue", issue_id, "dependency_added", {"depends_on_id": depends_on_id})
            return self.get_issue(issue_id, db)

    def remove_dependency(self, actor: int, issue_id: int, depends_on_id: int) -> dict[str, Any]:
        with self.transaction() as db:
            cur = db.execute(
                "DELETE FROM dependencies WHERE issue_id=? AND depends_on_id=?", (issue_id, depends_on_id)
            )
            if not cur.rowcount:
                raise KeyError("dependency not found")
            self._activity(db, actor, "issue", issue_id, "dependency_removed", {"depends_on_id": depends_on_id})
            return self.get_issue(issue_id, db)

    def add_git_links(self, actor: int, issue_id: int, refs: list[str], kind: str = "commit",
                      url: str | None = None) -> list[dict[str, Any]]:
        """Record one or more refs in a single transaction and return the link rows —
        the confirmation an agent needs, including ids for later update or delete."""
        if kind not in GIT_LINK_KINDS:
            raise ValueError("invalid Git link kind")
        if not refs:
            raise ValueError("at least one ref is required")
        with self.transaction() as db:
            board = self.get_board(db)
            issue = db.execute("SELECT number FROM issues WHERE id=?", (issue_id,)).fetchone()
            if not issue:
                raise KeyError("issue not found")
            identifier = f"{board['prefix']}-{issue['number']}"
            links = []
            for ref in refs:
                db.execute(
                    "INSERT OR IGNORE INTO git_links(issue_id,kind,ref,url,created_at) VALUES(?,?,?,?,?)",
                    (issue_id, kind, ref, url, now()),
                )
                row = db.execute(
                    "SELECT * FROM git_links WHERE issue_id=? AND kind=? AND ref=?",
                    (issue_id, kind, ref),
                ).fetchone()
                links.append({**dict(row), "issue": identifier})
            self._activity(db, actor, "issue", issue_id, "git_link_added",
                           {"kind": kind, "refs": list(refs)})
            return links

    def add_git_link(self, actor: int, issue_id: int, ref: str, kind: str = "commit",
                     url: str | None = None) -> dict[str, Any]:
        return self.add_git_links(actor, issue_id, [ref], kind, url)[0]

    def update_git_link(self, actor: int, link_id: int, **changes: Any) -> dict[str, Any]:
        allowed = {field: value for field, value in changes.items() if field in {"kind", "ref", "url"}}
        if not allowed:
            raise ValueError("at least one Git link field is required")
        if "kind" in allowed and allowed["kind"] not in GIT_LINK_KINDS:
            raise ValueError("invalid Git link kind")
        with self.transaction() as db:
            row = db.execute("SELECT issue_id FROM git_links WHERE id=?", (link_id,)).fetchone()
            if not row:
                raise KeyError("Git link not found")
            assignments = ",".join(f"{field}=?" for field in allowed)
            db.execute(f"UPDATE git_links SET {assignments} WHERE id=?", (*allowed.values(), link_id))
            self._activity(db, actor, "issue", row["issue_id"], "git_link_updated", {"link_id": link_id})
            return dict(db.execute("SELECT * FROM git_links WHERE id=?", (link_id,)).fetchone())

    def delete_git_link(self, actor: int, link_id: int) -> dict[str, Any]:
        with self.transaction() as db:
            row = db.execute("SELECT issue_id FROM git_links WHERE id=?", (link_id,)).fetchone()
            if not row:
                raise KeyError("Git link not found")
            db.execute("DELETE FROM git_links WHERE id=?", (link_id,))
            self._activity(db, actor, "issue", row["issue_id"], "git_link_deleted", {"link_id": link_id})
            return {"deleted": True, "id": link_id}

    # -- activity and dashboard ------------------------------------------------------

    def activity(self, entity_type: str | None = None, entity_id: int | None = None,
                 limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT x.*,a.name AS actor FROM activity x LEFT JOIN actors a ON a.id=x.actor_id WHERE 1=1"
        args: list[Any] = []
        if entity_type:
            sql += " AND entity_type=?"
            args.append(entity_type)
        if entity_id is not None:
            sql += " AND entity_id=?"
            args.append(entity_id)
        sql += " ORDER BY x.id DESC LIMIT ?"
        args.append(limit)
        with self.connect() as db:
            rows = []
            for row in db.execute(sql, args):
                item = dict(row)
                item["data"] = json.loads(item.pop("data_json"))
                rows.append(item)
            issue_ids = {item["entity_id"] for item in rows if item["entity_type"] == "issue"}
            if issue_ids:
                board_row = db.execute("SELECT prefix FROM board WHERE id=1").fetchone()
                if board_row:
                    marks = ",".join("?" for _ in issue_ids)
                    numbers = dict(
                        db.execute(f"SELECT id,number FROM issues WHERE id IN ({marks})", tuple(issue_ids))
                    )
                    for item in rows:
                        if item["entity_type"] == "issue" and item["entity_id"] in numbers:
                            item["identifier"] = f"{board_row[0]}-{numbers[item['entity_id']]}"
            return rows

    def dashboard(self) -> dict[str, Any]:
        return {
            "board": self.board_context(),
            "issues": self.list_issues(),
            "actors": self.list_actors(),
            "activity": self.activity(limit=30),
        }
