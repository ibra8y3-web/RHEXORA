import asyncio
import pytest

from rhexora.client import Peer
from rhexora.errors import ConfigurationError
from rhexora.ice import _signal_ws_url


def test_ice_signaling_url_builds_wss_path_and_encodes_room():
    url = _signal_ws_url("https://signal.example.com/base", "a" * 43, "client")
    assert url == "wss://signal.example.com/base/signal/" + "a" * 43 + "?role=client"


def test_ice_rejects_short_room_token():
    with pytest.raises(ValueError, match="random URL-safe token"):
        _signal_ws_url("https://signal.example.com", "short", "client")


def test_peer_requires_both_ice_url_and_room():
    base = {
        "name": "worker", "host": "127.0.0.1", "port": 47821,
        "tls_fingerprint": "a" * 64, "token": "b" * 32,
        "ice_signal_url": "https://signal.example.com",
    }
    with pytest.raises(ConfigurationError, match="both ice_signal_url and ice_room"):
        Peer.from_dict(base)


def test_peer_rejects_unsafe_room_token():
    base = {
        "name": "worker", "host": "127.0.0.1", "port": 47821,
        "tls_fingerprint": "a" * 64, "token": "b" * 32,
        "ice_signal_url": "https://signal.example.com", "ice_room": "../" + "a" * 40,
    }
    with pytest.raises(ConfigurationError, match="256-bit URL-safe token"):
        Peer.from_dict(base)


def test_peer_rejects_plaintext_public_signaling():
    base = {
        "name": "worker", "host": "127.0.0.1", "port": 47821,
        "tls_fingerprint": "a" * 64, "token": "b" * 32,
        "ice_signal_url": "ws://signal.example.com", "ice_room": "a" * 43,
    }
    with pytest.raises(ConfigurationError, match="must use HTTPS/WSS"):
        Peer.from_dict(base)


@pytest.mark.asyncio
async def test_signal_broker_enforces_room_capacity_and_cleans_sessions():
    aiohttp = pytest.importorskip("aiohttp")
    from rhexora.ice import IceSignalBroker

    broker = IceSignalBroker(max_rooms=1, room_idle_timeout=10)
    port = await broker.start("127.0.0.1", 0)
    room_one = "A" * 43
    room_two = "B" * 43
    async with aiohttp.ClientSession() as session:
        first = await session.ws_connect(f"http://127.0.0.1:{port}/signal/{room_one}?role=client")
        try:
            assert len(broker._rooms) == 1
            with pytest.raises(aiohttp.ClientResponseError) as error:
                await session.ws_connect(f"http://127.0.0.1:{port}/signal/{room_two}?role=client")
            assert error.value.status == 503
            second = await session.ws_connect(f"http://127.0.0.1:{port}/signal/{room_one}?role=worker")
            await second.close()
        finally:
            await first.close()
    await asyncio.sleep(0)
    assert broker._rooms == {}
    await broker.close()


def test_coturn_rest_credentials_are_expiring_and_hmac_compatible():
    import base64
    import hashlib
    import hmac
    from rhexora.ice import create_turn_credentials

    secret = "s" * 48
    username, credential = create_turn_credentials(secret, ttl_seconds=900, user_id="node_01", now=1_700_000_000)
    assert username == "1700000900:node_01"
    expected = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
    assert hmac.compare_digest(credential, expected)


def test_coturn_rest_credentials_reject_weak_secret_and_invalid_ttl():
    from rhexora.ice import create_turn_credentials

    with pytest.raises(ValueError, match="at least 32"):
        create_turn_credentials("short")
    with pytest.raises(ValueError, match="TTL"):
        create_turn_credentials("s" * 40, ttl_seconds=60)


def test_turn_credentials_are_reissued_for_new_sessions():
    from rhexora.ice import create_turn_credentials

    secret = "r" * 40
    first = create_turn_credentials(secret, ttl_seconds=900, now=1_700_000_000)
    second = create_turn_credentials(secret, ttl_seconds=900, now=1_700_000_000)
    assert first[0].startswith("1700000900:")
    assert second[0].startswith("1700000900:")
    assert first[0] != second[0]
    assert first[1] != second[1]


def test_ice_environment_generates_turn_rest_credentials(monkeypatch):
    import base64
    import time
    from rhexora.ice import ice_settings_from_environment

    monkeypatch.setenv("RHEXORA_ICE_STUN", "stun:example.net:3478")
    monkeypatch.setenv("RHEXORA_ICE_TURN_URL", "turn:turn.example.net:3478?transport=udp")
    monkeypatch.setenv("RHEXORA_ICE_TURN_SHARED_SECRET", "x" * 40)
    monkeypatch.setenv("RHEXORA_ICE_TURN_TTL_SECONDS", "900")
    before = int(time.time())
    settings = ice_settings_from_environment()
    expiry = int(settings["turn_username"].split(":", 1)[0])
    assert settings["turn_url"].startswith("turn:")
    assert before + 900 <= expiry <= int(time.time()) + 900
    assert len(base64.b64decode(settings["turn_credential"])) == 20
    assert settings["stun_urls"] == ["stun:example.net:3478"]
