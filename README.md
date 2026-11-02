# IdealChat (Python raw TCP sockets + web UI)

Files: `server.py` (server), `protocol.py` (hand-written HTTP + WebSocket on raw sockets), `web/` (the client: `index.html`, `app.js`, `style.css`). Python 3.8+ stdlib only, nothing to install. The client runs in any modern browser.

## Run
```
python server.py --port 5000          # on the server machine (LAN PC or cloud VM)
```
Then open `http://localhost:5000` in a browser (or `http://<server-ip>:5000` from another computer), type a unique name and press Connect. Open the page on a second computer, with a different name, to chat.

Cloud: open inbound TCP 5000 in the VM firewall/security group, then clients browse to `http://<public-ip>:5000`.
LAN fallback: server's IPv4 from `ipconfig`; allow Python through Windows Firewall.

## Architecture (R1)
```
 Browser A (PC 1) --TCP--+
                         +-- server.py (cloud VM / 3rd PC, port 5000)
 Browser B (PC 2) --TCP--+
```
Star topology: all messages go client -> server -> recipients. One port serves both the web page (HTTP) and the chat (WebSocket). The server holds `clients` (name -> connection), `groups` (group -> members) and `group_history` (group -> last 500 messages).

## Protocols (what / why / how)
- **TCP** - reliable, ordered byte stream; every client has one persistent connection, opened with `socket.SOCK_STREAM` (`server.py main()`).
- **IP** - addresses and routes between the PCs and the server; browsers connect to the server's (public) IP.
- **Port 5000** - identifies the chat server process on the host (`bind`).
- **HTTP** - a browser can only load a web page and open a WebSocket through HTTP, so the first request on each connection is plain HTTP: `GET /` returns `index.html`, `app.js`, `style.css` (`serve_static`).
- **WebSocket (RFC 6455)** - a browser cannot open a raw TCP socket. WebSocket is the standard way for a browser to keep a TCP connection open and exchange messages in both directions, so the server can push messages instantly instead of the client polling. It starts as an HTTP request with `Upgrade: websocket`; the server answers `101 Switching Protocols` and from then on both sides send frames. We implemented the handshake and the frame format ourselves in `protocol.py` (`accept_websocket`, `WSConn.send/recv`) using only the `socket` module - no HTTP or WebSocket library.
- **Application messages** - each chat message is one JSON object in one WebSocket text frame (table below).

## Message types
| type | direction | fields | req |
|---|---|---|---|
| `login` / `login_ok` / `login_error` | C->S / S->C | name / reason | R3 |
| `users` | S->C (broadcast on join/leave) | users[] | R4 |
| `pm` | C->S (`to`,`text`); S->C to receiver + sender echo (`from`,`to`,`text`,`ts`) | | R7 |
| `create_group` | C->S | group (creator is the only member) | R8 |
| `list_groups` / `groups` | C->S / S->C (also broadcast on change) | groups[{name, members}] | R9 |
| `join_group` | C->S | group (only adds the requester) | R10 |
| `group_msg` | C->S (`group`,`text`); S->C members only | | R11 |
| `group_history` | S->C to the joiner only, right after `join_group` | group, messages[] (latest 500) | R9/R11 |
| `error` | S->C | text | |

## Requirement map
R1/R2: `server.py main()` (`socket/bind/listen/accept`), `protocol.py` (`recv/sendall` + WebSocket frames), `web/app.js connect()`. R3: `login()` in server.py, login form in `web/index.html`. R4: `broadcast_users`, `renderUsers`. R5/R6: `ensureRoom` in `web/app.js` (one tab per chat, each with a chat window + chat box). R7-R11: `handle()` in server.py, `renderGroups`/`ensureRoom` in `web/app.js`.

Message text is always inserted into the page with `textContent` (never as HTML), so a message such as `<script>` is displayed literally.
