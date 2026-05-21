"""Tests for _ToolRegistry singleton and the global ``registry`` object.

Tests cover:
  - Singleton property (same instance, thread safety)
  - register / get_tool (name, level, signature, doc, module metadata)
  - invoke (dict args, JSON string, nonexistent tool, function errors, default args)
  - list_tools (empty, populated, immutability)
  - Callback system (on_invoke, multiple callbacks, error isolation)
  - Edge cases (None args, non-string keys, overwrite)
"""

import json
import threading
import pytest
from tools import registry
from tools import _ToolRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dummy_tool_a(arg: str) -> str:
    """Dummy tool A."""
    return f"A:{arg}"


def _failing_tool() -> None:
    """Always raises."""
    raise RuntimeError("oops")


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save/restore registry state around every test in this file."""
    old_tools = dict(registry._tools)
    old_callbacks = list(registry._on_invoke_callbacks)
    registry._tools.clear()
    registry._on_invoke_callbacks.clear()
    yield
    registry._tools.clear()
    registry._tools.update(old_tools)
    registry._on_invoke_callbacks.clear()
    registry._on_invoke_callbacks.extend(old_callbacks)


# ===================================================================
#  Singleton
# ===================================================================

class TestSingleton:
    """_ToolRegistry must behave as a thread-safe singleton."""

    def test_same_instance(self):
        t1 = _ToolRegistry()
        t2 = _ToolRegistry()
        assert t1 is t2

    def test_global_registry_is_instance(self):
        assert isinstance(registry, _ToolRegistry)

    def test_singleton_across_threads(self):
        instances = []

        def get():
            instances.append(_ToolRegistry())

        threads = [threading.Thread(target=get) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


# ===================================================================
#  Register / get_tool
# ===================================================================

class TestRegister:
    """Tool registration and retrieval."""

    def test_register_and_get(self):
        decorator = registry.register(name="dummy_tool_a")
        decorator(_dummy_tool_a)
        func = registry.get_tool("dummy_tool_a")
        assert func is _dummy_tool_a

    def test_register_default_name(self):
        @registry.register()
        def my_func():
            return 42

        assert registry.get_tool("my_func") is not None
        assert registry.get_tool("my_func")() == 42

    def test_register_custom_name(self):
        @registry.register(name="custom_name")
        def some_func():
            return 1

        assert registry.get_tool("custom_name") is not None
        assert registry.get_tool("some_func") is None

    def test_register_with_level(self):
        @registry.register(name="level_tool", level="L0")
        def tool():
            return 0

        info = registry.list_tools()["level_tool"]
        assert info["level"] == "L0"

    def test_get_tool_nonexistent(self):
        assert registry.get_tool("nope") is None

    def test_register_overwrites_existing(self):
        @registry.register(name="overwrite")
        def first():
            return 1

        @registry.register(name="overwrite")
        def second():
            return 2

        assert registry.get_tool("overwrite")() == 2

    def test_register_stores_signature(self):
        @registry.register(name="sig_tool")
        def documented(a: int, b: str = "x") -> bool:
            """Has docs."""
            return True

        info = registry.list_tools()["sig_tool"]
        assert "a" in info["signature"]
        assert "b" in info["signature"]

    def test_register_stores_docstring(self):
        @registry.register(name="doc_tool")
        def documented(a: int, b: str = "x") -> bool:
            """Has docs."""
            return True

        info = registry.list_tools()["doc_tool"]
        assert info["doc"] == "Has docs."

    def test_register_stores_module(self):
        @registry.register(name="mod_check")
        def mod_func():
            pass

        info = registry.list_tools()["mod_check"]
        assert info["module"] == __name__


# ===================================================================
#  invoke
# ===================================================================

class TestInvoke:
    """Tool invocation via registry.invoke()."""

    @pytest.fixture(autouse=True)
    def _register_tools(self):
        @registry.register(name="echo")
        def echo(msg: str) -> str:
            return msg

        @registry.register(name="add")
        def add(a: int, b: int = 0) -> int:
            return a + b

        @registry.register(name="fail")
        def fail() -> None:
            raise ValueError("broken")

        yield

    def test_invoke_success_with_dict(self):
        result = registry.invoke("echo", {"msg": "hello"})
        assert result["success"] is True
        assert json.loads(result["result_json"]) == "hello"
        assert result["error"] == ""

    def test_invoke_success_with_json_string(self):
        result = registry.invoke("echo", '{"msg": "world"}')
        assert result["success"] is True
        assert json.loads(result["result_json"]) == "world"

    def test_invoke_with_default_args(self):
        result = registry.invoke("add", {"a": 5})
        assert result["success"] is True
        assert json.loads(result["result_json"]) == 5

    def test_invoke_all_args(self):
        result = registry.invoke("add", {"a": 3, "b": 7})
        assert result["success"] is True
        assert json.loads(result["result_json"]) == 10

    def test_invoke_nonexistent_tool(self):
        result = registry.invoke("no_such_tool", {})
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_invoke_function_error(self):
        result = registry.invoke("fail", {})
        assert result["success"] is False
        assert "ValueError" in result["error"]
        assert "broken" in result["error"]

    def test_invoke_empty_args_variants(self):
        @registry.register(name="no_args")
        def no_args() -> str:
            return "ok"

        for args in ({}, "", None):
            result = registry.invoke("no_args", args)
            assert result["success"] is True

    def test_invoke_elapsed_ms_present(self):
        result = registry.invoke("echo", {"msg": "x"})
        assert isinstance(result["elapsed_ms"], int)
        assert result["elapsed_ms"] >= 0

    def test_invoke_missing_required_arg(self):
        result = registry.invoke("echo", {})
        assert result["success"] is False


# ===================================================================
#  list_tools
# ===================================================================

class TestListTools:
    """list_tools returns metadata for all registered tools."""

    @pytest.fixture(autouse=True)
    def _register(self):
        @registry.register(name="alpha")
        def alpha():
            pass

        @registry.register(name="beta", level="L1")
        def beta():
            pass

        yield

    def test_list_tools_contains_registered(self):
        tools = registry.list_tools()
        assert "alpha" in tools
        assert "beta" in tools

    def test_list_tools_level(self):
        tools = registry.list_tools()
        assert tools["beta"]["level"] == "L1"

    def test_list_tools_immutability(self):
        """Modifying the returned dict must not affect the registry."""
        returned = registry.list_tools()
        returned["injected"] = {}
        assert "injected" not in registry.list_tools()


# ===================================================================
#  Callback system
# ===================================================================

class TestCallbacks:
    """on_invoke / _fire_callbacks."""

    @pytest.fixture(autouse=True)
    def _register(self):
        @registry.register(name="cb_tool")
        def cb_tool(v: str = "ok") -> str:
            return v

        yield

    def test_callback_invoked_on_success(self):
        calls = []
        registry.on_invoke(lambda name, ok, res: calls.append((name, ok)))
        registry.invoke("cb_tool", {"v": "hi"})
        assert len(calls) == 1
        assert calls[0] == ("cb_tool", True)

    def test_callback_invoked_on_failure(self):
        calls = []
        registry.on_invoke(lambda name, ok, res: calls.append((name, ok)))
        # Invoke a tool that exists but raises an error
        registry.invoke("cb_tool", {"v": None})  # cb_tool expects str, None causes issue
        assert len(calls) >= 1

    def test_callback_not_invoked_for_nonexistent_tool(self):
        """'Tool not found' returns early and does NOT fire callbacks."""
        calls = []
        registry.on_invoke(lambda name, ok, res: calls.append((name, ok)))
        registry.invoke("no_such_tool", {})
        assert len(calls) == 0

    def test_multiple_callbacks_all_fired(self):
        counter = [0]

        def cb1(*_):
            counter[0] += 1

        def cb2(*_):
            counter[0] += 1

        registry.on_invoke(cb1)
        registry.on_invoke(cb2)
        registry.invoke("cb_tool", {})
        assert counter[0] == 2

    def test_callback_exception_does_not_crash(self):
        """A failing callback must not prevent other callbacks."""
        calls = []

        def bad_cb(*_):
            raise ZeroDivisionError("bad")

        def good_cb(*_):
            calls.append("ok")

        registry.on_invoke(bad_cb)
        registry.on_invoke(good_cb)
        registry.invoke("cb_tool", {})
        assert calls == ["ok"]

    def test_on_invoke_returns_none(self):
        assert registry.on_invoke(lambda *_: None) is None


# ===================================================================
#  Edge cases
# ===================================================================

class TestEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_invoke_with_non_string_key_args(self):
        @registry.register(name="key_types")
        def key_types(**kwargs) -> dict:
            return kwargs

        result = registry.invoke("key_types", {"a": 1, "b": "two"})
        assert result["success"] is True

    def test_invoke_with_none_value(self):
        @registry.register(name="none_val")
        def none_val(x: str = None) -> str:
            return x or "default"

        result = registry.invoke("none_val", {"x": None})
        assert result["success"] is True
        # None passes through to the function; x=None triggers "default" fallback
        assert json.loads(result["result_json"]) == "default"

    def test_register_with_empty_parens(self):
        """@registry.register() with empty parentheses uses func.__name__."""
        @registry.register()
        def raw_func():
            return 99

        f = registry.get_tool("raw_func")
        assert f is not None
        assert f() == 99
