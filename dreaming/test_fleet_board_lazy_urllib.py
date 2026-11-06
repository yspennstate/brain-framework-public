"""fleet_board imports urllib only when it talks to GitHub, and api()/gql() still work when they do."""
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


def test_importing_the_board_does_not_load_urllib_request():
    code = "import sys; sys.path.insert(0, %r); import fleet_board; print('urllib.request' in sys.modules)" % HERE
    r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "False"


class _Resp(io.BytesIO):
    status = 200
    headers = {"X-RateLimit-Remaining": "4999"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def board(monkeypatch):
    monkeypatch.syspath_prepend(HERE)
    sys.modules.pop("fleet_board", None)
    import fleet_board
    monkeypatch.setattr(fleet_board, "token", lambda: "t")
    seen = []

    def urlopen(req, timeout=None):
        seen.append((req.get_method(), req.full_url))
        if req.full_url.endswith("/missing"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b'{"message":"Not Found"}'))
        if req.full_url.endswith("/graphql"):
            return _Resp(json.dumps({"data": {"viewer": {"login": "x"}}}).encode())
        return _Resp(b'{"ok": 1}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return fleet_board, seen


def test_api_and_gql_reach_urlopen_and_read_the_answer(board):
    fb, seen = board
    assert fb.gql("query{viewer{login}}", {}) == {"viewer": {"login": "x"}}
    assert fb.api("GET", "/rate_limit") == (200, {"ok": 1})
    assert fb.api("GET", "/repos/a/b/missing") == (404, {"message": "Not Found"})   # HTTPError caught by its own class
    assert [m for m, _ in seen] == ["POST", "GET", "GET"]
