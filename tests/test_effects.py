"""Saved scene contracts, using captured TASK 2 data and labeled simulations."""

import asyncio
import json
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
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


@pytest.fixture(scope="module")
def observations() -> dict[str, dict[str, Any]]:
    """Load independent, unmodified response samples captured during TASK 2."""
    payload = json.loads(
        Path(__file__).with_name("fixtures").joinpath("task2_scenes.json").read_text()
    )
    return {item["label"]: item for item in payload["observations"]}


def effect_response(*effects: object) -> Mock:
    """Build a synthetic list response, including a truly empty full library."""
    return make_response({"code": 0, "data": {"effects": list(effects)}})


def scene_response(scene_id: object = 1) -> Mock:
    """Build synthetic scene state for boundary and concurrency tests."""
    return make_response(
        {
            "code": 0,
            "data": {
                "device_state": 1,
                "ic": 0,
                "scene_id": scene_id,
                "records": [{"zone_id": 1, "zone_on_off": 1, "output_mode": 0}],
            },
        }
    )


@pytest.mark.parametrize(
    ("label", "ids", "expected_ids"),
    [
        ("initial_effect_list", None, list(range(1, 16))),
        ("initial_effect_list", [], list(range(1, 16))),
        ("effect_list_filtered", (1, 5), [1, 5]),
        ("effect_list_absent_id", [120], []),
    ],
)
async def test_captured_lists(
    observations: dict[str, dict[str, Any]],
    label: str,
    ids: Sequence[int] | None,
    expected_ids: list[int],
) -> None:
    """Use library IDs, never the nested zone preset IDs."""
    sample = observations[label]
    client, post = make_client(make_response(sample["response"]))
    effects = await client.get_effect_list(ids)
    assert isinstance(effects, tuple)
    assert [effect.id for effect in effects] == expected_ids
    assert effects == tuple(
        TrimlightEffect(effect["id"], effect["name"])
        for effect in sample["response"]["data"]["effects"]
    )
    assert post.await_args_list[0].kwargs["json"] == sample["request"]
    if label == "effect_list_filtered":
        assert sample["response"]["data"]["effects"][0]["zones"][0]["effect_id"] != 1


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
    "label",
    [
        "initial_runtime",
        "off_static_runtime",
        "after_play_while_off_runtime",
        "on_effect_runtime",
        "mixed_runtime",
        "single_zone_4_runtime",
        "app_a_connected_runtime",
        "app_a_preview_runtime",
        "app_a_overwrite_runtime",
        "app_b_saved_runtime",
        "app_cleanup_runtime",
    ],
)
async def test_captured_runtime(
    observations: dict[str, dict[str, Any]], label: str
) -> None:
    """Expose numeric per-zone state and raw association across real transitions."""
    payload = deepcopy(observations[label]["response"])
    data = payload["data"]
    for record in data["records"]:
        record["output_mode_desc"] = "deliberately wrong: numeric mode wins"
    client, post = make_client(make_response(payload))
    state = await client.get_light_state()
    assert state.scene_id == data["scene_id"]
    assert state.is_on == (data["device_state"] != 0)
    assert state.zones == tuple(
        TrimlightZoneState(
            record["zone_id"],
            bool(record["zone_on_off"]),
            TrimlightOutputMode(record["output_mode"]),
        )
        for record in data["records"]
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
        assert state.brightness == data["records"][0]["static_output"]["brightness"]
    assert post.await_count == 1


@pytest.mark.parametrize(
    ("prefix", "expected_id"),
    [
        ("app_a_connected", 16),
        ("app_a_preview", 16),
        ("app_a_overwrite", 16),
        ("app_b_saved", 17),
        ("app_cleanup", 17),
    ],
)
async def test_app_transitions_and_deleted_id(
    observations: dict[str, dict[str, Any]], prefix: str, expected_id: int
) -> None:
    """Captured preview/save/delete transitions keep raw association separate."""
    client, _ = make_client(
        make_response(observations[f"{prefix}_list"]["response"]),
        make_response(observations[f"{prefix}_runtime"]["response"]),
    )
    effects = await client.get_effect_list()
    state = await client.get_light_state()
    assert state.scene_id == expected_id
    assert (expected_id in {effect.id for effect in effects}) == (
        prefix != "app_cleanup"
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


async def test_play_while_off_and_static_cache(
    observations: dict[str, dict[str, Any]],
) -> None:
    """Captured effect output does not turn on or overwrite remembered channels."""
    client, post = make_client(
        runtime_response(records=[static_record(red=22)]),
        make_response(),
        make_response(observations["after_play_while_off_runtime"]["response"]),
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
