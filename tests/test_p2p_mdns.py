"""Deterministic Zeroconf lifecycle tests; these do not certify physical LAN."""
import threading
import time
import types
import uuid
from unittest.mock import patch

import pytest
from app.services.p2p_discovery import MdnsDiscovery, DiscoveryError, SERVICE_TYPE


class Info:
    def __init__(self, service_type, name, *, addresses, port, properties, **kwargs):
        import ipaddress
        self.name = name
        self.port = port
        self.properties = {k.encode() if isinstance(k, str) else k:
                           v.encode() if isinstance(v, str) else v for k, v in properties.items()}
        self.addresses = [str(ipaddress.ip_address(a)) for a in addresses]
    def parsed_addresses(self):
        return self.addresses


def backend():
    state = types.SimpleNamespace(closed=0, cancelled=0, registered=[], services={}, thread_ids=[])
    class ZC:
        def __init__(self, **kwargs):
            state.thread_ids.append(threading.get_ident())
        def register_service(self, info):
            state.registered.append(info)
        def unregister_service(self, info):
            state.registered.remove(info)
        def get_service_info(self, kind, name, timeout):
            assert timeout <= 250
            return state.services.get(name)
        def close(self):
            state.closed += 1
    class Browser:
        def __init__(self, zc, kind, listener):
            state.listener = listener
        def cancel(self):
            state.cancelled += 1
    return types.SimpleNamespace(Zeroconf=ZC, ServiceInfo=Info, ServiceBrowser=Browser), state


def remote(state, *, hint='paired'):
    session = str(uuid.uuid4())
    info = Info(SERVICE_TYPE, session + '.' + SERVICE_TYPE, addresses=[b'\x7f\0\0\1'],
                port=4000, properties={'version': '1', 'session': session, 'pairing': hint})
    state.services[info.name] = info
    return info


def eventually(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def test_construct_is_network_free_and_missing_dependency_is_graceful():
    with patch('app.services.p2p_discovery.importlib.import_module', side_effect=ImportError):
        discovery = MdnsDiscovery()
        assert not discovery.running
        discovery.start(4000, addresses=['127.0.0.1'])
        with pytest.raises(DiscoveryError, match='Zeroconf'):
            discovery.wait_ready()
        discovery.stop()


def test_background_lifecycle_restart_and_private_advertisement():
    api, state = backend()
    discovery = MdnsDiscovery(backend=api)
    sessions = []
    for _ in range(3):
        discovery.start(4000, addresses=['127.0.0.1'])
        discovery.wait_ready()
        sessions.append(discovery.session_id)
        props = state.registered[0].properties
        assert set(props) == {b'version', b'session', b'pairing'}
        assert state.thread_ids[-1] != threading.get_ident()
        with pytest.raises(DiscoveryError):
            discovery.start(4000, addresses=['127.0.0.1'])
        discovery.stop()
        assert not discovery.running
    assert len(set(sessions)) == 3
    assert state.closed == state.cancelled == 3
    assert not state.registered


def test_local_workspace_and_pairing_filter_never_trusts_remote_hint():
    api, state = backend()
    grants = {}
    discovery = MdnsDiscovery(backend=api, trusted_scopes=lambda item: grants.get(item.session_id))
    discovery.start(4000, addresses=['127.0.0.1'])
    try:
        discovery.wait_ready()
        info = remote(state)
        discovery.add_service(None, SERVICE_TYPE, info.name)
        eventually(lambda: len(discovery.candidates()) == 1)
        assert not discovery.candidates()[0].paired
        assert discovery.candidates(paired_only=True) == []
        assert discovery.candidates(workspace_id='work') == []
        grants[info.properties[b'session'].decode()] = {'work'}
        assert len(discovery.candidates(workspace_id='work', paired_only=True)) == 1
        assert discovery.candidates(workspace_id='personal') == []
        discovery.remove_service(None, SERVICE_TYPE, info.name)
        eventually(lambda: not discovery.candidates())
    finally:
        discovery.stop()


@pytest.mark.parametrize('props', [
    {b'version': b'2', b'session': b'bad', b'pairing': b'paired'},
    {b'version': b'1', b'session': b'bad', b'pairing': b'paired'},
    {b'version': b'1', b'session': b'x' * 600, b'pairing': b'paired'},
    {b'version': b'1', b'session': b'bad', b'workspace': b'private'},
])
def test_malformed_txt_rejected(props):
    discovery = MdnsDiscovery()
    info = types.SimpleNamespace(port=4000, properties=props, parsed_addresses=lambda: ['127.0.0.1'])
    assert discovery._parse(info) is None


def test_bounded_queue_and_expiring_records():
    discovery = MdnsDiscovery()
    for i in range(1000):
        discovery.add_service(None, SERVICE_TYPE, str(i) + '.' + SERVICE_TYPE)
    assert discovery._events.qsize() == 64
    api, state = backend()
    info = remote(state)
    item = discovery._parse(info)
    discovery._records['old'] = item
    with patch('app.services.p2p_discovery.time.monotonic', return_value=item.discovered_at + 31):
        assert discovery.candidates() == []


@pytest.mark.parametrize('host', ['8.8.8.8', '0.0.0.0', '224.0.0.1'])
def test_nonlocal_advertisement_rejected(host):
    with pytest.raises(ValueError):
        MdnsDiscovery().start(4000, addresses=[host])


def test_optional_real_zeroconf_loopback(monkeypatch):
    """Explicit opt-in uses actual multicast sockets, not the fake backend."""
    import os
    if os.environ.get('ORANGE_NOTES_TEST_MDNS') != '1':
        pytest.skip('Set ORANGE_NOTES_TEST_MDNS=1 to exercise native multicast sockets')
    zc = pytest.importorskip('zeroconf')
    left = MdnsDiscovery(backend=zc, interfaces=['127.0.0.1'])
    right = MdnsDiscovery(backend=zc, interfaces=['127.0.0.1'])
    try:
        left.start(41001, addresses=['127.0.0.1'])
        right.start(41002, addresses=['127.0.0.1'])
        left.wait_ready(5)
        right.wait_ready(5)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if any(item.session_id == right.session_id for item in left.candidates()) and any(
                    item.session_id == left.session_id for item in right.candidates()):
                break
            time.sleep(.05)
        assert any(item.session_id == right.session_id for item in left.candidates())
        assert any(item.session_id == left.session_id for item in right.candidates())
    finally:
        left.stop()
        right.stop()
