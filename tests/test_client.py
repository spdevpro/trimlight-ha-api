"""Tests for the Trimlight HTTP client."""

import asyncio
import inspect
from json import JSONDecodeError
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import ClientConnectionError, ClientResponse, ClientSession
from yarl import URL

from aiotrimlight import (
    TrimlightClient,
    TrimlightCommandError,
    TrimlightConnectionError,
    TrimlightHTTPError,
    TrimlightICType,
    TrimlightLightState,
    TrimlightProtocolError,
    TrimlightUnsupportedICError,
)

HOST = "192.0.2.10"


def make_response(
    payload: object | None = None,
    *,
    status: int = 200,
) -> Mock:
    """Create a mocked aiohttp response."""
    response = Mock(spec=ClientResponse)
    response.status = status
    response.json = AsyncMock(return_value={"code": 0} if payload is None else payload)
    return response


def static_record(
    *,
    zone_id: int = 255,
    brightness: int = 64,
    red: int = 1,
    green: int = 2,
    blue: int = 3,
    warm_white: int = 4,
    cold_white: int = 5,
) -> dict[str, Any]:
    """Return one static runtime record."""
    return {
        "zone_id": zone_id,
        "zone_on_off": 1,
        "output_mode": 1,
        "output_mode_desc": "static",
        "static_output": {
            "brightness": brightness,
            "red": red,
            "green": green,
            "blue": blue,
            "warm_white": warm_white,
            "cold_white": cold_white,
        },
    }


def runtime_response(
    *,
    device_state: int = 1,
    ic: int = 0,
    records: list[object] | None = None,
) -> Mock:
    """Return a successful runtime state response."""
    return make_response(
        {
            "code": 0,
            "msg": "success",
            "data": {
                "device_state": device_state,
                "device_state_desc": "on",
                "ic": ic,
                "records": [static_record()] if records is None else records,
            },
        }
    )


def make_client(*responses: Mock) -> tuple[TrimlightClient, AsyncMock]:
    """Create a client backed by a mocked session."""
    session = Mock(spec=ClientSession)
    post = AsyncMock(side_effect=responses)
    session.post = post
    return TrimlightClient(HOST, cast(ClientSession, session)), post


@pytest.mark.parametrize(
    ("device_state", "is_on"),
    [
        pytest.param(0, False, id="off"),
        pytest.param(1, True, id="on"),
        pytest.param(2, True, id="timer"),
    ],
)
async def test_get_light_state_reads_uniform_static_output(
    device_state: int, is_on: bool
) -> None:
    """Test parsing authoritative whole-installation static state."""
    client, post = make_client(runtime_response(device_state=device_state))

    state = await client.get_light_state()

    assert post.await_args is not None
    assert state == TrimlightLightState(
        is_on=is_on,
        brightness=64,
        red=1,
        green=2,
        blue=3,
        warm_white=4,
        cold_white=5,
    )
    assert post.await_args.kwargs["json"] == {
        "cmd": "get_runtime_state",
        "data": {"zone_id": 255},
    }


@pytest.mark.parametrize(
    "records",
    [
        pytest.param(
            [
                {
                    "zone_id": 255,
                    "zone_on_off": 1,
                    "output_mode": 0,
                    "output_mode_desc": "effect",
                    "effect": {},
                }
            ],
            id="effect",
        ),
        pytest.param(
            [
                {
                    "zone_id": 255,
                    "zone_on_off": 0,
                    "output_mode": 2,
                    "output_mode_desc": "none",
                }
            ],
            id="none",
        ),
        pytest.param(
            [static_record(zone_id=1), static_record(zone_id=2)],
            id="multiple-zones",
        ),
        pytest.param([], id="no-records"),
    ],
)
async def test_get_light_state_returns_unknown_color(
    records: list[object],
) -> None:
    """Test non-uniform runtime output has no aggregate static color."""
    client, _ = make_client(runtime_response(records=records))

    assert await client.get_light_state() == TrimlightLightState(is_on=True)


