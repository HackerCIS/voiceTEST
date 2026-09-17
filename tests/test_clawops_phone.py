import asyncio
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app import clawops_phone, main, phone_session


@pytest.fixture(autouse=True)
def phone_config(monkeypatch):
    for name, value in (
        ("CLAWOPS_API_KEY", "clawops_test_key"),
        ("CLAWOPS_ACCOUNT_ID", "account_test"),
        ("CLAWOPS_FROM_NUMBER", "07000000000"),
        ("OPENAI_API_KEY", "openai_test_key"),
        ("CLAWOPS_TEST_TO_NUMBER", ""),
        ("CLAWOPS_OUTBOUND_VAD_EAGERNESS", ""),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(main, "_clawops_task", None)
    monkeypatch.setattr(main, "_clawops_session", None)
    monkeypatch.setattr(main, "_clawops_lock", asyncio.Lock())
    monkeypatch.setattr(phone_session, "end_phone_call", AsyncMock())


@pytest.fixture
def sdk(monkeypatch):
    # Tests must also work with only the base browser dependencies installed.
    package = ModuleType("clawops")
    package.__path__ = []
    module = ModuleType("clawops.agent")
    module.ClawOpsAgent = Mock()
    module.OpenAIRealtime = Mock()
    package.agent = module
    monkeypatch.setitem(sys.modules, "clawops", package)
    monkeypatch.setitem(sys.modules, "clawops.agent", module)
    monkeypatch.setattr(clawops_phone, "_build_realtime", lambda **kwargs: module.OpenAIRealtime(**kwargs))
    return module


@pytest.mark.parametrize("mode, eagerness", [("inbound", "low"), ("outbound", "high")])
def test_phone_vad_and_prewarm_configuration(sdk, mode, eagerness):
    clawops_phone.build_agent(mode=mode)
    assert sdk.OpenAIRealtime.call_args.kwargs["turn_detection"] == {
        "type": "semantic_vad", "eagerness": eagerness,
        "create_response": True, "interrupt_response": True,
    }
    assert sdk.ClawOpsAgent.call_args.kwargs["prewarm_enabled"] is True


def test_outbound_vad_override_and_invalid_config(sdk, monkeypatch):
    monkeypatch.setenv("CLAWOPS_OUTBOUND_VAD_EAGERNESS", "auto")
    clawops_phone.build_agent(mode="outbound")
    assert sdk.OpenAIRealtime.call_args.kwargs["turn_detection"]["eagerness"] == "auto"
    monkeypatch.setenv("CLAWOPS_OUTBOUND_VAD_EAGERNESS", "fast")
    with TestClient(main.app) as client:
        response = client.post("/api/clawops/start", json={"mode": "outbound", "toNumber": "01012345678"})
        assert response.status_code == 503
        assert "CLAWOPS_OUTBOUND_VAD_EAGERNESS" in response.json()["detail"]
        assert main._clawops_task is None


@pytest.mark.parametrize("missing_dependency", ["clawops", "agent", "openai"])
def test_missing_dependencies_return_503_without_stopping_server(
    monkeypatch, sdk, missing_dependency
):
    if missing_dependency == "clawops":
        monkeypatch.setitem(sys.modules, "clawops", None)
        monkeypatch.delitem(sys.modules, "clawops.agent")
    elif missing_dependency == "agent":
        monkeypatch.setitem(sys.modules, "clawops.agent", None)
    else:
        # The SDK lazily checks OpenAI extras when constructing OpenAIRealtime.
        sdk.OpenAIRealtime.side_effect = ImportError("openai is required")

    with TestClient(main.app) as client:
        response = client.post("/api/clawops/start")

        assert response.status_code == 503
        assert "requirements-clawops.txt" in response.json()["detail"]
        assert sys.executable in response.json()["detail"]
        assert main._clawops_task is None
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    "name",
    ["CLAWOPS_API_KEY", "CLAWOPS_ACCOUNT_ID", "CLAWOPS_FROM_NUMBER", "OPENAI_API_KEY"],
)
def test_missing_environment_is_recoverable(monkeypatch, sdk, name):
    monkeypatch.setenv(name, " ")

    with pytest.raises(clawops_phone.ClawOpsConfigurationError, match=name):
        clawops_phone.build_agent()

    with TestClient(main.app) as client:
        response = client.post("/api/clawops/start")
        assert response.status_code == 400
        assert name in response.json()["detail"]
        assert main._clawops_task is None
        assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("argv", [[], ["--to", "01000000000"]])
def test_standalone_cli_reports_setup_error(monkeypatch, argv):
    monkeypatch.setitem(sys.modules, "clawops", None)
    monkeypatch.delitem(sys.modules, "clawops.agent", raising=False)

    with pytest.raises(SystemExit) as error:
        clawops_phone.main(argv)

    assert "requirements-clawops.txt" in str(error.value.code)


