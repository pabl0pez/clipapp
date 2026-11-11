import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../stores/ui";
import { FakeWebSocket, makeJob } from "../test/server";
import type { Job } from "./client";
import { keys } from "./queries";
import { OFFLINE_AFTER, useEngineEvents } from "./useEngineEvents";

function setup() {
  const qc = new QueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => useEngineEvents(), { wrapper });
  return { qc, hook };
}

describe("useEngineEvents", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket);
    FakeWebSocket.instances = [];
    useUiStore.setState({ connection: "connecting" });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("connects with the token and patches the cache from events", () => {
    const { qc } = setup();
    expect(FakeWebSocket.last.url).toBe("ws://127.0.0.1:9999/events?token=test-token");
    qc.setQueryData<Job[]>(keys.jobs, [makeJob({ id: 4, progress: 0 })]);
    qc.setQueryData(keys.project(1), { id: 1 });

    act(() => {
      FakeWebSocket.last.open();
    });
    expect(useUiStore.getState().connection).toBe("connected");

    act(() => {
      FakeWebSocket.last.send({ type: "job_progress", id: 4, progress: 0.42 });
      FakeWebSocket.last.send({
        type: "job_status",
        id: 4,
        status: "done",
        error: null,
        output_path: null,
      });
      FakeWebSocket.last.send({ type: "project_updated", id: 1 });
    });
    expect(qc.getQueryData<Job[]>(keys.jobs)?.[0]).toMatchObject({
      progress: 0.42,
      status: "done",
    });
    expect(qc.getQueryState(keys.project(1))?.isInvalidated).toBe(true);
  });

  it("reconnects with growing delays, goes offline and recovers", () => {
    setup();
    const delays: number[] = [];
    for (let i = 0; i <= OFFLINE_AFTER; i++) {
      const before = FakeWebSocket.instances.length;
      act(() => {
        FakeWebSocket.last.drop();
      });
      let waited = 0;
      while (FakeWebSocket.instances.length === before) {
        act(() => {
          vi.advanceTimersByTime(100);
        });
        waited += 100;
      }
      delays.push(waited);
    }
    expect(delays).toEqual([500, 1000, 2000, 4000]);
    expect(useUiStore.getState().connection).toBe("offline");

    act(() => {
      FakeWebSocket.last.open();
    });
    expect(useUiStore.getState().connection).toBe("connected");
  });

  it("retries right away and closes the socket on unmount", () => {
    const { hook } = setup();
    act(() => {
      FakeWebSocket.last.drop();
    });
    const count = FakeWebSocket.instances.length;
    act(() => {
      hook.result.current();
    });
    expect(FakeWebSocket.instances.length).toBe(count + 1);
    hook.unmount();
    expect(FakeWebSocket.last.closed).toBe(true);
  });
});
