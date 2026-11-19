#!/usr/bin/env python3
"""不晚规范站本地预览：和 python3 -m http.server 一样，但告诉浏览器不要缓存，
改完文件刷新一下就是最新版本（规范站里内嵌的页面预览也一样）。

用法（在 design/buwan-ds 目录里）：
    python3 scripts/serve.py          # 默认端口 8765
    python3 scripts/serve.py 9000
"""
import http.server
import os
import sys


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)
    print(f"不晚规范站：http://localhost:{port}/")
    http.server.ThreadingHTTPServer(("", port), NoCacheHandler).serve_forever()
