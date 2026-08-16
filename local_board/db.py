from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS actors (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL CHECK(kind IN ('agent','human')),
  token_hash TEXT UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  milestone_id INTEGER REFERENCES milestones(id) ON DELETE SET NULL, number INTEGER NOT NULL,
  type TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'medium', assignee_id INTEGER REFERENCES actors(id) ON DELETE SET NULL,
  reviewer_id INTEGER REFERENCES actors(id) ON DELETE SET NULL, position REAL NOT NULL DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS idx_issues_project_status ON issues(project_id,status,position);
CREATE INDEX IF NOT EXISTS idx_activity_entity ON activity(entity_type,entity_id,id DESC);
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

    def create_actor(self, name: str, kind: str = "agent") -> dict[str, Any]:
        if kind not in ("agent", "human"):
            raise ValueError("kind must be agent or human")
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            cur = db.execute("INSERT INTO actors(name,kind,token_hash,created_at) VALUES(?,?,?,?)", (name, kind, self._hash(token), now()))
            return {"id": cur.lastrowid, "name": name, "kind": kind, "token": token}

    def authenticate(self, token: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT id,name,kind FROM actors WHERE token_hash=?", (self._hash(token),)).fetchone()
            return dict(row) if row else None

    def _activity(self, db: sqlite3.Connection, actor: int | None, entity: str, entity_id: int, action: str, data: dict[str, Any] | None = None) -> None:
        db.execute("INSERT INTO activity(actor_id,entity_type,entity_id,action,data_json,created_at) VALUES(?,?,?,?,?,?)", (actor, entity, entity_id, action, json.dumps(data or {}), now()))

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

    def create_milestone(self, actor: int, project_id: int, name: str, description: str = "", due_at: str | None = None) -> dict[str, Any]:
        with self.connect() as db:
            cur = db.execute("INSERT INTO milestones(project_id,name,description,due_at,created_at) VALUES(?,?,?,?,?)", (project_id, name, description, due_at, now()))
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

    def create_issue(self, actor: int, project_id: int, title: str, issue_type: str | None = None, description: str = "", priority: str | None = None, milestone_id: int | None = None, assignee_id: int | None = None, reviewer_id: int | None = None) -> dict[str, Any]:
        with self.connect() as db:
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
            number = db.execute("SELECT COALESCE(MAX(number),0)+1 FROM issues WHERE project_id=?", (project_id,)).fetchone()[0]
            position = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM issues WHERE project_id=? AND status=?", (project_id, status)).fetchone()[0]
            stamp = now()
            cur = db.execute("INSERT INTO issues(project_id,milestone_id,number,type,title,description,status,priority,assignee_id,reviewer_id,position,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (project_id, milestone_id, number, issue_type, title, description, status, priority, assignee_id, reviewer_id, position, actor, stamp, stamp))
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

    def list_issues(self, project_id: int | None = None, status: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT i.id,i.number,i.project_id,i.type,i.title,i.status,i.priority,i.assignee_id,i.reviewer_id,i.position,p.key,p.key || '-' || i.number identifier FROM issues i JOIN projects p ON p.id=i.project_id WHERE 1=1"
        args: list[Any] = []
        if project_id is not None: sql += " AND i.project_id=?"; args.append(project_id)
        if status: sql += " AND i.status=?"; args.append(status)
        if query: sql += " AND (i.title LIKE ? OR i.description LIKE ?)"; args.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY i.status,i.position,i.id"
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def update_issue(self, actor: int, issue_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {"title", "description", "priority", "milestone_id", "assignee_id", "reviewer_id", "position"}
        changes = {k: v for k, v in fields.items() if k in allowed}
        if not changes:
            return self.get_issue(issue_id)
        if "priority" in changes and changes["priority"] not in PRIORITIES:
            raise ValueError("invalid priority")
        with self.connect() as db:
            current = self.get_issue(issue_id, db)
            changes["updated_at"] = now()
            db.execute(f"UPDATE issues SET {','.join(f'{k}=?' for k in changes)} WHERE id=?", [*changes.values(), issue_id])
            self._activity(db, actor, "issue", issue_id, "updated", {"before": {k: current.get(k) for k in changes}, "after": changes})
            return self.get_issue(issue_id, db)

    def transition_issue(self, actor: int, issue_id: int, status: str) -> dict[str, Any]:
        with self.connect() as db:
            issue = self.get_issue(issue_id, db)
            wf = db.execute("SELECT states_json,transitions_json FROM workflows WHERE project_id=? AND issue_type=?", (issue["project_id"], issue["type"])).fetchone()
            states, transitions = json.loads(wf[0]), json.loads(wf[1])
            if status not in states or [issue["status"], status] not in transitions:
                raise ValueError(f"transition {issue['status']} -> {status} is not allowed")
            position = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM issues WHERE project_id=? AND status=?", (issue["project_id"], status)).fetchone()[0]
            db.execute("UPDATE issues SET status=?,position=?,updated_at=? WHERE id=?", (status, position, now(), issue_id))
            self._activity(db, actor, "issue", issue_id, "transitioned", {"from": issue["status"], "to": status})
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
                db.execute("INSERT INTO dependencies(issue_id,depends_on_id,kind) VALUES(?,?,?)", (issue_id, data["depends_on_id"], data.get("relation", "blocks"))); cur = type("Cursor", (), {"lastrowid": data["depends_on_id"]})()
            elif kind == "git_link":
                cur = db.execute("INSERT OR IGNORE INTO git_links(issue_id,kind,ref,url,created_at) VALUES(?,?,?,?,?)", (issue_id, data.get("link_kind", "branch"), data["ref"], data.get("url"), now()))
            else:
                raise ValueError("unsupported related item")
            self._activity(db, actor, "issue", issue_id, f"{kind}_added", data)
            return {"id": cur.lastrowid, "issue_id": issue_id, "kind": kind, **data}

    def create_label(self, actor: int, project_id: int, name: str, color: str = "#64748b") -> dict[str, Any]:
        with self.connect() as db:
            cur = db.execute("INSERT INTO labels(project_id,name,color) VALUES(?,?,?)", (project_id, name, color))
            self._activity(db, actor, "project", project_id, "label_created", {"name": name})
            return {"id": cur.lastrowid, "project_id": project_id, "name": name, "color": color}

    def add_label(self, actor: int, issue_id: int, label_id: int) -> dict[str, Any]:
        with self.connect() as db:
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

    def update_activity(self, activity_id: int, action: str | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
        changes: list[str] = []; args: list[Any] = []
        if action is not None: changes.append("action=?"); args.append(action)
        if data is not None: changes.append("data_json=?"); args.append(json.dumps(data))
        if not changes: raise ValueError("action or data is required")
        with self.connect() as db:
            args.append(activity_id); cur = db.execute(f"UPDATE activity SET {','.join(changes)} WHERE id=?", args)
            if not cur.rowcount: raise KeyError("activity not found")
            row = dict(db.execute("SELECT * FROM activity WHERE id=?", (activity_id,)).fetchone()); row["data"] = json.loads(row.pop("data_json")); return row

    def delete_activity(self, activity_id: int) -> dict[str, Any]:
        with self.connect() as db:
            cur = db.execute("DELETE FROM activity WHERE id=?", (activity_id,))
            if not cur.rowcount: raise KeyError("activity not found")
            return {"deleted": True, "id": activity_id}

    def dashboard(self) -> dict[str, Any]:
        with self.connect() as db:
            return {"projects": [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY id")], "issues": self.list_issues(), "actors": [dict(r) for r in db.execute("SELECT id,name,kind FROM actors ORDER BY name")], "activity": self.activity(limit=30)}
