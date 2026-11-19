"""Double-click launcher: reuse or detach the local development server, then open its page."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import Config


def ready(origin):
    # Local health checks must not go through an inherited HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(origin + '/api/health', timeout=1) as response:
            if not response.headers.get('Server', '').startswith('MVP1-Dev'):
                return False
            data = json.load(response)
            return data.get('status') == 'ok' and data.get('capabilities', {}).get('accounts') is True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def occupied(config):
    try:
        with socket.create_connection((config.host, config.port), timeout=.5):
            return True
    except OSError:
        return False


def ensure_server(config):
    runtime = ROOT / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    logs = runtime / 'logs'
    logs.mkdir(exist_ok=True)
    log = logs / ('local-server-' + str(config.port) + '.out.log')
    lock_path = runtime / ('local-server-' + str(config.port) + '.lock')
    with lock_path.open('a') as lock:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('另一个启动入口仍在处理，请稍后重试。')
                time.sleep(.2)
        if ready(config.origin):
            print('本地服务已运行，直接打开。', flush=True)
            return
        if occupied(config):
            # A previously started copy may still be migrating or loading.
            for _ in range(15):
                time.sleep(.2)
                if ready(config.origin):
                    print('本地服务已就绪。', flush=True)
                    return
            raise RuntimeError('端口 ' + str(config.port) + ' 已被占用，但不是就绪的未晚服务；未停止其他程序。')
        print('正在后台启动本地服务…', flush=True)
        with log.open('ab') as output:
            process = subprocess.Popen(
                [sys.executable, '-B', '-m', 'backend.server'], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True,
            )
        pid_path = runtime / ('local-server-' + str(config.port) + '.pid')
        pid_path.write_text(str(process.pid) + '\n')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('服务启动未成功。启动日志：' + str(log))
            if ready(config.origin):
                print('本地服务已就绪；关闭启动窗口不会停止服务。', flush=True)
                return
            time.sleep(.2)
        # Do not leave an unready child competing with the next explicit launch.
        process.terminate()
        raise RuntimeError('等待服务启动超时。启动日志：' + str(log))


def main():
    parser = argparse.ArgumentParser(description='启动未晚本地开发环境并打开页面')
    parser.add_argument('--no-browser', action='store_true', help='仅检查/启动服务，不打开浏览器')
    args = parser.parse_args()
    os.chdir(ROOT)
    config = Config.from_env()
    parsed = urlsplit(config.origin)
    if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or (parsed.port or (443 if parsed.scheme == 'https' else 80)) != config.port:
        raise RuntimeError('APP_ORIGIN 必须指向同端口的本机开发服务，请检查本地配置。')
    print('未晚 MVP 1.1 · 一键启动', flush=True)
    ensure_server(config)
    print('体验入口：' + config.origin + '/', flush=True)
    if not args.no_browser:
        opened = subprocess.run(['open', config.origin + '/'], check=False)
        if opened.returncode:
            raise RuntimeError('服务已启动，但浏览器未能自动打开；请使用上方链接。')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        print('启动提示：' + str(error), file=sys.stderr, flush=True)
        sys.exit(1)
