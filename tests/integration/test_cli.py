import json, os, subprocess, sys
from pathlib import Path
from local_board import Board

def run(*args,cwd=None):
    env=os.environ.copy(); env['PYTHONPATH']=str(Path(__file__).parents[2])
    return subprocess.run([sys.executable,'-m','local_board.cli',*map(str,args)],cwd=cwd,text=True,capture_output=True,check=True,env=env)
def test_init_actor_and_sync_branch(tmp_path):
    db=tmp_path/'board.db'; assert 'Initialized' in run('--db',db,'init').stdout; json.loads(run('--db',db,'actor','bot','--token','tok').stdout)
    with Board(db) as b: project=b.create_project('P','P','tok'); issue=b.create_issue(project['id'],'work',token='tok')
    subprocess.run(['git','init'],cwd=tmp_path,check=True,capture_output=True); subprocess.run(['git','config','user.email','a@b.c'],cwd=tmp_path,check=True); subprocess.run(['git','config','user.name','A'],cwd=tmp_path,check=True); (tmp_path/'x').write_text('x'); subprocess.run(['git','add','x'],cwd=tmp_path,check=True); subprocess.run(['git','commit','-m','init'],cwd=tmp_path,check=True,capture_output=True); subprocess.run(['git','checkout','-b',f'issue-{issue["id"]}'],cwd=tmp_path,check=True,capture_output=True)
    run('--db',db,'sync-branch','--project',project['id'],'--token','tok',cwd=tmp_path)
    with Board(db) as b: assert b.git_links(issue['id'])[0]['kind']=='commit'
