import threading
import pytest
from local_board import Board
from local_board.http import ThreadingHTTPServer, make_handler

@pytest.mark.browser
def test_login_columns_and_create_issue(page,tmp_path):
    board=Board(tmp_path/'db'); board.create_actor('browser','token'); board.create_project('WEB','Web','token'); server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(board)); threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        page.goto(f'http://127.0.0.1:{server.server_port}'); page.locator('#token').fill('token'); page.locator('#login').click(); page.locator('#columns').wait_for()
        page.locator('#title').fill('Created in browser'); page.locator('#create').click(); page.get_by_text('Created in browser').wait_for(); assert board.issues()[0]['title']=='Created in browser'
    finally: server.shutdown(); server.server_close(); board.close()
