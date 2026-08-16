"""Dependency-free HTTP and JSON API."""
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from .db import AuthenticationError, Board, BoardError, NotFound

HTML = """<!doctype html><meta charset=utf-8><title>Local Board</title>
<h1>Local Board</h1><label>Token <input id=token></label><button id=login>Login</button>
<div id=columns></div><input id=title placeholder='Issue title'><button id=create>Create issue</button>
<script>let actorToken='';
const tokenInput=document.querySelector('#token'), columns=document.querySelector('#columns'), titleInput=document.querySelector('#title');
document.querySelector('#login').onclick=()=>{actorToken=tokenInput.value;load()};
async function load(){let r=await fetch('/api/issues');let xs=await r.json();columns.textContent='Issues: '+xs.map(x=>x.title).join(', ')}
document.querySelector('#create').onclick=async()=>{let ps=await(await fetch('/api/projects')).json();await fetch('/api/issues',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+actorToken},body:JSON.stringify({project_id:ps[0].id,title:titleInput.value})});load()};load()</script>"""

def make_handler(board: Board):
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*_): pass
  def _token(self):
   value=self.headers.get("Authorization",""); return value[7:] if value.startswith("Bearer ") else None
  def _body(self):
   try:return json.loads(self.rfile.read(int(self.headers.get("Content-Length",0))) or b"{}")
   except (ValueError,TypeError):raise BoardError("invalid JSON")
  def _send(self,status,data,ctype="application/json"):
   raw=data.encode() if isinstance(data,str) else json.dumps(data).encode(); self.send_response(status); self.send_header("Content-Type",ctype); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
  def _run(self):
   path=urlparse(self.path).path; parts=path.strip('/').split('/')
   if self.command=='GET' and path=='/': return self._send(200,HTML,"text/html")
   if self.command=='GET' and path=='/api/dashboard': return self._send(200,board.dashboard())
   if self.command=='GET' and path=='/api/projects': return self._send(200,board.projects())
   if self.command=='GET' and path=='/api/issues': return self._send(200,board.issues())
   if self.command=='POST' and path=='/api/projects': return self._send(201,board.create_project(token=self._token(),**self._body()))
   if self.command=='POST' and path=='/api/issues': return self._send(201,board.create_issue(token=self._token(),**self._body()))
   if self.command=='POST' and len(parts)==4 and parts[:2]==['api','issues'] and parts[3]=='transition': return self._send(200,board.transition_issue(int(parts[2]),token=self._token(),**self._body()))
   if self.command=='POST' and len(parts)==4 and parts[:2]==['api','issues'] and parts[3]=='comments': return self._send(201,board.add_comment(int(parts[2]),token=self._token(),**self._body()))
   if self.command=='POST' and path=='/mcp': return self._send(200,mcp_message(board,self._body(),self._token()))
   raise NotFound("route not found")
  def do_GET(self): self._dispatch()
  def do_POST(self): self._dispatch()
  def _dispatch(self):
   try:self._run()
   except AuthenticationError as e:self._send(401,{"error":str(e)})
   except NotFound as e:self._send(404,{"error":str(e)})
   except (BoardError,TypeError,ValueError) as e:self._send(400,{"error":str(e)})
 return Handler

TOOLS=[{"name":"create_project","description":"Create a project"},{"name":"create_issue","description":"Create an issue"},{"name":"add_comment","description":"Comment on an issue"},{"name":"transition_issue","description":"Move an issue"}]
def mcp_message(board,msg,token=None):
 method=msg.get("method"); ident=msg.get("id")
 if method=="initialize": result={"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"local-board","version":"0.1.0"}}
 elif method=="notifications/initialized": return None
 elif method=="tools/list": result={"tools":TOOLS}
 elif method=="tools/call":
  params=msg.get("params",{}); name=params.get("name"); args=params.get("arguments",{}); call_token=args.pop("token",token)
  if name not in {x["name"] for x in TOOLS}: raise NotFound("unknown tool")
  value=getattr(board,name)(token=call_token,**args); result={"content":[{"type":"text","text":json.dumps(value)}],"structuredContent":value}
 else: raise NotFound("unknown method")
 return {"jsonrpc":"2.0","id":ident,"result":result}

def serve(board,host="127.0.0.1",port=8000): ThreadingHTTPServer((host,port),make_handler(board)).serve_forever()
