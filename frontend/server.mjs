import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { isIP } from "node:net";

import next from "next";

const host = process.env.LAN_LISTEN_HOST ?? "0.0.0.0";
const port = Number.parseInt(process.env.PORT ?? "3000", 10);
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
const development = process.env.NODE_ENV !== "production";
const realm = 'Basic realm="AV1 Pipeline", charset="UTF-8"';
const successCache = new Map();
const pendingChecks = new Map();
const failedAttempts = new Map();

function normalizedAddress(value) {
  return value?.startsWith("::ffff:") ? value.slice(7) : value ?? "";
}

function isLoopback(value) {
  const address = normalizedAddress(value);
  return address === "::1" || address.startsWith("127.");
}

function isPrivateNetwork(value) {
  const address = normalizedAddress(value);
  if (isIP(address) === 4) {
    const octets = address.split(".").map(Number);
    return (
      octets[0] === 10 ||
      (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
      (octets[0] === 192 && octets[1] === 168) ||
      (octets[0] === 169 && octets[1] === 254)
    );
  }
  const lowered = address.toLowerCase();
  return lowered.startsWith("fc") || lowered.startsWith("fd") || lowered.startsWith("fe8") ||
    lowered.startsWith("fe9") || lowered.startsWith("fea") || lowered.startsWith("feb");
}

function clearAuthorizationCache(pathname) {
  if (pathname.startsWith("/api/lan-toegang")) successCache.clear();
}

function failureState(address) {
  const now = Date.now();
  const previous = failedAttempts.get(address);
  if (!previous || now - previous.startedAt > 60_000) {
    const fresh = { count: 0, startedAt: now, blockedUntil: 0 };
    failedAttempts.set(address, fresh);
    return fresh;
  }
  return previous;
}

async function checkAuthorization(request) {
  const address = normalizedAddress(request.socket.remoteAddress);
  if (isLoopback(address)) return { allowed: true, status: 204 };
  if (!isPrivateNetwork(address)) return { allowed: false, status: 403 };

  const failure = failureState(address);
  if (failure.blockedUntil > Date.now()) return { allowed: false, status: 429 };
  const authorization = request.headers.authorization;
  if (!authorization) return { allowed: false, status: 401 };

  const key = createHash("sha256").update(authorization).digest("hex");
  const cachedUntil = successCache.get(key) ?? 0;
  if (cachedUntil > Date.now()) return { allowed: true, status: 204 };
  if (pendingChecks.has(key)) return pendingChecks.get(key);

  const pending = (async () => {
    try {
      const response = await fetch(`${backend}/api/lan-toegang/autoriseer`, {
        headers: { authorization },
        signal: AbortSignal.timeout(5_000),
      });
      if (response.status === 204) {
        successCache.set(key, Date.now() + 30_000);
        failedAttempts.delete(address);
        return { allowed: true, status: 204 };
      }
      if (response.status === 401) {
        failure.count += 1;
        if (failure.count >= 5) failure.blockedUntil = Date.now() + 30_000;
      }
      return { allowed: false, status: response.status };
    } catch {
      return { allowed: false, status: 503 };
    } finally {
      pendingChecks.delete(key);
    }
  })();
  pendingChecks.set(key, pending);
  return pending;
}

function httpRejection(response, status) {
  const headers = { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" };
  if (status === 401) headers["WWW-Authenticate"] = realm;
  response.writeHead(status, headers);
  const messages = {
    401: "Aanmelding voor AV1 Pipeline vereist.\n",
    403: "LAN-toegang staat uit of dit is geen privénetwerk.\n",
    429: "Te veel mislukte aanmeldpogingen; probeer het over 30 seconden opnieuw.\n",
    503: "De lokale AV1 Pipeline-backend is tijdelijk niet bereikbaar.\n",
  };
  response.end(messages[status] ?? "Toegang geweigerd.\n");
}

function socketRejection(socket, status) {
  const reason = status === 401 ? "Unauthorized" : status === 429 ? "Too Many Requests" :
    status === 503 ? "Service Unavailable" : "Forbidden";
  const authenticate = status === 401 ? `WWW-Authenticate: ${realm}\r\n` : "";
  socket.end(
    `HTTP/1.1 ${status} ${reason}\r\n${authenticate}Connection: close\r\n` +
      "Cache-Control: no-store\r\nContent-Length: 0\r\n\r\n",
  );
}

const application = next({ dev: development, hostname: host, port });
await application.prepare();
const handle = application.getRequestHandler();
const handleUpgrade = application.getUpgradeHandler();
const server = createServer(async (request, response) => {
  const authorization = await checkAuthorization(request);
  if (!authorization.allowed) {
    httpRejection(response, authorization.status);
    return;
  }
  response.once("finish", () => clearAuthorizationCache(request.url ?? ""));
  await handle(request, response);
});

server.on("upgrade", async (request, socket, head) => {
  const authorization = await checkAuthorization(request);
  if (!authorization.allowed) {
    socketRejection(socket, authorization.status);
    return;
  }
  await handleUpgrade(request, socket, head);
});

server.listen(port, host, () => {
  console.log(`AV1 Pipeline webinterface luistert beveiligd op ${host}:${port}`);
});
