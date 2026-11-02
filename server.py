"""IdealChat server: raw TCP sockets, one thread per connection.

One port serves both the web page (plain HTTP) and the chat (WebSocket upgrade on /ws).
"""
import argparse
import os
import socket
import threading
import time

from protocol import (DEFAULT_PORT, accept_websocket, is_websocket_upgrade, read_http_request,
                      send_http)

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# only these files are served (no path traversal possible)
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_NAME = 30  # max length of a user name / group name

lock = threading.Lock()
# R3/R4: connected clients, keyed by their unique name
clients = {}  # name -> WSConn
# R8/R9/R10/R11: all chat groups and their members
groups = {}  # group name -> list of member names (insertion order)
# R9/R11: past messages of each group, replayed to a client when it joins that group
group_history = {}  # group name -> list of group_msg dicts
MAX_HISTORY = 500  # keep only the latest messages per group


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


# R2: every message to a client goes out through its TCP socket (WSConn.send writes WebSocket frames)
def send_to(name, msg):
    """Send to one client by name; drop silently if it vanished."""
    with lock:
        conn = clients.get(name)
    if conn is None:
        return
    try:
        conn.send(msg)
    except OSError:
        pass


# R9: every group with its member list, sent to clients
def groups_payload():
    with lock:
        return [{"name": g, "members": list(m)} for g, m in groups.items()]


# R4: push the list of all connected names (including the receiver's own) to every client
def broadcast_users():
    with lock:
        names = list(clients)
    for n in names:
        send_to(n, {"type": "users", "users": names})


# R9: push the updated group list (with members) to every client whenever it changes
def broadcast_groups():
    payload = {"type": "groups", "groups": groups_payload()}
    with lock:
        names = list(clients)
    for n in names:
        send_to(n, payload)


def login(conn):
    """Loop until the client picks a free name. Returns the name."""
    # R3: each client must have a unique name
    while True:
        msg = conn.recv()
        if msg.get("type") != "login":
            conn.send({"type": "login_error", "reason": "Login required"})
            continue
        name = str(msg.get("name", "")).strip()
        if not name:
            conn.send({"type": "login_error", "reason": "Name cannot be empty"})
            continue
        if len(name) > MAX_NAME:
            conn.send({"type": "login_error", "reason": f"Name is too long (max {MAX_NAME})"})
            continue
        # R3: reject a name already in use (case-insensitive); otherwise register it
        with lock:
            taken = any(n.lower() == name.lower() for n in clients)
            if not taken:
                clients[name] = conn
        if taken:
            conn.send({"type": "login_error", "reason": f"Name '{name}' is already in use"})
            continue
        send_to(name, {"type": "login_ok", "name": name})
        return name


