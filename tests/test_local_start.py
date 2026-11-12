"""日常启动的依赖失败与就绪门槛，不访问 Docker、浏览器或模型。"""

from __future__ import annotations

import io
import json
import threading
import urllib.error
from types import SimpleNamespace

import pytest

from app import local_start


def ready_health():
    return {
        "status": "ready",
        "agent": "up",
        "analysis_database": "up",
        "session_store": "up",
        "document_database": "up",
        "document_retrieval": "up",
        "dataset_imports": "up",
        "analysis_history": "up",
    }


def test_optional_modules_disabled_at_start_are_not_ready():
    health = ready_health()
    health.update(status="degraded", document_database="down", analysis_history="disabled")
    assert not local_start.is_ready(health, ["document_database", "analysis_history"])
    assert local_start.is_ready(health, ["analysis_database", "session_store"])
    assert not local_start.is_ready(None, [])


def test_docker_desktop_found_in_per_user_cli_installation(tmp_path):
    installation = tmp_path / "Programs" / "DockerDesktop"
    desktop = installation / "Docker Desktop.exe"
    desktop.parent.mkdir(parents=True)
    desktop.touch()
    cli = installation / "resources" / "bin" / "docker.exe"
    assert local_start.find_docker_desktop(str(cli)) == desktop


def test_missing_docker_engine_is_a_blocking_error(monkeypatch):
    monkeypatch.setattr(local_start, "docker_ready", lambda _: False)
    monkeypatch.setattr(local_start, "find_docker_desktop", lambda _: None)
    with pytest.raises(local_start.StartupError, match="Docker 未运行"):
        local_start.ensure_docker("offline-docker")


def test_docker_wakeup_uses_hidden_process_and_literal_path(tmp_path, monkeypatch):
    desktop = tmp_path / "custom install's directory" / "Docker Desktop.exe"
    statuses = iter([False, True])
    monkeypatch.setattr(local_start.sys, "platform", "win32")
    monkeypatch.setattr(local_start, "docker_ready", lambda _: next(statuses))
    monkeypatch.setattr(local_start, "find_docker_desktop", lambda _: desktop)
    calls = []

    def start_desktop(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(local_start.subprocess, "run", start_desktop)
    local_start.ensure_docker("offline-docker")
    args, kwargs = calls[0]
    assert str(desktop) not in args
    assert "-WindowStyle Hidden" in args[-1]
    assert kwargs["env"]["DATA_ANALYST_DOCKER_DESKTOP"] == str(desktop)


def test_docker_desktop_launch_failure_stops_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(local_start.sys, "platform", "win32")
    monkeypatch.setattr(local_start, "docker_ready", lambda _: False)
    monkeypatch.setattr(
        local_start, "find_docker_desktop", lambda _: tmp_path / "Docker Desktop.exe"
    )
    monkeypatch.setattr(
        local_start.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1)
    )
    with pytest.raises(local_start.StartupError, match="无法自动打开"):
        local_start.ensure_docker("offline-docker")


def test_dependencies_wait_locally_and_fail_closed(tmp_path, monkeypatch):
    (tmp_path / ".env.phase2").touch()
    monkeypatch.setattr(local_start, "PROJECT_ROOT", tmp_path)
    calls = []

    def fail_compose(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(local_start.subprocess, "run", fail_compose)
    with pytest.raises(local_start.StartupError, match="工作台未启动"):
        local_start.start_dependencies("offline-docker")
    args, kwargs = calls[0]
    assert "--wait" in args and "--wait-timeout" in args
    assert args[args.index("--pull") + 1] == "never"
    assert args[-2:] == ["postgres", "redis"]
    assert kwargs["cwd"] == tmp_path
    assert "--build" not in args and "app" not in args


def test_read_health_handles_503_json_without_proxy(monkeypatch):
    url = "http://127.0.0.1:18080/api/v1/health/ready"
    body = {"status": "unavailable", "analysis_database": "down"}

    def open_request(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            url, 503, "Unavailable", {}, io.BytesIO(json.dumps(body).encode())
        )

    def build_opener(proxy):
        assert proxy.proxies == {}
        return SimpleNamespace(open=open_request)

    monkeypatch.setattr(local_start.urllib.request, "build_opener", build_opener)
    assert local_start.read_health(url) == body


@pytest.fixture()
def prepared_launch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.phase2").touch()
    monkeypatch.setattr(local_start, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(local_start.shutil, "which", lambda _: "offline-docker")
    monkeypatch.setattr(local_start, "ensure_docker", lambda _: None)
    monkeypatch.setattr(local_start, "start_dependencies", lambda _: None)
    monkeypatch.setattr(
        "app.config.Settings",
        lambda: SimpleNamespace(
            documents_enabled=True,
            datasets_enabled=True,
            analysis_history_enabled=True,
            api_prefix="/api/v1",
        ),
    )


def test_dependency_failure_never_starts_app(prepared_launch, monkeypatch, capsys):
    def fail_dependencies(_docker):
        raise local_start.StartupError("offline dependency failure")

    monkeypatch.setattr(local_start, "start_dependencies", fail_dependencies)
    monkeypatch.setattr(
        local_start, "port_in_use", lambda _: pytest.fail("must stop before app checks")
    )
    assert local_start.main(["--no-browser"]) == 1
    assert "offline dependency failure" in capsys.readouterr().err


def test_existing_degraded_app_requires_restart(prepared_launch, monkeypatch, capsys):
    monkeypatch.setattr(local_start, "port_in_use", lambda _: True)
    health = ready_health()
    health.update(status="degraded", document_database="down", analysis_history="disabled")
    monkeypatch.setattr(local_start, "read_health", lambda _: health)
    assert local_start.main(["--no-browser"]) == 1
    assert "原启动窗口按 Ctrl+C" in capsys.readouterr().err


def test_existing_healthy_app_is_reused(prepared_launch, monkeypatch):
    monkeypatch.setattr(local_start, "port_in_use", lambda _: True)
    monkeypatch.setattr(local_start, "read_health", lambda _: ready_health())
    monkeypatch.setattr(
        local_start.webbrowser, "open", lambda _: pytest.fail("browser is disabled")
    )
    assert local_start.main(["--no-browser"]) == 0


def test_browser_opens_only_after_full_readiness(monkeypatch):
    server = SimpleNamespace(started=True, should_exit=False)
    monkeypatch.setattr(local_start, "read_health", lambda _: ready_health())
    opened = []
    monkeypatch.setattr(local_start.webbrowser, "open", opened.append)
    errors = []
    local_start.watch_startup(
        server,
        "http://127.0.0.1:18080/custom/v2/health/ready",
        ["document_database", "analysis_history"],
        False,
        threading.Event(),
        errors,
    )
    assert opened == ["http://127.0.0.1:18080/"]
    assert not errors and not server.should_exit


def test_partial_startup_times_out_without_opening_browser(monkeypatch):
    server = SimpleNamespace(started=True, should_exit=False)
    health = ready_health()
    health.update(status="degraded", document_database="down")
    monkeypatch.setattr(local_start, "read_health", lambda _: health)
    clock = iter([0, 0, 2])
    monkeypatch.setattr(local_start.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(local_start.webbrowser, "open", lambda _: pytest.fail("not ready"))
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda _: None)
    errors = []
    local_start.watch_startup(
        server, "http://localhost/ready", ["document_database"], False, stop, errors, 1
    )
    assert server.should_exit and len(errors) == 1
