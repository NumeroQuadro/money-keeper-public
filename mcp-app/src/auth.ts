import { createRemoteJWKSet, jwtVerify } from "jose";
export type AuthConfig = { resource: string; issuer: string; audience: string; scope: string; allowedSubject: string };
let cached: { issuer: string; jwks: ReturnType<typeof createRemoteJWKSet> } | undefined;
export function authConfig(): AuthConfig {
  const values = { resource: process.env.MCP_RESOURCE ?? "", issuer: process.env.OAUTH_ISSUER ?? "", audience: process.env.OAUTH_AUDIENCE ?? "", scope: process.env.OAUTH_REQUIRED_SCOPE ?? "finance:read", allowedSubject: process.env.OAUTH_ALLOWED_SUBJECT ?? "" };
  if (process.env.NODE_ENV !== "test" && Object.values(values).some((value) => !value)) throw new Error("MCP OAuth environment is incomplete");
  return values;
}
export function challenge(config: AuthConfig) { return `Bearer resource_metadata="${config.resource}/.well-known/oauth-protected-resource", scope="${config.scope}"`; }
export async function authorize(header: string | undefined, config: AuthConfig): Promise<boolean> {
  const token = /^Bearer\s+(.+)$/i.exec(header ?? "")?.[1]; if (!token) return false;
  try {
    if (!cached || cached.issuer !== config.issuer) {
      const response = await fetch(new URL(".well-known/openid-configuration", config.issuer)); if (!response.ok) return false;
      const metadata = await response.json() as { jwks_uri?: string }; if (!metadata.jwks_uri) return false;
      cached = { issuer: config.issuer, jwks: createRemoteJWKSet(new URL(metadata.jwks_uri)) };
    }
    const result = await jwtVerify(token, cached.jwks, { issuer: config.issuer, audience: config.audience, algorithms: ["RS256"] });
    return result.payload.sub === config.allowedSubject && new Set(String(result.payload.scope ?? "").split(/\s+/)).has(config.scope);
  } catch { return false; }
}
