"""Shared wire layer: minimal HTTP + WebSocket (RFC 6455) written by hand on raw TCP sockets.

Browsers cannot open a raw TCP socket, so they talk to the server with the WebSocket
protocol, which starts as an HTTP request on a normal TCP connection and then "upgrades"
to framed messages. Everything here uses only the stdlib `socket` module (R2); there is no
HTTP or WebSocket library. Each chat message is one JSON object in one WebSocket text frame.
"""
import base64
import hashlib
import json
import struct
import threading

DEFAULT_PORT = 5000
MAX_MSG_SIZE = 1024 * 1024  # 1 MiB safety limit per message
MAX_HEADER_SIZE = 16 * 1024  # safety limit for the HTTP request head
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # fixed value from RFC 6455

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
REASONS = {101: "Switching Protocols", 200: "OK", 204: "No Content", 400: "Bad Request",
           404: "Not Found", 405: "Method Not Allowed"}


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf.extend(chunk)
    return bytes(buf)


# ---------- HTTP (request head + simple responses) ----------
def read_http_request(sock):
    """Read one HTTP request head. Returns (method, path, headers) with lower-case header names."""
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("connection closed")
        buf.extend(chunk)
        if len(buf) > MAX_HEADER_SIZE:
            raise ConnectionError("request head too large")
    lines = bytes(buf).split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise ConnectionError("bad request line")
    headers = {}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    return parts[0], parts[1].split("?", 1)[0], headers


def send_http(sock, status, body=b"", content_type="text/plain; charset=utf-8"):
    head = (f"HTTP/1.1 {status} {REASONS[status]}\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n")
    sock.sendall(head.encode("latin-1") + body)


def is_websocket_upgrade(headers):
    return ("websocket" in headers.get("upgrade", "").lower()
            and "upgrade" in headers.get("connection", "").lower())


# ---------- WebSocket ----------
def accept_websocket(sock, headers):
    """Do the server side of the opening handshake. Returns a WSConn, or None if the request is invalid."""
    key = headers.get("sec-websocket-key")
    if not key or headers.get("sec-websocket-version") != "13":
        send_http(sock, 400, b"Bad WebSocket handshake")
        return None
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    return WSConn(sock)


class WSConn:
    """One WebSocket connection: send()/recv() JSON objects. send() is safe from several threads."""

    def __init__(self, sock):
        self.sock = sock
        self._send_lock = threading.Lock()

    # R2: every message to the browser is written to the TCP socket as a WebSocket frame
    def _send_frame(self, opcode, payload):
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 127, n)  # server frames are never masked
        with self._send_lock:
            self.sock.sendall(head + payload)

    def send(self, obj):
        self._send_frame(OP_TEXT, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    # R2: every message from the browser is read from the TCP socket and decoded here
    def recv(self):
        """Block until one complete JSON object arrives. Raises OSError on close, ValueError on bad data."""
        data, first_opcode = bytearray(), None
        while True:
            b1, b2 = _recv_exact(self.sock, 2)
            fin, opcode, length = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if b1 & 0x70 or not b2 & 0x80:  # reserved bits set, or client frame not masked
                raise ConnectionError("protocol error")
            if length == 126:
                (length,) = struct.unpack("!H", _recv_exact(self.sock, 2))
            elif length == 127:
                (length,) = struct.unpack("!Q", _recv_exact(self.sock, 8))
            if length > MAX_MSG_SIZE or len(data) + length > MAX_MSG_SIZE:
                raise ConnectionError("message too large")
            mask = _recv_exact(self.sock, 4)
            payload = _recv_exact(self.sock, length)
            if length:  # unmask: XOR with the repeating 4-byte key
                key = (mask * (length // 4 + 1))[:length]
                payload = (int.from_bytes(payload, "big") ^ int.from_bytes(key, "big")).to_bytes(length, "big")

            if opcode >= OP_CLOSE:  # control frames
                if not fin or length > 125:
                    raise ConnectionError("protocol error")
                if opcode == OP_PING:
                    self._send_frame(OP_PONG, payload)
                elif opcode == OP_CLOSE:
                    try:
                        self._send_frame(OP_CLOSE, payload[:2])
                    except OSError:
                        pass
                    raise ConnectionError("closed by peer")
                continue  # pong: ignore

            if opcode == OP_CONT:
                if first_opcode is None:
                    raise ConnectionError("protocol error")
            elif opcode in (OP_TEXT, OP_BINARY):
                if first_opcode is not None:
                    raise ConnectionError("protocol error")
                first_opcode = opcode
            else:
                raise ConnectionError("protocol error")
            data.extend(payload)
            if not fin:
                continue
            if first_opcode == OP_BINARY:  # we only speak text frames: skip binary messages
                data, first_opcode = bytearray(), None
                continue
            msg = json.loads(data.decode("utf-8"))
            if not isinstance(msg, dict):
                raise ValueError("message must be a JSON object")
            return msg

    def close(self):
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
