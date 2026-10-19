from __future__ import annotations

import subprocess
import sys
import os
import time
import shutil
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def resolve_node() -> str:
    """Find Node.js even when PyCharm does not inherit the terminal PATH."""
    configured = os.environ.get("NODE_EXE")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_file():
            raise FileNotFoundError(f"NODE_EXE 指向的文件不存在：{candidate}")
        return str(candidate)
    on_path = shutil.which("node")
    if on_path:
        return on_path
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs/node.exe",
        Path.home() / "AppData/Local/Programs/nodejs/node.exe",
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError("找不到 Node.js，请安装 Node.js，或在 PyCharm 运行配置中设置 NODE_EXE 为 node.exe 的完整路径。")



def start_process(
    name: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    print(f"启动 {name}: {' '.join(command)}")
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    return subprocess.Popen(command, cwd=str(cwd), env=process_env)


def wait_for_backend(process: subprocess.Popen, timeout: float = 180) -> None:
    print("等待后端就绪：http://127.0.0.1:12024/health（调试时请确认后端未停在断点）", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise SystemExit(f"backend exited: {code}，请查看上方后端报错。")
        try:
            with urlopen("http://127.0.0.1:12024/health", timeout=1) as response:
                if response.status == 200:
                    print("后端已就绪，开始启动前端。", flush=True)
                    return
        except (URLError, OSError):
            pass
        time.sleep(0.5)
    raise TimeoutError("后端启动超时：请检查 PyCharm 断点、调试连接以及后端报错。")


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Windows 虚拟环境启动器会创建 Python 子进程，必须一起停止。
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    if process.poll() is None:
        process.terminate()


def main() -> None:
    """同时启动课程版后端和前端。

    后端运行在 http://127.0.0.1:12024。
    前端运行在 http://127.0.0.1:3000。
    按 Ctrl+C 会同时停止两个进程。
    """

    node_exe = resolve_node()
    vite_script = PROJECT_ROOT / "ui/node_modules/vite/bin/vite.js"
    if not vite_script.is_file():
        raise FileNotFoundError("缺少前端依赖，请先在 ui 目录运行 npm install。")
    processes: list[subprocess.Popen] = []
    try:
        backend = start_process(
            "backend",
            [
                str(PYTHON_EXE),
                "-m",
                "uvicorn",
                "agent.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "12024",
            ],
            PROJECT_ROOT,
        )
        processes.append(backend)
        wait_for_backend(backend)
        frontend = start_process(
            "frontend",
            [node_exe, str(vite_script), "dev", "--host", "127.0.0.1", "--port", "3000", "--strictPort"],
            PROJECT_ROOT / "ui",
            env={"VITE_DASHBOARD_API_BASE_URL": "http://127.0.0.1:12024"},
        )

        processes.append(frontend)
        while True:
            backend_code = backend.poll()
            frontend_code = frontend.poll()
            if backend_code is not None:
                raise SystemExit(f"backend exited: {backend_code}")
            if frontend_code is not None:
                raise SystemExit(f"frontend exited: {frontend_code}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("正在停止服务...")
    finally:
        # 先停止 start_all.py 直接创建的子进程。
        for process in reversed(processes):
            stop_process(process)
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
