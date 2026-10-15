"""Xiaomi Standard Auth and encrypted MIoT BLE session."""

from __future__ import annotations

import asyncio
import logging
import os
import struct
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol

from ..const import UUID_AUTH, UUID_MIOT_NOTIFY, UUID_MIOT_WRITE, UUID_UPNP
from ..profile import DeviceProfile, PropertySpec
from .crypto import SessionKeys
from .tlv import Operation, PropertyValue, build_set, build_subscribe, parse_properties

_LOGGER = logging.getLogger(__name__)

CMD_LOGIN = bytes.fromhex("24000000")
CONFIRM_LOGIN = bytes.fromhex("21000000")
DENY_LOGIN = bytes.fromhex("23000000")
RECEIVE_READY = bytes.fromhex("00000101")
RECEIVE_OK = bytes.fromhex("00000100")
ACK_DATA = bytes.fromhex("00000300")

AUTH_SEND_INFO = 0x0A
AUTH_SEND_KEY = 0x0B
AUTH_RESPONSE_INFO = 0x0C
AUTH_RESPONSE_KEY = 0x0D
AUTH_CHUNK_SIZE = 18


class MiotBleError(Exception):
    """Base error raised by the MIoT BLE protocol."""


class AuthenticationError(MiotBleError):
    """The device rejected authentication or returned an invalid proof."""


class ResponseTimeoutError(MiotBleError):
    """The device did not return the expected response in time."""


class GattClient(Protocol):
    """Subset of a Bleak client required by the protocol session."""

    async def start_notify(self, char_specifier: str, callback: Callable[..., None]) -> None:
        """Subscribe to a characteristic."""

    async def write_gatt_char(
        self, char_specifier: str, data: bytes, response: bool = False
    ) -> None:
        """Write one characteristic value."""


