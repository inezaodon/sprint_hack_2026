"""The deployed site serves web/app.html at /. It is committed, so it must match a fresh build of artifact/."""
from fastapi.testclient import TestClient

from goodwill_pulse import webapp
from goodwill_pulse.api import app
from goodwill_pulse.config import WEB_DIR


def test_committed_app_page_is_up_to_date(tmp_path):
    fresh = webapp.build(tmp_path / "app.html").read_text()
    assert (WEB_DIR / "app.html").read_text() == fresh, \
        "web/app.html is stale: run .venv/bin/python -m goodwill_pulse.webapp and commit it"


def test_root_serves_the_full_app():
    r = TestClient(app).get("/")
    assert r.status_code == 200
    for tab in ("home", "dashboard", "ask", "bc", "upload", "lineage", "close", "quality"):
        assert f'registerTab("{tab}"' in r.text
    assert "window.claude = {use" in r.text


def test_page_docs_are_served():
    """The page embeds only an index of its documents and fetches each from /static/docs/ (webapp.publish_docs)."""
    c = TestClient(app)
    for name in ("hub__meta.json", "hub__days.json", "thriftly__all.json", "daily__all.json"):
        r = c.get("/static/docs/" + name)
        assert r.status_code == 200, name
        assert r.json()
