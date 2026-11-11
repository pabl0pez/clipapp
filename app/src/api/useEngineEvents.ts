/** Live link to the engine's `/events` WebSocket: reconnects with backoff and patches the cache. */
import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";
import { useUiStore } from "../stores/ui";
import { ENGINE_TOKEN, ENGINE_URL, type Job } from "./client";
import { keys } from "./queries";

export const BASE_DELAY_MS = 500;
export const MAX_DELAY_MS = 10_000;
/** Failed attempts in a row after which the UI shows the offline screen (retrying continues). */
export const OFFLINE_AFTER = 3;

export type EngineEvent =
  | { type: "job_progress"; id: number; progress: number }
  | {
      type: "job_status";
      id: number;
      status: Job["status"];
      error: string | null;
      output_path: string | null;
    }
  | { type: "project_updated"; id: number };

/** WebSocket URL of `/events` (the token goes in the query: browsers cannot set WS headers). */
export function eventsUrl(): string {
  return `${ENGINE_URL.replace(/^http/, "ws")}/events?token=${encodeURIComponent(ENGINE_TOKEN)}`;
}

function patchJob(qc: QueryClient, id: number, fields: Partial<Job>): boolean {
  let found = false;
  qc.setQueryData<Job[]>(keys.jobs, (jobs) =>
    jobs?.map((j) => {
      if (j.id !== id) return j;
      found = true;
      return { ...j, ...fields };
    }),
  );
  return found;
}

/** Apply one engine event to the cache; jobs not in the cache yet are refetched instead. */
export function applyEvent(qc: QueryClient, event: EngineEvent): void {
  switch (event.type) {
    case "job_progress":
      patchJob(qc, event.id, { progress: event.progress });
      break;
    case "job_status": {
      const { status, error, output_path } = event;
      if (!patchJob(qc, event.id, { status, error, output_path })) {
        void qc.invalidateQueries({ queryKey: keys.jobs });
      }
      break;
    }
    case "project_updated":
      void qc.invalidateQueries({ queryKey: keys.projects, exact: true });
      void qc.invalidateQueries({ queryKey: keys.project(event.id) });
      break;
  }
}

/**
 * Keep the `/events` socket open while mounted and mirror its state in `useUiStore().connection`.
 *
 * Every (re)connection refetches all queries, so whatever was missed while offline is recovered.
 *
 * @returns A function that retries right away (for the offline screen's button).
 */
export function useEngineEvents(): () => void {
  const qc = useQueryClient();
  const setConnection = useUiStore((s) => s.setConnection);
  const retryRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    const drop = () => {
      if (!ws) return;
      ws.onopen = null;
      ws.onmessage = null;
      ws.onclose = null;
      ws.close();
      ws = null;
    };
    const connect = () => {
      clearTimeout(timer);
      drop();
      const socket = new WebSocket(eventsUrl());
      ws = socket;
      socket.onopen = () => {
        failures = 0;
        setConnection("connected");
        void qc.invalidateQueries();
      };
      socket.onmessage = (e: MessageEvent<string>) => {
        applyEvent(qc, JSON.parse(e.data) as EngineEvent);
      };
      socket.onclose = () => {
        failures += 1;
        setConnection(failures > OFFLINE_AFTER ? "offline" : "reconnecting");
        timer = setTimeout(connect, Math.min(BASE_DELAY_MS * 2 ** (failures - 1), MAX_DELAY_MS));
      };
    };
    retryRef.current = () => {
      setConnection("reconnecting");
      connect();
    };
    connect();
    return () => {
      clearTimeout(timer);
      drop();
    };
  }, [qc, setConnection]);

  return useCallback(() => {
    retryRef.current();
  }, []);
}
