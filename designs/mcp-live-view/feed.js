// Shared live feed for the MCP live-view drafts. Draft only: nothing here is in the design doc.
// Grounded in the doc as written: Claude Desktop drives the MCP server over stdio (§5, D4) and is
// not in the real-time loop, so play_round is one call; the round moves through the §9.4 states;
// tools are the §9.3 catalogue; the hand is the four D6 channels; moves, rules and sequences use
// the D11 / D20 formats; the XIAO lines are §9.2 and the camera frames §9.6.
//
// Each draft calls LiveFeed.start(render). render(state, event) runs on every event.
//   - default: plays a scripted mock session (Claude registering a player, a rigged round, a new move...)
//   - ?feed=<url> : listens to a Server-Sent Events stream instead (a suggestion for later, not a decided design)
//
// Proposed event shape (one JSON object per event). How the server would publish it is open.
//   {type:"tool.call",   id, tool, args}                       Claude called a tool
//   {type:"tool.result", id, ok, ms, result?, error?}          ...and what came back
//   {type:"xiao",        dir:"tx"|"rx", line}                  §9.2 JSON line to/from the XIAO
//   {type:"husky",       dir:"tx"|"rx", hex, name}             raw HuskyLens frame (§9.6)
//   {type:"state",       ...partial state}                     merged into `state` below
//   {type:"game",        event, round:{phase}}  phase = a §9.4 state:
//                        setup | idle | countdown | hold_fist | commit | read | score | replay | report
//   {type:"banter",      text}
(function () {
  const state = {
    connected: false, link: null,
    hand: { fingers: [1, 1, 1, 1], pose: "paper" },       // 0 closed .. 1 open: thumb, index, middle, ring+little
    led: "#000000", relay: false,
    vision: { algorithm: "object_classification", sees: null, fps: null },
    player: null,                                       // {name, mode, face_id}
    score: { robot: 0, human: 0, draw: 0 },
    round: { n: 0, phase: "idle", beat: 0, robot: null, human: null, outcome: null },
    banter: null, calls: [], log: [],
    // gesture training (D9: IDs 1-4) and the accuracy test (T7, G2); same fields as tools/train_ui.py
    training: { phase: "idle", cls: null, count: 0, progress: [0, 0], learned_ids: 0,
                samples: { rock: 0, paper: 0, scissors: 0, nothing: 0 }, test: null },
  };
  const MAX = 60;

  function apply(ev) {
    ev.ts = ev.ts || Date.now();
    if (ev.type === "tool.call") {
      state.calls.unshift({ id: ev.id, tool: ev.tool, args: ev.args || {}, ts: ev.ts, pending: true });
      state.calls.length = Math.min(state.calls.length, MAX);
    } else if (ev.type === "tool.result") {
      const c = state.calls.find(c => c.id === ev.id);
      if (c) Object.assign(c, { pending: false, ok: ev.ok, ms: ev.ms, result: ev.result, error: ev.error });
    } else if (ev.type === "state") {
      for (const [k, v] of Object.entries(ev)) {
        if (k === "type" || k === "ts") continue;
        state[k] = v && typeof v === "object" && !Array.isArray(v) && state[k] ? { ...state[k], ...v } : v;
      }
    } else if (ev.type === "game") {
      Object.assign(state.round, ev.round || {});
    } else if (ev.type === "banter") {
      state.banter = ev.text;
    }
    if (ev.type === "xiao" || ev.type === "husky") {
      state.log.unshift(ev);
      state.log.length = Math.min(state.log.length, MAX);
    }
  }

  // ---------------------------------------------------------------- mock session
  const POSES = { rock: [0, 0, 0, 0], paper: [1, 1, 1, 1], scissors: [0, 1, 1, 0], claw: [0.5, 0.5, 0.5, 0.5] };
  const BEATS = { rock: "scissors", paper: "rock", scissors: "paper" };
  const COUNTER = { rock: "paper", paper: "scissors", scissors: "rock" };
  let nextId = 1;

  function script() {
    const s = [];
    const wait = ms => s.push({ wait: ms });
    const emit = ev => s.push({ ev });
    const tool = (tool, args, result, ms = 40, ok = true) => {
      const id = nextId++;
      emit({ type: "tool.call", id, tool, args });
      return { id, done: () => { emit({ type: "tool.result", id, ok, ms, result }); } };
    };
    const xiao = (cmd, extra = {}, reply = {}) => {
      const id = Math.floor(Math.random() * 900) + 100;
      emit({ type: "xiao", dir: "tx", line: JSON.stringify({ id, cmd, ...extra }) });
      emit({ type: "xiao", dir: "rx", line: JSON.stringify({ id, ok: true, ...reply }) });
    };
    const husky = (name, tx, rx) => {
      emit({ type: "husky", dir: "tx", hex: tx, name });
      if (rx) emit({ type: "husky", dir: "rx", hex: rx, name: name === "KNOCK" ? "OK" : "INFO+BLOCK" });
    };
    const pose = (p, ms = 250) => {
      xiao("pose", { us: POSES[p].map(v => Math.round(2000 - v * 1000)), ms });
      emit({ type: "state", hand: { fingers: POSES[p], pose: p } });
    };

    // connect
    let t = tool("husky_connect", { link: "auto" });
    husky("KNOCK", "55 AA 11 00 2C 3C", "55 AA 11 00 2E 3E"); wait(350);
    emit({ type: "state", connected: true, link: "XIAO bridge · /dev/cu.usbmodem103 · cpy-bridge 0.5" });
    t.done(); wait(900);

    // identify player
    t = tool("identify_player", {}); wait(200);
    emit({ type: "game", event: "setup", round: { phase: "setup" } });
    xiao("husky", { hex: "55AA11022D00003F" }); husky("ALGORITHM face", "55 AA 11 02 2D 00 00 3F", "55 AA 11 00 2E 3E");
    emit({ type: "state", vision: { algorithm: "face_recognition", sees: { name: "face", id: 1 } } }); wait(900);
    emit({ type: "state", player: { name: "Anhad", face_id: 1, mode: "rigged_win", wins: 0, losses: 7 } });
    husky("CUSTOM_TEXT", "55 AA 11 0B 34 0A 00 0A 00 05 41 6E 68 61 64 …");
    husky("ALGORITHM classification", "55 AA 11 02 2D 06 00 45", "55 AA 11 00 2E 3E");
    emit({ type: "state", vision: { algorithm: "object_classification", sees: { name: "nothing", id: 4 }, fps: 18.8 } });
    emit({ type: "game", event: "idle", round: { phase: "idle" } });
    t.done(); wait(1200);

    // a rigged-win round (D10): hold a fist through the pump, read the throw, then snap to the counter
    const human = ["paper", "rock", "scissors"][Math.floor(Math.random() * 3)];
    t = tool("play_round", {}); wait(200);
    emit({ type: "game", event: "countdown", round: { phase: "countdown", n: 0, robot: null, human: null, outcome: null } });
    xiao("countdown", { beats: 3, period_ms: 450, colours: ["#ff3030", "#ffb000", "#30ff60"] });
    xiao("voice", { play_ms: 1400 });
    pose("rock", 0);
    for (const [i, word] of ["rock", "paper", "scissors"].entries()) {
      emit({ type: "state", led: ["#ff3030", "#ffb000", "#30ff60"][i] });
      emit({ type: "game", event: "countdown", round: { beat: i + 1, word } });
      pose(i % 2 ? "rock" : "paper", 120); wait(220); pose("rock", 120); wait(230);
    }
    emit({ type: "game", event: "shoot", round: { phase: "hold_fist", beat: 4, word: "shoot" } });
    emit({ type: "game", event: "read", round: { phase: "read" } });
    for (let i = 0; i < 5; i++) husky("REQUEST_BLOCKS", "55 AA 11 00 21 31", "55 AA 11 0A 29 01 00 04 00 …");
    emit({ type: "state", vision: { sees: { name: human, id: { rock: 1, paper: 2, scissors: 3 }[human] } } }); wait(260);
    const robot = COUNTER[human];
    emit({ type: "game", event: "commit", round: { phase: "commit", robot, human } });
    pose(robot, 120); wait(500);
    emit({ type: "state", led: "#a000ff" });
    emit({ type: "game", event: "score", round: { phase: "score" } });
    emit({ type: "game", event: "report", round: { phase: "report", outcome: "robot", verb: robot === "rock" ? "crushes" : robot === "paper" ? "covers" : "cuts" } });
    s.push({ fn: () => { state.score.robot++; } });
    emit({ type: "banter", text: [
      "Glare. Definitely glare. Also I win.", "Seven losses and counting, Anhad. I admire the persistence.",
      "My circuits saw that coming three business days ago.", "You blinked. Robots don't blink."][Math.floor(Math.random() * 4)] });
    t.done(); wait(400);
    xiao("relax"); emit({ type: "state", hand: { fingers: [1, 1, 1, 1], pose: "relaxed" } });
    emit({ type: "game", event: "idle", round: { phase: "idle" } });
    wait(1800);

    // Claude invents a move + rule, then a sequence
    t = tool("define_move", { name: "the claw", fingers: [0.5, 0.5, 0.5, 0.5], led: "#ff00aa", husky_id: 5 }); wait(250); t.done(); wait(500);
    t = tool("define_rule", { winner: "the claw", loser: "paper", verb: "shreds" }); wait(200); t.done(); wait(500);
    t = tool("define_sequence", { name: "victory dance", steps: [{ led: "#ff00aa" }, { pose: [0.5, 0.5, 0.5, 0.5], ms: 120 }, { led: "#30ff60" }, { pose: [1, 1, 1, 1], ms: 120 }, { repeat: 3 }] }); wait(200); t.done(); wait(300);
    t = tool("play_sequence", { name: "victory dance" });
    for (let i = 0; i < 3; i++) {
      emit({ type: "state", led: "#ff00aa" }); pose("claw", 120); wait(180);
      emit({ type: "state", led: "#30ff60" }); pose("paper", 120); wait(180);
    }
    t.done(); xiao("relax"); wait(600);
    const bad = tool("hand_pose", { fingers: [1.4, 0, 0, 0] }, null, 3, false);
    s.push({ ev: { type: "tool.result", id: bad.id, ok: false, ms: 3, error: "finger values must be in [0, 1] (NFR-05)" } });
    wait(2400);
    return s;
  }

  // Training session: Claude trains the four classes with train_gesture, then a 30-throw
  // accuracy test with read_gesture, people swapping halfway (T7 mixed players, G2 >= 85 %).
  function trainScript() {
    const s = [];
    const wait = ms => s.push({ wait: ms });
    const emit = ev => s.push({ ev });
    const fn = f => s.push({ fn: f });
    const tr = patch => fn(() => Object.assign(state.training, patch));
    const tool = (tool, args) => { const id = nextId++; emit({ type: "tool.call", id, tool, args });
      return (result, ok = true) => emit({ type: "tool.result", id, ok, ms: 60, result }); };
    fn(() => Object.assign(state.training, { phase: "idle", cls: null, learned_ids: 0, test: null,
      samples: { rock: 0, paper: 0, scissors: 0, nothing: 0 } }));
    let done = tool("husky_connect", { link: "auto" });
    emit({ type: "husky", dir: "tx", hex: "55 AA 11 00 2C 3C", name: "KNOCK" });
    emit({ type: "husky", dir: "rx", hex: "55 AA 11 00 2E 3E", name: "OK" }); wait(300);
    emit({ type: "state", connected: true, link: "camera micro-USB · 3 Mbaud · fw 0.5.1b",
           vision: { algorithm: "object_classification", sees: null, fps: 24.6 } });
    done({ ok: true }); wait(700);
    const ids = { rock: 1, paper: 2, scissors: 3, nothing: 4 };
    for (const cls of ["rock", "paper", "scissors", "nothing"]) {
      const n = 15;
      done = tool("train_gesture", { move: cls, samples: n });
      for (let c = 3; c >= 1; c--) { tr({ phase: "countdown", cls, count: c, progress: [0, n] });
        emit({ type: "state", led: ["#ff0000", "#00ff00", "#0040ff", "#ffffff"][ids[cls] - 1] }); wait(330); }
      tr({ phase: "capturing", count: 0 });
      emit({ type: "state", vision: { sees: { name: cls, id: ids[cls] } } });
      for (let i = 1; i <= n; i++) {
        emit({ type: "husky", dir: "tx", hex: `55 AA 11 02 36 0${ids[cls]} 00 …`, name: `LEARN ${ids[cls]}` });
        fn(() => { state.training.samples[cls]++; state.training.progress = [i, n]; });
        emit({ type: "state" }); wait(70);
      }
      fn(() => { state.training.learned_ids = ids[cls]; });
      tr({ phase: "idle", cls: null }); emit({ type: "state", led: "#a000ff" });
      done({ move: cls, id: ids[cls], accepted: n, learned_ids: ids[cls] }); wait(500);
      emit({ type: "state", led: "#000000", vision: { sees: null } });
    }
    // accuracy test
    const N = 30, G = ["rock", "paper", "scissors"];
    const targets = Array.from({ length: N }, (_, i) => G[(i * 7 + 1) % 3]);
    const misses = { 8: "paper", 21: "unknown" };
    fn(() => { state.training.test = { n: N, idx: 0, target: null, results: [], correct: 0, accuracy: null,
      confusion: { rock: {}, paper: {}, scissors: {} }, players: {}, done: false }; });
    tr({ phase: "testing" });
    targets.forEach((target, i) => {
      const got = misses[i] || target, player = i < 15 ? "Anhad" : "Sam";
      fn(() => Object.assign(state.training.test, { idx: i + 1, target }));
      tr({ cls: target, progress: [i, N] }); emit({ type: "state", vision: { sees: null } }); wait(260);
      const d = tool("read_gesture", { window_s: 0.3 });
      emit({ type: "state", vision: { sees: { name: got, id: ids[got] || 0 } } }); wait(140);
      d({ gesture: got, reads: 6 });
      fn(() => { const t = state.training.test, hit = got === target;
        t.results.push({ target, got, hit, player }); t.correct += hit; t.accuracy = t.correct / t.results.length;
        t.confusion[target][got] = (t.confusion[target][got] || 0) + 1;
        const p = t.players[player] || (t.players[player] = [0, 0]); p[0] += hit; p[1]++;
        state.training.progress = [i + 1, N]; });
      emit({ type: "state" });
    });
    fn(() => { state.training.test.done = true; }); tr({ phase: "idle", cls: null }); emit({ type: "state" });
    wait(6000);
    return s;
  }

  async function runMock(render, scenario) {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    for (;;) {
      for (const step of (scenario === "train" ? trainScript() : script())) {
        if (step.wait) { await sleep(step.wait); continue; }
        if (step.fn) { step.fn(); continue; }
        apply(step.ev); render(state, step.ev);
        await sleep(25);
      }
    }
  }

  function runLive(url, render) {
    const es = new EventSource(url);
    es.onmessage = m => { const ev = JSON.parse(m.data); apply(ev); render(state, ev); };
    es.onerror = () => { state.connected = false; render(state, { type: "state" }); };
  }

  // Shared header: the same brand, the four screens in the order you use them, and link status.
  const SCREENS = [["train.html", "Train", "before play"], ["operate.html", "Operate", "your Mac"],
                   ["stage.html", "Stage", "audience"], ["trace.html", "Trace", "how it works"]];
  function chrome(active) {
    const el = document.querySelector(".chrome");
    if (!el) return;
    el.innerHTML = `<span class="brand">${window.handSVG([0, 1, 1, 0], { size: 16, color: "currentColor", dim: "transparent" })}RPS-Bot</span>
      <nav>${SCREENS.map(([href, name, sub]) => `<a href="${href}${location.search}" class="${href === active ? "on" : ""}">${name} <small>${sub}</small></a>`).join("")}</nav>
      <span class="sp"></span><span class="pill" id="linkpill"><i class="dot"></i>offline</span>
      <span class="pill">${new URLSearchParams(location.search).get("feed") ? "live feed" : "mock session"}</span>`;
  }
  function chromeStatus(s) {
    const p = document.getElementById("linkpill");
    if (p) p.innerHTML = `<i class="dot ${s.connected ? "on" : ""}"></i>${s.connected ? s.link : "offline"}`;
  }

  window.LiveFeed = {
    state,
    start(render, { scenario = "play", screen } = {}) {
      chrome(screen);
      const url = new URLSearchParams(location.search).get("feed");
      const r = (s, ev) => { chromeStatus(s); render(s, ev); };
      r(state, { type: "state" });
      return url ? runLive(url, r) : runMock(r, scenario);
    },
  };

  // ---------------------------------------------------------------- shared hand drawing
  // A schematic hand: thumb + index + middle + ring/little (CH0..CH3). Each finger's
  // visible length follows its 0..1 value, so poses read at a glance from across the room.
  window.handSVG = function (fingers, { size = 220, color = "currentColor", dim = "rgba(255,255,255,.12)" } = {}) {
    const f = fingers || [1, 1, 1, 1];
    const fx = [{ x: 60, len: 50, w: 22, rot: -52, base: 150 }, { x: 70, len: 74, w: 21, rot: -5, base: 100 },
      { x: 97, len: 82, w: 21, rot: 0, base: 98 }, { x: 127, len: 68, w: 27, rot: 7, base: 102 }];
    const parts = fx.map((d, i) => {
      const L = 14 + d.len * Math.max(0, Math.min(1, f[i]));
      return `<g transform="rotate(${d.rot} ${d.x} ${d.base})">
        <rect x="${d.x - d.w / 2}" y="${d.base - d.len - 14}" width="${d.w}" height="${d.len + 14}" rx="${d.w / 2}" fill="${dim}"/>
        <rect x="${d.x - d.w / 2}" y="${d.base - L}" width="${d.w}" height="${L}" rx="${d.w / 2}" fill="${color}" style="transition:all .12s"/>
      </g>`;
    }).join("");
    return `<svg viewBox="0 0 170 200" width="${size}" height="${size * 200 / 170}" aria-label="robot hand">
      ${parts}<rect x="52" y="86" width="96" height="92" rx="22" fill="${color}"/></svg>`;
  };
})();
