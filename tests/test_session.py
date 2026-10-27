"""Tests for the Xiaomi Standard Auth and Miot BLE session."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from custom_components.miot_ble.const import (
    UUID_AUTH,
    UUID_MESH_CHANNEL,
    UUID_MESH_LOGIN,
    UUID_MIOT_NOTIFY,
    UUID_MIOT_WRITE,
    UUID_UPNP,
)
from custom_components.miot_ble.profiles import MUXNW_DRYER_M73, SOOCARE_MAGIC_TOUCH_M14
from custom_components.miot_ble.protocol import miot_v2
from custom_components.miot_ble.protocol.crypto import MeshSessionKeys, SessionKeys
from custom_components.miot_ble.protocol.session import (
    ACK_DATA,
    AUTH_RESPONSE_INFO,
    AUTH_RESPONSE_KEY,
    CONFIRM_LOGIN,
    DENY_LOGIN,
    MESH_CMD_LOGIN,
    RECEIVE_OK,
    RECEIVE_READY,
    AuthenticationError,
    MiotBleError,
    MiotBleSession,
    NotificationInbox,
    ResponseTimeoutError,
)

TOKEN = bytes.fromhex("00112233445566778899aabb")
APP_RANDOM = bytes(range(16))
DEVICE_RANDOM = bytes(range(16, 32))


class FakeClient:
    """Record GATT calls made by a protocol session."""

    def __init__(self) -> None:
        self.subscriptions: list[str] = []
        self.writes: list[tuple[str, bytes, bool]] = []
        self.write_error: Exception | None = None

    async def start_notify(self, char_specifier: str, callback) -> None:
        self.subscriptions.append(char_specifier)

    async def write_gatt_char(
        self, char_specifier: str, data: bytes, response: bool = False
    ) -> None:
        if self.write_error is not None:
            raise self.write_error
        self.writes.append((char_specifier, bytes(data), response))


@dataclass
class Sender:
    """Provide the Bleak sender UUID attribute used by notifications."""

    uuid: str


def feed(session: MiotBleSession, uuid: str, *frames: bytes) -> None:
    """Feed captured notifications into a session inbox."""
    for frame in frames:
        session._on_notify(Sender(uuid.upper()), bytearray(frame))


def auth_header(kind: int, count: int) -> bytes:
    return bytes((0, 0, 0, kind, count, 0))


def parcel(number: int, data: bytes) -> bytes:
    return number.to_bytes(2, "little") + data


def direct_response(keys: SessionKeys, counter: int, plaintext: bytes) -> bytes:
    ciphertext = keys.encrypt_from_device(counter, plaintext)
    return bytes.fromhex("00000200") + counter.to_bytes(2, "little") + ciphertext


@pytest.mark.asyncio
async def test_notification_inbox_retains_unmatched_frames() -> None:
    inbox = NotificationInbox()
    inbox.feed("first", b"1")
    inbox.feed("second", b"2")
    assert await inbox.wait_for(lambda uuid, data: uuid == "second", 0.1) == ("second", b"2")
    assert await inbox.wait_for(lambda uuid, data: data == b"1", 0.1) == ("first", b"1")
    with pytest.raises(ResponseTimeoutError):
        await inbox.wait_for(lambda uuid, data: False, 0.001)


@pytest.mark.asyncio
async def test_start_subscribes_once() -> None:
    client = FakeClient()
    session = MiotBleSession(client, TOKEN, MUXNW_DRYER_M73)
    await session.start()
    await session.start()
    assert client.subscriptions == [UUID_UPNP, UUID_AUTH, UUID_MIOT_WRITE, UUID_MIOT_NOTIFY]


@pytest.mark.asyncio
async def test_standard_auth_login_with_framed_device_data() -> None:
    client = FakeClient()
    session = MiotBleSession(
        client,
        TOKEN,
        MUXNW_DRYER_M73,
        random_bytes=lambda size: APP_RANDOM,
    )
    await session.start()
    keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    proof = keys.expected_device_proof(APP_RANDOM, DEVICE_RANDOM)
    feed(session, UUID_AUTH, RECEIVE_READY, RECEIVE_OK)
    feed(session, UUID_AUTH, auth_header(AUTH_RESPONSE_KEY, 1), parcel(1, DEVICE_RANDOM))
    feed(
        session,
        UUID_AUTH,
        auth_header(AUTH_RESPONSE_INFO, 2),
        parcel(1, proof[:18]),
        parcel(2, proof[18:]),
        RECEIVE_READY,
        RECEIVE_OK,
    )
    feed(session, UUID_UPNP, CONFIRM_LOGIN)

    await session.login(open_property_session=False)

    assert session.authenticated
    writes = [(uuid, data) for uuid, data, _ in client.writes]
    assert (UUID_UPNP, bytes.fromhex("24000000")) in writes
    assert (UUID_AUTH, bytes.fromhex("0000000b0100")) in writes
    assert (UUID_AUTH, bytes.fromhex("0000000a0200")) in writes


@pytest.mark.asyncio
async def test_auth_login_accepts_direct_device_data() -> None:
    client = FakeClient()
    session = MiotBleSession(
        client,
        TOKEN,
        MUXNW_DRYER_M73,
        random_bytes=lambda size: APP_RANDOM,
    )
    await session.start()
    keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    proof = keys.expected_device_proof(APP_RANDOM, DEVICE_RANDOM)
    feed(session, UUID_AUTH, RECEIVE_READY, RECEIVE_OK)
    feed(
        session,
        UUID_AUTH,
        bytes((0, 0, 2, AUTH_RESPONSE_KEY)) + DEVICE_RANDOM,
        bytes((0, 0, 2, AUTH_RESPONSE_INFO)) + proof,
        RECEIVE_READY,
        RECEIVE_OK,
    )
    feed(session, UUID_UPNP, CONFIRM_LOGIN)

    await session.login(open_property_session=False)

    assert [write[1] for write in client.writes].count(ACK_DATA) == 2


@pytest.mark.asyncio
async def test_mesh_login_uses_mesh_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    client = FakeClient()
    session = MiotBleSession(client, bytes(32), SOOCARE_MAGIC_TOUCH_M14)
    await session.start()
    app_private_key = ec.derive_private_key(1, ec.SECP256R1())
    device_private_key = ec.derive_private_key(2, ec.SECP256R1())
    device_numbers = device_private_key.public_key().public_numbers()
    device_public_key = device_numbers.x.to_bytes(32, "big") + device_numbers.y.to_bytes(32, "big")
    monkeypatch.setattr(
        "custom_components.miot_ble.protocol.session.ec.generate_private_key",
        lambda curve: app_private_key,
    )
    monkeypatch.setattr("custom_components.miot_ble.protocol.session.asyncio.sleep", AsyncMock())
    feed(session, UUID_MESH_CHANNEL, RECEIVE_READY)
    feed(session, UUID_MESH_CHANNEL, bytes((0, 0, 2, 3)) + device_public_key)
    feed(session, UUID_MESH_CHANNEL, RECEIVE_READY)
    feed(session, UUID_MESH_LOGIN, b"\x51\x00\x00\x00")

    await session.login(open_property_session=False)

    writes = [(uuid, data) for uuid, data, _ in client.writes]
    assert (UUID_MESH_LOGIN, MESH_CMD_LOGIN) in writes
    assert (UUID_MESH_LOGIN, bytes.fromhex("24000000")) not in writes
    assert session.authenticated
    await session.async_close()


@pytest.mark.asyncio
async def test_mesh_open_and_read_properties() -> None:
    session = MiotBleSession(FakeClient(), bytes(32), SOOCARE_MAGIC_TOUCH_M14)
    session._transaction_id = 0xFFFF
    sent: list[bytes] = []
    session._send_request = AsyncMock(side_effect=lambda payload: sent.append(payload))
    session._receive_encrypted = AsyncMock(
        side_effect=[
            bytes.fromhex("05200000f0"),
            bytes.fromhex("0e2001000301030100000001102a"),
        ]
    )

    await session.open_property_session()
    values = await session.read_properties((SOOCARE_MAGIC_TOUCH_M14.property(3, 1),))

    assert sent[0] == miot_v2.build_message(0, 0xF0)
    assert values[(3, 1)].value == 42
    assert session._receive_encrypted.await_count == 2


@pytest.mark.asyncio
async def test_mesh_receive_decrypts_and_acknowledges() -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    device = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    public_key = device.x.to_bytes(32, "big") + device.y.to_bytes(32, "big")
    keys = MeshSessionKeys.from_private_key(
        ec.derive_private_key(1, ec.SECP256R1()), public_key, bytes(32)
    )
    client = FakeClient()
    session = MiotBleSession(client, bytes(32), SOOCARE_MAGIC_TOUCH_M14)
    session._mesh_keys = keys
    feed(
        session,
        UUID_MIOT_NOTIFY,
        bytes.fromhex("000002000700") + keys.encrypt_from_device(7, b"payload"),
    )

    assert await session._receive_encrypted() == b"payload"
    assert client.writes[-1][1] == ACK_DATA


@pytest.mark.asyncio
async def test_mesh_send_request_uses_one_fragment() -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    device = ec.generate_private_key(ec.SECP256R1()).public_key().public_numbers()
    public_key = device.x.to_bytes(32, "big") + device.y.to_bytes(32, "big")
    keys = MeshSessionKeys.from_private_key(
        ec.derive_private_key(1, ec.SECP256R1()), public_key, bytes(32)
    )
    client = FakeClient()
    session = MiotBleSession(client, bytes(32), SOOCARE_MAGIC_TOUCH_M14)
    session._mesh_keys = keys
    session._wait_data = AsyncMock()

    await session._send_request(b"payload")

    assert client.writes[0][1] == bytes.fromhex("000000000100")
    assert client.writes[1][1] == b"\x01\x00" + keys.encrypt_from_app(0, b"payload")


@pytest.mark.asyncio
async def test_mesh_mng_loop_negotiates_mtu() -> None:
    client = FakeClient()
    session = MiotBleSession(client, bytes(32), SOOCARE_MAGIC_TOUCH_M14)
    task = asyncio.create_task(session._mesh_mng_loop())
    feed(session, UUID_MESH_CHANNEL, bytes.fromhex("0000040006f2"))
    await asyncio.sleep(0)
    feed(session, UUID_MESH_CHANNEL, bytes.fromhex("00000401") + bytes([0xF2]) * 240)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (UUID_MESH_CHANNEL, bytes.fromhex("0000050006f2")) in [
        (uuid, data) for uuid, data, _ in client.writes
    ]
    assert client.writes[-1][1][:4] == bytes.fromhex("00000501")


@pytest.mark.asyncio
async def test_mesh_mng_loop_records_failures_and_protocol_reports_them(caplog) -> None:
    client = FakeClient()
    client.write_error = RuntimeError("write failed")
    session = MiotBleSession(client, bytes(32), SOOCARE_MAGIC_TOUCH_M14)
    session._mesh_mng_task = asyncio.create_task(session._mesh_mng_loop())
    feed(session, UUID_MESH_CHANNEL, bytes.fromhex("0000040006f2"))
    await session._mesh_mng_task

    assert "Mesh management task stopped: write failed" in caplog.text
    with pytest.raises(MiotBleError, match="management task stopped"):
        await session._receive_encrypted(timeout=0.01)
    await session.async_close()


@pytest.mark.asyncio
async def test_login_rejects_bad_proof_and_denial() -> None:
    client = FakeClient()
    session = MiotBleSession(
        client,
        TOKEN,
        MUXNW_DRYER_M73,
        random_bytes=lambda size: APP_RANDOM,
    )
    await session.start()
    feed(session, UUID_AUTH, RECEIVE_READY, RECEIVE_OK)
    feed(
        session,
        UUID_AUTH,
        bytes((0, 0, 2, AUTH_RESPONSE_KEY)) + DEVICE_RANDOM,
        bytes((0, 0, 2, AUTH_RESPONSE_INFO)) + bytes(32),
    )
    with pytest.raises(AuthenticationError, match="proof"):
        await session.login(open_property_session=False)

    client = FakeClient()
    session = MiotBleSession(
        client,
        TOKEN,
        MUXNW_DRYER_M73,
        random_bytes=lambda size: APP_RANDOM,
    )
    await session.start()
    keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    feed(session, UUID_AUTH, RECEIVE_READY, RECEIVE_OK)
    feed(
        session,
        UUID_AUTH,
        bytes((0, 0, 2, AUTH_RESPONSE_KEY)) + DEVICE_RANDOM,
        bytes((0, 0, 2, AUTH_RESPONSE_INFO))
        + keys.expected_device_proof(APP_RANDOM, DEVICE_RANDOM),
        RECEIVE_READY,
        RECEIVE_OK,
    )
    feed(session, UUID_UPNP, DENY_LOGIN)
    with pytest.raises(AuthenticationError, match="rejected"):
        await session.login(open_property_session=False)


@pytest.mark.asyncio
async def test_login_validates_session_state_and_random_source() -> None:
    session = MiotBleSession(FakeClient(), TOKEN, MUXNW_DRYER_M73)
    with pytest.raises(MiotBleError, match="started"):
        await session.login(open_property_session=False)
    await session.start()
    session._random_bytes = lambda size: b"short"
    with pytest.raises(ValueError, match="16 bytes"):
        await session.login(open_property_session=False)
    with pytest.raises(ValueError, match="Token"):
        MiotBleSession(FakeClient(), b"", MUXNW_DRYER_M73)


@pytest.mark.asyncio
async def test_auth_transfer_rejects_malformed_data() -> None:
    session = MiotBleSession(FakeClient(), TOKEN, MUXNW_DRYER_M73)
    feed(session, UUID_AUTH, bytes((0, 0, 0, AUTH_RESPONSE_KEY, 1)))
    with pytest.raises(MiotBleError, match="malformed"):
        await session._receive_auth_payload(AUTH_RESPONSE_KEY, 16)

    session = MiotBleSession(FakeClient(), TOKEN, MUXNW_DRYER_M73)
    feed(session, UUID_AUTH, auth_header(AUTH_RESPONSE_KEY, 0))
    with pytest.raises(MiotBleError, match="no parcels"):
        await session._receive_auth_payload(AUTH_RESPONSE_KEY, 16)

    session = MiotBleSession(FakeClient(), TOKEN, MUXNW_DRYER_M73)
    feed(session, UUID_AUTH, bytes((0, 0, 2, AUTH_RESPONSE_KEY)) + b"short")
    with pytest.raises(MiotBleError, match="expected 16"):
        await session._receive_auth_payload(AUTH_RESPONSE_KEY, 16)


def test_parcel_and_encryption_validation() -> None:
    session = MiotBleSession(FakeClient(), TOKEN, MUXNW_DRYER_M73)
    with pytest.raises(ValueError, match="must not be empty"):
        session._parcels(b"")
    with pytest.raises(AuthenticationError):
        session._encrypt(b"payload")
    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    session._tx_counter = 0xFFFF
    with pytest.raises(MiotBleError, match="exhausted"):
        session._encrypt(b"payload")


@pytest.mark.asyncio
async def test_read_and_set_properties_with_direct_notifications() -> None:
    client = FakeClient()
    session = MiotBleSession(client, TOKEN, MUXNW_DRYER_M73)
    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    power = MUXNW_DRYER_M73.property(2, 1)

    feed(session, UUID_MIOT_WRITE, RECEIVE_READY, RECEIVE_OK)
    snapshot = bytes.fromhex("0e20b10003010201000000011001")
    feed(session, UUID_MIOT_NOTIFY, direct_response(session._keys, 0, snapshot))
    values = await session.read_properties((power,))
    assert values[(2, 1)].value == 1

    feed(session, UUID_MIOT_WRITE, RECEIVE_READY, RECEIVE_OK)
    read_request = bytes.fromhex("0620aa000100")
    notification = bytes.fromhex("0c20b2000401020100011000")
    feed(
        session,
        UUID_MIOT_NOTIFY,
        direct_response(session._keys, 1, read_request),
        direct_response(session._keys, 2, notification),
    )
    changed = await session.set_property(power, 0)
    assert changed.value == 0
    assert [write[1] for write in client.writes].count(ACK_DATA) == 3


@pytest.mark.asyncio
async def test_subscribe_continuously_updates_property_cache() -> None:
    client = FakeClient()
    session = MiotBleSession(client, TOKEN, MUXNW_DRYER_M73)
    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    power = MUXNW_DRYER_M73.property(2, 1)
    updates: list[tuple] = []
    changed = asyncio.Event()
    assert not session.receiver_running

    def callback(values: tuple) -> None:
        updates.append(values)
        if values[0].value == 0:
            changed.set()

    feed(session, UUID_MIOT_WRITE, RECEIVE_READY, RECEIVE_OK)
    snapshot = bytes.fromhex("0e20b10003010201000000011001")
    feed(session, UUID_MIOT_NOTIFY, direct_response(session._keys, 0, snapshot))
    initial = await session.subscribe_properties((power,), callback)
    assert session.receiver_running

    notification = bytes.fromhex("0c2044000401020100011000")
    feed(session, UUID_MIOT_NOTIFY, direct_response(session._keys, 1, notification))
    await asyncio.wait_for(changed.wait(), 0.1)

    assert initial[(2, 1)].value == 1
    assert session.property_values[(2, 1)].value == 0
    assert [values[0].value for values in updates] == [1, 0]
    with pytest.raises(MiotBleError, match="already running"):
        await session.subscribe_properties((power,), callback)
    with pytest.raises(MiotBleError, match="already owns"):
        await session.read_properties((power,))
    await session.async_close()
    assert not session.receiver_running


@pytest.mark.asyncio
async def test_background_receiver_matches_target_value_across_transaction_ids() -> None:
    client = FakeClient()
    session = MiotBleSession(client, TOKEN, MUXNW_DRYER_M73)
    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    power = MUXNW_DRYER_M73.property(2, 1)

    feed(session, UUID_MIOT_WRITE, RECEIVE_READY, RECEIVE_OK)
    snapshot = bytes.fromhex("0e20b10003010201000000011001")
    feed(session, UUID_MIOT_NOTIFY, direct_response(session._keys, 0, snapshot))
    await session.subscribe_properties((power,), lambda values: None)

    feed(session, UUID_MIOT_WRITE, RECEIVE_READY, RECEIVE_OK)
    setter = asyncio.create_task(session.set_property(power, 0))
    await asyncio.sleep(0)
    stale_value = bytes.fromhex("0c20b2000401020100011001")
    notification = bytes.fromhex("0c2044000401020100011000")
    feed(
        session,
        UUID_MIOT_NOTIFY,
        direct_response(session._keys, 1, stale_value),
        direct_response(session._keys, 2, notification),
    )

    assert (await setter).value == 0
    assert session.property_values[(2, 1)].value == 0
    await session.async_close()


@pytest.mark.asyncio
async def test_background_set_timeout_has_a_clear_error() -> None:
    session = MiotBleSession(FakeClient(), TOKEN, MUXNW_DRYER_M73, timeout=0.001)
    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    session._send_request = AsyncMock()
    session._receiver_task = asyncio.create_task(asyncio.Event().wait())
    power = MUXNW_DRYER_M73.property(2, 1)

    with pytest.raises(ResponseTimeoutError, match="power write confirmation"):
        await session.set_property(power, 0)

    assert session._pending_responses == {}
    await session.async_close()


@pytest.mark.asyncio
async def test_receiver_ignores_non_property_payload_and_reports_failure() -> None:
    session = MiotBleSession(FakeClient(), TOKEN, MUXNW_DRYER_M73)
    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    session._receive_encrypted = AsyncMock(
        side_effect=[bytes((5, 0x20, 1, 0, 0xF0)), RuntimeError("receiver failed")]
    )
    session._receiver_task = asyncio.create_task(session._receive_property_notifications())
    await session._receiver_task

    with pytest.raises(MiotBleError, match="receiver stopped") as raised:
        session.raise_if_receiver_failed()
    assert isinstance(raised.value.__cause__, RuntimeError)
    await session.async_close()


@pytest.mark.asyncio
async def test_read_properties_subscribes_to_a_batch_once() -> None:
    client = FakeClient()
    session = MiotBleSession(client, TOKEN, MUXNW_DRYER_M73)
    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    power = MUXNW_DRYER_M73.property(2, 1)
    uv = MUXNW_DRYER_M73.property(2, 2)

    feed(session, UUID_MIOT_WRITE, RECEIVE_READY, RECEIVE_OK)
    snapshot = bytes.fromhex("1620b100030202010000000110010202000000011000")
    feed(session, UUID_MIOT_NOTIFY, direct_response(session._keys, 0, snapshot))

    values = await session.read_properties((power, uv))

    assert {key: value.value for key, value in values.items()} == {(2, 1): 1, (2, 2): 0}
    headers = [
        data
        for uuid, data, _ in client.writes
        if uuid == UUID_MIOT_WRITE and data.startswith(bytes(4))
    ]
    assert headers == [bytes.fromhex("000000000100")]


@pytest.mark.asyncio
async def test_framed_encrypted_response_and_property_open() -> None:
    client = FakeClient()
    session = MiotBleSession(client, TOKEN, MUXNW_DRYER_M73)
    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    feed(session, UUID_MIOT_WRITE, RECEIVE_READY, RECEIVE_OK)
    response = bytes((5, 0x20, 0xB1, 0, 0xF0))
    encrypted = (3).to_bytes(2, "little") + session._keys.encrypt_from_device(3, response)
    feed(
        session,
        UUID_MIOT_NOTIFY,
        bytes.fromhex("000000000100"),
        parcel(1, encrypted),
    )

    await session.open_property_session()

    assert (UUID_MIOT_NOTIFY, RECEIVE_READY, False) in client.writes
    assert (UUID_MIOT_NOTIFY, RECEIVE_OK, False) in client.writes


@pytest.mark.asyncio
async def test_property_open_allows_no_echo() -> None:
    session = MiotBleSession(FakeClient(), TOKEN, MUXNW_DRYER_M73, timeout=0.001)
    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    feed(session, UUID_MIOT_WRITE, RECEIVE_READY, RECEIVE_OK)
    await session.open_property_session()


@pytest.mark.asyncio
async def test_encrypted_response_validation() -> None:
    session = MiotBleSession(FakeClient(), TOKEN, MUXNW_DRYER_M73)
    with pytest.raises(AuthenticationError):
        await session._receive_encrypted()

    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    feed(session, UUID_MIOT_NOTIFY, bytes.fromhex("000000000000"))
    with pytest.raises(MiotBleError, match="no parcels"):
        await session._receive_encrypted()

    feed(session, UUID_MIOT_NOTIFY, bytes.fromhex("000002000100010203"))
    with pytest.raises(MiotBleError, match="truncated"):
        await session._receive_encrypted()


@pytest.mark.asyncio
async def test_unexpected_property_open_response() -> None:
    session = MiotBleSession(FakeClient(), TOKEN, MUXNW_DRYER_M73)
    session._keys = SessionKeys.derive(TOKEN, APP_RANDOM, DEVICE_RANDOM)
    feed(session, UUID_MIOT_WRITE, RECEIVE_READY, RECEIVE_OK)
    response = bytes.fromhex("0c20b1000401020100011001")
    feed(session, UUID_MIOT_NOTIFY, direct_response(session._keys, 0, response))
    with pytest.raises(MiotBleError, match="Unexpected"):
        await session.open_property_session()


@pytest.mark.asyncio
async def test_notification_arriving_after_wait_starts() -> None:
    inbox = NotificationInbox()

    async def notify() -> None:
        await asyncio.sleep(0)
        inbox.feed("uuid", b"data")

    task = asyncio.create_task(notify())
    assert await inbox.wait_for(lambda uuid, data: True, 0.1) == ("uuid", b"data")
    await task