def make_waiting_agent():
    started = threading.Event()

    async def connect():
        started.set()

    async def wait():
        started.set()
        await asyncio.Event().wait()

    call = SimpleNamespace(wait=wait, status="queued", call_id="CA_test")
    agent = SimpleNamespace(
        connect=AsyncMock(side_effect=connect),
        call=AsyncMock(return_value=call),
        disconnect=AsyncMock(),
        on=lambda event: lambda handler: handler,
    )
    return agent, started


@pytest.mark.parametrize("to_number", ["", "01000000000"])
def test_phone_start_stop_and_restart(monkeypatch, to_number):
    agent, started = make_waiting_agent()
    build = Mock(return_value=(agent, "07000000000"))
    monkeypatch.setattr(clawops_phone, "build_agent", build)

    with TestClient(main.app) as client:
        for _ in range(2):
            started.clear()
            mode = "outbound" if to_number else "inbound"
            response = client.post("/api/clawops/start", json={"mode": mode, "toNumber": to_number})
            assert response.status_code == 200
            data = response.json()
            assert data["mode"] == mode
            assert data["fromNumber"] == "07000000000"
            assert data["toNumber"] == (to_number or None)
            assert data["sessionId"]
            assert data["active"] is True
            build.assert_called_with(mode=mode)
            assert started.wait(timeout=2)
            task = main._clawops_task
            build_count = build.call_count

            assert client.post("/api/clawops/start").status_code == 409
            assert build.call_count == build_count
            assert client.post("/api/clawops/stop", json={"sessionId": data["sessionId"]}).json() == {"status": "stopped"}
            assert task.cancelled()
            assert main._clawops_task is None

        assert client.post("/api/clawops/stop").status_code == 200
        assert client.get("/api/health").status_code == 200

    assert agent.disconnect.await_count == 2
    if to_number:
        assert agent.call.await_count == 2
        agent.call.assert_awaited_with(to_number, timeout=30)
        assert phone_session.end_phone_call.await_count == 2
        phone_session.end_phone_call.assert_awaited_with("CA_test")
    else:
        assert agent.connect.await_count == 2
        agent.call.assert_not_awaited()


def test_server_shutdown_disconnects_phone(monkeypatch):
    agent, started = make_waiting_agent()
    monkeypatch.setattr(clawops_phone, "build_agent", lambda **kwargs: (agent, "07000000000"))

    with TestClient(main.app) as client:
        assert client.post("/api/clawops/start").status_code == 200
        assert started.wait(timeout=2)
        task = main._clawops_task

    assert task.cancelled()
    agent.disconnect.assert_awaited_once()
    assert main._clawops_task is None


def test_failed_phone_task_is_reported_and_disconnected(monkeypatch, caplog):
    agent, _ = make_waiting_agent()
    finished = threading.Event()
    agent.connect.side_effect = RuntimeError("control connection failed")
    agent.disconnect.side_effect = finished.set
    monkeypatch.setattr(clawops_phone, "build_agent", lambda **kwargs: (agent, "07000000000"))

    with TestClient(main.app) as client:
        assert client.post("/api/clawops/start").status_code == 200
        assert finished.wait(timeout=2)
        response = client.get("/api/clawops/status")
        assert response.json()["status"] == "failed"
        assert response.json()["active"] is False
        assert "연결에 실패" in response.json()["error"]
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/health").status_code == 200

    agent.disconnect.assert_awaited_once()
    assert "ClawOps phone session failed" in caplog.text
    assert "control connection failed" in caplog.text


def test_environment_target_does_not_implicitly_dial(monkeypatch):
    monkeypatch.setenv("CLAWOPS_TEST_TO_NUMBER", "01099999999")
    agent, _ = make_waiting_agent()
    monkeypatch.setattr(clawops_phone, "build_agent", lambda **kwargs: (agent, "07000000000"))
    with TestClient(main.app) as client:
        response = client.post("/api/clawops/start")
        assert response.json()["mode"] == "inbound"
        assert response.json()["toNumber"] is None
    agent.call.assert_not_awaited()


@pytest.mark.parametrize("number", ["", "   ", "abc", "119", "010;12345678", "+0123456789"])
def test_invalid_outbound_number_never_starts_a_call(monkeypatch, number):
    build = Mock()
    monkeypatch.setattr(clawops_phone, "build_agent", build)
    with TestClient(main.app) as client:
        response = client.post("/api/clawops/start", json={"mode": "outbound", "toNumber": number})
        assert response.status_code == 400
        assert "전화번호" in response.json()["detail"]
    build.assert_not_called()


@pytest.mark.parametrize("number, expected", [
    ("010-1234-5678", "01012345678"),
    (" +82 (10) 1234-5678 ", "+821012345678"),
    ("02-123-4567", "021234567"),
])
def test_outbound_uses_normalized_number(monkeypatch, number, expected):
    agent, _ = make_waiting_agent()
    monkeypatch.setattr(clawops_phone, "build_agent", lambda **kwargs: (agent, "07000000000"))
    with TestClient(main.app) as client:
        response = client.post("/api/clawops/start", json={"mode": "outbound", "toNumber": number})
        assert response.status_code == 200
        assert response.json()["toNumber"] == expected
        client.post("/api/clawops/stop")
    agent.call.assert_awaited_once_with(expected, timeout=30)


