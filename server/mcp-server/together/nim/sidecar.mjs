// Listen Together room transport. Only wraps the official node-nim SDK; every
// decision (what to send, how to read messages) is made by the Python side.
// Protocol: one JSON object per line. stdin = commands, stdout = events.
//   in : {op:"connect", rid, chatRoomId, accId, token, profile:{nick, avatar}}
//        {op:"send", rid, msg}   {op:"disconnect", rid}   {op:"shutdown"}
//   out: {ev:"ready"} {ev:"connected", rid} {ev:"sent", rid, code, id}
//        {ev:"message", msg} {ev:"link", condition} {ev:"exit", reason}
//        {ev:"disconnected", rid} {ev:"error", rid, op, message} {ev:"fatal", message}
import { createInterface } from "node:readline";
import { fork } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

// The SDK must not write to stdout: that channel carries our protocol.
console.log = (...args) => console.error(...args);
console.info = console.log;

const APP_KEY = "3a6a3e48f6854dfa4e4464f3bdaec3b4"; // NetEase Cloud Music's IM app key
const ENTER_TIMEOUT_MS = 15_000;
const TICKET_TIMEOUT_MS = 20_000;

const emit = (event) => process.stdout.write(JSON.stringify(event) + "\n");

let nim;
try {
  const mod = await import("node-nim");
  nim = mod.default ?? mod;
  if (typeof nim.ChatRoom !== "function") throw new Error("node-nim ChatRoom export missing");
} catch (error) {
  emit({ ev: "fatal", message: `node-nim unavailable: ${error.message}` });
  process.exit(1);
}

let chatroom = null;
let roomNumber = null;
let pendingEnter = null;

function runtime() {
  if (chatroom) return chatroom;
  const room = new nim.ChatRoom();
  if (!room.init("", "")) throw new Error("NIM chatroom init failed");
  room.initEventHandlers();
  room.on("enter", (room_, step, code) => {
    if (!pendingEnter || Number(room_) !== roomNumber || Number(step) !== 5) return;
    const pending = pendingEnter;
    pendingEnter = null;
    Number(code) === 200 ? pending.resolve() : pending.reject(new Error(`chatroom enter failed code=${code}`));
  });
  room.on("sendMsg", (room_, code, msg) => {
    const id = msg?.client_msg_id_;
    if (id) emit({ ev: "sent", rid: sendRids.get(id), id, code: Number(code) });
    sendRids.delete(id);
  });
  room.on("receiveMsg", (room_, msg) => {
    if (Number(room_) === roomNumber) emit({ ev: "message", msg });
  });
  room.on("exit", (room_, ...rest) => {
    if (Number(room_) === roomNumber) emit({ ev: "exit", reason: rest.map(Number).find((n) => !Number.isNaN(n)) ?? null });
  });
  room.on("linkCondition", (room_, condition) => {
    if (Number(room_) === roomNumber) emit({ ev: "link", condition: Number(condition) });
  });
  chatroom = room;
  return room;
}

const sendRids = new Map();

// The IM login that yields the room ticket runs in a throwaway child process:
// node-nim's cleanup can hang a native thread, so we kill the child instead.
function requestTicket(accId, token, number) {
  const dataDir = mkdtempSync(join(tmpdir(), "ncm-nim-"));
  const child = fork(fileURLToPath(new URL("./ticket.mjs", import.meta.url)), [], {
    stdio: ["ignore", "ignore", "pipe", "ipc"],
  });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("room ticket timed out")), TICKET_TIMEOUT_MS);
    child.once("message", (m) => {
      clearTimeout(timer);
      if (m?.ok && Array.isArray(m.result) && Number(m.result[0]) === 200 && m.result[1]) resolve(String(m.result[1]));
      else reject(new Error(m?.error || `room ticket failed code=${m?.result?.[0]}`));
    });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`ticket process exited code=${code}`)); });
    child.send({ appKey: APP_KEY, dataDir, accId, token, roomNumber: number });
  }).finally(() => {
    child.kill("SIGKILL");
    try { rmSync(dataDir, { recursive: true, force: true }); } catch {}
  });
}

async function connect(cmd) {
  const number = Number(cmd.chatRoomId);
  if (!Number.isSafeInteger(number) || number <= 0) throw new Error(`bad chatRoomId ${cmd.chatRoomId}`);
  const room = runtime();
  if (roomNumber !== null) { try { room.exit(roomNumber, ""); } catch {} }
  roomNumber = number;
  const ticket = await requestTicket(cmd.accId, cmd.token, number);
  const values = {};
  if (cmd.profile?.nick) values.nick = cmd.profile.nick;
  if (cmd.profile?.avatar) values.avatar = cmd.profile.avatar;
  const entered = new Promise((resolve, reject) => { pendingEnter = { resolve, reject }; });
  if (!room.enter(number, ticket, { values_: values }, "")) throw new Error("chatroom enter rejected locally");
  let timer;
  await Promise.race([entered, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("chatroom enter timed out")), ENTER_TIMEOUT_MS);
  })]).finally(() => { clearTimeout(timer); pendingEnter = null; });
  if (values.nick || values.avatar) {
    const update = { account_id_: cmd.accId };
    if (values.nick) update.nick_ = values.nick;
    if (values.avatar) update.avatar_ = values.avatar;
    try { await room.updateMyRoomRoleAsync(number, update, false, "", null, ""); } catch {}
  }
}

const handlers = {
  async connect(cmd) { await connect(cmd); emit({ ev: "connected", rid: cmd.rid }); },
  async send(cmd) {
    if (!chatroom || roomNumber === null) throw new Error("not connected");
    const id = cmd.msg?.client_msg_id_;
    sendRids.set(id, cmd.rid);
    if (!chatroom.sendMsg(roomNumber, cmd.msg, "")) {
      sendRids.delete(id);
      throw new Error("send rejected locally");
    }
  },
  async disconnect(cmd) {
    if (chatroom && roomNumber !== null) { try { chatroom.exit(roomNumber, ""); } catch {} }
    roomNumber = null;
    emit({ ev: "disconnected", rid: cmd.rid });
  },
  async shutdown() {
    if (chatroom && roomNumber !== null) { try { chatroom.exit(roomNumber, ""); } catch {} }
    process.exit(0);
  },
};

createInterface({ input: process.stdin }).on("line", async (line) => {
  let cmd;
  try { cmd = JSON.parse(line); } catch { return; }
  const handler = handlers[cmd.op];
  if (!handler) return emit({ ev: "error", rid: cmd.rid, op: cmd.op, message: "unknown op" });
  try { await handler(cmd); } catch (error) {
    emit({ ev: "error", rid: cmd.rid, op: cmd.op, message: String(error?.message || error) });
  }
}).on("close", () => process.exit(0));

emit({ ev: "ready" });
