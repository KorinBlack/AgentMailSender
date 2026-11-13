// Thin fetch wrapper for the operator API. Paths are relative so the app works behind a sub-path.

let csrfToken = null;
let unauthorizedHandler = () => {};

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export function setCsrf(token) { csrfToken = token; }
export function onUnauthorized(fn) { unauthorizedHandler = fn; }

async function request(method, path, body) {
  const headers = { Accept: "application/json" };
  const init = { method, headers, credentials: "same-origin" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError("Cannot reach the server. Check the connection and try again.", 0, null);
  }
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json().catch(() => null) : null;
  if (res.status === 401 && !path.startsWith("api/auth/")) unauthorizedHandler();
  if (!res.ok) {
    const message = (data && typeof data.detail === "string" && data.detail) || `Request failed (${res.status}).`;
    throw new ApiError(message, res.status, data);
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body = {}) => request("POST", path, body),
  put: (path, body) => request("PUT", path, body),
  del: (path) => request("DELETE", path),
};
