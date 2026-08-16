from local_board import Board

def test_two_authenticated_agents(tmp_path):
    with Board(tmp_path/'db') as b:
        alice=b.create_actor('alice','alice-token'); bob=b.create_actor('bob','bob-token')
        project=b.create_project('TEAM','Team','alice-token'); issue=b.create_issue(project['id'],'Collaborate',token='alice-token')
        b.add_comment(issue['id'],'I can take this','bob-token'); b.assign_issue(issue['id'],bob['id'],'alice-token'); b.transition_issue(issue['id'],'doing','bob-token'); b.add_git_link(issue['id'],'branch','issue-1','bob-token')
        actions=b.activity(); assert {x['actor_name'] for x in actions if x['entity'] in {'issue','git_link'}}=={'alice','bob'}
        assert b.comments(issue['id'])[0]['actor_id']==bob['id'] and b.get_issue(issue['id'])['assignee_id']==bob['id']