@pytest.mark.parametrize(
    ("raw_ic_type", "expected"),
    [
        pytest.param(0, TrimlightICType.RGB, id="rgb"),
        pytest.param(1, TrimlightICType.RGBW, id="rgbw"),
        pytest.param(2, TrimlightICType.RGBCW, id="rgbcw"),
    ],
)
async def test_get_device_info_uses_runtime_ic(
    raw_ic_type: int, expected: TrimlightICType
) -> None:
    """Test firmware metadata and runtime IC capability parsing."""
    client, post = make_client(
        make_response(
            {
                "code": 0,
                "data": {
                    "sys_info": {"firmware_version": "1.2.3"},
                    "light_config": {"ic": 99},
                },
            }
        ),
        runtime_response(ic=raw_ic_type),
    )

    info = await client.get_device_info()

    assert info.firmware_version == "1.2.3"
    assert info.ic_type is expected
    assert post.await_args_list[0].kwargs["json"]["cmd"] == "get_device_data"
    assert isinstance(post.await_args_list[0].kwargs["json"]["data"]["timestamp"], int)
    assert post.await_args_list[1].kwargs["json"] == {
        "cmd": "get_runtime_state",
        "data": {"zone_id": 255},
    }


async def test_get_device_info_rejects_unknown_runtime_ic() -> None:
    """Test rejecting an unsupported light IC reported at runtime."""
    client, _ = make_client(
        make_response({"code": 0, "data": {"sys_info": {"firmware_version": "1.2.3"}}}),
        runtime_response(ic=3),
    )

    with pytest.raises(TrimlightUnsupportedICError) as error:
        await client.get_device_info()

    assert error.value.ic_type == 3


async def test_set_light_state_uses_static_output_switch_and_readback() -> None:
    """Test the exact whole-installation static ON request sequence."""
    client, post = make_client(
        make_response(),
        make_response(),
        runtime_response(),
    )

    state = await client.set_light_state(
        on=True,
        brightness=64,
        red=1,
        green=2,
        blue=3,
        warm_white=4,
        cold_white=5,
    )

    assert state == TrimlightLightState(
        is_on=True,
        brightness=64,
        red=1,
        green=2,
        blue=3,
        warm_white=4,
        cold_white=5,
    )
    assert [call.kwargs["json"] for call in post.await_args_list] == [
        {
            "cmd": "set_static_output",
            "data": {
                "zone_id": 255,
                "brightness": 64,
                "red": 1,
                "green": 2,
                "blue": 3,
                "warm_white": 4,
                "cold_white": 5,
            },
        },
        {"cmd": "switch", "data": {"state": 1}},
        {"cmd": "get_runtime_state", "data": {"zone_id": 255}},
    ]
    request_url = post.await_args_list[0].args[0]
    assert request_url.host == HOST
    assert request_url.port == 80
    assert request_url.path == "/api/light"
    assert post.await_args_list[0].kwargs["timeout"].total == 10.0


@pytest.mark.parametrize(
    ("on", "device_state", "expected_state"),
    [
        pytest.param(True, 1, 1, id="on"),
        pytest.param(False, 0, 0, id="off"),
    ],
)
async def test_set_light_state_switch_only(
    on: bool, device_state: int, expected_state: int
) -> None:
    """Test ON/OFF does not replace the remembered static output."""
    client, post = make_client(
        make_response(),
        runtime_response(device_state=device_state),
    )

    await client.set_light_state(on=on)

    assert [call.kwargs["json"] for call in post.await_args_list] == [
        {"cmd": "switch", "data": {"state": expected_state}},
        {"cmd": "get_runtime_state", "data": {"zone_id": 255}},
    ]


async def test_partial_static_update_uses_last_reported_output() -> None:
    """Test partial changes preserve the most recent uniform static values."""
    client, post = make_client(
        runtime_response(
            records=[
                static_record(
                    brightness=10,
                    red=11,
                    green=12,
                    blue=13,
                    warm_white=14,
                    cold_white=15,
                )
            ]
        ),
        make_response(),
        runtime_response(
            records=[
                static_record(
                    brightness=10,
                    red=20,
                    green=12,
                    blue=13,
                    warm_white=14,
                    cold_white=15,
                )
            ]
        ),
    )
    await client.get_light_state()

    await client.set_light_state(red=20)

    assert post.await_args_list[1].kwargs["json"] == {
        "cmd": "set_static_output",
        "data": {
            "zone_id": 255,
            "brightness": 10,
            "red": 20,
            "green": 12,
            "blue": 13,
            "warm_white": 14,
            "cold_white": 15,
        },
    }


async def test_partial_static_update_uses_default_white() -> None:
    """Test the first partial change uses the documented static defaults."""
    client, post = make_client(make_response(), runtime_response())

    await client.set_light_state(green=20)

    assert post.await_args_list[0].kwargs["json"]["data"] == {
        "zone_id": 255,
        "brightness": 255,
        "red": 255,
        "green": 20,
        "blue": 255,
        "warm_white": 0,
        "cold_white": 0,
    }


