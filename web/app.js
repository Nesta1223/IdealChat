"use strict";

const $ = (id) => document.getElementById(id);

let ws = null;
let me = null; // our own unique name once logged in
let users = [];
let groups = [];
let active = null; // key of the visible room
const rooms = new Map(); // "pm:bob" / "group:study" -> { el, log, input, tab }
let toastTimer = null;

// ---------- connection ----------
// R1/R2: one WebSocket (a TCP connection upgraded from HTTP) to the server it was loaded from
function connect(name) {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${scheme}://${location.host}/ws`);
  ws.onopen = () => send({ type: "login", name });
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => {
    if (me) {
      $("offline").hidden = false;
    } else {
      ws = null;
      $("login-error").textContent = "Cannot connect to the server";
    }
  };
}

function send(obj) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    toast("Not connected to the server");
    return;
  }
  ws.send(JSON.stringify(obj));
}

// R3: ask the server for a unique name; the server answers login_ok or login_error
$("login-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const name = $("name").value.trim();
  if (!name) {
    $("login-error").textContent = "Please enter a name";
    return;
  }
  $("login-error").textContent = "";
  if (ws && ws.readyState === WebSocket.OPEN) send({ type: "login", name });
  else connect(name);
});

$("reconnect").addEventListener("click", () => location.reload());

// ---------- incoming messages ----------
function handle(m) {
  switch (m.type) {
    case "login_ok":
      me = m.name;
      $("login").hidden = true;
      $("app").hidden = false;
      $("me").textContent = `signed in as ${me}`;
      break;
    case "login_error": // R3: name taken or invalid -> stay on the login screen
      $("login-error").textContent = m.reason;
      break;
    case "users": // R4: everyone currently connected, including us
      users = m.users;
      renderUsers();
      break;
    case "groups": // R9: all groups with members; R8/R10: groups we created or joined get a room
      groups = m.groups;
      renderGroups();
      for (const g of groups) if (g.members.includes(me)) ensureRoom(`group:${g.name}`);
      break;
    case "pm": // R7: private message -> room named after the other person
      addMessage(`pm:${m.from === me ? m.to : m.from}`, m);
      break;
    case "group_msg": // R11: group message -> that group's room (server sends it to members only)
      addMessage(`group:${m.group}`, m);
      break;
    case "group_history": // R9/R11: on joining, the group's earlier messages (sent to us only)
      showHistory(`group:${m.group}`, m.messages);
      break;
    case "error":
      toast(m.text);
      break;
  }
}

// ---------- sidebar ----------
// R4: list of connected users (click one to open a private chat, R7)
function renderUsers() {
  const list = $("user-list");
  list.replaceChildren();
  for (const u of users) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = u;
    if (u === me) {
      const you = document.createElement("span");
      you.className = "you";
      you.textContent = " (you)";
      b.append(you);
    }
    b.addEventListener("click", () => openRoom(`pm:${u}`));
    li.append(b);
    list.append(li);
  }
}

// R9: every group with its member list; R10: the Join button only joins the user who clicks it
function renderGroups() {
  const list = $("group-list");
  list.replaceChildren();
  if (!groups.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No groups yet";
    list.append(li);
    return;
  }
  for (const g of groups) {
    const joined = g.members.includes(me);
    const li = document.createElement("li");
    const info = document.createElement("div");
    info.className = "info";
    const name = document.createElement("div");
    name.className = "gname";
    name.textContent = g.name;
    const members = document.createElement("div");
    members.className = "members";
    members.textContent = g.members.length ? g.members.join(", ") : "no members";
    info.append(name, members);
    const b = document.createElement("button");
    b.type = "button";
    if (joined) {
      b.className = "open";
      b.textContent = "Open";
      b.addEventListener("click", () => openRoom(`group:${g.name}`));
    } else {
      b.textContent = "Join";
      b.addEventListener("click", () => send({ type: "join_group", group: g.name }));
    }
    li.append(info, b);
    list.append(li);
  }
}

// R8: create a group; the server makes us its only member
$("create-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const group = $("group-name").value.trim();
  if (!group) return;
  send({ type: "create_group", group });
  $("group-name").value = "";
});

// ---------- chat rooms ----------
// R5: one room (tab + panel) per conversation, created once and then reused
function ensureRoom(key) {
  if (rooms.has(key)) return rooms.get(key);
  const sep = key.indexOf(":");
  const kind = key.slice(0, sep);
  const target = key.slice(sep + 1);

  const tab = document.createElement("button");
  tab.type = "button";
  tab.textContent = (kind === "pm" ? "@ " : "# ") + target;
  tab.addEventListener("click", () => openRoom(key));
  $("tabs").append(tab);

  const el = document.createElement("div");
  el.className = "room";
  el.hidden = true;
  // R6: chat window - shows sent messages and new incoming messages
  const log = document.createElement("div");
  log.className = "log";
  log.setAttribute("role", "log");
  // R6: chat box - type a text message, press Enter or Send
  const form = document.createElement("form");
  form.className = "compose";
  form.autocomplete = "off";
  const input = document.createElement("input");
  input.placeholder = kind === "pm" ? `Message ${target}` : `Message #${target}`;
  const btn = document.createElement("button");
  btn.type = "submit";
  btn.textContent = "Send";
  form.append(input, btn);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    // R7: direct message to that client / R11: message to a group we have joined
    if (kind === "pm") send({ type: "pm", to: target, text });
    else send({ type: "group_msg", group: target, text });
  });
  el.append(log, form);
  $("rooms").append(el);

  const room = { el, log, input, tab };
  rooms.set(key, room);
  return room;
}

function openRoom(key) {
  ensureRoom(key);
  active = key;
  $("placeholder").hidden = true;
  for (const [k, r] of rooms) {
    r.el.hidden = k !== key;
    r.tab.classList.toggle("active", k === key);
  }
  rooms.get(key).tab.classList.remove("unread");
  rooms.get(key).input.focus();
}

// R6: add a sent/incoming message to the chat window of the room it belongs to.
// Text is inserted with textContent, never as HTML, so messages cannot inject markup.
function appendLine(key, m) {
  const room = ensureRoom(key);
  const div = document.createElement("div");
  div.className = "msg" + (m.from === me ? " own" : "");
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = `${m.from} · ${m.ts}`;
  const text = document.createElement("span");
  text.className = "text";
  text.textContent = m.text;
  div.append(meta, text);
  room.log.append(div);
  return room;
}

function appendNote(key, text) {
  const note = document.createElement("div");
  note.className = "note";
  note.textContent = text;
  ensureRoom(key).log.append(note);
}

function scrollDown(room) {
  room.log.scrollTop = room.log.scrollHeight;
}

function addMessage(key, m) {
  const room = appendLine(key, m);
  scrollDown(room);
  if (active !== key) room.tab.classList.add("unread");
}

// R9/R11: the group's old messages, shown between two markers when we join
function showHistory(key, messages) {
  if (!messages.length) return;
  const room = ensureRoom(key);
  appendNote(key, "earlier messages");
  for (const m of messages) appendLine(key, m);
  appendNote(key, "you joined");
  scrollDown(room);
  if (active !== key) room.tab.classList.add("unread");
}

// ---------- misc ----------
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3500);
}
