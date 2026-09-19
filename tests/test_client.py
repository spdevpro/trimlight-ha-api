"""Tests for the Trimlight HTTP client."""

import asyncio
from dataclasses import replace
from json import JSONDecodeError
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from aiohttp import (
    ClientConnectionError,
    ClientPayloadError,
    ClientResponse,
    ClientSession,
    web,
)
from aiohttp.test_utils import TestServer
from yarl import URL

from aiotrimlight import (
    TrimlightClient,
    TrimlightCommandError,
    TrimlightConnectionError,
    TrimlightHTTPError,
    TrimlightICType,
    TrimlightLightState,
    TrimlightOutputMode,
    TrimlightProtocolError,
    TrimlightZoneState,
)

HOST = "192.0.2.10"


def make_response(
    payload: object | None = None,
    *,
    status: int = 200,
) -> Mock:
    """Create a mocked aiohttp response."""
    response = MagicMock(spec=ClientResponse)
    response.__aenter__.return_value = response
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


def blocked_client(
    first_response: Mock | None = None,
) -> tuple[TrimlightClient, AsyncMock, asyncio.Event, asyncio.Event]:
    """Block the first HTTP response while concurrent calls enter the client."""
    response = make_response() if first_response is None else first_response
    payload = response.json.return_value
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked(*, content_type: str | None) -> object:
        entered.set()
        await release.wait()
        return payload

    response.json.side_effect = blocked
    client, post = make_client(response, *(make_response() for _ in range(6)))
    return client, post, entered, release


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
        zones=(TrimlightZoneState(255, True, TrimlightOutputMode.STATIC),),
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

    state = await client.get_light_state()
    assert replace(state, zones=()) == TrimlightLightState(is_on=True)
    assert len(state.zones) == len(records)


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
    """Test rejecting an invalid light IC reported at runtime."""
    client, _ = make_client(
        make_response({"code": 0, "data": {"sys_info": {"firmware_version": "1.2.3"}}}),
        runtime_response(ic=3),
    )

    with pytest.raises(TrimlightProtocolError, match="ic must be 0, 1, or 2"):
        await client.get_device_info()


async def test_empty_state_command_makes_no_requests() -> None:
    """An empty command does not trigger an implicit state query."""
    client, post = make_client()
    await client.set_light_state()
    post.assert_not_awaited()


async def test_cancelled_state_query_releases_lock() -> None:
    """Cancelling an explicit query does not strand subsequent network calls."""
    client, post = make_client()
    entered = asyncio.Event()

    async def blocked(_url: URL, **kwargs: object) -> Mock:
        entered.set()
        await asyncio.Event().wait()
        return runtime_response()

    post.side_effect = blocked
    task = asyncio.create_task(client.get_light_state())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    post.side_effect = [runtime_response()]
    assert (await client.get_light_state()).is_on
    assert post.await_count == 2


