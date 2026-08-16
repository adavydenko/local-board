import json, os, subprocess, sys
from local_board import Board

def test_stdio_round_trip(tmp_path):
    db=tmp_path/'board.db'; p=subprocess.Popen([sys.executable,'-m','local_board.cli','--db',str(db),'mcp'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,env=os.environ.copy())
    def call(message,answer=True):
        p.stdin.write(json.dumps(message)+'\n'); p.stdin.flush(); return json.loads(p.stdout.readline()) if answer else None
    assert call({'jsonrpc':'2.0','id':1,'method':'initialize','params':{}})['result']['serverInfo']['name']=='local-board'; call({'jsonrpc':'2.0','method':'notifications/initialized'},False); assert call({'jsonrpc':'2.0','id':2,'method':'tools/list'})['result']['tools']
    result=call({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'create_project','arguments':{'key':'MCP','name':'MCP project'}}}); assert result['result']['structuredContent']['key']=='MCP'; p.terminate(); p.wait()
    with Board(db) as board: assert board.projects()[0]['name']=='MCP project'
