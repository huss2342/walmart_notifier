"""Personal watches drive dedicated searches without disclosing member identities."""

from types import SimpleNamespace

from discord_bridge.request_runtime import RequestRuntime
from discord_bridge.request_store import RequestStore


def test_searches_follow_watch_edits_deletes_and_shared_matches(tmp_path):
    store = RequestStore(tmp_path / 'requests.sqlite3')
    first, second = '111111111111111111', '222222222222222222'
    watch = store.add(first, 'Ice cream maker')
    store.add(second, 'maker ice cream', min_value=100)
    exact = store.add(first, 'https://www.walmart.com/ip/123456789')
    assert store.search_queries() == ['123456789', 'ice cream maker']
    store.edit(first, watch['id'], 'garden bed')
    assert store.search_queries() == ['123456789', 'garden bed', 'maker ice cream']
    store.delete(first, exact['id'])
    store.revoke_user(second)
    assert store.search_queries() == ['garden bed']
    store.revoke_user(first)
    assert store.search_queries() == []


def test_runtime_searches_require_ready_listener(tmp_path):
    runtime = RequestRuntime(None, {}, tmp_path / 'requests.sqlite3', token='test')
    store = RequestStore(runtime.path)
    store.add('111111111111111111', 'couch')
    runtime.worker = SimpleNamespace(store=store)
    assert runtime.search_queries() == []
    runtime._ready = True
    assert runtime.search_queries() == ['couch']
    runtime._stop.set()
    assert runtime.search_queries() == []


def test_status_accepts_dedicated_lane_with_existing_three_lanes():
    from copy import deepcopy

    from scanner_control import validate_status
    from test_scanner_control import report

    value = report()
    base = value['lanes'][0]
    value['lanes'] = [dict(deepcopy(base), lane=name)
                      for name in ('departments', 'keywords', 'priority', 'requests')]
    value['errors'] = [{'component': 'requests', 'code': 'requests-sync-failed'}]
    assert validate_status(value)['lanes'] == value['lanes']