async def test_set_light_state_uses_static_output_and_switch_without_query() -> None:
    """Test the exact whole-installation static ON request sequence."""
    client, post = make_client(
        make_response(),
        make_response(),
    )

    await client.set_light_state(
        on=True,
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
        return make_response()

    session = Mock(spec=ClientSession)
    session.post = AsyncMock(side_effect=post_side_effect)
    client = TrimlightClient(HOST, cast(ClientSession, session))

    first = asyncio.create_task(client.set_light_state(red=10))
    await first_entered.wait()
    second = asyncio.create_task(client.set_light_state(green=20))
    release_first.set()
    await asyncio.gather(first, second)

    assert len(requests) == 2
    assert requests[1]["data"] == {
        "zone_id": 255,
        "brightness": 255,
        "red": 10,
        "green": 20,
        "blue": 255,
        "warm_white": 0,
        "cold_white": 0,
    }


async def test_pending_updates_merge_fields_and_send_only_latest() -> None:
    """A running write completes, and only the merged latest target is sent."""
    client, post, entered, release = blocked_client()
    first = asyncio.create_task(client.set_light_state(on=True, red=10))
    await entered.wait()
    second = asyncio.create_task(
        client.set_light_state(on=False, brightness=0, green=20, warm_white=40)
    )
    await asyncio.sleep(0)
    third = asyncio.create_task(client.set_light_state(blue=30, cold_white=50))
    await asyncio.sleep(0)
    latest = asyncio.create_task(client.set_light_state(on=True, green=0))
    await asyncio.sleep(0)
    await client.set_light_state()
    with pytest.raises(ValueError, match="red"):
        await client.set_light_state(red=-1)
    assert post.await_count == 1
    release.set()
    await asyncio.gather(first, second, third, latest)

    assert [call.kwargs["json"] for call in post.await_args_list] == [
        {
            "cmd": "set_static_output",
            "data": {
                "zone_id": 255,
                "brightness": 255,
                "red": 10,
                "green": 255,
                "blue": 255,
                "warm_white": 0,
                "cold_white": 0,
            },
        },
        {"cmd": "switch", "data": {"state": 1}},
        {
            "cmd": "set_static_output",
            "data": {
                "zone_id": 255,
                "brightness": 0,
                "red": 10,
                "green": 0,
                "blue": 30,
                "warm_white": 40,
                "cold_white": 50,
            },
        },
        {"cmd": "switch", "data": {"state": 1}},
    ]


async def test_pending_power_uses_latest_explicit_value() -> None:
    """Superseded power-only calls do not generate intermediate switches."""
    client, post, entered, release = blocked_client()
    first = asyncio.create_task(client.set_light_state(red=10))
    await entered.wait()
    on = asyncio.create_task(client.set_light_state(on=True))
    await asyncio.sleep(0)
    off = asyncio.create_task(client.set_light_state(on=False))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, on, off)
    assert post.await_count == 2
    assert post.await_args_list[1].kwargs["json"] == {
        "cmd": "switch",
        "data": {"state": 0},
    }


async def test_pending_update_uses_completed_readback() -> None:
    """A query remains real I/O and seeds omitted channels before the write."""
    client, post, entered, release = blocked_client(runtime_response())
    query = asyncio.create_task(client.get_light_state())
    await entered.wait()
    brightness = asyncio.create_task(client.set_light_state(brightness=0))
    await asyncio.sleep(0)
    color = asyncio.create_task(client.set_light_state(red=20))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(query, brightness, color)
    assert post.await_count == 2
    assert post.await_args_list[1].kwargs["json"]["data"] == {
        "zone_id": 255,
        "brightness": 0,
        "red": 20,
        "green": 2,
        "blue": 3,
        "warm_white": 4,
        "cold_white": 5,
    }


async def test_failed_inflight_write_does_not_strand_pending_target() -> None:
    """A failed active write releases the lock without caching its values."""
    client, post, entered, release = blocked_client(make_response({"code": 201}))
    first = asyncio.create_task(client.set_light_state(red=10))
    await entered.wait()
    latest = asyncio.create_task(client.set_light_state(green=20))
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(TrimlightCommandError):
        await first
    await latest
    assert post.await_count == 2
    assert post.await_args_list[1].kwargs["json"]["data"]["red"] == 255
    assert post.await_args_list[1].kwargs["json"]["data"]["green"] == 20


async def test_failed_pending_batch_is_not_replayed() -> None:
    """A failed merged write neither changes the cache nor leaks into later calls."""
    response = make_response()
    client, post, entered, release = blocked_client(response)
    post.side_effect = [response, make_response({"code": 201}), make_response()]
    first = asyncio.create_task(client.set_light_state(red=10))
    await entered.wait()
    older = asyncio.create_task(client.set_light_state(brightness=0))
    await asyncio.sleep(0)
    latest = asyncio.create_task(client.set_light_state(green=20))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, older)
    with pytest.raises(TrimlightCommandError):
        await latest
    assert post.await_count == 2
    await client.set_light_state(blue=30)
    assert post.await_args_list[2].kwargs["json"]["data"] == {
        "zone_id": 255,
        "brightness": 255,
        "red": 10,
        "green": 255,
        "blue": 30,
        "warm_white": 0,
        "cold_white": 0,
    }


