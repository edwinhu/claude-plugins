# Codebook (hosted JupyterHub) over CDP

LSEG Codebook is a JupyterHub at `https://workspace.refinitiv.com/codebook/`, authenticated
by the same Workspace Web cookies. Its kernels run a pre-authenticated `refinitiv.data`
session (`{name='codebook'}`) with the account's **full** entitlements — which makes it the
most capable surface in principle.

## Status: read/write yes, execute no

**Verified 2026-07-27, Linux + Chromium/CDP.** The REST surface works with browser cookies.
Kernel *execution* does not — the WebSocket channel never completes its handshake.

Caveat on scope: this was observed on **one account, one machine, one (unsupported)
browser**. The block sits in front of JupyterHub, but a stale session and an unsupported
browser have not been ruled out — see "Try these two" below before treating it as
permanent.

| Capability | Endpoint | Status |
|-----------|----------|--------|
| Hub identity / spawn state | `/codebook/hub/api/user` | works |
| List / read / write / delete notebooks | `/user/{name}/api/contents/...` | works (200/201/204) |
| List kernelspecs | `/user/{name}/api/kernelspecs` | works — `python3`, `python3_legacy` |
| Create / delete kernels | `/user/{name}/api/kernels` | works |
| Sessions | `/user/{name}/api/sessions` | works |
| Terminals | `/user/{name}/api/terminals` | 404 (disabled) |
| **Execute code** | `wss://.../api/kernels/{id}/channels` | **refused — close 1006** |

### The execution blocker — an infrastructure block, not an auth problem

Every route to running code fails with close 1006:

- direct `WebSocket` from a page, with and without the
  `v1.kernel.websocket.jupyter.org` subprotocol
- from Python with the browser's cookie jar (Tornado replied `400` once, then upgrades
  began hanging)
- **Codebook's own JupyterLab UI** — driving a real cell via CDP input events typed the
  code and submitted it, and the prompt sat at `[*]` indefinitely with no output, with
  `api/kernels` reporting `connections: 0`

**Do not spend time on headers, cookies, or subprotocols — it is not an app-level problem.**
Traced with the CDP Network domain (`Network.webSocket*` events) on the real Codebook tab:

1. `webSocketCreated` fires.
2. `webSocketWillSendHandshakeRequest` fires with a **fully correct** request — `Upgrade:
   websocket`, `Connection: Upgrade`, `Sec-WebSocket-Key`, `Sec-WebSocket-Version: 13`,
   the subprotocol, `permessage-deflate`, and the complete cookie jar.
3. `webSocketHandshakeResponseReceived` **never fires** — no HTTP response comes back at
   all, not even an error status.
4. ~0.5–1.5s later, `webSocketFrameError` (empty message) then `webSocketClosed`.

The decisive test: a **nonexistent kernel ID** (`00000000-...`) fails *identically* to a
freshly created, valid one. A request that actually reached JupyterHub would distinguish
them with a 404. Identical failure proves an intermediary terminates the connection
**before it reaches the notebook backend**.

Suspect is the F5 BIG-IP in the path (persistence cookie `BIGipServerHDCP-DATACLOUD-VIP-1080`)
or an nginx/CDN hop not passing the Upgrade through. One early raw-socket attempt did get
Tornado's literal `Can "Upgrade" only to "WebSocket".` body back, which shows the backend
is reachable and behaves correctly when a request does get proxied — so the layer may be
flaky rather than a hard deny. In practice it is indistinguishable from always-blocked:
**0 successful handshakes in ~6 attempts across two sessions.**

Ruled out: the CloudFront signed cookies are a red herring — `CloudFront-Policy` scopes its
`Resource` to `https://workspace.refinitiv.com/assets/*`, nothing to do with `/codebook/`.

It is **not** a general WebSocket problem with the browser — from the same tab,
`wss://echo.websocket.org` opens fine while the Codebook kernel channel returns 1006.

Because the official UI is equally broken, scripted access did not cause this.

### Try these two before concluding it is unfixable

The "terminated by an intermediary" evidence is solid, but it does **not** establish that
the cause is purely server-side. Two things were observed later and are untried:

