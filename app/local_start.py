"""Windows 本机完整模式入口：先等待依赖，再启动并检查工作台。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StartupError(RuntimeError):
    """可直接展示、且不含配置秘密的启动错误。"""


def docker_ready(docker: str) -> bool:
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_docker_desktop(docker: str) -> Path | None:
    candidates = [Path(docker).resolve().parent.parent.parent / "Docker Desktop.exe"]
    for variable, suffix in (
        ("LOCALAPPDATA", "Programs/DockerDesktop/Docker Desktop.exe"),
        ("ProgramFiles", "Docker/Docker/Docker Desktop.exe"),
    ):
        if location := os.environ.get(variable):
            candidates.append(Path(location) / suffix)
    return next((path for path in candidates if path.is_file()), None)


def ensure_docker(docker: str) -> None:
    if docker_ready(docker):
        return
    desktop = find_docker_desktop(docker)
    if sys.platform != "win32" or desktop is None:
        raise StartupError("Docker 未运行。请先打开 Docker Desktop，等待引擎就绪后重试。")
    print("Docker 未运行，正在打开 Docker Desktop；首次唤醒可能需要一两分钟……", flush=True)
    # 路径通过环境变量传递，不拼接 PowerShell 命令，也不改变用户已有配置。
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Start-Process -FilePath $env:DATA_ANALYST_DOCKER_DESKTOP -WindowStyle Hidden",
        ],
        env={**os.environ, "DATA_ANALYST_DOCKER_DESKTOP": str(desktop)},
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise StartupError("Docker Desktop 无法自动打开，请手动启动它后重试。")
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if docker_ready(docker):
            return
        time.sleep(2)
    raise StartupError("Docker 引擎在 150 秒内未就绪，请检查 Docker Desktop 的启动提示。")


def start_dependencies(docker: str) -> None:
    if not (PROJECT_ROOT / ".env.phase2").is_file():
        raise StartupError("缺少 .env.phase2。此入口用于已完成 README 初始化的本机完整模式。")
    print("正在启动 PostgreSQL 和 Redis，并等待健康检查通过……", flush=True)
    result = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            ".env.phase2",
            "--profile",
            "postgres",
            "--profile",
            "redis",
            "up",
            "-d",
            "--no-deps",
            "--wait",
            "--wait-timeout",
            "90",
            "--pull",
            "never",
            "postgres",
            "redis",
        ],
        cwd=PROJECT_ROOT,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise StartupError("PostgreSQL / Redis 未能就绪，请检查上方 Docker 日志。工作台未启动。")


def read_health(url: str) -> dict[str, Any] | None:
    # 本机探测绕过系统代理，不向模型或外部地址发请求。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        try:
            response = opener.open(url, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            value = json.loads(response.read(65536))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def is_ready(health: dict[str, Any] | None, required: list[str]) -> bool:
    return bool(
        health
        and health.get("status") in {"ready", "degraded"}
        and health.get("agent") == "up"
        and all(health.get(name) == "up" for name in required)
    )


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def watch_startup(
    server: Any,
    health_url: str,
    required: list[str],
    no_browser: bool,
    stop: threading.Event,
    errors: list[str],
    timeout: float = 60,
) -> None:
    deadline = time.monotonic() + timeout
    while not stop.is_set() and time.monotonic() < deadline:
        if server.started and is_ready(read_health(health_url), required):
            if not stop.is_set():
                print(
                    "工作台及已启用的数据服务均已就绪。保持此窗口打开，按 Ctrl+C 停止。", flush=True
                )
                if not no_browser:
                    address = urlsplit(health_url)
                    try:
                        webbrowser.open(f"{address.scheme}://{address.netloc}/")
                    except (OSError, webbrowser.Error):
                        print("浏览器未能自动打开，请手动访问上方工作台地址。", flush=True)
            return
        stop.wait(0.5)
    if not stop.is_set():
        errors.append(f"工作台在 {timeout:g} 秒内未完全就绪，请查看上方启动日志。未打开浏览器。")
        server.should_exit = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("端口须在 1024 到 65535 之间")
    os.chdir(PROJECT_ROOT)

    try:
        from pydantic import ValidationError

        from app.config import Settings

        try:
            settings = Settings()
        except ValidationError:
            raise StartupError(
                "本机配置格式有误，请检查 .env 和 .env.phase2；不要公开其中的 Key 或密码。"
            ) from None
        docker = shutil.which("docker")
        if docker is None:
            raise StartupError("未找到 Docker，请安装或打开 Docker Desktop 后重试。")
        if not (PROJECT_ROOT / ".env.phase2").is_file():
            raise StartupError("缺少 .env.phase2，请先完成 README 中的完整模式初始化。")
        ensure_docker(docker)
        start_dependencies(docker)

        required = ["analysis_database", "session_store"]
        if settings.documents_enabled:
            required.extend(["document_database", "document_retrieval"])
        if settings.datasets_enabled:
            required.append("dataset_imports")
        if settings.analysis_history_enabled:
            required.append("analysis_history")
        url = f"http://127.0.0.1:{args.port}"
        health_url = f"{url}{settings.api_prefix.rstrip('/')}/health/ready"
        if port_in_use(args.port):
            if not is_ready(read_health(health_url), required):
                raise StartupError(
                    f"端口 {args.port} 已有未就绪的服务。请在原启动窗口按 Ctrl+C，再重新运行本入口。"
                )
            print(f"助手已在运行：{url}/", flush=True)
            if not args.no_browser:
                webbrowser.open(url + "/")
            return 0

        from uvicorn import Config, Server

        print(f"正在启动工作台：{url}/", flush=True)
        server = Server(Config("app.main:app", host="127.0.0.1", port=args.port))
        stop = threading.Event()
        errors: list[str] = []
        watcher = threading.Thread(
            target=watch_startup,
            args=(server, health_url, required, args.no_browser, stop, errors),
            daemon=True,
        )
        watcher.start()
        try:
            server.run()
        finally:
            stop.set()
            watcher.join(timeout=5)
        if errors:
            raise StartupError(errors[0])
        if not server.started:
            raise StartupError("工作台未能启动，请查看上方应用日志。")
        return 0
    except KeyboardInterrupt:
        return 0
    except StartupError as exc:
        print(f"\n启动未完成：{exc}", file=sys.stderr, flush=True)
        return 1
    except (OSError, subprocess.TimeoutExpired):
        print(
            "\n启动未完成：本机启动命令执行失败或超时，请检查 Docker Desktop 与文件路径。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
