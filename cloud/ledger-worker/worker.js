// ebaybiz-ledgers — the only door to the ledger bucket (GH #380, Phase 1).
//
//   GET /k/<key>          the ledger; ETag header
//   PUT /k/<key>          needs If-Match: <etag>, or If-None-Match: * to seed;
//                         the version it replaces is kept as history/<key>/<stamp>
//   GET /h/<key>          JSON list of that key's saved versions
//   GET /h/<key>/<stamp>  one saved version
//
// Only keys in ALLOWED (403 otherwise); only GET and PUT (405 otherwise). No
// DELETE, and no listing outside one allowlisted key's history. Every request
// needs `Authorization: Bearer <LEDGER_TOKEN>` (a Worker secret).
// ALLOWED must equal LEDGERS in lib/ledger_store.py (tests/test_ledger_store.py).

const ALLOWED = [
  "listings_ledger.csv",
  "sales_ledger.csv",
  "unwound_orders.csv",
  "listings_ledger-junk.csv",
  "sales_ledger-junk.csv",
  "unwound_orders-junk.csv",
  "client_ledger-K01.csv",
];
const MAX_BYTES = 5 * 1024 * 1024;

const say = (status, text, headers = {}) => new Response(text, { status, headers });

function authorized(request, secret) {
  const got = new TextEncoder().encode(request.headers.get("Authorization") || "");
  const want = new TextEncoder().encode(`Bearer ${secret || ""}`);
  return Boolean(secret) && got.byteLength === want.byteLength &&
    crypto.subtle.timingSafeEqual(got, want);
}

const unquote = (etag) => (etag || "").trim().replace(/^W\//, "").replace(/^"|"$/g, "");

export default {
  async fetch(request, env) {
    if (!authorized(request, env.LEDGER_TOKEN)) return say(401, "unauthorized");
    if (request.method !== "GET" && request.method !== "PUT") {
      return say(405, "method not allowed", { Allow: "GET, PUT" });
    }
    const m = new URL(request.url).pathname.match(/^\/([kh])\/([^/]+)(?:\/([^/]+))?$/);
    if (!m) return say(404, "no such route");
    const [, route, rawKey, stamp] = m;
    const key = decodeURIComponent(rawKey);
    if (!ALLOWED.includes(key)) return say(403, "key not allowed");

    if (route === "h") {
      if (request.method !== "GET") return say(405, "method not allowed", { Allow: "GET" });
      if (stamp) {
        const obj = await env.BUCKET.get(`history/${key}/${decodeURIComponent(stamp)}`);
        return obj ? say(200, obj.body) : say(404, "no such version");
      }
      const versions = [];
      let cursor;
      do {
        const page = await env.BUCKET.list({ prefix: `history/${key}/`, cursor });
        for (const o of page.objects) {
          versions.push({ stamp: o.key.slice(`history/${key}/`.length), size: o.size });
        }
        cursor = page.truncated ? page.cursor : undefined;
      } while (cursor);
      return say(200, JSON.stringify(versions), { "Content-Type": "application/json" });
    }
    if (stamp) return say(404, "no such route");

    if (request.method === "GET") {
      const obj = await env.BUCKET.get(key);
      if (!obj) return say(404, "not seeded");
      return say(200, obj.body, { ETag: obj.httpEtag, "Content-Type": "text/csv" });
    }

    // PUT
    const ifMatch = request.headers.get("If-Match");
    const seeding = (request.headers.get("If-None-Match") || "").trim() === "*";
    if (!ifMatch && !seeding) return say(428, "If-Match or If-None-Match: * required");
    const body = await request.arrayBuffer();
    if (body.byteLength > MAX_BYTES) return say(413, "over 5 MB");

    const prev = await env.BUCKET.get(key);
    if (seeding ? prev : (!prev || prev.etag !== unquote(ifMatch))) {
      return say(412, "precondition failed");
    }
    if (prev) {
      const stampNow = new Date().toISOString().replace(/[:.]/g, "-");
      await env.BUCKET.put(`history/${key}/${stampNow}`, prev.body);
    }
    // The conditional put closes the gap between the check above and now.
    const opts = prev ? { onlyIf: { etagMatches: prev.etag } } : {};
    const put = await env.BUCKET.put(key, body, opts);
    if (!put) return say(412, "precondition failed");
    return say(200, "ok", { ETag: put.httpEtag });
  },
};
