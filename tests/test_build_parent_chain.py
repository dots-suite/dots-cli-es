import asyncio

from dots_es.cli import build_parent_chain, get_parent_collection


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append(params["id"])
        return FakeResponse(self.responses[params["id"]])


def make_app():
    return type("App", (), {"config": {"DTS_URL": "http://dts"}})()


def member(parent_id, parent_label, title):
    return {"member": [{"@id": parent_id, "title": parent_label}], "title": title}


def test_shared_ancestor_fetched_once():
    client = FakeClient(
        {
            "A": member("C", "C", "A"),
            "B": member("C", "C", "B"),
            "C": member("R", "Root", "C"),
        }
    )
    cache = {}
    app = make_app()

    chain_a, _ = asyncio.run(build_parent_chain(app, "A", "R", "Root", client=client, cache=cache))
    chain_b, _ = asyncio.run(build_parent_chain(app, "B", "R", "Root", client=client, cache=cache))

    assert chain_a == ["R", "C", "A"]
    assert chain_b == ["R", "C", "B"]
    assert client.calls == ["A", "C", "B"]


def test_error_breaks_chain_without_crashing():
    client = FakeClient({"A": member("C", "C", "A"), "C": None})
    app = make_app()

    chain, _ = asyncio.run(build_parent_chain(app, "A", "R", "Root", client=client, cache={}))

    assert chain == ["A"]


def test_passed_client_is_reused(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("httpx.AsyncClient must not be created")

    monkeypatch.setattr("dots_es.cli.httpx.AsyncClient", fail)
    client = FakeClient({"A": member("R", "Root", "A")})
    app = make_app()

    result = asyncio.run(get_parent_collection(app, "A", "R", "Root", client=client, cache={}))

    assert result == ["R", "Root", "A"]


def test_none_is_cached():
    client = FakeClient({"A": None})
    cache = {}
    app = make_app()

    first = asyncio.run(get_parent_collection(app, "A", "R", "Root", client=client, cache=cache))
    second = asyncio.run(get_parent_collection(app, "A", "R", "Root", client=client, cache=cache))

    assert first is None
    assert second is None
    assert client.calls == ["A"]