def test_stale_stop_cannot_cancel_a_new_session(monkeypatch):
    agent, _ = make_waiting_agent()
    monkeypatch.setattr(clawops_phone, "build_agent", lambda **kwargs: (agent, "07000000000"))
    with TestClient(main.app) as client:
        first = client.post("/api/clawops/start").json()
        client.post("/api/clawops/stop", json={"sessionId": first["sessionId"]})
        second = client.post("/api/clawops/start").json()
        assert first["sessionId"] != second["sessionId"]
        response = client.post("/api/clawops/stop", json={"sessionId": first["sessionId"]})
        assert response.status_code == 409
        assert client.get("/api/clawops/status").json()["active"] is True


@pytest.mark.parametrize("result", ["completed", "no-answer", "busy", "rejected", "failed"])
def test_outbound_completion_preserves_result(monkeypatch, result):
    agent, _ = make_waiting_agent()
    call = agent.call.return_value
    finished = threading.Event()

    async def wait():
        call.status = result

    call.wait = wait
    agent.disconnect.side_effect = finished.set
    monkeypatch.setattr(clawops_phone, "build_agent", lambda **kwargs: (agent, "07000000000"))
    with TestClient(main.app) as client:
        client.post("/api/clawops/start", json={"mode": "outbound", "toNumber": "01012345678"})
        assert finished.wait(timeout=2)
        data = client.get("/api/clawops/status").json()
        assert data["status"] == result
        assert data["active"] is False
    phone_session.end_phone_call.assert_not_awaited()


def test_stop_waits_for_originate_result_before_canceling():
    async def run():
        originating = asyncio.Event()
        release = asyncio.Event()
        agent, _ = make_waiting_agent()
        call = agent.call.return_value

        async def originate(*args, **kwargs):
            originating.set()
            await release.wait()
            return call

        agent.call.side_effect = originate
        session = phone_session.PhoneSession(agent, "07000000000", "01012345678")
        session.task = asyncio.create_task(session.run())
        await originating.wait()
        stop = asyncio.create_task(session.stop())
        await asyncio.sleep(0)
        assert not stop.done()
        assert not session.task.cancelled()
        release.set()
        await stop
        phone_session.end_phone_call.assert_awaited_once_with("CA_test")
        assert session.snapshot()["active"] is False
        assert session.snapshot()["status"] == "stopped"

    asyncio.run(run())


def test_remote_stop_failure_can_be_retried(monkeypatch):
    phone_session.end_phone_call.side_effect = [RuntimeError("hangup failed"), None]
    agent, _ = make_waiting_agent()
    monkeypatch.setattr(clawops_phone, "build_agent", lambda **kwargs: (agent, "07000000000"))
    with TestClient(main.app) as client:
        client.post("/api/clawops/start", json={"mode": "outbound", "toNumber": "01012345678"})
        assert client.post("/api/clawops/stop").status_code == 502
        assert client.get("/api/clawops/status").json()["active"] is True
        assert client.post("/api/clawops/stop").status_code == 200
        assert client.get("/api/clawops/status").json()["active"] is False


def test_inbound_call_events_return_to_listening():
    async def run():
        agent, _ = make_waiting_agent()
        session = phone_session.PhoneSession(agent, "07000000000")
        session.task = asyncio.create_task(session.run())
        await asyncio.sleep(0)
        call = SimpleNamespace(call_id="CA_inbound", status="ringing")
        await session._call_started(call)
        assert session.snapshot()["status"] == "in-progress"
        await session._call_ended(call)
        assert session.snapshot()["status"] == "listening"
        assert session.snapshot()["callId"] is None
        await session.stop()

    asyncio.run(run())


def test_connection_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(phone_session, "START_TIMEOUT", 0.01)

    async def run():
        agent, _ = make_waiting_agent()

        async def never_connect():
            await asyncio.Event().wait()

        agent.connect.side_effect = never_connect
        session = phone_session.PhoneSession(agent, "07000000000", "01012345678")
        session.task = asyncio.create_task(session.run())
        await session.task
        assert session.snapshot()["status"] == "failed"
        assert "초과" in session.snapshot()["error"]
        agent.call.assert_not_awaited()
        agent.disconnect.assert_awaited_once()

    asyncio.run(run())


def test_stop_does_not_interrupt_cleanup_of_an_ended_call():
    async def run():
        cleaning = asyncio.Event()
        release = asyncio.Event()
        agent, _ = make_waiting_agent()
        call = agent.call.return_value

        async def ended():
            call.status = "completed"

        async def disconnect():
            cleaning.set()
            await release.wait()

        call.wait = ended
        agent.disconnect.side_effect = disconnect
        session = phone_session.PhoneSession(agent, "07000000000", "01012345678")
        session.task = asyncio.create_task(session.run())
        await cleaning.wait()
        stop = asyncio.create_task(session.stop())
        await asyncio.sleep(0)
        assert not stop.done()
        release.set()
        await stop
        assert session.snapshot()["status"] == "completed"
        assert not session.task.cancelled()

    asyncio.run(run())
