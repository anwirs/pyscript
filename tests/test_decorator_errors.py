"""Test pyscript decorator syntax error and eval-time exception reporting."""
from ast import literal_eval
import asyncio
from datetime import datetime as dt
import pathlib

from custom_components.pyscript.const import DOMAIN
import custom_components.pyscript.trigger as trigger
from pytest_homeassistant.async_mock import mock_open, patch

from homeassistant import loader
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.setup import async_setup_component


async def setup_script(hass, notify_q, now, source):
    """Initialize and load the given pyscript."""
    scripts = [
        "/some/config/dir/pyscripts/hello.py",
    ]
    integration = loader.Integration(
        hass,
        "custom_components.pyscript",
        pathlib.Path("custom_components/pyscript"),
        {"name": "pyscript", "dependencies": [], "requirements": [], "domain": "automation"},
    )

    with patch("homeassistant.loader.async_get_integration", return_value=integration), patch(
        "custom_components.pyscript.os.path.isdir", return_value=True
    ), patch("custom_components.pyscript.glob.iglob", return_value=scripts), patch(
        "custom_components.pyscript.open", mock_open(read_data=source), create=True,
    ), patch(
        "custom_components.pyscript.trigger.dt_now", return_value=now
    ):
        assert await async_setup_component(hass, "pyscript", {DOMAIN: {}})

    #
    # I'm not sure how to run the mock all the time, so just force the dt_now()
    # trigger function to return the given list of times in now.
    #
    def return_next_time():
        nonlocal now
        if isinstance(now, list):
            if len(now) > 1:
                return now.pop(0)
            return now[0]
        return now

    trigger.__dict__["dt_now"] = return_next_time

    if notify_q:

        async def state_changed(event):
            var_name = event.data["entity_id"]
            if var_name != "pyscript.done":
                return
            value = event.data["new_state"].state
            await notify_q.put(value)

        hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)


async def wait_until_done(notify_q):
    """Wait for the done handshake."""
    return await asyncio.wait_for(notify_q.get(), timeout=4)


async def test_decorator_errors(hass, caplog):
    """Test decorator syntax and run-time errors."""
    notify_q = asyncio.Queue(0)
    await setup_script(
        hass,
        notify_q,
        [dt(2020, 7, 1, 10, 59, 59, 999999), dt(2020, 7, 1, 11, 59, 59, 999999)],
        """
seq_num = 0

@time_trigger("startup")
def func_startup_sync(trigger_type=None, trigger_time=None):
    global seq_num

    seq_num += 1
    log.info(f"func_startup_sync setting pyscript.done = {seq_num}, trigger_type = {trigger_type}, trigger_time = {trigger_time}")
    pyscript.done = seq_num

@state_trigger("z + ")
def func1():
    pass

@event_trigger("some_event", "func(")
def func2():
    pass

@state_trigger("True")
@state_active("z + ")
def func3():
    pass

@state_active("z + ")
def func4():
    pass

@state_trigger("1 / int(pyscript.var1)")
def func5():
    pass

@state_trigger("True or pyscript.var1")
@state_active("1 / pyscript.var1")
def func6():
    pass

@state_trigger("pyscript.var7")
def func7():
    global seq_num

    try:
        task.wait_until(state_trigger="z +")
    except SyntaxError as exc:
        log.error(exc)

    try:
        task.wait_until(event_trigger=["event", "z+"])
    except SyntaxError as exc:
        log.error(exc)

    try:
        task.wait_until(state_trigger="pyscript.var1 + 1")
    except TypeError as exc:
        log.error(exc)

    seq_num += 1
    pyscript.done = seq_num

@state_trigger("pyscript.var_done")
def func_wrapup():
    global seq_num

    seq_num += 1
    pyscript.done = seq_num

""",
    )
    seq_num = 0

    seq_num += 1
    # fire event to start triggers, and handshake when they are running
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert literal_eval(await wait_until_done(notify_q)) == seq_num

    hass.states.async_set("pyscript.var1", 1)
    hass.states.async_set("pyscript.var1", 0)

    seq_num += 1
    hass.states.async_set("pyscript.var7", 1)
    assert literal_eval(await wait_until_done(notify_q)) == seq_num

    seq_num += 1
    hass.states.async_set("pyscript.var_done", 1)
    assert literal_eval(await wait_until_done(notify_q)) == seq_num

    assert "SyntaxError: invalid syntax (file.hello.func1 @state_trigger(), line 1)" in caplog.text
    assert (
        "SyntaxError: unexpected EOF while parsing (file.hello.func2 @event_trigger(), line 1)"
        in caplog.text
    )
    assert "SyntaxError: invalid syntax (file.hello.func3 @state_active(), line 1)" in caplog.text
    assert (
        "func4 defined in file.hello: needs at least one trigger decorator (ie: event_trigger, state_trigger, time_trigger)"
        in caplog.text
    )
    assert (
        """Exception in <file.hello.func5 @state_trigger()> line 1:
    1 / int(pyscript.var1)
            ^
ZeroDivisionError: division by zero"""
        in caplog.text
    )

    assert (
        """Exception in <file.hello.func6 @state_active()> line 1:
    1 / pyscript.var1
        ^
TypeError: unsupported operand type(s) for /: 'int' and 'str'"""
        in caplog.text
    )

    assert (
        """Exception in <file.hello.func6 @state_active()> line 1:
    1 / pyscript.var1
        ^
TypeError: unsupported operand type(s) for /: 'int' and 'str'"""
        in caplog.text
    )

    assert "invalid syntax (file.hello.func7 state_trigger, line 1)" in caplog.text
    assert "invalid syntax (file.hello.func7 event_trigger, line 1)" in caplog.text
    assert 'can only concatenate str (not "int") to str' in caplog.text
