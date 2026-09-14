"""Saved scene contracts using minimal synthetic data, never device captures."""

import asyncio
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, replace
from typing import Any
from unittest.mock import Mock

import pytest
from aiohttp import ClientConnectionError
from test_client import make_client, make_response, runtime_response, static_record
from yarl import URL

from aiotrimlight import (
    TrimlightClient,
    TrimlightCommandError,
    TrimlightConnectionError,
    TrimlightEffect,
    TrimlightHTTPError,
    TrimlightLightState,
    TrimlightOutputMode,
    TrimlightProtocolError,
    TrimlightZoneState,
)


def effect_response(*effects: object) -> Mock:
    """Build a synthetic list response, including a truly empty full library."""
    return make_response({"code": 0, "data": {"effects": list(effects)}})


def effect_record(
    zone_id: int = 1, *, enabled: int = 1, brightness: int = 40
) -> dict[str, Any]:
    """Build a minimal synthetic effect record with an unrelated preset ID."""
    return {
        "zone_id": zone_id,
        "zone_on_off": enabled,
        "output_mode": 0,
        "effect": {"effect_id": 95, "brightness": brightness},
    }


def scene_response(
    scene_id: object = 1,
    *,
    device_state: int = 1,
    records: list[dict[str, Any]] | None = None,
) -> Mock:
    """Build synthetic scene state for boundary and concurrency tests."""
    return make_response(
        {
            "code": 0,
            "data": {
                "device_state": device_state,
                "ic": 0,
                "scene_id": scene_id,
                "records": [effect_record()] if records is None else records,
            },
        }
    )


@pytest.mark.parametrize(
    ("ids", "returned_ids"),
    [
        pytest.param(None, [7, 3], id="all-default"),
        pytest.param([], [7, 3], id="all-empty-filter"),
        pytest.param((3, 7), [7, 3], id="filtered"),
        pytest.param([120], [], id="absent-id"),
    ],
)
async def test_effect_list_queries(
    ids: Sequence[int] | None,
    returned_ids: list[int],
) -> None:
    """Use library IDs, never the nested zone preset IDs."""
    client, post = make_client(
        effect_response(
            *[
                {
                    "id": effect_id,
                    "name": f"Scene {effect_id}",
                    "zones": [{"zone_id": 255, "effect_id": 95}],
                }
                for effect_id in returned_ids
            ]
        )
    )
    effects = await client.get_effect_list(ids)
    assert isinstance(effects, tuple)
    assert effects == tuple(
        TrimlightEffect(effect_id, f"Scene {effect_id}")
        for effect_id in sorted(returned_ids)
    )
    assert [call.kwargs["json"] for call in post.await_args_list] == [
        {
            "cmd": "get_effect_list",
            "data": {"effect_ids": [] if ids is None else list(ids)},
        }
    ]


async def test_names_sorting_and_empty_library() -> None:
    """Synthetic duplicate/blank names are preserved; lists are never cached."""
    client, _ = make_client(
        effect_response(
            {"id": 120, "name": "Same"},
            {"id": 2, "name": "  "},
            {"id": 1, "name": "Same"},
            {"id": 3, "name": ""},
        ),
        effect_response({"id": 1, "name": "Renamed"}),
        effect_response(),
        scene_response(120),
        make_response(),
        runtime_response(device_state=0),
    )
    assert await client.get_effect_list() == (
        TrimlightEffect(1, "Same"),
        TrimlightEffect(2, "  "),
        TrimlightEffect(3, ""),
        TrimlightEffect(120, "Same"),
    )
    assert await client.get_effect_list() == (TrimlightEffect(1, "Renamed"),)
    assert await client.get_effect_list() == ()
    assert (await client.get_light_state()).scene_id == 120
    assert not (await client.set_light_state(on=False)).is_on


