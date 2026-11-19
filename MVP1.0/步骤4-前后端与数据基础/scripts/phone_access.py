"""Explicit LAN access to the existing loopback app; no second app/database worker."""
import argparse
import fcntl
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import Config
from launch_local import ensure_server


def lan_ip():
    for interface in ('en0', 'en1'):
        p = subprocess.run(['ipconfig', 'getifaddr', interface], capture_output=True, text=True)
        address = p.stdout.strip()
        try:
            value = ipaddress.ip_address(address)
            if value.version == 4 and value.is_private and not value.is_loopback and not value.is_link_local:
                return address
        except ValueError:
            pass
    raise RuntimeError('未找到局域网地址，请先让电脑连接 Wi-Fi 或手机热点。')


def probe(origin):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(origin + '/api/health', timeout=2) as r:
            return r.headers.get('Server', '').startswith('MVP1-Phone') and json.load(r).get('status') == 'ok'
    except (OSError, ValueError):
        return False


def make_gateway(address, port, upstream):
    parsed = urlsplit(upstream)
    if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.scheme != 'http':
        raise RuntimeError('手机入口仅转发本机 HTTP 开发服务。')
    origin = 'http://' + address + ':' + str(port)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'MVP1-Phone'
        sys_version = ''

        def log_message(self, *args):
            pass

        def request(self):
            # Keep the original app's Host/Origin and CSRF boundary meaningful.
            # Only this exact LAN origin is translated to its loopback equivalent.
            if self.headers.get('Host') != address + ':' + str(port):
                self.send_error(421, 'Unexpected host')
                return
            if not self.path.startswith('/') or self.path.startswith('//'):
                self.send_error(400)
                return
            request_origin = self.headers.get('Origin')
            if request_origin is not None and request_origin != origin:
                self.send_error(403, 'Cross-origin request rejected')
                return
            if self.command in ('POST', 'PATCH', 'DELETE', 'PUT') and request_origin != origin:
                self.send_error(403, 'Origin required')
                return
            if self.headers.get('Transfer-Encoding'):
                self.send_error(400)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 16384:
                    self.send_error(413)
                    return
                self.connection.settimeout(30)
                body = self.rfile.read(size)
                if len(body) != size:
                    self.send_error(400)
                    return
            except (ValueError, OSError):
                self.send_error(400)
                return
            headers = {name: self.headers[name] for name in ('Cookie', 'X-CSRF-Token', 'Content-Type', 'Accept', 'Sec-Fetch-Site') if name in self.headers}
            headers['Host'] = parsed.netloc
            if request_origin is not None:
                headers['Origin'] = upstream
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=90)
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                response = conn.getresponse()
            except OSError:
                conn.close()
                self.send_error(503, 'Local app unavailable; run the startup entry again')
                return
            try:
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in ('server', 'date', 'connection', 'transfer-encoding'):
                        self.send_header(name, value)
                self.send_header('Connection', 'close')
                self.end_headers()
                while True:
                    chunk = response.read1(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException):
                pass
            finally:
                conn.close()
                self.close_connection = True

        do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = do_OPTIONS = request

    server = ThreadingHTTPServer((address, port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--address')
    args = parser.parse_args()
    config = Config.from_env()
    address = args.address or lan_ip()
    value = ipaddress.ip_address(address)
    if value.version != 4 or not value.is_private or value.is_loopback or value.is_link_local:
        raise RuntimeError('仅允许电脑当前的局域网 IPv4 地址。')
    port = 8848
    origin = 'http://' + address + ':' + str(port)
    if args.serve:
        server = make_gateway(address, port, config.origin)
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return
    ensure_server(config)
    runtime = ROOT / 'runtime'
    with (runtime / 'phone-access.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not probe(origin):
            log = runtime / 'logs/phone-access.out.log'
            with log.open('ab') as output:
                process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--serve', '--address', address],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    close_fds=True, start_new_session=True)
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError('手机入口启动失败，端口可能被占用。日志：' + str(log))
                if probe(origin):
                    break
                time.sleep(.2)
            else:
                process.terminate()
                raise RuntimeError('手机入口启动超时。日志：' + str(log))
            (runtime / 'phone-access.pid').write_text(str(process.pid) + '\n')
        (runtime / 'phone-access.json').write_text(json.dumps({'origin': origin, 'upstream': config.origin}, ensure_ascii=False) + '\n')
    print('手机访问链接：' + origin + '/', flush=True)
    print('手机和电脑需连接同一 Wi-Fi / 热点；电脑保持开机。网络变化后重新双击本入口获取新链接。', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print('启动提示：' + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
