// Dedicated ports; fail rather than connect to a developer's running service.
export const API_PORT = Number(process.env.EVALOPS_E2E_API_PORT ?? 8001);
export const WEB_PORT = Number(process.env.EVALOPS_E2E_WEB_PORT ?? 5174);
for (const port of [API_PORT, WEB_PORT]) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid E2E port");
}
if (API_PORT === WEB_PORT) throw new Error("E2E API and web ports must differ");
export const API_URL = `http://127.0.0.1:${API_PORT}`;
export const WEB_URL = `http://127.0.0.1:${WEB_PORT}`;
