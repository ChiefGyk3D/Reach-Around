// SPDX-License-Identifier: Apache-2.0
// Drives the shipped editor logic (store.js, api.js) against a REAL editor
// server: argv = port token config-path scenario. Prints one JSON line.
import fs from "node:fs";

const [port, token, configPath, scenario] = process.argv.slice(2);
const { createClient } = await import("./api.js");
const store = await import("./store.js");

const origin = "http://127.0.0.1:" + port;
const fetcher = (path, init) =>
  fetch(origin + path, { ...init, headers: { ...init.headers, Origin: origin } });
const client = createClient(() => token, fetcher);

const first = await client.get("state");
const app = store.createStore({ config: first.config, hash: first.hash, message: "" });
const said = [];
const announce = (m) => said.push(m);
const saver = store.createSaver({ client, store: app, announce });
const actions = store.createActions({ store: app, saver, announce });

function link(id, extra) {
  const found = store.findLink(app.get().config, id).data;
  return {
    title: found.title,
    url: found.url,
    subtitle: found.subtitle ?? "",
    icon: found.icon ?? "",
    affiliate: found.affiliate === true,
    featured: found.featured === true,
    ...extra,
  };
}

function item(block, index, extra) {
  const found = store.findPlatformBlock(app.get().config, block).items[index - 1];
  const base = {};
  for (const f of store.PLATFORM_FIELDS) base[f] = found[f] === undefined ? "" : String(found[f]);
  return { ...base, ...extra };
}

const out = {};
if (scenario === "edit") {
  out.settings = await actions.saveSettings({
    "creator.name": "New Name",
    "theme.mode": "dark",
    "theme.background_dim": "0.4",
    "theme.colors.bg": "#112233",
  });
  out.link = await actions.saveLink("second", link("second", { title: "Second!", subtitle: "hello", featured: true }));
  out.platform = await actions.savePlatform("socials", 1, item("socials", 1, { count: "11", label: "followers" }));
  out.moveLink = await actions.moveLink("second", -1);
  out.movePlatform = await actions.movePlatform("socials", 2, -1);
  out.clear = await actions.saveLink("second", link("second", { subtitle: "" }));
} else if (scenario === "refused") {
  out.result = await actions.saveLink("first", link("first", { url: "not a url at all\n" }));
  out.empty = await actions.saveSettings({ "creator.name": "" });
} else if (scenario === "stale") {
  const text = fs.readFileSync(configPath, "utf8");
  fs.writeFileSync(configPath, text.replace("title: First", "title: Changed elsewhere"));
  out.result = await actions.saveLink("first", link("first", { title: "Mine" }));
  out.title = store.findLink(app.get().config, "first").data.title;
  out.message = app.get().message;
} else if (scenario === "merge") {
  const text = fs.readFileSync(configPath, "utf8");
  fs.writeFileSync(configPath, text.replace("name: Example Creator", "name: Someone Else"));
  out.result = await actions.saveLink("second", link("second", { title: "Mine" }));
  out.name = app.get().config.creator.name;
} else if (scenario === "limit") {
  const results = [];
  for (let i = 0; i < 22; i += 1) {
    results.push(await actions.saveLink("first", link("first", { title: "T" + i })));
  }
  out.okCount = results.filter((r) => r.ok).length;
  out.last = results.at(-1);
}
out.said = said;
process.stdout.write(JSON.stringify(out) + "\n");
