import { describe, it, expect, vi } from "vitest";
import { openLiveFeed } from "../api.js";

describe("openLiveFeed: mock replay", () => {
  it("emits each message of a batch per tick and cycles, until closed", () => {
    let cb = null;
    const timers = {
      setInterval: (fn) => {
        cb = fn;
        return 1;
      },
      clearInterval: vi.fn(),
    };
    const frames = [[{ type: "a" }], [{ type: "b" }, { type: "c" }]];
    const seen = [];
    const handle = openLiveFeed("home", (m) => seen.push(m.type), {
      mock: true,
      frames,
      ...timers,
    });

    cb(); // tick 1 → batch 0
    cb(); // tick 2 → batch 1
    cb(); // tick 3 → wraps to batch 0
    expect(seen).toEqual(["a", "b", "c", "a"]);

    handle.close();
    expect(timers.clearInterval).toHaveBeenCalledWith(1);
  });
});

describe("openLiveFeed: websocket", () => {
  it("opens a ws:// url, parses JSON frames, and ignores malformed ones", () => {
    let opened;
    class FakeSocket {
      constructor(url) {
        opened = url;
        this.onmessage = null;
      }
      close() {
        this.closed = true;
      }
    }
    const seen = [];
    const handle = openLiveFeed("rosewood", (m) => seen.push(m), {
      WebSocket: FakeSocket,
    });

    expect(opened).toMatch(/^wss?:\/\//);
    expect(opened).toContain("/homes/rosewood/live");

    handle.socket.onmessage({ data: JSON.stringify({ type: "room_activity" }) });
    handle.socket.onmessage({ data: "not json {" });
    expect(seen).toEqual([{ type: "room_activity" }]);

    handle.close();
    expect(handle.socket.closed).toBe(true);
  });
});