class NotificationInbox:
    """Buffer notifications without discarding interleaved protocol frames."""

    def __init__(self, maximum_size: int = 256) -> None:
        self._items: deque[tuple[str, bytes]] = deque(maxlen=maximum_size)
        self._event = asyncio.Event()

    def feed(self, uuid: str, data: bytes) -> None:
        """Add one notification to the inbox."""
        self._items.append((uuid.lower(), bytes(data)))
        self._event.set()

    def _take(self, predicate: Callable[[str, bytes], bool]) -> tuple[str, bytes] | None:
        for index, item in enumerate(self._items):
            if predicate(*item):
                del self._items[index]
                return item
        return None

    async def wait_for(
        self,
        predicate: Callable[[str, bytes], bool],
        timeout: float,
    ) -> tuple[str, bytes]:
        """Wait for a matching notification and retain non-matching frames."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            item = self._take(predicate)
            if item is not None:
                return item
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ResponseTimeoutError("Timed out waiting for a BLE notification")
            self._event.clear()
            try:
                await asyncio.wait_for(self._event.wait(), remaining)
            except TimeoutError:
                item = self._take(predicate)
                if item is not None:
                    return item
                raise ResponseTimeoutError("Timed out waiting for a BLE notification") from None


class MiotBleSession:
    """Own an authenticated Xiaomi BLE connection and MIoT transaction state."""

    def __init__(
        self,
        client: GattClient,
        token: bytes,
        profile: DeviceProfile,
        *,
        timeout: float = 8.0,
        random_bytes: Callable[[int], bytes] = os.urandom,
    ) -> None:
        if not token:
            raise ValueError("Token must not be empty")
        self._client = client
        self._token = token
        self.profile = profile
        self._timeout = timeout
        self._random_bytes = random_bytes
        self._inbox = NotificationInbox()
        self._keys: SessionKeys | None = None
        self._started = False
        self._transaction_id = 0xB0
        self._tx_counter = -1
        self._command_lock = asyncio.Lock()
        self._property_values: dict[tuple[int, int], PropertyValue] = {}
        self._property_callback: Callable[[tuple[PropertyValue, ...]], None] | None = None
        self._pending_responses: dict[int, asyncio.Future[tuple[PropertyValue, ...]]] = {}
        self._receiver_task: asyncio.Task[None] | None = None
        self._receiver_error: Exception | None = None

    @property
    def authenticated(self) -> bool:
        """Return whether Standard Auth completed successfully."""
        return self._keys is not None

    @property
    def property_values(self) -> dict[tuple[int, int], PropertyValue]:
        """Return the latest property values received from the device."""
        return dict(self._property_values)

    def _on_notify(self, sender: Any, data: bytearray) -> None:
        uuid = str(getattr(sender, "uuid", sender)).lower()
        _LOGGER.debug("BLE notification %s <- %s", uuid, bytes(data).hex())
        self._inbox.feed(uuid, bytes(data))

    async def start(self) -> None:
        """Subscribe to every characteristic used by the protocol."""
        if self._started:
            return
        for uuid in (UUID_UPNP, UUID_AUTH, UUID_MIOT_WRITE, UUID_MIOT_NOTIFY):
            await self._client.start_notify(uuid, self._on_notify)
        self._started = True

    async def _write(self, uuid: str, data: bytes) -> None:
        _LOGGER.debug("BLE write %s -> %s", uuid, data.hex())
        await self._client.write_gatt_char(uuid, data, response=False)

    async def _wait_data(self, uuid: str, expected: bytes) -> None:
        await self._inbox.wait_for(
            lambda received_uuid, data: received_uuid == uuid and data == expected,
            self._timeout,
        )

    @staticmethod
    def _parcels(payload: bytes, chunk_size: int = AUTH_CHUNK_SIZE) -> tuple[bytes, ...]:
        if not payload:
            raise ValueError("A framed transfer payload must not be empty")
        return tuple(
            struct.pack("<H", index) + payload[offset : offset + chunk_size]
            for index, offset in enumerate(range(0, len(payload), chunk_size), start=1)
        )

    async def _send_auth_payload(self, kind: int, payload: bytes) -> None:
        parcels = self._parcels(payload)
        await self._write(UUID_AUTH, bytes((0, 0, 0, kind)) + struct.pack("<H", len(parcels)))
        await self._wait_data(UUID_AUTH, RECEIVE_READY)
        for parcel in parcels:
            await self._write(UUID_AUTH, parcel)
        await self._wait_data(UUID_AUTH, RECEIVE_OK)

    async def _receive_auth_payload(self, kind: int, expected_length: int) -> bytes:
        _, frame = await self._inbox.wait_for(
            lambda uuid, data: (
                uuid == UUID_AUTH
                and len(data) >= 4
                and data[0:2] == bytes(2)
                and data[3] == kind
                and data[2] in (0, 2)
            ),
            self._timeout,
        )
        if frame[2] == 2:
            payload = frame[4:]
            await self._write(UUID_AUTH, ACK_DATA)
        else:
            if len(frame) != 6:
                raise MiotBleError("Authentication transfer header is malformed")
            parcel_count = struct.unpack("<H", frame[4:6])[0]
            if parcel_count == 0:
                raise MiotBleError("Authentication transfer contains no parcels")
            await self._write(UUID_AUTH, RECEIVE_READY)
            chunks: list[bytes] = []
            for parcel_number in range(1, parcel_count + 1):
                _, parcel = await self._inbox.wait_for(
                    lambda uuid, data, number=parcel_number: (
                        uuid == UUID_AUTH
                        and len(data) >= 2
                        and data[0:2] == struct.pack("<H", number)
                    ),
                    self._timeout,
                )
                chunks.append(parcel[2:])
            payload = b"".join(chunks)
            await self._write(UUID_AUTH, RECEIVE_OK)
        if len(payload) != expected_length:
            raise MiotBleError(
                f"Authentication payload has {len(payload)} bytes, expected {expected_length}"
            )
        return payload

    async def login(self, *, open_property_session: bool = True) -> None:
        """Authenticate with a device that was previously bound in Mi Home."""
        if not self._started:
            raise MiotBleError("Notification subscriptions must be started before login")
        app_random = self._random_bytes(16)
        if len(app_random) != 16:
            raise ValueError("Random source must return exactly 16 bytes")

        await self._write(UUID_UPNP, CMD_LOGIN)
        await self._send_auth_payload(AUTH_SEND_KEY, app_random)
        device_random = await self._receive_auth_payload(AUTH_RESPONSE_KEY, 16)
        device_proof = await self._receive_auth_payload(AUTH_RESPONSE_INFO, 32)

        keys = SessionKeys.derive(self._token, app_random, device_random)
        if not keys.verify_device_proof(device_proof, app_random, device_random):
            raise AuthenticationError("Device proof does not match the configured token")
        await self._send_auth_payload(
            AUTH_SEND_INFO,
            keys.app_proof(app_random, device_random),
        )
        _, confirmation = await self._inbox.wait_for(
            lambda uuid, data: (
                uuid == UUID_UPNP and (data in (CONFIRM_LOGIN, DENY_LOGIN) or data[:1] == b"\xe0")
            ),
            self._timeout,
        )
        if confirmation != CONFIRM_LOGIN:
            raise AuthenticationError(f"Device rejected login: {confirmation.hex()}")
        self._keys = keys
        self._tx_counter = -1
        if open_property_session:
            await self.open_property_session()

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id + 1) & 0xFF
        return self._transaction_id

    def _encrypt(self, plaintext: bytes) -> bytes:
        if self._keys is None:
            raise AuthenticationError("The BLE session is not authenticated")
        self._tx_counter += 1
        if self._tx_counter > 0xFFFF:
            raise MiotBleError("The BLE session transmit counter is exhausted")
        ciphertext = self._keys.encrypt_from_app(self._tx_counter, plaintext)
        return struct.pack("<H", self._tx_counter) + ciphertext

    async def _send_request(self, plaintext: bytes) -> None:
        payload = self._encrypt(plaintext)
        parcels = self._parcels(payload)
        await self._write(UUID_MIOT_WRITE, bytes(4) + struct.pack("<H", len(parcels)))
        await self._wait_data(UUID_MIOT_WRITE, RECEIVE_READY)
        for parcel in parcels:
            await self._write(UUID_MIOT_WRITE, parcel)
        await self._wait_data(UUID_MIOT_WRITE, RECEIVE_OK)

    async def _receive_encrypted(self, timeout: float | None = None) -> bytes:
        if self._keys is None:
            raise AuthenticationError("The BLE session is not authenticated")
        _, frame = await self._inbox.wait_for(
            lambda uuid, data: (
                uuid == UUID_MIOT_NOTIFY
                and len(data) >= 6
                and data[0:2] == bytes(2)
                and data[2:4] in (bytes(2), b"\x02\x00")
            ),
            timeout or self._timeout,
        )
        if frame[2:4] == b"\x02\x00":
            encrypted = frame[4:]
            await self._write(UUID_MIOT_NOTIFY, ACK_DATA)
        else:
            parcel_count = struct.unpack("<H", frame[4:6])[0]
            if parcel_count == 0:
                raise MiotBleError("Encrypted transfer contains no parcels")
            await self._write(UUID_MIOT_NOTIFY, RECEIVE_READY)
            chunks: list[bytes] = []
            for parcel_number in range(1, parcel_count + 1):
                _, parcel = await self._inbox.wait_for(
                    lambda uuid, data, number=parcel_number: (
                        uuid == UUID_MIOT_NOTIFY
                        and len(data) >= 2
                        and data[0:2] == struct.pack("<H", number)
                    ),
                    timeout or self._timeout,
                )
                chunks.append(parcel[2:])
            encrypted = b"".join(chunks)
            await self._write(UUID_MIOT_NOTIFY, RECEIVE_OK)
        if len(encrypted) < 7:
            raise MiotBleError("Encrypted MIoT response is truncated")
        counter = struct.unpack("<H", encrypted[:2])[0]
        return self._keys.decrypt_from_device(counter, encrypted[2:])

    async def open_property_session(self) -> None:
        """Open the encrypted MIoT property channel."""
        async with self._command_lock:
            payload = bytes((5, 0x20, self._next_transaction_id(), 0, Operation.OPEN))
            await self._send_request(payload)
            try:
                response = await self._receive_encrypted(timeout=min(1.0, self._timeout))
            except ResponseTimeoutError:
                return
            if len(response) < 5 or response[4] != Operation.OPEN:
                raise MiotBleError("Unexpected property-session response")

    async def read_properties(
        self,
        properties: tuple[PropertySpec, ...],
    ) -> dict[tuple[int, int], PropertyValue]:
        """Subscribe to properties and return their initial values."""
        if self._receiver_task is not None:
            raise MiotBleError("The property receiver already owns the notification stream")
        expected = {prop.key for prop in properties}
        if not expected:
            return {}
        result: dict[tuple[int, int], PropertyValue] = {}
        async with self._command_lock:
            request = build_subscribe(self._next_transaction_id(), properties)
            await self._send_request(request)
            while not expected.issubset(result):
                response = await self._receive_encrypted()
                try:
                    values = parse_properties(response)
                except ValueError as error:
                    _LOGGER.debug(
                        "Ignoring MIoT payload while waiting for initial properties: %s (%s)",
                        response.hex(),
                        error,
                    )
                    continue
                for value in values:
                    result[value.key] = value
        return result

    async def subscribe_properties(
        self,
        properties: tuple[PropertySpec, ...],
        callback: Callable[[tuple[PropertyValue, ...]], None],
    ) -> dict[tuple[int, int], PropertyValue]:
        """Read initial values and continuously dispatch subsequent notifications."""
        if self._receiver_task is not None:
            raise MiotBleError("Property notification receiver is already running")
        self._property_callback = callback
        values = await self.read_properties(properties)
        self._record_properties(tuple(values.values()))
        self._receiver_error = None
        self._receiver_task = asyncio.create_task(
            self._receive_property_notifications(),
            name="miot-ble-property-notifications",
        )
        return values

    def _record_properties(self, values: tuple[PropertyValue, ...]) -> None:
        """Store property values and notify the integration callback."""
        if not values:
            return
        for value in values:
            self._property_values[value.key] = value
        if self._property_callback is not None:
            try:
                self._property_callback(values)
            except Exception:
                _LOGGER.exception("Xiaomi BLE property callback failed")

    async def _receive_property_notifications(self) -> None:
        """Continuously consume and dispatch encrypted property notifications."""
        try:
            while True:
                try:
                    payload = await self._receive_encrypted()
                except ResponseTimeoutError:
                    continue
                try:
                    values = parse_properties(payload)
                except ValueError:
                    _LOGGER.debug(
                        "Ignoring Xiaomi BLE payload without property values: %s",
                        payload.hex(),
                    )
                    continue
                self._record_properties(values)
                pending = self._pending_responses.get(payload[2])
                if pending is not None and not pending.done():
                    pending.set_result(values)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._receiver_error = error
            _LOGGER.debug("Xiaomi BLE property receiver stopped: %s", error)

    def raise_if_receiver_failed(self) -> None:
        """Raise the error that stopped the background property receiver."""
        if self._receiver_error is not None:
            raise MiotBleError("Property notification receiver stopped") from self._receiver_error
        if self._receiver_task is not None and self._receiver_task.done():
            raise MiotBleError("Property notification receiver stopped")

    def cancel_background_tasks(self) -> asyncio.Task[None] | None:
        """Cancel notification processing and fail outstanding commands."""
        task = self._receiver_task
        self._receiver_task = None
        self._property_callback = None
        if task is not None:
            task.cancel()
        for pending in self._pending_responses.values():
            if not pending.done():
                pending.set_exception(ConnectionError("Bluetooth session closed"))
        self._pending_responses.clear()
        return task

    async def async_close(self) -> None:
        """Stop background notification processing."""
        task = self.cancel_background_tasks()
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task

    async def set_property(self, prop: PropertySpec, value: int | bool) -> PropertyValue:
        """Write one property and wait for the device's change notification."""
        async with self._command_lock:
            self.raise_if_receiver_failed()
            transaction_id = self._next_transaction_id()
            request = build_set(transaction_id, prop, value)
            if self._receiver_task is not None:
                pending = asyncio.get_running_loop().create_future()
                self._pending_responses[transaction_id] = pending
                try:
                    await self._send_request(request)
                    changed_values = await asyncio.wait_for(pending, self._timeout)
                finally:
                    self._pending_responses.pop(transaction_id, None)
                for changed in changed_values:
                    if changed.key == prop.key:
                        return changed
                raise MiotBleError("Property write response omitted the changed property")
            await self._send_request(request)
            while True:
                response = await self._receive_encrypted()
                for changed in parse_properties(response):
                    if changed.key == prop.key:
                        return changed
