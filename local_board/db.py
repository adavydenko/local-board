"""Persistence and public domain API for local-board."""
from __future__ import annotations

import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any


class BoardError(ValueError): pass
class ValidationError(BoardError): pass
class NotFound(BoardError): pass
class AuthenticationError(BoardError): pass


class Board:
    """SQLite-backed board. Mutations require an actor token when actors exist."""
    PRIORITIES = {"low", "medium", "high", "urgent"}

    def __init__(self, path: str | Path = "board.db"):
        self.path = str(path)
        # HTTP handlers execute in worker threads; SQLite may safely share this
        # connection because each mutation is committed as one short operation.
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self._schema()

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()

    def _schema(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS actors(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, token TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS milestones(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), name TEXT NOT NULL, UNIQUE(project_id,name));
        CREATE TABLE IF NOT EXISTS workflows(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), name TEXT NOT NULL, states TEXT NOT NULL, transitions TEXT NOT NULL, UNIQUE(project_id,name));
        CREATE TABLE IF NOT EXISTS issues(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, priority TEXT NOT NULL, assignee_id INTEGER REFERENCES actors(id), milestone_id INTEGER REFERENCES milestones(id));
        CREATE TABLE IF NOT EXISTS labels(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '#888888', UNIQUE(project_id,name));
        CREATE TABLE IF NOT EXISTS issue_labels(issue_id INTEGER REFERENCES issues(id) ON DELETE CASCADE,label_id INTEGER REFERENCES labels(id) ON DELETE CASCADE,PRIMARY KEY(issue_id,label_id));
        CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id), body TEXT NOT NULL, actor_id INTEGER REFERENCES actors(id));
        CREATE TABLE IF NOT EXISTS checklists(id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id), text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS dependencies(issue_id INTEGER REFERENCES issues(id), depends_on INTEGER REFERENCES issues(id), PRIMARY KEY(issue_id,depends_on));
        CREATE TABLE IF NOT EXISTS attachments(id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id), name TEXT NOT NULL, url TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS git_links(id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL REFERENCES issues(id), kind TEXT NOT NULL, ref TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES actors(id), action TEXT NOT NULL, entity TEXT NOT NULL, entity_id INTEGER NOT NULL, data TEXT NOT NULL DEFAULT '{}');
        """)
        self.db.commit()

    @staticmethod
    def _id(value, what="id"):
        if isinstance(value, bool) or not isinstance(value, int): raise ValidationError(f"{what} must be an integer")
        return value

    def _one(self, table, ident):
        row = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (self._id(ident),)).fetchone()
        if not row: raise NotFound(f"unknown {table.rstrip('s')}: {ident}")
        return dict(row)

    def _actor(self, token, required=True):
        count = self.db.execute("SELECT count(*) FROM actors").fetchone()[0]
        if not token and (required and count): raise AuthenticationError("actor token required")
        if not token: return None
        row = self.db.execute("SELECT * FROM actors WHERE token=?", (token,)).fetchone()
        if not row: raise AuthenticationError("invalid actor token")
        return dict(row)

    def _log(self, actor, action, entity, ident, data=None):
        self.db.execute("INSERT INTO activity(actor_id,action,entity,entity_id,data) VALUES(?,?,?,?,?)", (actor and actor["id"],action,entity,ident,json.dumps(data or {})))

    def _insert(self, sql, args, actor, action, entity):
        try:
            cur=self.db.execute(sql,args); ident=cur.lastrowid; self._log(actor,action,entity,ident); self.db.commit(); return ident
        except sqlite3.IntegrityError as e: raise ValidationError(str(e)) from e

    def create_actor(self, name, token=None):
        if not isinstance(name,str) or not name.strip(): raise ValidationError("name must be a non-empty string")
        token=token or secrets.token_urlsafe(24)
        ident=self._insert("INSERT INTO actors(name,token) VALUES(?,?)",(name.strip(),token),None,"create","actor")
        return {**self._one("actors",ident), "token": token}
    def actors(self): return [dict(x) for x in self.db.execute("SELECT id,name FROM actors ORDER BY id")]
    def get_actor(self, ident): return self._one("actors",ident)

    def create_project(self,key,name,token=None):
        actor=self._actor(token); 
        if not all(isinstance(x,str) and x.strip() for x in (key,name)): raise ValidationError("key and name must be strings")
        i=self._insert("INSERT INTO projects(key,name) VALUES(?,?)",(key.upper(),name),actor,"create","project"); return self._one("projects",i)
    def projects(self): return [dict(x) for x in self.db.execute("SELECT * FROM projects ORDER BY id")]
    def get_project(self, ident): return self._one("projects",ident)
    def create_milestone(self,project_id,name,token=None):
        self._one("projects",project_id); actor=self._actor(token); i=self._insert("INSERT INTO milestones(project_id,name) VALUES(?,?)",(project_id,name),actor,"create","milestone"); return self._one("milestones",i)
    def milestones(self,project_id=None): return self._list("milestones",project_id)
    def create_workflow(self,project_id,name,states,transitions,token=None):
        self._one("projects",project_id); actor=self._actor(token)
        if not isinstance(states,list) or not states or not all(isinstance(x,str) for x in states): raise ValidationError("states must be a non-empty list of strings")
        if not isinstance(transitions,list) or not all(isinstance(x,(list,tuple)) and len(x)==2 and x[0] in states and x[1] in states for x in transitions): raise ValidationError("invalid transitions")
        i=self._insert("INSERT INTO workflows(project_id,name,states,transitions) VALUES(?,?,?,?)",(project_id,name,json.dumps(states),json.dumps(transitions)),actor,"create","workflow"); return self.get_workflow(i)
    def get_workflow(self,i):
        x=self._one("workflows",i); x["states"]=json.loads(x["states"]); x["transitions"]=json.loads(x["transitions"]); return x
    def workflows(self,project_id=None): return [self.get_workflow(x["id"]) for x in self._rows("workflows",project_id)]
    def _rows(self,table,project_id=None):
        if project_id is None:return self.db.execute(f"SELECT * FROM {table} ORDER BY id")
        self._one("projects",project_id); return self.db.execute(f"SELECT * FROM {table} WHERE project_id=? ORDER BY id",(project_id,))
    def _list(self,table,project_id=None): return [dict(x) for x in self._rows(table,project_id)]

    def create_issue(self,project_id,title,body="",priority="medium",status="todo",assignee_id=None,milestone_id=None,token=None):
        self._one("projects",project_id); actor=self._actor(token)
        if not isinstance(title,str) or not title.strip(): raise ValidationError("title must be a string")
        if priority not in self.PRIORITIES: raise ValidationError("invalid priority")
        if assignee_id is not None:self._one("actors",assignee_id)
        if milestone_id is not None:self._one("milestones",milestone_id)
        i=self._insert("INSERT INTO issues(project_id,title,body,status,priority,assignee_id,milestone_id) VALUES(?,?,?,?,?,?,?)",(project_id,title,body,status,priority,assignee_id,milestone_id),actor,"create","issue"); return self._one("issues",i)
    def issues(self,project_id=None): return self._list("issues",project_id)
    def get_issue(self,i): return self._one("issues",i)
    def assign_issue(self,i,actor_id,token=None): self._one("actors",actor_id); return self._update_issue(i,"assignee_id",actor_id,"assign",token)
    def transition_issue(self,i,status,token=None):
        issue=self._one("issues",i); workflows=self.workflows(issue["project_id"])
        if workflows and [issue["status"],status] not in workflows[0]["transitions"]: raise ValidationError("workflow transition is not allowed")
        return self._update_issue(i,"status",status,"transition",token)
    def _update_issue(self,i,column,value,action,token):
        self._one("issues",i); actor=self._actor(token); self.db.execute(f"UPDATE issues SET {column}=? WHERE id=?",(value,i)); self._log(actor,action,"issue",i,{column:value}); self.db.commit(); return self._one("issues",i)
    def create_label(self,project_id,name,color="#888888",token=None):
        self._one("projects",project_id); actor=self._actor(token); i=self._insert("INSERT INTO labels(project_id,name,color) VALUES(?,?,?)",(project_id,name,color),actor,"create","label"); return self._one("labels",i)
    def labels(self,project_id=None): return self._list("labels",project_id)
    def add_label(self,issue_id,label_id,token=None):
        self._one("issues",issue_id); self._one("labels",label_id); actor=self._actor(token); self._insert("INSERT INTO issue_labels(issue_id,label_id) VALUES(?,?)",(issue_id,label_id),actor,"label","issue"); return self.get_issue(issue_id)
    def add_comment(self,issue_id,body,token=None):
        self._one("issues",issue_id); actor=self._actor(token)
        if not isinstance(body,str) or not body.strip(): raise ValidationError("body must be a non-empty string")
        i=self._insert("INSERT INTO comments(issue_id,body,actor_id) VALUES(?,?,?)",(issue_id,body,actor and actor["id"]),actor,"comment","issue"); return self._one("comments",i)
    def comments(self,issue_id): self._one("issues",issue_id); return [dict(x) for x in self.db.execute("SELECT * FROM comments WHERE issue_id=? ORDER BY id",(issue_id,))]
    def add_checklist_item(self,issue_id,text,done=False,token=None): return self._child("checklists",issue_id,(text,int(done)),token,"checklist", "text,done")
    def checklists(self,issue_id): return self._children("checklists",issue_id)
    def add_dependency(self,issue_id,depends_on,token=None):
        self._one("issues",issue_id); self._one("issues",depends_on)
        if issue_id==depends_on: raise ValidationError("self-dependency is not allowed")
        actor=self._actor(token); self._insert("INSERT INTO dependencies(issue_id,depends_on) VALUES(?,?)",(issue_id,depends_on),actor,"dependency","issue"); return {"issue_id":issue_id,"depends_on":depends_on}
    def dependencies(self,issue_id): self._one("issues",issue_id); return [dict(x) for x in self.db.execute("SELECT * FROM dependencies WHERE issue_id=?",(issue_id,))]
    def add_attachment(self,issue_id,name,url,token=None): return self._child("attachments",issue_id,(name,url),token,"attachment","name,url")
    def attachments(self,issue_id): return self._children("attachments",issue_id)
    def add_git_link(self,issue_id,kind,ref,token=None): return self._child("git_links",issue_id,(kind,ref),token,"git_link","kind,ref")
    def git_links(self,issue_id): return self._children("git_links",issue_id)
    def _child(self,table,issue_id,values,token,entity,columns):
        self._one("issues",issue_id); actor=self._actor(token); qs=','.join('?' for _ in values); i=self._insert(f"INSERT INTO {table}(issue_id,{columns}) VALUES(?,{qs})",(issue_id,*values),actor,"create",entity); return self._one(table,i)
    def _children(self,table,issue_id): self._one("issues",issue_id); return [dict(x) for x in self.db.execute(f"SELECT * FROM {table} WHERE issue_id=? ORDER BY id",(issue_id,))]
    def activity(self):
        return [dict(x) | {"data":json.loads(x["data"])} for x in self.db.execute("SELECT activity.*,actors.name actor_name FROM activity LEFT JOIN actors ON actors.id=activity.actor_id ORDER BY activity.id")]
    def dashboard(self): return {"projects":len(self.projects()),"issues":len(self.issues()),"actors":len(self.actors())}
