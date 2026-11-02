"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { EventMessage, LiveWorker, TickMessage } from "@/types";

interface LiveState {
  tick: TickMessage | null;
  verbonden: boolean;
  events: EventMessage[];
  metrics: Array<{
    tijd: string;
    cpu: number;
    gpuVideo: number | null;
    gpuRender: number | null;
    gpuPower: number | null;
    packagePower: number | null;
    platformPower: number | null;
    memoryPercent: number;
    memoryUsedBytes: number;
  }>;
  volg: (jobId: number) => void;
  stopVolgen: (jobId: number) => void;
}

const LiveContext = createContext<LiveState | null>(null);

function websocketUrl(): string {
  if (process.env.NEXT_PUBLIC_WS_URL) return process.env.NEXT_PUBLIC_WS_URL;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws`;
}

function chartTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleTimeString("nl-NL", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function mergeWorkers(previous: LiveWorker[], current: LiveWorker[]): LiveWorker[] {
  return current.map((worker) => {
    const old = previous.find(
      (candidate) => candidate.baan === worker.baan && candidate.job_id === worker.job_id,
    );
    if (!old || worker.job_id === null) return worker;
    const liveKeys: Array<keyof LiveWorker> = [
      "percent",
      "fps",
      "speed",
      "eta_seconden",
      "bitrate_kbps",
      "grootte_nu",
    ];
    const merged = { ...worker };
    let stale = false;
    for (const key of liveKeys) {
      if (worker[key] === null || worker[key] === undefined) {
        Object.assign(merged, { [key]: old[key] });
        stale = true;
      }
    }
    return { ...merged, verouderd: stale };
  });
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const [tick, setTick] = useState<TickMessage | null>(null);
  const [verbonden, setVerbonden] = useState(false);
  const [events, setEvents] = useState<EventMessage[]>([]);
  const [metrics, setMetrics] = useState<LiveState["metrics"]>([]);
  const socket = useRef<WebSocket | null>(null);
  const subscriptions = useRef(new Set<number>());
  const notificationPreferences = useRef({ fouten: false, batches: false });

  useEffect(() => {
    let stopped = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    fetch("/api/instellingen", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((value: unknown) => {
        if (typeof value === "object" && value !== null) {
          const preferences = value as Record<string, unknown>;
          notificationPreferences.current = {
            fouten: preferences.meld_browser_fouten === true,
            batches: preferences.meld_browser_batches === true,
          };
        }
      })
      .catch(() => undefined);

    fetch("/api/systeem/metingen?uren=1", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : [])
      .then((value: unknown) => {
        if (!Array.isArray(value)) return;
        const history = value.map((item) => {
          const metric = item as LiveState["metrics"][number];
          return { ...metric, tijd: chartTime(metric.tijd) };
        }).slice(-3600);
        setMetrics((old) => [...history, ...old].slice(-3600));
      })
      .catch(() => undefined);

    const connect = () => {
      if (stopped) return;
      const connection = new WebSocket(websocketUrl());
      socket.current = connection;
      connection.addEventListener("open", () => {
        retry = 0;
        setVerbonden(true);
        for (const jobId of subscriptions.current) {
          connection.send(JSON.stringify({ actie: "volg", job_id: jobId }));
        }
      });
      connection.addEventListener("message", (message) => {
        const value = JSON.parse(String(message.data)) as TickMessage | EventMessage;
        if (value.type === "tick") {
          setTick((old) => ({
            ...value,
            workers: mergeWorkers(old?.workers ?? [], value.workers),
          }));
          setMetrics((old) => [
            ...old.slice(-3599),
            {
              tijd: chartTime(new Date().toISOString()),
              cpu: value.systeem.cpu_percent,
              gpuVideo: value.systeem.gpu.beschikbaar
                ? value.systeem.gpu.video_percent : null,
              gpuRender: value.systeem.gpu.beschikbaar
                ? value.systeem.gpu.render_percent : null,
              gpuPower: value.systeem.gpu.power_w,
              packagePower: value.systeem.gpu.package_power_w,
              platformPower: value.systeem.gpu.platform_power_w,
              memoryPercent: value.systeem.geheugen?.percent ?? 0,
              memoryUsedBytes: value.systeem.geheugen?.gebruikt_bytes ?? 0,
            },
          ]);
        } else {
          setEvents((old) => [...old.slice(-99), value]);
          try {
            if ("Notification" in window && Notification.permission === "granted") {
              if (value.type === "job_mislukt" && notificationPreferences.current.fouten) {
                new Notification("AV1 Pipeline: job mislukt", { body: value.reden });
              } else if (value.type === "job_klaar" && notificationPreferences.current.batches) {
                new Notification("AV1 Pipeline: job afgerond", { body: `Job #${value.job_id} is veilig geverifieerd.` });
              }
            }
          } catch {
            // Geweigerde of defecte browsermeldingen mogen live bediening nooit breken.
          }
        }
      });
      connection.addEventListener("close", () => {
        setVerbonden(false);
        socket.current = null;
        if (!stopped) {
          const delay = Math.min(30_000, 1_000 * 2 ** retry) + Math.random() * 500;
          retry += 1;
          timer = setTimeout(connect, delay);
        }
      });
      connection.addEventListener("error", () => connection.close());
    };

    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      socket.current?.close();
    };
  }, []);

  const volg = useCallback((jobId: number) => {
    subscriptions.current.add(jobId);
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ actie: "volg", job_id: jobId }));
    }
  }, []);

  const stopVolgen = useCallback((jobId: number) => {
    subscriptions.current.delete(jobId);
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ actie: "stop_volgen", job_id: jobId }));
    }
  }, []);

  const value = useMemo(
    () => ({ tick, verbonden, events, metrics, volg, stopVolgen }),
    [events, metrics, stopVolgen, tick, verbonden, volg],
  );
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveState {
  const context = useContext(LiveContext);
  if (!context) throw new Error("useLive moet binnen LiveProvider worden gebruikt");
  return context;
}
