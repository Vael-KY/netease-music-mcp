// One-shot IM login that returns a chatroom enter ticket, then waits to be killed.
// Kept in its own process because node-nim cleanup can hang a native thread.
import { mkdirSync } from "node:fs";

console.log = (...args) => console.error(...args);

process.once("message", async (req) => {
  try {
    mkdirSync(req.dataDir, { recursive: true });
    const mod = await import("node-nim");
    const nim = mod.default ?? mod;
    const client = new nim.NIMClient();
    const plugin = new nim.NIMPlugin();
    const config = { database_encrypt_key_: req.appKey, use_https_: true, sdk_log_level_: 2 };
    if (!client.init(req.appKey, req.dataDir + "/", "", config)) throw new Error("IM client init failed");
    client.initEventHandlers();
    plugin.initEventHandlers();
    const [login] = await client.login(req.appKey, req.accId, req.token, null, "");
    if (Number(login?.res_code_) !== 200) throw new Error(`IM login failed code=${login?.res_code_}`);
    process.send({ ok: true, result: await plugin.chatRoomRequestEnterAsync(req.roomNumber, null, "") });
  } catch (error) {
    process.send({ ok: false, error: String(error?.message || error) });
  }
});

setInterval(() => {}, 60_000);
