# IdealChat (Python, raw TCP + Tkinter)

Files: `server.py` (server), `client.py` (GUI client), `protocol.py` (shared framing). Python 3.8+ stdlib only.

## Run
```
python server.py --port 5000          # on the server machine (LAN PC or cloud VM)
python client.py                      # on each client PC: enter server IP, port, unique name
```
Cloud: open inbound TCP 5000 in the VM firewall/security group, then clients connect to `<public-ip>:5000`.
LAN fallback: server's IPv4 from `ipconfig`; allow Python through Windows Firewall.

## Architecture (R1)
```
 Client A (PC 1) ──TCP──┐
                        ├── Server (cloud VM / 3rd PC, port 5000)
 Client B (PC 2) ──TCP──┘
```
Star topology: all messages go client -> server -> recipients. The server holds `clients` (name -> socket) and `groups` (group -> members).

## Protocols (what / why / how)
- **TCP** — reliable, ordered byte stream, which chat needs; used via `socket.SOCK_STREAM`, one persistent connection per client.
- **IP** — addresses and routes between the PCs and the server; clients connect to the server's (public) IP.
- **Port 5000** — identifies the chat server process on the host (`bind`/`connect`).
- **Application protocol (ours)** — TCP has no message boundaries, so each message is `4-byte big-endian length + UTF-8 JSON`.

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
R2 sockets only: `protocol.py`, `server.py` (`socket/bind/listen/accept/recv/sendall`). R3: `login()` in server.py. R4: `broadcast_users`. R5/R6: `open_room` in client.py (one Notebook tab per chat, each with a chat window + chat box). R7-R11: `handle()` in server.py.