async def test_failed_static_command_does_not_update_cache() -> None:
    """Test a rejected static output does not replace cached values."""
    client, post = make_client(
        make_response({"code": 201, "msg": "internal error"}),
        make_response(),
        runtime_response(),
    )

    with pytest.raises(TrimlightCommandError):
        await client.set_light_state(red=10)

    await client.set_light_state(green=20)

    assert post.await_args_list[1].kwargs["json"]["data"]["red"] == 255


async def test_static_cache_survives_later_switch_failure() -> None:
    """Test successful static output remains cached if switch fails afterward."""
    client, post = make_client(
        make_response(),
        make_response({"code": 201, "msg": "internal error"}),
        make_response(),
        runtime_response(),
    )

    with pytest.raises(TrimlightCommandError):
        await client.set_light_state(on=True, red=10)

    await client.set_light_state(green=20)

    assert post.await_args_list[2].kwargs["json"]["data"]["red"] == 10


async def test_state_updates_are_serialized() -> None:
    """Test concurrent partial updates merge in submission order."""
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    requests: list[dict[str, Any]] = []

    async def post_side_effect(_url: URL, **kwargs: object) -> Mock:
        request_json = cast(dict[str, Any], kwargs["json"])
        requests.append(request_json)
        if len(requests) == 1:
            first_entered.set()
            await release_first.wait()
        if request_json["cmd"] == "get_runtime_state":
            red = 10
            green = 20 if len(requests) == 4 else 255
            return runtime_response(records=[static_record(red=red, green=green)])
        return make_response()

    session = Mock(spec=ClientSession)
    session.post = AsyncMock(side_effect=post_side_effect)
    client = TrimlightClient(HOST, cast(ClientSession, session))

    first = asyncio.create_task(client.set_light_state(red=10))
    await first_entered.wait()
    second = asyncio.create_task(client.set_light_state(green=20))
    release_first.set()
    await asyncio.gather(first, second)

    assert requests[2]["data"] == {
        "zone_id": 255,
        "brightness": 64,
        "red": 10,
        "green": 20,
        "blue": 3,
        "warm_white": 4,
        "cold_white": 5,
    }


@pytest.mark.parametrize(
    ("value", "exception"),
    [
        pytest.param(-1, ValueError, id="below-range"),
        pytest.param(256, ValueError, id="above-range"),
        pytest.param(True, TypeError, id="boolean"),
        pytest.param(1.5, TypeError, id="not-integer"),
    ],
)
async def test_set_light_state_validates_channels(
    value: Any, exception: type[Exception]
) -> None:
    """Test light channels must be integers in the byte range."""
    client, post = make_client()

    with pytest.raises(exception):
        await client.set_light_state(brightness=value)

    post.assert_not_awaited()


async def test_set_light_state_validates_on() -> None:
    """Test the on value must be boolean."""
    client, post = make_client()

    with pytest.raises(TypeError, match="on must be a boolean"):
        await client.set_light_state(on=cast(bool, 1))

    post.assert_not_awaited()


@pytest.mark.parametrize(
    ("side_effect", "message"),
    [
        pytest.param(ClientConnectionError(), "request failed", id="connection"),
        pytest.param(TimeoutError(), "request timed out", id="timeout"),
    ],
)
async def test_connection_errors(side_effect: Exception, message: str) -> None:
    """Test transport failures become library connection errors."""
    session = Mock(spec=ClientSession)
    session.post = AsyncMock(side_effect=side_effect)
    client = TrimlightClient(HOST, cast(ClientSession, session))

    with pytest.raises(TrimlightConnectionError, match=message):
        await client.get_light_state()


async def test_http_error() -> None:
    """Test a non-success HTTP response."""
    response = make_response(status=503)
    client, _ = make_client(response)

    with pytest.raises(TrimlightHTTPError) as error:
        await client.get_light_state()

    assert error.value.status == 503
    response.release.assert_called_once_with()


