"""Command line interface and stdio MCP transport."""
import argparse, json, subprocess, sys
from pathlib import Path
from .db import Board, BoardError
from .http import mcp_message, serve

def mcp_stdio(board):
 for line in sys.stdin:
  try:
   response=mcp_message(board,json.loads(line))
   if response is not None: print(json.dumps(response),flush=True)
  except Exception as e: print(json.dumps({"jsonrpc":"2.0","id":None,"error":{"code":-32602,"message":str(e)}}),flush=True)

def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument('--db',default='board.db'); sub=p.add_subparsers(dest='command',required=True)
 sub.add_parser('init'); a=sub.add_parser('actor'); a.add_argument('name'); a.add_argument('--token')
 s=sub.add_parser('sync-branch'); s.add_argument('--token'); s.add_argument('--project',type=int,required=True)
 h=sub.add_parser('serve'); h.add_argument('--host',default='127.0.0.1'); h.add_argument('--port',type=int,default=8000)
 sub.add_parser('mcp'); ns=p.parse_args(argv)
 try:
  with Board(ns.db) as board:
   if ns.command=='init': print(f"Initialized {ns.db}")
   elif ns.command=='actor': print(json.dumps(board.create_actor(ns.name,ns.token)))
   elif ns.command=='mcp': mcp_stdio(board)
   elif ns.command=='serve': serve(board,ns.host,ns.port)
   elif ns.command=='sync-branch':
    branch=subprocess.check_output(['git','branch','--show-current'],text=True).strip(); sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    issue=next((x for x in board.issues(ns.project) if f"{x['id']}" in branch),None)
    if not issue: raise BoardError("branch name must contain an issue id")
    print(json.dumps(board.add_git_link(issue['id'],'commit',sha,ns.token)))
 except BoardError as e: p.error(str(e))

if __name__=='__main__': main()
