import json, threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import pytest
from local_board import Board
from local_board.http import ThreadingHTTPServer, make_handler

@pytest.fixture
def api(tmp_path):
    board=Board(tmp_path/'db'); board.create_actor('api','tok'); server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(board)); threading.Thread(target=server.serve_forever,daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}',board
    server.shutdown(); server.server_close(); board.close()

def request(base,path,method='GET',data=None,token=None):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    raw=data if isinstance(data,bytes) else None if data is None else json.dumps(data).encode(); req=Request(base+path,method=method,headers=headers,data=raw)
    try:
        with urlopen(req) as r:return r.status,r.read(),r.headers.get_content_type()
    except HTTPError as e:return e.code,e.read(),e.headers.get_content_type()

def test_routes_and_failures(api):
    base,_=api; assert request(base,'/')[0:3:2]==(200,'text/html'); assert request(base,'/api/dashboard')[0]==200 and request(base,'/api/projects')[0]==200 and request(base,'/api/issues')[0]==200
    assert request(base,'/api/projects','POST',{'key':'P','name':'Project'})[0]==401
    status,body,_=request(base,'/api/projects','POST',{'key':'P','name':'Project'},'tok'); project=json.loads(body); assert status==201
    status,body,_=request(base,'/api/issues','POST',{'project_id':project['id'],'title':'Issue'},'tok'); issue=json.loads(body); assert status==201
    assert request(base,f"/api/issues/{issue['id']}/comments",'POST',{'body':'hello'},'tok')[0]==201; assert request(base,f"/api/issues/{issue['id']}/transition",'POST',{'status':'done'},'tok')[0]==200
    assert request(base,'/mcp','POST',{'jsonrpc':'2.0','id':1,'method':'tools/list'},'tok')[0]==200; assert request(base,'/api/projects','POST',b'bad')[0]==400; assert request(base,'/missing')[0]==404
