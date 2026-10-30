"""Registry guarantees, independent of business tools."""

import time

from pydantic import BaseModel

from app.tools.registry import ErrorCode, Tool, ToolRegistry


class Args(BaseModel):
    x: int


def make(handler, **kw):
    return ToolRegistry([Tool("t", "test tool", Args, handler, **kw)], timeout_seconds=0.1)


def test_success_returns_data_and_duration():
    r = make(lambda a: {"double": a.x * 2}).execute("t", {"x": 2})
    assert r.ok and r.data == {"double": 4} and r.duration_ms >= 0


def test_unknown_tool():
    assert make(lambda a: {}).execute("nope", {}).error.code == ErrorCode.UNKNOWN_TOOL


def test_invalid_arguments_are_rejected_before_handler_runs():
    called = []
    r = make(lambda a: called.append(1) or {}).execute("t", {"x": "not-int"})
    assert r.error.code == ErrorCode.INVALID_ARGUMENTS and not called


def test_timeout_is_typed_and_retryable():
    r = make(lambda a: time.sleep(0.5) or {}).execute("t", {"x": 1})
    assert r.error.code == ErrorCode.TIMEOUT and r.error.retryable


def test_unexpected_exception_is_contained_without_traceback():
    def boom(a):
        raise KeyError("secret internals")

    r = make(boom).execute("t", {"x": 1})
    assert r.error.code == ErrorCode.INTERNAL
    assert "secret internals" not in r.error.message


def test_approval_gate_blocks_unapproved_call():
    called = []
    reg = make(lambda a: called.append(1) or {}, requires_approval=True)
    assert reg.execute("t", {"x": 1}).error.code == ErrorCode.APPROVAL_REQUIRED
    assert not called
    assert reg.execute("t", {"x": 1}, approved=True).ok


def test_schema_is_openai_function_format():
    schema = make(lambda a: {}).schemas()[0]
    assert schema["type"] == "function"
    assert schema["function"]["parameters"]["properties"]["x"]["type"] == "integer"
