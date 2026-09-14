import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";
import { exportJWK, generateKeyPair, SignJWT } from "jose";
import { authorize, challenge } from "../dist/auth.js";

test("OAuth verifier accepts only the configured owner, audience, and scope", async (t) => {
  const { publicKey, privateKey } = await generateKeyPair("RS256");
  const jwk = await exportJWK(publicKey); jwk.kid = "test-key"; jwk.use = "sig"; jwk.alg = "RS256";
  const server = createServer((request, response) => {
    response.setHeader("content-type", "application/json");
    response.end(JSON.stringify(request.url?.includes("openid-configuration") ? { jwks_uri: `${base}/jwks` } : { keys: [jwk] }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  const address = server.address(); const base = `http://127.0.0.1:${address.port}/`;
  const config = { resource: "https://mcp.test/money-keeper", issuer: base, audience: "money-keeper", scope: "finance:read", allowedSubject: "owner" };
  const sign = ({ scope = "finance:read", subject = "owner", audience = "money-keeper" } = {}) => new SignJWT({ scope }).setProtectedHeader({ alg: "RS256", kid: "test-key" }).setIssuer(base).setAudience(audience).setSubject(subject).setIssuedAt().setExpirationTime("5m").sign(privateKey);
  assert.equal(await authorize(`Bearer ${await sign()}`, config), true);
  assert.equal(await authorize(`Bearer ${await sign({ scope: "other:read" })}`, config), false);
  assert.equal(await authorize(`Bearer ${await sign({ subject: "someone-else" })}`, config), false);
  assert.equal(await authorize(`Bearer ${await sign({ audience: "another-api" })}`, config), false);
  assert.equal(await authorize(undefined, config), false);
  assert.match(challenge(config), /oauth-protected-resource/);
});
