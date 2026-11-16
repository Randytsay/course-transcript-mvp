import assert from "node:assert/strict";
import test from "node:test";
import { generateKeyPairSync, sign } from "node:crypto";
import { isVerifiedGoldenV2PaidOperator } from "../../lib/golden-v2-paid-console-auth.server.ts";

const team = "owner-test.cloudflareaccess.com";
const audience = "0123456789abcdef0123456789abcdef";
const email = "operator@example.test";
const env = {
  NODE_ENV: "production", COURSE_TRANSCRIPT_V2_ADMIN_UI_ENABLED: "true",
  COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ENABLED: "true",
  ADMIN_PUBLIC_HOSTNAMES: "transcript.example.test",
  COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ACCESS_TEAM_DOMAIN: team,
  COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ACCESS_AUD: audience,
  COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ALLOWED_EMAILS: email,
};
const { publicKey, privateKey } = generateKeyPairSync("rsa",{ modulusLength:2048 });
const jwk = { ...publicKey.export({format:"jwk"}),kid:"test-key",use:"sig" };

function token(claim = {}, key = privateKey) {
  const now = Math.floor(Date.now()/1000);
  const header = Buffer.from(JSON.stringify({alg:"RS256",kid:"test-key"})).toString("base64url");
  const payload = Buffer.from(JSON.stringify({
    iss:"https://" + team, aud:[audience], email, sub:"user1", iat:now-10,exp:now+1000,...claim,
  })).toString("base64url");
  const sig = sign("RSA-SHA256",Buffer.from(header+"."+payload),key).toString("base64url");
  return header+"."+payload+"."+sig;
}
function headers(jwt) {
  const map = {
    host:"transcript.example.test",
    "cf-access-authenticated-user-email":email,
    "cf-access-jwt-assertion":jwt,
  };
  return {get(name){return map[name.toLowerCase()]??null;}};
}
let networkCalls=0;
const mockFetch = async (url) => {
  networkCalls++;
  assert.equal(url,"https://"+team+"/cdn-cgi/access/certs");
  return { ok:true, json:async()=>({keys:[jwk]}) };
};

test("signed Cloudflare Access JWT + exact owner email and audience permitted",async()=>{
  assert.equal(await isVerifiedGoldenV2PaidOperator(headers(token()),env,mockFetch),true);
});

test("forged Access identity header does not substitute for valid JWT",async()=>{
  const {privateKey:wrongKey} = generateKeyPairSync("rsa",{modulusLength:2048});
  assert.equal(await isVerifiedGoldenV2PaidOperator(headers(token({},wrongKey)),env,mockFetch),false);
  assert.equal(await isVerifiedGoldenV2PaidOperator(headers("not a jwt"),env,mockFetch),false);
});

test("wrong audience, expiration, email, issuer or disabled feature rejects",async()=>{
  const bad = [
    {aud:["wrong-audience"]}, {exp:Math.floor(Date.now()/1000)-1},
    {email:"someone-else@example.test"}, {iss:"https://evil.example.test"},
    {nbf:Math.floor(Date.now()/1000)+1000},
  ];
  for (const claim of bad) {
    assert.equal(await isVerifiedGoldenV2PaidOperator(headers(token(claim)),env,mockFetch),false);
  }
  assert.equal(await isVerifiedGoldenV2PaidOperator(headers(token()),{
    ...env,COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ENABLED:"false"
  },mockFetch),false);
  assert.equal(await isVerifiedGoldenV2PaidOperator(headers(token()),{
    ...env,COURSE_TRANSCRIPT_V2_PAID_CONSOLE_ACCESS_TEAM_DOMAIN:"evil.example.test"
  },mockFetch),false);
});

test("key retrieval outage rejects instead of trusting headers",async()=>{
  assert.equal(await isVerifiedGoldenV2PaidOperator(headers(token()),env,async()=>{
    throw Error("key service unavailable");
  }),false);
});