1. **Stale session / oversized cookie jar.** The `Cookie` header for
   `workspace.refinitiv.com` is ~6.9 KB. Proxies commonly reject requests whose headers
   exceed a limit, and a WS upgrade can hit a tighter limit than ordinary GETs — which
   would produce exactly this signature (no response, or a malformed-request 400,
   regardless of kernel ID). Supporting this: the one community report of the same symptom
   was fixed by clearing **cache, history, and active logins** — cache alone was explicitly
   *not* enough ([community thread 133850](https://community.developers.lseg.com/discussion/133850/error-on-kernel-connecting-in-codebook)).
   So: clear site data for the Refinitiv/LSEG domains, sign in fresh, retry.
2. **Unsupported browser.** Workspace Web itself renders a banner: *"BROWSER NOT SUPPORTED
   — Some Workspace Web access features may not work correctly. Please use one of our
   supported browsers."* Chromium on Linux is not on LSEG's supported list. This is weak
   evidence on its own (the non-browser Python attempts failed too, and Chromium's UA
   reports as Chrome), but it is cheap to rule out on a supported browser/OS.

Also note the embedded CodeBook's kernel status reads **"Restarting" / "Waiting…"**, and
the community report mentions *"Kernel seems to have died. It will be restarted
automatically."* A dying kernel is a different failure from a blocked socket, and both may
be in play — do not assume one explains the other.

If both avenues fail, **open an LSEG support ticket**: *"WebSocket upgrade to
`/codebook/user/<id>/api/kernels/*/channels` never completes — no handshake response, 0%
success rate, JupyterLab UI cells stuck at `[*]`."* Note the official answer in the
community thread to this exact symptom was also "submit a ticket to the Helpdesk".

No alternative execution route exists in the exposed API surface: only `contents`,
`kernels`, `sessions` and `kernelspecs` are live — no `terminals`, no run/execute REST
route, no nbconvert or jupyter-server-proxy execution path. None of those run code without
the kernel channel.

**Diagnostic — re-run before assuming Codebook is still blocked:**

```javascript
// in the Codebook tab's console
new WebSocket(location.href.split('/lab')[0] + '/api/kernels/x/channels')
  .onclose = e => console.log('close', e.code);   // 1006 == still blocked
```

If that ever opens, Codebook becomes the best path available — a fully entitled
`refinitiv.data` session that covers what the lifted token cannot, notably **news**
(`/data/news/v1/headlines` is 403 with the Workspace token). Wire it up then.

## What Codebook is still good for

The contents API is fully functional, so Codebook works as a **file exchange with a
human-run kernel**:

1. `PUT api/contents/<path>` a notebook built locally
2. the user opens it in Codebook and runs it (execution works for them interactively —
   subject to the same block, so confirm before relying on it)
3. `GET api/contents/<path>` to pull back the executed notebook **with its outputs**, and
   parse them with the `notebook-debug` skill's approach

You can also read notebooks the user already has. This account's Codebook home holds
`Event Study/`, `Filings/`, `Insurance/`, `SDC/`, `News.ipynb`, `PermID.ipynb`,
`RD_Funds.ipynb`, `SearchBrowserLib.ipynb` and the `__Examples__` / `__Guidelines__`
folders — useful as worked references for LSEG API patterns.

## Access recipe

```python
import json, urllib.request, websocket   # websocket-client

# 1. cookies from the CDP browser
bws = json.loads(urllib.request.urlopen("http://localhost:9222/json/version").read())["webSocketDebuggerUrl"]
ws = websocket.create_connection(bws, timeout=30)
ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
while True:
    m = json.loads(ws.recv())
    if m.get("id") == 1: break
ws.close()
jar = "; ".join(f"{c['name']}={c['value']}" for c in m["result"]["cookies"]
                if c["domain"].endswith("workspace.refinitiv.com"))

# 2. the hub tells you your user name; the server must be spawned first
hub = "https://workspace.refinitiv.com/codebook/"
user = json.loads(urllib.request.urlopen(
    urllib.request.Request(hub + "hub/api/user", headers={"Cookie": jar})).read())["name"]
base = f"{hub}user/{user}/"

# 3. contents API (writes need the _xsrf cookie echoed as a header)
xsrf = [c.split("=", 1)[1] for c in jar.split("; ") if c.startswith("_xsrf=")][0]
req = urllib.request.Request(base + "api/contents", headers={"Cookie": jar})
print(json.loads(urllib.request.urlopen(req).read())["content"][:5])
```

## Gotchas

- **The server must be spawned.** A cold `GET /codebook/` redirects to
  `hub/spawn-pending/<user>` and the `/user/.../api/*` routes return **503** until it comes
  up. Opening `https://workspace.refinitiv.com/codebook/` in a real tab spawns it; bare
  `fetch()` calls do not reliably drive the spawn to completion. Allow ~45s.
- **Lab can get stuck on "Loading…"** with a *"clear the workspace or keep waiting"* prompt.
  Clicking **CLEAR WORKSPACE** fixes it — it resets the lab *layout*, not your files.
- **A first-run tour modal** ("Welcome to CodeBook!") overlays the UI; dismiss with **SKIP**
  before driving anything.
- **Writes need `X-XSRFToken`** set to the `_xsrf` cookie value, or you get a 403.
- **Don't hijack the user's kernels.** Create your own via `POST api/kernels` and
  `DELETE` them when finished; killing a kernel they are using loses their in-memory state.
