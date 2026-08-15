import pytest
from local_board import AuthenticationError, Board, NotFound, ValidationError

@pytest.fixture
def board(tmp_path):
    with Board(tmp_path/'board.db') as value: yield value

def setup(board):
    actor=board.create_actor('alice','secret'); project=board.create_project('APP','Application','secret'); return actor,project

def test_all_public_entities(board):
    actor,project=setup(board); milestone=board.create_milestone(project['id'],'v1','secret'); workflow=board.create_workflow(project['id'],'default',['todo','done'],[['todo','done']],'secret')
    issue=board.create_issue(project['id'],'Ship it',priority='high',milestone_id=milestone['id'],token='secret'); label=board.create_label(project['id'],'bug','#f00','secret'); board.add_label(issue['id'],label['id'],'secret')
    board.add_comment(issue['id'],'Looks good','secret'); board.add_checklist_item(issue['id'],'Tests',token='secret'); other=board.create_issue(project['id'],'Dependency',token='secret'); board.add_dependency(issue['id'],other['id'],'secret')
    board.add_attachment(issue['id'],'spec','file:///spec','secret'); board.add_git_link(issue['id'],'commit','abc','secret'); board.assign_issue(issue['id'],actor['id'],'secret'); board.transition_issue(issue['id'],'done','secret')
    assert board.actors()[0]['name']=='alice' and board.get_actor(actor['id'])['token']=='secret'; assert board.projects()==[project] and board.get_project(project['id'])==project
    assert board.milestones(project['id'])==[milestone] and board.workflows()[0]==workflow; assert len(board.issues(project['id']))==2 and board.get_issue(issue['id'])['status']=='done'
    assert board.labels()==[label] and board.comments(issue['id'])[0]['body']=='Looks good'; assert board.checklists(issue['id'])[0]['text']=='Tests' and board.dependencies(issue['id'])[0]['depends_on']==other['id']
    assert board.attachments(issue['id'])[0]['name']=='spec' and board.git_links(issue['id'])[0]['ref']=='abc'; assert board.dashboard()=={'projects':1,'issues':2,'actors':1} and any(x['actor_name']=='alice' for x in board.activity())

def test_negative_cases(board):
    with pytest.raises(NotFound): board.get_project(999)
    with pytest.raises(ValidationError): board.get_project('one')
    with pytest.raises(ValidationError): board.create_actor('')
    setup(board)
    with pytest.raises(ValidationError): board.create_actor('alice')
    with pytest.raises(AuthenticationError): board.create_project('X','x','wrong')
    with pytest.raises(AuthenticationError): board.create_project('X','x')
    with pytest.raises(ValidationError): board.create_issue(1,'x',priority='impossible',token='secret')
    with pytest.raises(ValidationError): board.create_workflow(1,'bad','todo',[],token='secret')
    board.create_workflow(1,'flow',['todo','done'],[['todo','done']],token='secret'); issue=board.create_issue(1,'x',token='secret')
    with pytest.raises(ValidationError): board.transition_issue(issue['id'],'blocked','secret')
    with pytest.raises(ValidationError): board.add_dependency(issue['id'],issue['id'],'secret')
