"""Shared wire protocol: 4-byte big-endian length prefix + UTF-8 JSON body.

TCP is a byte stream with no message boundaries, so every message is framed
with its length. Only the stdlib `socket` module is used (R2).
"""
import json
import struct

DEFAULT_PORT = 5000
MAX_MSG_SIZE = 1024 * 1024  # 1 MiB safety limit
HEADER = struct.Struct(">I")


# R2: write one framed message to a TCP socket (sendall)
def send_msg(sock, obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sock.sendall(HEADER.pack(len(body)) + body)


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf.extend(chunk)
    return bytes(buf)


# R2: read one framed message from a TCP socket (recv until the full length has arrived)
def recv_msg(sock):
    (length,) = HEADER.unpack(_recv_exact(sock, HEADER.size))
    if length > MAX_MSG_SIZE:
        raise ConnectionError("message too large")
    return json.loads(_recv_exact(sock, length).decode("utf-8"))