async def test_invalid_json() -> None:
    """Test rejecting a response that is not valid JSON."""
    response = make_response()
    response.json.side_effect = JSONDecodeError("invalid", "", 0)
    client, _ = make_client(response)

    with pytest.raises(TrimlightProtocolError, match="response is not valid JSON"):
        await client.get_light_state()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param([], "response must be an object", id="not-object"),
        pytest.param({}, "response code must be an integer", id="missing-code"),
        pytest.param(
            {"code": True},
            "response code must be an integer",
            id="boolean-code",
        ),
    ],
)
async def test_response_shape_errors(payload: object, message: str) -> None:
    """Test rejecting malformed response envelopes."""
    client, _ = make_client(make_response(payload))

    with pytest.raises(TrimlightProtocolError, match=message):
        await client.get_light_state()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(
            {"code": 0},
            "response data must be an object",
            id="missing-data",
        ),
        pytest.param(
            {"code": 0, "data": {"sys_info": []}},
            "sys_info must be an object",
            id="invalid-sys-info",
        ),
        pytest.param(
            {"code": 0, "data": {"sys_info": {"firmware_version": 123}}},
            "firmware_version must be a string",
            id="invalid-firmware",
        ),
    ],
)
async def test_device_info_shape_errors(
    payload: dict[str, object], message: str
) -> None:
    """Test rejecting malformed device information."""
    client, _ = make_client(make_response(payload))

    with pytest.raises(TrimlightProtocolError, match=message):
        await client.get_device_info()


@pytest.mark.parametrize(
    ("data", "message"),
    [
        pytest.param(None, "response data must be an object", id="missing-data"),
        pytest.param({}, "device_state must be an integer", id="missing-state"),
        pytest.param(
            {"device_state": 3, "ic": 0, "records": []},
            "device_state must be 0, 1, or 2",
            id="invalid-state",
        ),
        pytest.param(
            {"device_state": 1, "ic": 0, "records": {}},
            "records must be an array",
            id="invalid-records",
        ),
        pytest.param(
            {"device_state": 1, "ic": 0, "records": [None]},
            r"records\[0\] must be an object",
            id="invalid-record",
        ),
        pytest.param(
            {
                "device_state": 1,
                "ic": 0,
                "records": [{"zone_id": 8, "zone_on_off": 1, "output_mode": 2}],
            },
            r"zone_id is not supported",
            id="invalid-response-zone",
        ),
        pytest.param(
            {
                "device_state": 1,
                "ic": 0,
                "records": [{"zone_id": 255, "zone_on_off": 2, "output_mode": 2}],
            },
            r"zone_on_off must be 0 or 1",
            id="invalid-zone-state",
        ),
        pytest.param(
            {
                "device_state": 1,
                "ic": 0,
                "records": [{"zone_id": 255, "zone_on_off": 1, "output_mode": 3}],
            },
            r"output_mode must be 0, 1, or 2",
            id="invalid-output-mode",
        ),
        pytest.param(
            {
                "device_state": 1,
                "ic": 0,
                "records": [{"zone_id": 255, "zone_on_off": 1, "output_mode": 1}],
            },
            r"static_output must be an object",
            id="missing-static-output",
        ),
        pytest.param(
            {
                "device_state": 1,
                "ic": 0,
                "records": [
                    {
                        "zone_id": 255,
                        "zone_on_off": 1,
                        "output_mode": 1,
                        "static_output": {
                            **static_record()["static_output"],
                            "red": 256,
                        },
                    }
                ],
            },
            r"static_output.red must be between 0 and 255",
            id="invalid-channel",
        ),
    ],
)
async def test_runtime_state_shape_errors(
    data: dict[str, object] | None, message: str
) -> None:
    """Test rejecting malformed runtime state data."""
    client, _ = make_client(make_response({"code": 0, "data": data}))

    with pytest.raises(TrimlightProtocolError, match=message):
        await client.get_light_state()


def test_client_constructor_validation() -> None:
    """Test constructor arguments are validated."""
    session = cast(ClientSession, Mock(spec=ClientSession))

    with pytest.raises(ValueError, match="host must not be empty"):
        TrimlightClient("", session)
    with pytest.raises(ValueError, match="timeout must be greater than zero"):
        TrimlightClient(HOST, session, timeout=0)


def test_client_builds_ipv6_url() -> None:
    """Test the fixed HTTP endpoint supports an IPv6 host."""
    client = TrimlightClient(
        "2001:db8::1",
        cast(ClientSession, Mock(spec=ClientSession)),
    )

    assert client.host == "2001:db8::1"
    assert str(client.url) == "http://[2001:db8::1]/api/light"


def test_client_no_longer_uses_preview_effect() -> None:
    """Test the v1 preview adapter is absent from the client."""
    assert "preview_effect" not in inspect.getsource(TrimlightClient)
