from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .api import Application
from .config import Config


def make_server(app, host=None, port=None):
    class Handler(BaseHTTPRequestHandler):
        # This server is deliberately local-development only.
        server_version = 'MVP1-Dev'
        sys_version = ''

        def log_message(self, *args):
            pass  # Application emits sanitized JSON logs instead.

        def request(self):
            headers = dict(self.headers.items())
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0:
                    raise ValueError()
            except ValueError:
                self.send_error(400)
                return
            if self.headers.get('Transfer-Encoding'):
                self.send_error(400)
                return
            # Oversize requests are rejected without reading or retaining their full body.
            if length > 16384:
                raw = b'x'*16385
            else:
                self.connection.settimeout(10)
                try:
                    raw = self.rfile.read(length)
                except (TimeoutError, OSError):
                    self.send_error(408)
                    return
            response = app.handle(self.command, self.path, headers, raw, self.client_address[0])
            try:
                self.send_response(response.status)
                for key, value in response.headers.items():
                    self.send_header(key, value)
                if response.stream is None:
                    self.send_header('Content-Length', str(len(response.body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                if response.stream is not None:
                    self.wfile.flush()
                    for chunk in response.stream:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                else:
                    self.wfile.write(response.body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

        do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = do_OPTIONS = request

    server = ThreadingHTTPServer((host or app.config.host, app.config.port if port is None else port), Handler)
    server.daemon_threads = True
    return server


def main():
    config = Config.from_env()
    app = Application(config)
    server = make_server(app)
    print('MVP 1.1 开发环境：'+config.origin, flush=True)
    print('真实账号与数据库已启用；三角色模型与语音状态见页面。Ctrl+C 停止。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
