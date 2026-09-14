import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { registerAppResource, registerAppTool, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";
import { authConfig, authorize, challenge } from "./auth.js";
import { loadOverview, type OverviewInput } from "./data.js";

const PORT = Number(process.env.PORT ?? 8787);
const MCP_PATH = "/mcp";
const WIDGET_URI = "ui://money-keeper/monthly-overview.html";
const html = readFileSync(fileURLToPath(new URL("../public/overview.html", import.meta.url)), "utf8");
const inputSchema = { period: z.enum(["current_month", "previous_month", "last_3_months"]).optional(), accountId: z.string().optional(), category: z.string().optional() };
const oauthScheme = { type: "oauth2" as const, scopes: [process.env.OAUTH_REQUIRED_SCOPE ?? "finance:read"] };

function result(data: Awaited<ReturnType<typeof loadOverview>>, intro: string) {
  return { content: [{ type: "text" as const, text: `${intro} ${data.periodLabel}: spent ₽${Math.round(data.outflow).toLocaleString("en-US")}, income ₽${Math.round(data.inflow).toLocaleString("en-US")}, net ₽${Math.round(data.net).toLocaleString("en-US")}. ${data.reviewCount} item(s) need review.` }], structuredContent: data };
}

export function createMoneyKeeperMcpServer() {
  const server = new McpServer({ name: "money-keeper", version: "0.1.0" });
  registerAppResource(server, "Money Keeper monthly overview", WIDGET_URI, {}, async () => ({ contents: [{ uri: WIDGET_URI, mimeType: RESOURCE_MIME_TYPE, text: html, _meta: { ui: { prefersBorder: true } } }] }));
  const common = { inputSchema, annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false }, securitySchemes: [oauthScheme] };
  registerAppTool(server, "get_finance_overview", {
    title: "Get Money Keeper overview data",
    description: "Read normalized Money Keeper cashflow, categories, balances, review count, and recent transactions for a period or account.",
    ...common, _meta: { securitySchemes: [oauthScheme] },
  }, async (input: OverviewInput) => result(await loadOverview(input), "Money Keeper data."));
  registerAppTool(server, "show_finance_overview", {
    title: "Show Money Keeper overview",
    description: "Display the interactive Money Keeper monthly overview. Use when the user asks about spending, cashflow, balances, categories, recent activity, or items needing review.",
    ...common,
    _meta: { securitySchemes: [oauthScheme], ui: { resourceUri: WIDGET_URI }, "openai/toolInvocation/invoking": "Loading Money Keeper…", "openai/toolInvocation/invoked": "Money Keeper is ready." },
  }, async (input: OverviewInput) => result(await loadOverview(input), "Here is the live Money Keeper overview."));
  return server;
}

function cors(res: ServerResponse) { res.setHeader("Access-Control-Allow-Origin", "*"); res.setHeader("Access-Control-Expose-Headers", "Mcp-Session-Id, WWW-Authenticate"); }
async function handleMcp(req: IncomingMessage, res: ServerResponse) {
  cors(res); const server = createMoneyKeeperMcpServer(); const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
  res.on("close", () => { void transport.close(); void server.close(); });
  try { await server.connect(transport); await transport.handleRequest(req, res); }
  catch (error) { console.error("MCP request failed", error); if (!res.headersSent) { res.writeHead(500, { "content-type": "application/json" }); res.end(JSON.stringify({ error: "Internal server error" })); } }
}

export const httpServer = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`); cors(res);
  if (req.method === "GET" && url.pathname === "/healthz") { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify({ status: "ok" })); return; }
  const config = authConfig();
  if (req.method === "GET" && url.pathname === "/.well-known/oauth-protected-resource") { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify({ resource: config.resource, authorization_servers: [config.issuer], scopes_supported: [config.scope] })); return; }
  if (req.method === "OPTIONS" && url.pathname === MCP_PATH) { res.writeHead(204, { "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS", "Access-Control-Allow-Headers": "authorization, content-type, mcp-session-id" }); res.end(); return; }
  if (url.pathname === MCP_PATH && req.method && ["POST", "GET", "DELETE"].includes(req.method)) {
    if (!(await authorize(req.headers.authorization, config))) { res.writeHead(401, { "content-type": "application/json", "WWW-Authenticate": challenge(config) }); res.end(JSON.stringify({ error: "unauthorized" })); return; }
    await handleMcp(req, res); return;
  }
  res.writeHead(404).end("Not found");
});

if (process.env.NODE_ENV !== "test") httpServer.listen(PORT, "0.0.0.0", () => console.log(`Money Keeper MCP listening on :${PORT}${MCP_PATH}`));