@pytest.mark.parametrize("value", [None, True, "1", 1.5, 0, -1, 121])
@pytest.mark.parametrize("operation", ["list", "play"])
async def test_invalid_request_ids(value: Any, operation: str) -> None:
    """Synthetic invalid caller IDs fail before any network request."""
    client, post = make_client()
    error = ValueError if type(value) is int else TypeError
    with pytest.raises(error):
        if operation == "list":
            await client.get_effect_list([value])
        else:
            await client.play_effect(value)
    post.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0},
        {"code": 0, "data": []},
        {"code": 0, "data": {}},
        {"code": 0, "data": {"effects": {}}},
        {"code": 0, "data": {"effects": [None]}},
        {"code": 0, "data": {"effects": [{"id": 1}]}},
        {"code": 0, "data": {"effects": [{"id": 1, "name": 3}]}},
        {
            "code": 0,
            "data": {"effects": [{"id": 1, "name": "A"}, {"id": 1, "name": "B"}]},
        },
    ],
)
async def test_invalid_lists(payload: object) -> None:
    """Synthetic malformed lists are not converted to empty or partial success."""
    client, _ = make_client(make_response(payload))
    with pytest.raises(TrimlightProtocolError):
        await client.get_effect_list()


@pytest.mark.parametrize("value", [None, True, "1", 1.5, 0, -1, 121])
async def test_invalid_response_ids(value: object) -> None:
    """Present but invalid IDs are protocol errors, not legacy missing fields."""
    client, _ = make_client(effect_response({"id": value, "name": "A"}))
    with pytest.raises(TrimlightProtocolError):
        await client.get_effect_list()
    client, _ = make_client(scene_response(value))
    with pytest.raises(TrimlightProtocolError):
        await client.get_light_state()


@pytest.mark.parametrize(
    ("device_state", "records"),
    [
        pytest.param(1, [static_record()], id="static-on"),
        pytest.param(0, [static_record()], id="static-off"),
        pytest.param(0, [effect_record()], id="effect-off"),
        pytest.param(1, [effect_record()], id="effect-on"),
        pytest.param(2, [effect_record()], id="effect-timer"),
        pytest.param(
            1,
            [effect_record(), static_record(zone_id=2), effect_record(4)],
            id="mixed-zones",
        ),
        pytest.param(1, [static_record(zone_id=4)], id="zone-four-is-not-bitmask"),
        pytest.param(1, [effect_record(), effect_record(4)], id="multiple-effects"),
        pytest.param(1, [effect_record(enabled=0)], id="disabled-effect"),
        pytest.param(
            1, [{"zone_id": 255, "zone_on_off": 1, "output_mode": 2}], id="no-output"
        ),
        pytest.param(1, [], id="no-records"),
    ],
)
async def test_runtime_association_and_zones(
    device_state: int, records: list[dict[str, Any]]
) -> None:
    """Numeric zone fields win over descriptions, independently of association."""
    client, post = make_client(
        scene_response(
            7,
            device_state=device_state,
            records=[
                {**record, "output_mode_desc": "deliberately wrong"}
                for record in records
            ],
        )
    )
    state = await client.get_light_state()
    assert state.scene_id == 7
    assert state.is_on == (device_state != 0)
    assert state.zones == tuple(
        TrimlightZoneState(
            record["zone_id"],
            bool(record["zone_on_off"]),
            TrimlightOutputMode(record["output_mode"]),
        )
        for record in records
    )
    if (
        len(state.zones) != 1
        or state.zones[0].output_mode != TrimlightOutputMode.STATIC
    ):
        assert (
            replace(state, is_on=False, scene_id=None, zones=())
            == TrimlightLightState()
        )
    else:
        assert state.brightness == records[0]["static_output"]["brightness"]
    assert [call.kwargs["json"] for call in post.await_args_list] == [
        {"cmd": "get_runtime_state", "data": {"zone_id": 255}}
    ]


