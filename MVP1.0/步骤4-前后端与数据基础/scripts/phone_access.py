"""Explicit LAN access to the existing loopback app; no second app/database worker.

Served over HTTPS with a certificate this Mac generates for its own LAN address:
browsers only allow the microphone (voice input, roadmap 2.6) on HTTPS pages.
The certificate is self-signed, so a phone shows a warning the first time and
the user chooses to continue. The private key never leaves runtime/."""
import argparse
import fcntl
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import Config
from backend.asr import body_limit
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


def certificate(address, folder):
    """A key and self-signed certificate for exactly this LAN address, reused until it nears expiry."""
    folder.mkdir(parents=True, exist_ok=True)
    cert, key = folder / (address + '.pem'), folder / (address + '.key')
    if cert.is_file() and key.is_file() and time.time() - cert.stat().st_mtime < 300 * 86400:
        return cert, key
    settings = folder / (address + '.cnf')
    settings.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=' + address +
                        '\n[ext]\nsubjectAltName=IP:' + address + '\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n')
    old = os.umask(0o077)
    try:
        done = subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '397', '-sha256',
                               '-config', str(settings), '-keyout', str(key), '-out', str(cert)], capture_output=True)
    finally:
        os.umask(old)
        settings.unlink()
    if done.returncode or not cert.is_file() or not key.is_file():
        raise RuntimeError('无法生成手机入口的 HTTPS 证书。')
    return cert, key


def probe(origin, cert):
    # Trust only the certificate generated above; nothing else can answer as the gateway.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(cert))))
    try:
        with opener.open(origin + '/api/health', timeout=2) as r:
            return r.headers.get('Server', '').startswith('MVP1-Phone') and json.load(r).get('status') == 'ok'
    except (OSError, ValueError):
        return False


def make_gateway(address, port, upstream, cert=None, key=None):
    parsed = urlsplit(upstream)
    if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.scheme != 'http':
        raise RuntimeError('手机入口仅转发本机 HTTP 开发服务。')
    origin = ('https://' if cert else 'http://') + address + ':' + str(port)

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
                # Same limits as the app itself: small JSON everywhere, one larger WAV route for voice input.
                if not 0 <= size <= body_limit(self.command, self.path, self.headers.get('Content-Type')):
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

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # Failed TLS handshakes and dropped phones are routine; nothing about them is logged.

    server = Server((address, port), Handler)
    server.daemon_threads = True
    if cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(cert), str(key))
        # Handshake in the request thread, so one stalled phone cannot block new connections.
        server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
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
    origin = 'https://' + address + ':' + str(port)
    cert, key = certificate(address, ROOT / 'runtime' / 'phone-cert')
    if args.serve:
        server = make_gateway(address, port, config.origin, cert, key)
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return
    ensure_server(config)
    runtime = ROOT / 'runtime'
    with (runtime / 'phone-access.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not probe(origin, cert):
            # A gateway left from another network or from the earlier plain-HTTP version holds the port: replace it.
            try:
                old = int((runtime / 'phone-access.pid').read_text().strip())
                if 'phone_access.py' in subprocess.run(['ps', '-p', str(old), '-o', 'command='], capture_output=True, text=True).stdout:
                    os.kill(old, 15)
                    time.sleep(.5)
            except (OSError, ValueError):
                pass
            log = runtime / 'logs/phone-access.out.log'
            with log.open('ab') as output:
                process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--serve', '--address', address],
                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    close_fds=True, start_new_session=True)
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError('手机入口启动失败，端口可能被占用。日志：' + str(log))
                if probe(origin, cert):
                    break
                time.sleep(.2)
            else:
                process.terminate()
                raise RuntimeError('手机入口启动超时。日志：' + str(log))
            (runtime / 'phone-access.pid').write_text(str(process.pid) + '\n')
        (runtime / 'phone-access.json').write_text(json.dumps({'origin': origin, 'upstream': config.origin}, ensure_ascii=False) + '\n')
    print('手机访问链接：' + origin + '/', flush=True)
    print('手机和电脑需连接同一 Wi-Fi / 热点；电脑保持开机。网络变化后重新双击本入口获取新链接。', flush=True)
    print('第一次打开会提示“连接不是私密连接”：这是本机自己签发的证书，点“高级 → 继续访问”即可。使用 https 是为了让手机能用话筒。', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print('启动提示：' + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
