"""Trimlight V3 HTTP client."""

import asyncio
import json
import time
from dataclasses import dataclass, replace
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout
from yarl import URL

from .exceptions import (
    TrimlightCommandError,
    TrimlightConnectionError,
    TrimlightHTTPError,
    TrimlightProtocolError,
    TrimlightUnsupportedICError,
)
from .models import TrimlightDeviceInfo, TrimlightICType, TrimlightLightState

_HTTP_PORT = 80
_API_PATH = "/api/light"
_DEFAULT_TIMEOUT = 10.0
_ALL_ZONES = 255


@dataclass(frozen=True, slots=True)
class _StaticOutput:
    """Static output values used to complete partial updates."""

    brightness: int = 255
    red: int = 255
    green: int = 255
    blue: int = 255
    warm_white: int = 0
    cold_white: int = 0


@dataclass(frozen=True, slots=True)
class _RuntimeState:
    """Parsed runtime response including device capability."""

    light_state: TrimlightLightState
    ic_type: TrimlightICType


class TrimlightClient:
    """Asynchronous client for a Trimlight V3 controller."""

    def __init__(
        self,
        host: str,
        session: ClientSession,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client."""
        if not host:
            msg = "host must not be empty"
            raise ValueError(msg)
        if timeout <= 0:
            msg = "timeout must be greater than zero"
            raise ValueError(msg)

        self._host = host
        self._session = session
        self._timeout = ClientTimeout(total=timeout)
        self._url = URL.build(
            scheme="http",
            host=host,
            port=_HTTP_PORT,
            path=_API_PATH,
        )
        self._static_output = _StaticOutput()
        self._state_lock = asyncio.Lock()

    @property
    def host(self) -> str:
        """Return the controller host."""
        return self._host

    @property
    def url(self) -> URL:
        """Return the fixed HTTP API URL."""
        return self._url

    async def get_device_info(self) -> TrimlightDeviceInfo:
        """Return device metadata and light capability."""
        response = await self._request(
            "get_device_data",
            {"timestamp": int(time.time())},
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise TrimlightProtocolError("response data must be an object")

        sys_info = data.get("sys_info")
        if not isinstance(sys_info, dict):
            raise TrimlightProtocolError("sys_info must be an object")
        firmware_version = sys_info.get("firmware_version")
        if firmware_version is not None and not isinstance(firmware_version, str):
            raise TrimlightProtocolError("firmware_version must be a string")

        async with self._state_lock:
            runtime_state = await self._get_runtime_state()

        return TrimlightDeviceInfo(firmware_version, runtime_state.ic_type)

    async def get_light_state(self) -> TrimlightLightState:
        """Return the whole-installation state reported by the controller."""
        async with self._state_lock:
            return (await self._get_runtime_state()).light_state

    async def set_light_state(
        self,
        *,
        on: bool | None = None,
        brightness: int | None = None,
        red: int | None = None,
        green: int | None = None,
        blue: int | None = None,
        warm_white: int | None = None,
        cold_white: int | None = None,
    ) -> TrimlightLightState:
        """Set whole-installation static output and switch state."""
        self._validate_on(on)
        values = {
            "brightness": brightness,
            "red": red,
            "green": green,
            "blue": blue,
            "warm_white": warm_white,
            "cold_white": cold_white,
        }
        for name, value in values.items():
            self._validate_channel(name, value)

        async with self._state_lock:
            changes = {
                name: value for name, value in values.items() if value is not None
            }
            static_output = replace(self._static_output, **changes)

            if on is False:
                await self._request("switch", {"state": 0})

            if changes:
                await self._request(
                    "set_static_output",
                    self._static_output_data(static_output),
                )
                self._static_output = static_output

            if on is True:
                await self._request("switch", {"state": 1})

            return (await self._get_runtime_state()).light_state

    @staticmethod
    def _validate_on(value: object | None) -> None:
        """Validate a switch state value."""
        if value is not None and not isinstance(value, bool):
            msg = "on must be a boolean"
            raise TypeError(msg)

    @staticmethod
    def _validate_channel(name: str, value: int | None) -> None:
        """Validate a light channel value."""
        if value is None:
            return
        if not isinstance(value, int) or isinstance(value, bool):
            msg = f"{name} must be an integer"
            raise TypeError(msg)
        if not 0 <= value <= 255:
            msg = f"{name} must be between 0 and 255"
            raise ValueError(msg)

    @staticmethod
    def _static_output_data(static_output: _StaticOutput) -> dict[str, int]:
        """Build a whole-installation static output request."""
        return {
            "zone_id": _ALL_ZONES,
            "brightness": static_output.brightness,
            "red": static_output.red,
            "green": static_output.green,
            "blue": static_output.blue,
            "warm_white": static_output.warm_white,
            "cold_white": static_output.cold_white,
        }

    async def _get_runtime_state(self) -> _RuntimeState:
        """Fetch and parse the current whole-installation runtime state."""
        response = await self._request(
            "get_runtime_state",
            {"zone_id": _ALL_ZONES},
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise TrimlightProtocolError("response data must be an object")

        device_state = self._parse_integer(data, "device_state")
        if device_state not in (0, 1, 2):
            raise TrimlightProtocolError("device_state must be 0, 1, or 2")

        raw_ic_type = self._parse_integer(data, "ic")
        try:
            ic_type = TrimlightICType(raw_ic_type)
        except ValueError as err:
            raise TrimlightUnsupportedICError(raw_ic_type) from err

        records = data.get("records")
        if not isinstance(records, list):
            raise TrimlightProtocolError("records must be an array")

        static_outputs: list[_StaticOutput | None] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise TrimlightProtocolError(f"records[{index}] must be an object")
            zone_id = self._parse_integer(record, "zone_id")
            if zone_id not in (1, 2, 3, 4, _ALL_ZONES):
                raise TrimlightProtocolError(
                    f"records[{index}].zone_id is not supported"
                )
            zone_on_off = self._parse_integer(record, "zone_on_off")
            if zone_on_off not in (0, 1):
                raise TrimlightProtocolError(
                    f"records[{index}].zone_on_off must be 0 or 1"
                )
            output_mode = self._parse_integer(record, "output_mode")
            if output_mode not in (0, 1, 2):
                raise TrimlightProtocolError(
                    f"records[{index}].output_mode must be 0, 1, or 2"
                )
            static_outputs.append(
                self._parse_static_output(record, index) if output_mode == 1 else None
            )

        state = TrimlightLightState(is_on=device_state != 0)
        if len(static_outputs) == 1 and (static_output := static_outputs[0]):
            self._static_output = static_output
            state = TrimlightLightState(
                is_on=device_state != 0,
                brightness=static_output.brightness,
                red=static_output.red,
                green=static_output.green,
                blue=static_output.blue,
                warm_white=static_output.warm_white,
                cold_white=static_output.cold_white,
            )

        return _RuntimeState(state, ic_type)

    @classmethod
    def _parse_static_output(
        cls,
        record: dict[str, Any],
        index: int,
    ) -> _StaticOutput:
        """Parse one static output record."""
        static_output = record.get("static_output")
        if not isinstance(static_output, dict):
            raise TrimlightProtocolError(
                f"records[{index}].static_output must be an object"
            )
        values = {
            name: cls._parse_channel(static_output, name, index)
            for name in (
                "brightness",
                "red",
                "green",
                "blue",
                "warm_white",
                "cold_white",
            )
        }
        return _StaticOutput(**values)

    @staticmethod
    def _parse_channel(data: dict[str, Any], name: str, index: int) -> int:
        """Parse one byte channel from a runtime record."""
        value = TrimlightClient._parse_integer(data, name)
        if not 0 <= value <= 255:
            raise TrimlightProtocolError(
                f"records[{index}].static_output.{name} must be between 0 and 255"
            )
        return value

    @staticmethod
    def _parse_integer(data: dict[str, Any], name: str) -> int:
        """Parse a required integer response field."""
        value = data.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            raise TrimlightProtocolError(f"{name} must be an integer")
        return value

    async def _request(self, command: str, data: dict[str, Any]) -> dict[str, Any]:
        """Send one JSON command to the controller."""
        payload = {"cmd": command, "data": data}
        try:
            response = await self._session.post(
                self._url,
                json=payload,
                timeout=self._timeout,
            )
        except TimeoutError as err:
            raise TrimlightConnectionError("request timed out") from err
        except ClientError as err:
            raise TrimlightConnectionError("request failed") from err

        try:
            self._raise_for_http_status(response)
            try:
                result = await response.json(content_type=None)
            except (json.JSONDecodeError, UnicodeDecodeError) as err:
                raise TrimlightProtocolError("response is not valid JSON") from err
        finally:
            response.release()

        if not isinstance(result, dict):
            raise TrimlightProtocolError("response must be an object")
        code = result.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise TrimlightProtocolError("response code must be an integer")
        if code != 0:
            message = result.get("msg")
            raise TrimlightCommandError(
                code,
                message if isinstance(message, str) else None,
            )
        return result

    @staticmethod
    def _raise_for_http_status(response: ClientResponse) -> None:
        """Raise a client error for a non-success HTTP status."""
        if response.status < 200 or response.status >= 300:
            raise TrimlightHTTPError(response.status)
