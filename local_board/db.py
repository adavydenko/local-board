from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS actors (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('agent','human')),
  role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('admin','member','viewer')),
  token_hash TEXT UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  next_issue_number INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS milestones (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', due_at TEXT, created_at TEXT NOT NULL,
  UNIQUE(project_id,name)
);
CREATE TABLE IF NOT EXISTS workflows (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  issue_type TEXT NOT NULL, states_json TEXT NOT NULL, transitions_json TEXT NOT NULL,
  UNIQUE(project_id,issue_type)
);
CREATE TABLE IF NOT EXISTS releases (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned'
    CHECK(status IN ('planned','active','released','cancelled')),
  description TEXT NOT NULL DEFAULT '', target_at TEXT, released_at TEXT,
  revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(project_id,version)
);
CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  milestone_id INTEGER REFERENCES milestones(id) ON DELETE SET NULL,
  release_id INTEGER REFERENCES releases(id) ON DELETE SET NULL, number INTEGER NOT NULL,
  type TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'medium', assignee_id INTEGER REFERENCES actors(id) ON DELETE SET NULL,
  reviewer_id INTEGER REFERENCES actors(id) ON DELETE SET NULL, position REAL NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 1, claimed_at TEXT, claim_expires_at TEXT,
  created_by INTEGER NOT NULL REFERENCES actors(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(project_id,number)
);
CREATE TABLE IF NOT EXISTS checklist_items (
  id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
  text TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0, position REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
  author_id INTEGER NOT NULL REFERENCES actors(id), body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS labels (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#64748b', UNIQUE(project_id,name));
CREATE TABLE IF NOT EXISTS issue_labels (issue_id INTEGER REFERENCES issues(id) ON DELETE CASCADE, label_id INTEGER REFERENCES labels(id) ON DELETE CASCADE, PRIMARY KEY(issue_id,label_id));
CREATE TABLE IF NOT EXISTS dependencies (issue_id INTEGER REFERENCES issues(id) ON DELETE CASCADE, depends_on_id INTEGER REFERENCES issues(id) ON DELETE CASCADE, kind TEXT NOT NULL DEFAULT 'blocks', PRIMARY KEY(issue_id,depends_on_id,kind), CHECK(issue_id != depends_on_id));
CREATE TABLE IF NOT EXISTS attachments (id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE, name TEXT NOT NULL, path TEXT NOT NULL, media_type TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS git_links (id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE, kind TEXT NOT NULL, ref TEXT NOT NULL, url TEXT, created_at TEXT NOT NULL, UNIQUE(issue_id,kind,ref));
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES actors(id) ON DELETE SET NULL, entity_type TEXT NOT NULL,
  entity_id INTEGER NOT NULL, action TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_counters (
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  status TEXT NOT NULL, next_position INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(project_id,status)
);
CREATE INDEX IF NOT EXISTS idx_issues_project_status ON issues(project_id,status,position);
CREATE INDEX IF NOT EXISTS idx_activity_entity ON activity(entity_type,entity_id,id DESC);
CREATE TRIGGER IF NOT EXISTS activity_no_update BEFORE UPDATE ON activity
BEGIN SELECT RAISE(ABORT, 'activity is append-only'); END;
CREATE TRIGGER IF NOT EXISTS activity_no_delete BEFORE DELETE ON activity
BEGIN SELECT RAISE(ABORT, 'activity is append-only'); END;
"""

MIGRATION_2 = """
ALTER TABLE projects ADD COLUMN managed_by TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE milestones ADD COLUMN key TEXT;
ALTER TABLE milestones ADD COLUMN managed_by TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE workflows ADD COLUMN managed_by TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE labels ADD COLUMN key TEXT;
ALTER TABLE labels ADD COLUMN managed_by TEXT NOT NULL DEFAULT 'manual';
CREATE UNIQUE INDEX IF NOT EXISTS idx_milestones_project_key ON milestones(project_id,key) WHERE key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_labels_project_key ON labels(project_id,key) WHERE key IS NOT NULL;
CREATE TABLE IF NOT EXISTS config_applies (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  digest TEXT NOT NULL, schema_version INTEGER NOT NULL, diff_json TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_config (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  digest TEXT NOT NULL, schema_version INTEGER NOT NULL, defaults_json TEXT NOT NULL DEFAULT '{}',
  agent_policy_json TEXT NOT NULL DEFAULT '{}', applied_at TEXT NOT NULL
);
"""

SCHEMA_VERSION = 2
MIGRATIONS = {1: SCHEMA, 2: MIGRATION_2}

ISSUE_TYPES = ("task", "bug", "feature", "chore", "epic")
PRIORITIES = ("none", "low", "medium", "high", "urgent")
DEFAULT_STATES = ["backlog", "todo", "in_progress", "in_review", "done", "cancelled"]


class ConflictError(ValueError):
    """A write was based on stale state or lost an atomic claim race."""


class AuthorizationError(PermissionError):
    """The authenticated actor does not have permission for an operation."""


def now() -> str:
    return datetime.now(UTC).isoformat()


class Board:
    def __init__(self, path: str | Path = ".local-board/board.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def init(self) -> None:
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {version} is newer than supported schema {SCHEMA_VERSION}"
                )
            for target in range(version + 1, SCHEMA_VERSION + 1):
                script = MIGRATIONS[target]
                db.executescript(
                    f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version={target};\nCOMMIT;"
                )

    def schema_version(self) -> int:
        with self.connect() as db:
            return int(db.execute("PRAGMA user_version").fetchone()[0])

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_actor(self, name: str, kind: str = "agent", role: str | None = None) -> dict[str, Any]:
        if kind not in ("agent", "human"):
            raise ValueError("kind must be agent or human")
        if role not in (None, "admin", "member", "viewer"):
            raise ValueError("role must be admin, member, or viewer")
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            role = role or ("admin" if not db.execute("SELECT 1 FROM actors LIMIT 1").fetchone() else "member")
            cur = db.execute("INSERT INTO actors(name,kind,role,token_hash,created_at) VALUES(?,?,?,?,?)", (name, kind, role, self._hash(token), now()))
            return {"id": cur.lastrowid, "name": name, "kind": kind, "role": role, "token": token}

    def authenticate(self, token: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT id,name,kind,role FROM actors WHERE token_hash=?", (self._hash(token),)).fetchone()
            return dict(row) if row else None

    def get_actor(self, actor: int | str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT id,name,kind,role,created_at FROM actors WHERE id=?" if isinstance(actor, int) else "SELECT id,name,kind,role,created_at FROM actors WHERE name=?", (actor,)).fetchone()
            if not row: raise KeyError("actor not found")
            return dict(row)

    def list_actors(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,name,kind,role,created_at FROM actors ORDER BY name")]

    def require_role(self, actor: int, *roles: str) -> dict[str, Any]:
        value = self.get_actor(actor)
        if value["role"] not in roles:
            raise AuthorizationError(f"{value['role']} role cannot perform this operation")
        return value

    def set_actor_role(self, actor: int, target: int | str, role: str) -> dict[str, Any]:
        self.require_role(actor, "admin")
        if role not in ("admin", "member", "viewer"):
            raise ValueError("role must be admin, member, or viewer")
        target_id = self.get_actor(target)["id"]
        with self.connect() as db:
            if role != "admin" and db.execute("SELECT role FROM actors WHERE id=?", (target_id,)).fetchone()[0] == "admin":
                if db.execute("SELECT count(*) FROM actors WHERE role='admin'").fetchone()[0] == 1:
                    raise ValueError("cannot remove the last admin")
            db.execute("UPDATE actors SET role=? WHERE id=?", (role, target_id))
            self._activity(db, actor, "actor", target_id, "role_changed", {"role": role})
        return self.get_actor(target_id)

    def resolve_project(self, project: int | str, db: sqlite3.Connection | None = None) -> int:
        if db is None:
            with self.connect() as conn: return self.resolve_project(project, conn)
        row = db.execute("SELECT id FROM projects WHERE id=?" if isinstance(project, int) else "SELECT id FROM projects WHERE key=?", (project if isinstance(project, int) else project.upper(),)).fetchone()
        if not row: raise KeyError("project not found")
        return int(row[0])

    def resolve_issue(self, issue: int | str, db: sqlite3.Connection | None = None) -> int:
        if db is None:
            with self.connect() as conn: return self.resolve_issue(issue, conn)
        if isinstance(issue, int):
            row = db.execute("SELECT id FROM issues WHERE id=?", (issue,)).fetchone()
        else:
            try: key, number = issue.upper().rsplit("-", 1); number = int(number)
            except (ValueError, AttributeError) as exc: raise ValueError("issue identifier must look like APP-12") from exc
            row = db.execute("SELECT i.id FROM issues i JOIN projects p ON p.id=i.project_id WHERE p.key=? AND i.number=?", (key, number)).fetchone()
        if not row: raise KeyError("issue not found")
        return int(row[0])

    def _activity(self, db: sqlite3.Connection, actor: int | None, entity: str, entity_id: int, action: str, data: dict[str, Any] | None = None) -> None:
        db.execute("INSERT INTO activity(actor_id,entity_type,entity_id,action,data_json,created_at) VALUES(?,?,?,?,?,?)", (actor, entity, entity_id, action, json.dumps(data or {}), now()))

    @staticmethod
    def _assert_milestone_project(db: sqlite3.Connection, milestone_id: int | None, project_id: int) -> None:
        if milestone_id is not None and not db.execute("SELECT 1 FROM milestones WHERE id=? AND project_id=?", (milestone_id, project_id)).fetchone():
            raise ValueError("milestone belongs to another project or does not exist")

    @staticmethod
    def _next_position(db: sqlite3.Connection, project_id: int, status: str) -> int:
        return int(db.execute(
            "INSERT INTO state_counters(project_id,status,next_position) VALUES(?,?,2) "
            "ON CONFLICT(project_id,status) DO UPDATE SET next_position=next_position+1 "
            "RETURNING next_position-1",
            (project_id, status),
        ).fetchone()[0])

    @staticmethod
    def _is_blocked(db: sqlite3.Connection, issue_id: int) -> bool:
        rows = db.execute("SELECT target.status,w.transitions_json FROM dependencies d JOIN issues target ON target.id=d.depends_on_id JOIN workflows w ON w.project_id=target.project_id AND w.issue_type=target.type WHERE d.issue_id=? AND d.kind='blocks'", (issue_id,))
        return any(any(source == row["status"] for source, _ in json.loads(row["transitions_json"])) for row in rows)

    def create_project(self, actor: int, key: str, name: str, description: str = "") -> dict[str, Any]:
        key = key.upper()
        if not key.isalnum() or not (2 <= len(key) <= 10):
            raise ValueError("project key must be 2-10 alphanumeric characters")
        stamp = now()
        with self.connect() as db:
            cur = db.execute("INSERT INTO projects(key,name,description,created_at,updated_at) VALUES(?,?,?,?,?)", (key, name, description, stamp, stamp))
            pid = cur.lastrowid
            transitions = [[DEFAULT_STATES[i], DEFAULT_STATES[i + 1]] for i in range(len(DEFAULT_STATES) - 2)] + [[s, "cancelled"] for s in DEFAULT_STATES[:-1]]
            for issue_type in ISSUE_TYPES:
                db.execute("INSERT INTO workflows(project_id,issue_type,states_json,transitions_json) VALUES(?,?,?,?)", (pid, issue_type, json.dumps(DEFAULT_STATES), json.dumps(transitions)))
            self._activity(db, actor, "project", pid, "created", {"key": key})
            return self.get_project(pid, db)

    def get_project(self, project_id: int, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        if db is None:
            with self.connect() as conn:
                return self.get_project(project_id, conn)
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError("project not found")
        return dict(row)

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY id")]

    def project_context(self, project: int | str) -> dict[str, Any]:
        with self.connect() as db:
            project_id = self.resolve_project(project, db)
            result = self.get_project(project_id, db)
            result["workflows"] = [{**dict(row), "states": json.loads(row["states_json"]), "transitions": json.loads(row["transitions_json"])} for row in db.execute("SELECT * FROM workflows WHERE project_id=? ORDER BY issue_type", (project_id,))]
            for workflow in result["workflows"]: workflow.pop("states_json"); workflow.pop("transitions_json")
            result["labels"] = [dict(row) for row in db.execute("SELECT * FROM labels WHERE project_id=? ORDER BY name", (project_id,))]
            result["milestones"] = [dict(row) for row in db.execute("SELECT * FROM milestones WHERE project_id=? ORDER BY id", (project_id,))]
            result["releases"] = [dict(row) for row in db.execute("SELECT * FROM releases WHERE project_id=? ORDER BY id DESC", (project_id,))]
            configured = db.execute("SELECT defaults_json,agent_policy_json,digest FROM project_config WHERE project_id=?", (project_id,)).fetchone()
            result["defaults"] = json.loads(configured["defaults_json"]) if configured else {}
            result["agent_policy"] = json.loads(configured["agent_policy_json"]) if configured else {}
            result["config_digest"] = configured["digest"] if configured else None
            return result

    def create_milestone(self, actor: int, project_id: int, name: str, description: str = "", due_at: str | None = None, key: str | None = None) -> dict[str, Any]:
        with self.connect() as db:
            cur = db.execute("INSERT INTO milestones(project_id,key,name,description,due_at,created_at) VALUES(?,?,?,?,?,?)", (project_id, key, name, description, due_at, now()))
            self._activity(db, actor, "milestone", cur.lastrowid, "created")
            return dict(db.execute("SELECT * FROM milestones WHERE id=?", (cur.lastrowid,)).fetchone())

    def set_workflow(self, actor: int, project_id: int, issue_type: str, states: list[str], transitions: list[list[str]]) -> dict[str, Any]:
        if issue_type not in ISSUE_TYPES or not states or any(a not in states or b not in states for a, b in transitions):
            raise ValueError("invalid workflow")
        with self.connect() as db:
            current = db.execute("SELECT managed_by FROM workflows WHERE project_id=? AND issue_type=?", (project_id, issue_type)).fetchone()
            if current and current["managed_by"] == "config":
                raise ValueError("workflow is managed by .local-board/project.toml")
            db.execute("INSERT INTO workflows(project_id,issue_type,states_json,transitions_json) VALUES(?,?,?,?) ON CONFLICT(project_id,issue_type) DO UPDATE SET states_json=excluded.states_json, transitions_json=excluded.transitions_json", (project_id, issue_type, json.dumps(states), json.dumps(transitions)))
            self._activity(db, actor, "project", project_id, "workflow_changed", {"issue_type": issue_type})
        return {"project_id": project_id, "issue_type": issue_type, "states": states, "transitions": transitions}

    def create_release(self, actor: int, project_id: int, name: str, version: str, description: str = "", target_at: str | None = None) -> dict[str, Any]:
        stamp = now()
        with self.connect() as db:
            cur = db.execute(
                "INSERT INTO releases(project_id,name,version,description,target_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (project_id, name, version, description, target_at, stamp, stamp),
            )
            self._activity(db, actor, "release", cur.lastrowid, "created", {"version": version})
            return dict(db.execute("SELECT * FROM releases WHERE id=?", (cur.lastrowid,)).fetchone())

    def list_releases(self, project: int | str) -> list[dict[str, Any]]:
        with self.connect() as db:
            project_id = self.resolve_project(project, db)
            return [dict(row) for row in db.execute("SELECT * FROM releases WHERE project_id=? ORDER BY id DESC", (project_id,))]

    def transition_release(self, actor: int, release_id: int, status: str, expected_revision: int) -> dict[str, Any]:
        allowed = {"planned": {"active", "cancelled"}, "active": {"released", "cancelled"}, "released": set(), "cancelled": set()}
        with self.connect() as db:
            release = db.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
            if not release: raise KeyError("release not found")
            if release["revision"] != expected_revision: raise ConflictError("stale release revision")
            if status not in allowed[release["status"]]: raise ValueError(f"invalid release transition {release['status']} -> {status}")
            stamp = now(); released_at = stamp if status == "released" else None
            db.execute("UPDATE releases SET status=?,released_at=?,revision=revision+1,updated_at=? WHERE id=?", (status, released_at, stamp, release_id))
            self._activity(db, actor, "release", release_id, "transitioned", {"from": release["status"], "to": status})
            return dict(db.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone())

    def create_issue(self, actor: int, project_id: int, title: str, issue_type: str | None = None, description: str = "", priority: str | None = None, milestone_id: int | None = None, release_id: int | None = None, assignee_id: int | None = None, reviewer_id: int | None = None) -> dict[str, Any]:
        with self.connect() as db:
            self._assert_milestone_project(db, milestone_id, project_id)
            if release_id is not None and not db.execute("SELECT 1 FROM releases WHERE id=? AND project_id=?", (release_id, project_id)).fetchone():
                raise ValueError("release belongs to another project or does not exist")
            configured = db.execute("SELECT defaults_json FROM project_config WHERE project_id=?", (project_id,)).fetchone()
            defaults = json.loads(configured[0]) if configured else {}
            issue_type = issue_type or defaults.get("issue_type", "task")
            priority = priority or defaults.get("priority", "medium")
            if issue_type not in ISSUE_TYPES or priority not in PRIORITIES:
                raise ValueError("invalid issue type or priority")
            wf = db.execute("SELECT states_json FROM workflows WHERE project_id=? AND issue_type=?", (project_id, issue_type)).fetchone()
            if not wf:
                raise KeyError("workflow not found")
            status = json.loads(wf[0])[0]
            allocated = db.execute("UPDATE projects SET next_issue_number=next_issue_number+1 WHERE id=? RETURNING next_issue_number-1", (project_id,)).fetchone()
            if not allocated:
                raise KeyError("project not found")
            number = allocated[0]
            position = self._next_position(db, project_id, status)
            stamp = now()
            cur = db.execute("INSERT INTO issues(project_id,milestone_id,release_id,number,type,title,description,status,priority,assignee_id,reviewer_id,position,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (project_id, milestone_id, release_id, number, issue_type, title, description, status, priority, assignee_id, reviewer_id, position, actor, stamp, stamp))
            self._activity(db, actor, "issue", cur.lastrowid, "created")
            return self.get_issue(cur.lastrowid, db)

    def get_issue(self, issue_id: int, db: sqlite3.Connection | None = None) -> dict[str, Any]:
        if db is None:
            with self.connect() as conn:
                return self.get_issue(issue_id, conn)
        row = db.execute("SELECT i.*,p.key, p.key || '-' || i.number AS identifier, a.name assignee, r.name reviewer FROM issues i JOIN projects p ON p.id=i.project_id LEFT JOIN actors a ON a.id=i.assignee_id LEFT JOIN actors r ON r.id=i.reviewer_id WHERE i.id=?", (issue_id,)).fetchone()
        if not row:
            raise KeyError("issue not found")
        result = dict(row)
        result["checklist"] = [dict(r) for r in db.execute("SELECT * FROM checklist_items WHERE issue_id=? ORDER BY position,id", (issue_id,))]
        result["labels"] = [dict(r) for r in db.execute("SELECT l.* FROM labels l JOIN issue_labels il ON il.label_id=l.id WHERE il.issue_id=?", (issue_id,))]
        return result

    def get_issue_context(self, issue: int | str) -> dict[str, Any]:
        with self.connect() as db:
            issue_id = self.resolve_issue(issue, db)
            result = self.get_issue(issue_id, db)
            result["comments"] = [dict(row) for row in db.execute("SELECT c.*,a.name author FROM comments c JOIN actors a ON a.id=c.author_id WHERE c.issue_id=? ORDER BY c.id", (issue_id,))]
            result["dependencies"] = [dict(row) for row in db.execute("SELECT d.*,p.key || '-' || target.number identifier,target.title,target.status,target.type,w.transitions_json FROM dependencies d JOIN issues target ON target.id=d.depends_on_id JOIN projects p ON p.id=target.project_id JOIN workflows w ON w.project_id=target.project_id AND w.issue_type=target.type WHERE d.issue_id=?", (issue_id,))]
            for dependency in result["dependencies"]:
                transitions = json.loads(dependency.pop("transitions_json"))
                dependency["completed"] = not any(source == dependency["status"] for source, _ in transitions)
            result["blocked"] = any(dependency["kind"] == "blocks" and not dependency["completed"] for dependency in result["dependencies"])
            result["dependents"] = [dict(row) for row in db.execute("SELECT d.*,p.key || '-' || source.number identifier,source.title FROM dependencies d JOIN issues source ON source.id=d.issue_id JOIN projects p ON p.id=source.project_id WHERE d.depends_on_id=?", (issue_id,))]
            result["attachments"] = [dict(row) for row in db.execute("SELECT * FROM attachments WHERE issue_id=? ORDER BY id", (issue_id,))]
            result["git_links"] = [dict(row) for row in db.execute("SELECT * FROM git_links WHERE issue_id=? ORDER BY id", (issue_id,))]
            workflow = db.execute("SELECT states_json,transitions_json FROM workflows WHERE project_id=? AND issue_type=?", (result["project_id"], result["type"])).fetchone()
            transitions = json.loads(workflow["transitions_json"])
            result["available_transitions"] = [target for source, target in transitions if source == result["status"]]
            configured = db.execute("SELECT agent_policy_json FROM project_config WHERE project_id=?", (result["project_id"],)).fetchone()
            policy = json.loads(configured[0]) if configured else {}
            if (result["blocked"] or (policy.get("require_assignee_before_start") and result["assignee_id"] is None)) and "in_progress" in result["available_transitions"]:
                result["available_transitions"].remove("in_progress")
            result["activity"] = self.activity("issue", issue_id)
            return result

    def list_issues(self, project_id: int | None = None, status: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT i.id,i.number,i.project_id,i.type,i.title,i.status,i.priority,i.assignee_id,i.reviewer_id,i.position,i.revision,i.claimed_at,i.claim_expires_at,p.key,p.key || '-' || i.number identifier FROM issues i JOIN projects p ON p.id=i.project_id WHERE 1=1"
        args: list[Any] = []
        if project_id is not None: sql += " AND i.project_id=?"; args.append(project_id)
        if status: sql += " AND i.status=?"; args.append(status)
        if query: sql += " AND (i.title LIKE ? OR i.description LIKE ?)"; args.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY i.status,i.position,i.id"
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def update_issue(self, actor: int, issue_id: int, **fields: Any) -> dict[str, Any]:
        expected_revision = fields.pop("expected_revision", None)
        allowed = {"title", "description", "priority", "milestone_id", "release_id", "assignee_id", "reviewer_id", "position"}
        changes = {k: v for k, v in fields.items() if k in allowed}
        if not changes:
            return self.get_issue(issue_id)
        if "priority" in changes and changes["priority"] not in PRIORITIES:
            raise ValueError("invalid priority")
        if "assignee_id" in changes:
            changes["claimed_at"] = None
            changes["claim_expires_at"] = None
        with self.connect() as db:
            current = self.get_issue(issue_id, db)
            if "milestone_id" in changes:
                self._assert_milestone_project(db, changes["milestone_id"], current["project_id"])
            if "release_id" in changes and changes["release_id"] is not None and not db.execute("SELECT 1 FROM releases WHERE id=? AND project_id=?", (changes["release_id"], current["project_id"])).fetchone():
                raise ValueError("release belongs to another project or does not exist")
            expected_revision = current["revision"] if expected_revision is None else expected_revision
            changes["updated_at"] = now()
            changes["revision"] = current["revision"] + 1
            cur = db.execute(f"UPDATE issues SET {','.join(f'{k}=?' for k in changes)} WHERE id=? AND revision=?", [*changes.values(), issue_id, expected_revision])
            if not cur.rowcount:
                raise ConflictError(f"issue revision conflict: expected {expected_revision}")
            self._activity(db, actor, "issue", issue_id, "updated", {"before": {k: current.get(k) for k in changes}, "after": changes})
            return self.get_issue(issue_id, db)

    def transition_issue(self, actor: int, issue_id: int, status: str, expected_revision: int | None = None) -> dict[str, Any]:
        with self.connect() as db:
            issue = self.get_issue(issue_id, db)
            wf = db.execute("SELECT states_json,transitions_json FROM workflows WHERE project_id=? AND issue_type=?", (issue["project_id"], issue["type"])).fetchone()
            states, transitions = json.loads(wf[0]), json.loads(wf[1])
            if status not in states or [issue["status"], status] not in transitions:
                raise ValueError(f"transition {issue['status']} -> {status} is not allowed")
            policy_row = db.execute("SELECT agent_policy_json FROM project_config WHERE project_id=?", (issue["project_id"],)).fetchone()
            policy = json.loads(policy_row[0]) if policy_row else {}
            if status == "in_progress" and policy.get("require_assignee_before_start") and issue["assignee_id"] is None:
                raise ValueError("issue must be claimed or assigned before entering in_progress")
            if status == "in_progress" and self._is_blocked(db, issue_id):
                raise ValueError("issue is blocked by an incomplete dependency")
            expected_revision = issue["revision"] if expected_revision is None else expected_revision
            position = self._next_position(db, issue["project_id"], status)
            cur = db.execute("UPDATE issues SET status=?,position=?,revision=revision+1,updated_at=? WHERE id=? AND status=? AND revision=?", (status, position, now(), issue_id, issue["status"], expected_revision))
            if not cur.rowcount:
                raise ConflictError(f"issue transition conflict: expected revision {expected_revision}")
            self._activity(db, actor, "issue", issue_id, "transitioned", {"from": issue["status"], "to": status})
            return self.get_issue(issue_id, db)

    def claim_issue(self, actor: int, issue_id: int, expected_revision: int, lease_seconds: int = 1800) -> dict[str, Any]:
        if not 60 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be between 60 and 86400")
        stamp = datetime.now(UTC)
        expires = stamp + timedelta(seconds=lease_seconds)
        with self.connect() as db:
            cur = db.execute(
                "UPDATE issues SET assignee_id=?,claimed_at=?,claim_expires_at=?,revision=revision+1,updated_at=? "
                "WHERE id=? AND revision=? AND (assignee_id IS NULL OR assignee_id=? OR (claim_expires_at IS NOT NULL AND claim_expires_at<=?))",
                (actor, stamp.isoformat(), expires.isoformat(), stamp.isoformat(), issue_id, expected_revision, actor, stamp.isoformat()),
            )
            if not cur.rowcount:
                raise ConflictError(f"issue claim conflict: expected revision {expected_revision}")
            self._activity(db, actor, "issue", issue_id, "claimed", {"lease_seconds": lease_seconds})
            return self.get_issue(issue_id, db)

    def release_issue(self, actor: int, issue_id: int, expected_revision: int) -> dict[str, Any]:
        with self.connect() as db:
            cur = db.execute(
                "UPDATE issues SET assignee_id=NULL,claimed_at=NULL,claim_expires_at=NULL,revision=revision+1,updated_at=? "
                "WHERE id=? AND revision=? AND assignee_id=?",
                (now(), issue_id, expected_revision, actor),
            )
            if not cur.rowcount:
                raise ConflictError(f"issue release conflict: expected revision {expected_revision}")
            self._activity(db, actor, "issue", issue_id, "released")
            return self.get_issue(issue_id, db)

    def add_related(self, actor: int, issue_id: int, kind: str, **data: Any) -> dict[str, Any]:
        with self.connect() as db:
            if kind == "comment":
                stamp = now(); cur = db.execute("INSERT INTO comments(issue_id,author_id,body,created_at,updated_at) VALUES(?,?,?,?,?)", (issue_id, actor, data["body"], stamp, stamp))
            elif kind == "checklist":
                cur = db.execute("INSERT INTO checklist_items(issue_id,text,completed,position) VALUES(?,?,?,?)", (issue_id, data["text"], int(data.get("completed", False)), data.get("position", 0)))
            elif kind == "attachment":
                cur = db.execute("INSERT INTO attachments(issue_id,name,path,media_type,created_at) VALUES(?,?,?,?,?)", (issue_id, data["name"], data["path"], data.get("media_type"), now()))
            elif kind == "dependency":
                projects = db.execute("SELECT id,project_id FROM issues WHERE id IN (?,?)", (issue_id, data["depends_on_id"])).fetchall()
                if len(projects) != 2 or len({row["project_id"] for row in projects}) != 1:
                    raise ValueError("dependencies must connect issues in the same project")
                db.execute("INSERT INTO dependencies(issue_id,depends_on_id,kind) VALUES(?,?,?)", (issue_id, data["depends_on_id"], data.get("relation", "blocks"))); cur = type("Cursor", (), {"lastrowid": data["depends_on_id"]})()
            elif kind == "git_link":
                cur = db.execute("INSERT OR IGNORE INTO git_links(issue_id,kind,ref,url,created_at) VALUES(?,?,?,?,?)", (issue_id, data.get("link_kind", "branch"), data["ref"], data.get("url"), now()))
            else:
                raise ValueError("unsupported related item")
            self._activity(db, actor, "issue", issue_id, f"{kind}_added", data)
            return {"id": cur.lastrowid, "issue_id": issue_id, "kind": kind, **data}

    def update_checklist_item(self, actor: int, item_id: int, text: str | None = None, completed: bool | None = None) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if text is not None: changes["text"] = text
        if completed is not None: changes["completed"] = int(completed)
        if not changes: raise ValueError("text or completed is required")
        with self.connect() as db:
            row = db.execute("SELECT issue_id FROM checklist_items WHERE id=?", (item_id,)).fetchone()
            if not row: raise KeyError("checklist item not found")
            db.execute(f"UPDATE checklist_items SET {','.join(f'{key}=?' for key in changes)} WHERE id=?", [*changes.values(), item_id])
            self._activity(db, actor, "issue", row["issue_id"], "checklist_updated", {"item_id": item_id, **changes})
            return dict(db.execute("SELECT * FROM checklist_items WHERE id=?", (item_id,)).fetchone())

    def delete_checklist_item(self, actor: int, item_id: int) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT issue_id FROM checklist_items WHERE id=?", (item_id,)).fetchone()
            if not row: raise KeyError("checklist item not found")
            db.execute("DELETE FROM checklist_items WHERE id=?", (item_id,))
            self._activity(db, actor, "issue", row["issue_id"], "checklist_deleted", {"item_id": item_id})
            return {"deleted": True, "id": item_id}

    def update_comment(self, actor: int, comment_id: int, body: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT issue_id FROM comments WHERE id=?", (comment_id,)).fetchone()
            if not row: raise KeyError("comment not found")
            db.execute("UPDATE comments SET body=?,updated_at=? WHERE id=?", (body, now(), comment_id))
            self._activity(db, actor, "issue", row["issue_id"], "comment_updated", {"comment_id": comment_id})
            return dict(db.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone())

    def delete_comment(self, actor: int, comment_id: int) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT issue_id FROM comments WHERE id=?", (comment_id,)).fetchone()
            if not row: raise KeyError("comment not found")
            db.execute("DELETE FROM comments WHERE id=?", (comment_id,))
            self._activity(db, actor, "issue", row["issue_id"], "comment_deleted", {"comment_id": comment_id})
            return {"deleted": True, "id": comment_id}

    def remove_label(self, actor: int, issue_id: int, label_id: int) -> dict[str, Any]:
        with self.connect() as db:
            cur = db.execute("DELETE FROM issue_labels WHERE issue_id=? AND label_id=?", (issue_id, label_id))
            if not cur.rowcount: raise KeyError("issue label not found")
            self._activity(db, actor, "issue", issue_id, "label_removed", {"label_id": label_id})
            return self.get_issue(issue_id, db)

    def remove_dependency(self, actor: int, issue_id: int, depends_on_id: int, relation: str = "blocks") -> dict[str, Any]:
        with self.connect() as db:
            cur = db.execute("DELETE FROM dependencies WHERE issue_id=? AND depends_on_id=? AND kind=?", (issue_id, depends_on_id, relation))
            if not cur.rowcount: raise KeyError("dependency not found")
            self._activity(db, actor, "issue", issue_id, "dependency_removed", {"depends_on_id": depends_on_id, "relation": relation})
            return {"deleted": True, "issue_id": issue_id, "depends_on_id": depends_on_id, "relation": relation}

    def delete_attachment(self, actor: int, attachment_id: int) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT issue_id FROM attachments WHERE id=?", (attachment_id,)).fetchone()
            if not row: raise KeyError("attachment not found")
            db.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
            self._activity(db, actor, "issue", row["issue_id"], "attachment_deleted", {"attachment_id": attachment_id})
            return {"deleted": True, "id": attachment_id}

    def delete_git_link(self, actor: int, link_id: int) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT issue_id FROM git_links WHERE id=?", (link_id,)).fetchone()
            if not row: raise KeyError("git link not found")
            db.execute("DELETE FROM git_links WHERE id=?", (link_id,))
            self._activity(db, actor, "issue", row["issue_id"], "git_link_deleted", {"link_id": link_id})
            return {"deleted": True, "id": link_id}

    def create_label(self, actor: int, project_id: int, name: str, color: str = "#64748b", key: str | None = None) -> dict[str, Any]:
        with self.connect() as db:
            cur = db.execute("INSERT INTO labels(project_id,key,name,color) VALUES(?,?,?,?)", (project_id, key, name, color))
            self._activity(db, actor, "project", project_id, "label_created", {"name": name})
            return {"id": cur.lastrowid, "project_id": project_id, "key": key, "name": name, "color": color}

    def add_label(self, actor: int, issue_id: int, label_id: int) -> dict[str, Any]:
        with self.connect() as db:
            valid = db.execute("SELECT 1 FROM issues i JOIN labels l ON l.project_id=i.project_id WHERE i.id=? AND l.id=?", (issue_id, label_id)).fetchone()
            if not valid:
                raise ValueError("label belongs to another project or does not exist")
            db.execute("INSERT OR IGNORE INTO issue_labels(issue_id,label_id) VALUES(?,?)", (issue_id, label_id))
            self._activity(db, actor, "issue", issue_id, "label_added", {"label_id": label_id})
            return self.get_issue(issue_id, db)

    def activity(self, entity_type: str | None = None, entity_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT x.*,a.name actor FROM activity x LEFT JOIN actors a ON a.id=x.actor_id WHERE 1=1"; args: list[Any] = []
        if entity_type: sql += " AND entity_type=?"; args.append(entity_type)
        if entity_id is not None: sql += " AND entity_id=?"; args.append(entity_id)
        sql += " ORDER BY x.id DESC LIMIT ?"; args.append(limit)
        with self.connect() as db:
            rows = []
            for r in db.execute(sql, args):
                item = dict(r); item["data"] = json.loads(item.pop("data_json")); rows.append(item)
            return rows

    def update_activity(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AuthorizationError("activity is an immutable append-only audit log")

    def delete_activity(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AuthorizationError("activity is an immutable append-only audit log")

    def dashboard(self) -> dict[str, Any]:
        with self.connect() as db:
            return {"projects": [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY id")], "issues": self.list_issues(), "actors": [dict(r) for r in db.execute("SELECT id,name,kind,role FROM actors ORDER BY name")], "activity": self.activity(limit=30)}
