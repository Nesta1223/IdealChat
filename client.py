"""Chat client: raw TCP socket + Tkinter GUI."""
import queue
import socket
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from protocol import DEFAULT_PORT, recv_msg, send_msg


class ChatApp:
    def __init__(self, root):
        self.root = root
        self.sock = None
        self.name = None
        self.events = queue.Queue()
        self.rooms = {}  # ("pm", user) / ("group", name) -> dict(frame, log, entry)
        self.groups = []  # latest group list from server
        self.users = []
        root.title("IdealChat")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.build_login()

    # ---------- login ----------
    def build_login(self):
        self.login_frame = f = ttk.Frame(self.root, padding=20)
        f.pack()
        self.host_var = tk.StringVar(value="127.0.0.1")
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.name_var = tk.StringVar()
        for i, (label, var) in enumerate(
            [("Server host", self.host_var), ("Port", self.port_var), ("Your name", self.name_var)]
        ):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=3)
            e = ttk.Entry(f, textvariable=var, width=26)
            e.grid(row=i, column=1, pady=3)
            e.bind("<Return>", lambda _e: self.connect())
        self.login_err = ttk.Label(f, foreground="red")
        self.login_err.grid(row=3, column=0, columnspan=2)
        ttk.Button(f, text="Connect", command=self.connect).grid(row=4, column=0, columnspan=2, pady=8)

    def connect(self):
        name = self.name_var.get().strip()
        if not name:
            self.login_err.config(text="Please enter a name")
            return
        try:
            # R1/R2: open a TCP socket to the server (any computer/IP) - socket programming only
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((self.host_var.get().strip(), int(self.port_var.get())))
            # R3: ask the server to register this name; it must be unique
            send_msg(sock, {"type": "login", "name": name})
            reply = recv_msg(sock)
            sock.settimeout(None)
        except (OSError, ValueError) as e:
            self.login_err.config(text=f"Cannot connect: {e}")
            return
        # R3: name already taken (or invalid) -> show the server's reason and stay on the login screen
        if reply.get("type") != "login_ok":
            sock.close()
            self.login_err.config(text=reply.get("reason", "Login failed"))
            return
        self.sock, self.name = sock, reply["name"]
        # R2: a background thread reads incoming messages from the socket
        threading.Thread(target=self.receiver, daemon=True).start()
        self.login_frame.destroy()
        self.build_main()
        self.root.after(100, self.poll)

    # ---------- main UI ----------
    def build_main(self):
        self.root.title(f"IdealChat - {self.name}")
        self.root.geometry("820x520")
        side = ttk.Frame(self.root, padding=6)
        side.pack(side="left", fill="y")

        # R4: list of all connected clients, including this client's own name
        ttk.Label(side, text="Online users (double-click to chat)").pack(anchor="w")
        self.user_list = tk.Listbox(side, width=28, height=10, exportselection=False)
        self.user_list.pack(fill="x")
        # R7: double-click a user to open a private chat with them
        self.user_list.bind("<Double-Button-1>", self.open_pm_from_list)

        # R9: list of all existing groups with their members (created by any client)
        ttk.Label(side, text="Groups").pack(anchor="w", pady=(10, 0))
        self.group_list = tk.Listbox(side, width=28, height=10, exportselection=False)
        self.group_list.pack(fill="x")
        self.group_list.bind("<Double-Button-1>", self.open_group_from_list)
        btns = ttk.Frame(side)
        btns.pack(fill="x", pady=4)
        # R8: button to create a group; R10: button to join a selected group yourself
        ttk.Button(btns, text="Create group", command=self.create_group).pack(side="left", expand=True, fill="x")
        ttk.Button(btns, text="Join group", command=self.join_group).pack(side="left", expand=True, fill="x")

        # R5: each chat (private or group) is shown as its own tab = its own chat room
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side="right", fill="both", expand=True, padx=6, pady=6)
        self.notebook.bind("<<NotebookTabChanged>>", self.clear_unread)

    # R5: one chat room per conversation, keyed ("pm", user) or ("group", name); created once, then reused
    def open_room(self, key, focus=True):
        if key not in self.rooms:
            frame = ttk.Frame(self.notebook)
            # R6: chat window - shows sent messages and new incoming messages (read-only)
            chat = ScrolledText(frame, state="disabled", wrap="word")
            chat.pack(fill="both", expand=True)
            bottom = ttk.Frame(frame)
            bottom.pack(fill="x")
            # R6: chat box - type a text message, press Enter or Send
            entry = ttk.Entry(bottom)
            entry.pack(side="left", fill="x", expand=True)
            entry.bind("<Return>", lambda _e, k=key: self.send_text(k))
            ttk.Button(bottom, text="Send", command=lambda k=key: self.send_text(k)).pack(side="right")
            self.rooms[key] = {"frame": frame, "log": chat, "entry": entry, "title": self.tab_title(key)}
            self.notebook.add(frame, text=self.tab_title(key))
        if focus:
            self.notebook.select(self.rooms[key]["frame"])
            self.rooms[key]["entry"].focus_set()
        return self.rooms[key]

    @staticmethod
    def tab_title(key):
        return ("@ " if key[0] == "pm" else "# ") + key[1]

    # R6: add a sent/incoming message to the chat window of the room it belongs to
    def append(self, key, text, focus=False):
        room =self.open_room(key, focus=focus)
        log = room["log"]
        log.config(state="normal")
        log.insert("end", text + "\n")
        log.see("end")
        log.config(state="disabled")
        if self.notebook.select() != str(room["frame"]):
            self.notebook.tab(room["frame"], text="* " + room["title"])

    def clear_unread(self, _event=None):
        for room in self.rooms.values():
            if self.notebook.select() == str(room["frame"]):
                self.notebook.tab(room["frame"], text=room["title"])

    # ---------- actions ----------
    # R2: all requests to the server are written to the TCP socket
    def send(self, msg):
        try:
            send_msg(self.sock, msg)
        except OSError:
            messagebox.showerror("Disconnected", "Lost connection to the server")
            self.close()

    def send_text(self, key):
        entry = self.rooms[key]["entry"]
        text = entry.get().strip()
        if not text:
            return
        entry.delete(0, "end")
        if key[0] == "pm":
            # R7: direct message to the selected client
            self.send({"type": "pm", "to": key[1], "text": text})
        else:
            # R11: message to a group this client has joined
            self.send({"type": "group_msg", "group": key[1], "text": text})

    # R7: open (or switch to) the private chat room with the double-clicked user
    def open_pm_from_list(self, _event):
        sel = self.user_list.curselection()
        if sel:
            self.open_room(("pm", self.users[sel[0]]))

    def selected_group(self):
        sel = self.group_list.curselection()
        return self.groups[sel[0]] if sel else None

    # R5/R11: open the group's chat room only if this client is a member
    def open_group_from_list(self, _event):
        g = self.selected_group()
        if g and self.name in g["members"]:
            self.open_room(("group", g["name"]))

    # R8: ask for a group name and send create_group (the server makes us its only member)
    def create_group(self):
        g =simpledialog.askstring("Create group", "Group name:", parent=self.root)
        if g and g.strip():
            self.send({"type": "create_group", "group": g.strip()})

    # R10: the user chooses to join a group themself; nothing is joined automatically
    def join_group(self):
        g = self.selected_group()
        if not g:
            messagebox.showinfo("Join group", "Select a group in the list first")
        elif self.name in g["members"]:
            self.open_room(("group", g["name"]))
        else:
            self.send({"type": "join_group", "group": g["name"]})

    # ---------- receiving ----------
    # R2: receive messages from the server over the TCP socket
    def receiver(self):
        """Background thread: only reads the socket, hands events to the Tk thread."""
        try:
            while True:
                self.events.put(recv_msg(self.sock))
        except (OSError, ValueError):
            self.events.put({"type": "_closed"})

    def poll(self):
        try:
            while True:
                self.dispatch(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def dispatch(self, m):
        t = m.get("type")
        # R4: refresh the online-user list (it includes our own name, marked "(you)")
        if t == "users":
            self.users = m["users"]
            self.user_list.delete(0, "end")
            for u in self.users:
                self.user_list.insert("end", u + ("  (you)" if u == self.name else ""))
        # R9: refresh the group list with members; R8/R10: a group we created or joined gets its own room
        elif t == "groups":
            selected =self.selected_group()  # keep the user's selection across refreshes
            self.groups = m["groups"]
            self.group_list.delete(0, "end")
            for i, g in enumerate(self.groups):
                joined = self.name in g["members"]
                members = ", ".join(g["members"]) or "no members"
                self.group_list.insert("end", f"{'[joined] ' if joined else ''}{g['name']} ({members})")
                if selected and g["name"] == selected["name"]:
                    self.group_list.selection_set(i)
                if joined:  # created or joined -> own chat room
                    self.open_room(("group", g["name"]), focus=False)
        # R7: private message -> the room named after the other person (sent or received)
        elif t == "pm":
            other = m["to"] if m["from"] == self.name else m["from"]
            self.append(("pm", other), f"[{m['ts']}] {m['from']}: {m['text']}")
        # R11: group message -> the room of that group (the server only sends it to members)
        elif t == "group_msg":
            self.append(("group", m["group"]), f"[{m['ts']}] {m['from']}: {m['text']}")
        # R9/R11: on joining, show the group's earlier messages in its room (sent by the server to us only)
        elif t == "group_history":
            key = ("group", m["group"])
            if m["messages"]:
                self.append(key, "--- earlier messages ---")
                for old in m["messages"]:
                    self.append(key, f"[{old['ts']}] {old['from']}: {old['text']}")
                self.append(key, "--- you joined ---")
        elif t == "error":
            messagebox.showwarning("Error", m["text"])
        elif t == "_closed":
            messagebox.showerror("Disconnected", "Lost connection to the server")
            self.close()

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    ChatApp(root)
    root.mainloop()