@pytest.mark.parametrize(
    ("scene_id", "library_ids", "runtime_brightness", "saved_brightness"),
    [
        pytest.param(3, [3], 40, 40, id="selected"),
        pytest.param(3, [3], 80, 40, id="unsaved-preview"),
        pytest.param(3, [3], 80, 80, id="overwrite"),
        pytest.param(7, [3, 7], 80, 80, id="save-as"),
        pytest.param(7, [3], 80, 40, id="deleted-association"),
    ],
)
async def test_scene_transitions_and_deleted_id(
    scene_id: int,
    library_ids: list[int],
    runtime_brightness: int,
    saved_brightness: int,
) -> None:
    """Simulate App transitions without inferring association from effect parameters."""
    client, _ = make_client(
        effect_response({"id": 3, "name": "Scene 3"}),
        scene_response(3),
        effect_response(
            *[
                {
                    "id": effect_id,
                    "name": f"Scene {effect_id}",
                    "zones": [
                        {
                            "zone_id": 255,
                            "effect_id": 95,
                            "brightness": saved_brightness,
                        }
                    ],
                }
                for effect_id in library_ids
            ]
        ),
        scene_response(
            scene_id, records=[effect_record(brightness=runtime_brightness)]
        ),
    )
    assert await client.get_effect_list() == (TrimlightEffect(3, "Scene 3"),)
    assert (await client.get_light_state()).scene_id == 3
    effects = await client.get_effect_list()
    state = await client.get_light_state()
    assert effects == tuple(
        TrimlightEffect(effect_id, f"Scene {effect_id}") for effect_id in library_ids
    )
    assert state == TrimlightLightState(
        is_on=True,
        scene_id=scene_id,
        zones=(TrimlightZoneState(1, True, TrimlightOutputMode.EFFECT),),
    )


async def test_missing_scene_id_and_disabled_zones() -> None:
    """Synthetic old firmware has unknown association; no output is not a scene."""
    client, _ = make_client(
        runtime_response(
            records=[
                {"zone_id": 3, "zone_on_off": 0, "output_mode": 0},
                {"zone_id": 4, "zone_on_off": 1, "output_mode": 2},
            ]
        )
    )
    assert await client.get_light_state() == TrimlightLightState(
        is_on=True,
        zones=(
            TrimlightZoneState(3, False, TrimlightOutputMode.EFFECT),
            TrimlightZoneState(4, True, TrimlightOutputMode.NONE),
        ),
    )


@pytest.mark.parametrize("effect_id", [1, 120])
async def test_play_uses_readback_not_requested_id(effect_id: int) -> None:
    """Synthetic differing readback is returned without list lookup or guessing."""
    client, post = make_client(make_response(), scene_response(2))
    assert (await client.play_effect(effect_id)).scene_id == 2
    assert [call.kwargs["json"] for call in post.await_args_list] == [
        {"cmd": "play_effect", "data": {"effect_id": effect_id}},
        {"cmd": "get_runtime_state", "data": {"zone_id": 255}},
    ]


async def test_play_while_off_and_static_cache() -> None:
    """Synthetic effect output does not turn on or overwrite remembered channels."""
    client, post = make_client(
        runtime_response(records=[static_record(red=22)]),
        make_response(),
        scene_response(device_state=0),
        make_response(),
        runtime_response(),
    )
    await client.get_light_state()
    state = await client.play_effect(1)
    assert not state.is_on
    assert state.scene_id == 1
    await client.set_light_state(green=33)
    assert post.await_args_list[3].kwargs["json"] == {
        "cmd": "set_static_output",
        "data": {
            "zone_id": 255,
            "brightness": 64,
            "red": 22,
            "green": 33,
            "blue": 3,
            "warm_white": 4,
            "cold_white": 5,
        },
    }


@pytest.mark.parametrize("code", [101, 201])
@pytest.mark.parametrize("operation", ["list", "play"])
async def test_command_failure_preserves_basic_control(
    code: int, operation: str
) -> None:
    """Unsupported and internal errors propagate without retries or lock leaks."""
    client, post = make_client(
        make_response({"code": code, "msg": "device error"}),
        make_response(),
        runtime_response(device_state=0),
    )
    with pytest.raises(TrimlightCommandError) as error:
        if operation == "list":
            await client.get_effect_list()
        else:
            await client.play_effect(1)
    assert error.value.code == code
    assert post.await_count == 1
    assert not (await client.set_light_state(on=False)).is_on


@pytest.mark.parametrize("phase", ["command", "readback"])
@pytest.mark.parametrize("failure", ["connection", "timeout", "http", "protocol"])
async def test_play_failure_and_recovery(phase: str, failure: str) -> None:
    """Synthetic failures are surfaced once; later polling can recover."""
    failures: dict[str, tuple[object, type[Exception]]] = {
        "connection": (ClientConnectionError(), TrimlightConnectionError),
        "timeout": (TimeoutError(), TrimlightConnectionError),
        "http": (make_response(status=503), TrimlightHTTPError),
        "protocol": (make_response([]), TrimlightProtocolError),
    }
    response, error = failures[failure]
    client, post = make_client()
    post.side_effect = ([make_response()] if phase == "readback" else []) + [
        response,
        scene_response(2),
    ]
    with pytest.raises(error):
        await client.play_effect(1)
    assert post.await_count == (2 if phase == "readback" else 1)
    assert (await client.get_light_state()).scene_id == 2