async def test_cancel_latest_waiter_discards_unsent_batch() -> None:
    """Canceling the latest waiter never revives superseded writes or fields."""
    client, post, entered, release = blocked_client()
    first = asyncio.create_task(client.set_light_state(red=10))
    await entered.wait()
    older = asyncio.create_task(client.set_light_state(brightness=0))
    await asyncio.sleep(0)
    latest = asyncio.create_task(client.set_light_state(green=20))
    await asyncio.sleep(0)
    latest.cancel()
    with pytest.raises(asyncio.CancelledError):
        await latest
    release.set()
    await asyncio.gather(first, older)
    assert post.await_count == 1
    await client.set_light_state(blue=30)
    assert post.await_args_list[1].kwargs["json"]["data"] == {
        "zone_id": 255,
        "brightness": 255,
        "red": 10,
        "green": 255,
        "blue": 30,
        "warm_white": 0,
        "cold_white": 0,
    }


async def test_cancel_older_waiter_preserves_merged_latest_target() -> None:
    """Older cancellation does not clear the newer batch it contributed to."""
    client, post, entered, release = blocked_client()
    first = asyncio.create_task(client.set_light_state(red=10))
    await entered.wait()
    older = asyncio.create_task(client.set_light_state(brightness=0))
    await asyncio.sleep(0)
    latest = asyncio.create_task(client.set_light_state(green=20))
    await asyncio.sleep(0)
    older.cancel()
    with pytest.raises(asyncio.CancelledError):
        await older
    release.set()
    await asyncio.gather(first, latest)
    assert post.await_count == 2
    assert post.await_args_list[1].kwargs["json"]["data"]["brightness"] == 0
    assert post.await_args_list[1].kwargs["json"]["data"]["green"] == 20


async def test_cancel_inflight_write_releases_pending_target() -> None:
    """Canceling an active write does not cancel or replay the newer target."""
    response = make_response()
    client, post, entered, _ = blocked_client(response)
    first = asyncio.create_task(client.set_light_state(red=10))
    await entered.wait()
    latest = asyncio.create_task(client.set_light_state(green=20))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await latest
    response.__aexit__.assert_awaited_once()
    assert post.await_count == 2
    assert post.await_args_list[1].kwargs["json"]["data"]["red"] == 255
    assert post.await_args_list[1].kwargs["json"]["data"]["green"] == 20


async def test_pending_targets_are_per_client() -> None:
    """One controller's pending updates never replace another's target."""
    client, post, entered, release = blocked_client()
    other, other_post = make_client(make_response())
    first = asyncio.create_task(client.set_light_state(red=10))
    await entered.wait()
    latest = asyncio.create_task(client.set_light_state(green=20))
    await asyncio.sleep(0)
    await other.set_light_state(blue=30)
    release.set()
    await asyncio.gather(first, latest)
    assert post.await_count == 2
    assert other_post.await_count == 1
    assert other_post.await_args is not None
    assert other_post.await_args.kwargs["json"]["data"]["green"] == 255


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


@pytest.mark.parametrize(
    ("side_effect", "message"),
    [
        pytest.param(TimeoutError(), "request timed out", id="timeout"),
        pytest.param(
            ClientPayloadError("response payload is not completed"),
            "request failed",
            id="payload",
        ),
    ],
)
async def test_response_body_connection_errors(
    side_effect: Exception, message: str
) -> None:
    """Test response body transport failures become library connection errors."""
    response = make_response()
    response.json.side_effect = side_effect
    client, _ = make_client(response)

    with pytest.raises(TrimlightConnectionError, match=message):
        await client.get_light_state()

    response.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("status", [199, 302, 503])
async def test_http_error(status: int) -> None:
    """Test a non-success HTTP response."""
    response = make_response(status=status)
    client, _ = make_client(response)

    with pytest.raises(TrimlightHTTPError) as error:
        await client.get_light_state()

    assert error.value.status == status
    response.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("raise_for_status", [False, True])
async def test_http_error_ignores_session_raise_for_status(
    monkeypatch: pytest.MonkeyPatch, raise_for_status: bool
) -> None:
    """Keep HTTP error classification independent of the shared session policy."""

    async def unavailable(request: web.Request) -> web.Response:
        return web.Response(status=503)

    app = web.Application()
    app.router.add_post("/api/light", unavailable)
    async with (
        TestServer(app) as server,
        ClientSession(raise_for_status=raise_for_status) as session,
    ):
        monkeypatch.setattr("aiotrimlight.client._HTTP_PORT", server.port)
        client = TrimlightClient(server.host, session)

        with pytest.raises(TrimlightHTTPError) as error:
            await client.set_light_state(on=True)

        assert error.value.status == 503


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