def handle(name, msg):
    t = msg.get("type")
    ts = time.strftime("%H:%M:%S")

    # R7: private message - delivered only to the receiver, plus an echo to the sender
    if t == "pm":
        to, text = msg.get("to"), str(msg.get("text", ""))
        with lock:
            online = to in clients
        if not online:
            return send_to(name, {"type": "error", "text": f"User '{to}' is not online"})
        if not text.strip():
            return send_to(name, {"type": "error", "text": "Message cannot be empty"})
        out = {"type": "pm", "from": name, "to": to, "text": text, "ts": ts}
        send_to(to, out)
        if to != name:
            send_to(name, out)

    # R8: create a group; the creator is its only initial member
    elif t == "create_group":
        g = str(msg.get("group", "")).strip()
        if not g:
            return send_to(name, {"type": "error", "text": "Group name cannot be empty"})
        if len(g) > MAX_NAME:
            return send_to(name, {"type": "error", "text": f"Group name is too long (max {MAX_NAME})"})
        with lock:
            exists = any(x.lower() == g.lower() for x in groups)
            if not exists:
                groups[g] = [name]  # creator is the only member
                group_history[g] = []
        if exists:
            return send_to(name, {"type": "error", "text": f"Group '{g}' already exists"})
        log(f"{name} created group {g}")
        broadcast_groups()

    # R9: any client can request all existing groups with their members
    elif t == "list_groups":
        send_to(name, {"type": "groups", "groups": groups_payload()})

    # R10: a client joins only by itself - the server adds the requester's own name, never anyone else
    elif t == "join_group":
        g = msg.get("group")
        with lock:
            if g not in groups:
                err = f"Group '{g}' does not exist"
            elif name in groups[g]:
                err = f"You are already in '{g}'"
            else:
                groups[g].append(name)
                history = list(group_history[g])  # snapshot taken atomically with the join
                err = None
        if err:
            return send_to(name, {"type": "error", "text": err})
        log(f"{name} joined group {g}")
        # R9/R11: the new member (and only the new member) gets the group's earlier messages
        send_to(name, {"type": "group_history", "group": g, "messages": history})
        broadcast_groups()

    # R11: group message - the sender must be a member, and only members receive it
    elif t == "group_msg":
        g, text = msg.get("group"), str(msg.get("text", ""))
        out = {"type": "group_msg", "group": g, "from": name, "text": text, "ts": ts}
        with lock:
            members = list(groups.get(g, []))
            if name in members and text.strip():
                # stored in the same lock as the member snapshot, so a concurrent join never misses it
                group_history[g].append(out)
                del group_history[g][:-MAX_HISTORY]
        if name not in members:
            return send_to(name, {"type": "error", "text": f"You are not a member of '{g}'"})
        if not text.strip():
            return send_to(name, {"type": "error", "text": "Message cannot be empty"})
        for m in members:
            send_to(m, out)


def serve_static(sock, method, path):
    """Plain HTTP: hand out the web client (index.html, app.js, style.css)."""
    if method != "GET":
        return send_http(sock, 405, b"Method not allowed")
    if path == "/favicon.ico":
        return send_http(sock, 204)
    entry = STATIC.get(path)
    if entry is None:
        return send_http(sock, 404, b"Not found")
    filename, content_type = entry
    try:
        with open(os.path.join(WEB_DIR, filename), "rb") as f:
            body = f.read()
    except OSError:
        return send_http(sock, 404, b"Not found")
    send_http(sock, 200, body, content_type)


def client_thread(sock, addr):
    name = None
    conn = None
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)  # notice dead peers
        method, path, headers = read_http_request(sock)
        if not is_websocket_upgrade(headers):
            return serve_static(sock, method, path)
        conn = accept_websocket(sock, headers)
        if conn is None:
            return
        name = login(conn)
        log(f"{name} connected from {addr[0]}:{addr[1]}")
        # R4: tell everyone (including the new client) who is online
        broadcast_users()
        # R9: a client that connects later still sees the existing groups
        send_to(name, {"type": "groups", "groups": groups_payload()})
        # R2: read client requests from the TCP socket
        while True:
            msg = conn.recv()
            try:
                handle(name, msg)
            except (TypeError, AttributeError, KeyError):  # malformed request: reject, keep connection
                send_to(name, {"type": "error", "text": "Malformed request"})
    except (OSError, ValueError):
        pass  # disconnect, malformed frame or bad HTTP request
    finally:
        if name:
            # R4/R9: on disconnect, drop the client from the online list and from every group
            with lock:
                clients.pop(name, None)
                for members in groups.values():
                    if name in members:
                        members.remove(name)
            log(f"{name} disconnected")
            broadcast_users()
            broadcast_groups()
        if conn:
            conn.close()
        else:
            try:
                sock.close()
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    # R1/R2: TCP server socket bound to a port; chat uses socket programming only
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen()
    log(f"IdealChat server listening on {args.host}:{args.port}")
    log(f"Open http://localhost:{args.port} (or http://<this-computer-ip>:{args.port} from another computer)")
    try:
        # R1: accept clients from different computers; one thread serves each connection
        while True:
            sock, addr = srv.accept()
            threading.Thread(target=client_thread, args=(sock, addr), daemon=True).start()
    except KeyboardInterrupt:
        log("Shutting down")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