async def call_operation(client: TrimlightClient, operation: str) -> None:
    """Invoke one public network operation as a concurrency competitor."""
    if operation == "list":
        await client.get_effect_list()
    elif operation == "info":
        await client.get_device_info()
    elif operation == "set":
        await client.set_light_state(on=True)
    elif operation == "play":
        await client.play_effect(2)
    else:
        await client.get_light_state()


@pytest.mark.parametrize("blocked_request", [1, 2])
@pytest.mark.parametrize("operation", ["list", "info", "set", "play", "state"])
async def test_play_and_readback_are_atomic(
    blocked_request: int, operation: str
) -> None:
    """No public operation can insert HTTP requests inside playback/readback."""
    entered = asyncio.Event()
    release = asyncio.Event()
    requests: list[str] = []

    async def send(_url: URL, **kwargs: Any) -> Mock:
        command = kwargs["json"]["cmd"]
        requests.append(command)
        if len(requests) == blocked_request:
            entered.set()
            await release.wait()
        if command == "get_runtime_state":
            return scene_response()
        if command == "get_effect_list":
            return effect_response()
        if command == "get_device_data":
            return make_response({"code": 0, "data": {"sys_info": {}}})
        return make_response()

    client, post = make_client()
    post.side_effect = send
    async with asyncio.timeout(2):
        first = asyncio.create_task(client.play_effect(1))
        await entered.wait()
        second = asyncio.create_task(call_operation(client, operation))
        try:
            await asyncio.sleep(0)
            assert len(requests) == blocked_request
        finally:
            release.set()
            await asyncio.gather(first, second)
    assert requests[:2] == ["play_effect", "get_runtime_state"]


@pytest.mark.parametrize("cancel_during", ["command", "readback", "waiting"])
async def test_play_cancellation_releases_lock(cancel_during: str) -> None:
    """Cancellation at either I/O or while queued cannot strand the client lock."""
    entered = asyncio.Event()
    release = asyncio.Event()
    client, post = make_client()
    requests: list[str] = []

    async def send(_url: URL, **kwargs: Any) -> Mock:
        command = kwargs["json"]["cmd"]
        requests.append(command)
        if len(requests) == (2 if cancel_during == "readback" else 1):
            entered.set()
            await release.wait()
        return scene_response() if command == "get_runtime_state" else make_response()

    post.side_effect = send
    async with asyncio.timeout(2):
        first = asyncio.create_task(client.play_effect(1))
        await entered.wait()
        target = first
        if cancel_during == "waiting":
            target = asyncio.create_task(client.play_effect(2))
            await asyncio.sleep(0)
        target.cancel()
        with pytest.raises(asyncio.CancelledError):
            await target
        release.set()
        if target is not first:
            await first
        assert (await client.get_light_state()).scene_id == 1
    assert requests.count("play_effect") == 1


def test_public_models_are_immutable_and_backward_compatible() -> None:
    """New metadata has defaults and value equality suitable for cached consumers."""
    scene = TrimlightEffect(1, "Name")
    zone = TrimlightZoneState(1, True, TrimlightOutputMode.EFFECT)
    state = TrimlightLightState(True, 2, 3, 4, 5, 6, 7)
    assert state.scene_id is None
    assert state.zones == ()
    for model, field in [(scene, "name"), (zone, "zone_id"), (state, "scene_id")]:
        with pytest.raises(FrozenInstanceError):
            setattr(model, field, None)
    assert TrimlightLightState(scene_id=1, zones=(zone,)) == TrimlightLightState(
        scene_id=1, zones=(TrimlightZoneState(1, True, TrimlightOutputMode.EFFECT),)
    )
    assert replace(state, scene_id=1) != state
    assert replace(state, zones=(zone,)) != state
