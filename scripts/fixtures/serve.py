#!/usr/bin/env python3
"""靶场静态服务器：ThreadingHTTPServer + HTTP/1.1 keep-alive。

python -m http.server 的 HTTP/1.0 短连接会让 Playwright route.fetch
socket hang up（第七阶段实测），导致 route 改写静默跳过。
用法：python3 serve.py <port> <directory>
"""

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1])
    directory = sys.argv[2]
    server = ThreadingHTTPServer(
        ("127.0.0.1", port), partial(Handler, directory=directory))
    server.serve_forever()
