"""Network helpers for the PvZ Hybrid auto-updater: version API, bilibili post, Quark share/drive, OpenList.

Secrets (Quark cookie, OpenList token) are read at runtime from the user's own OpenList install and are never
printed or written anywhere else.
"""
import html
import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
OPENLIST = "http://127.0.0.1:5244"
OPENLIST_DB = os.path.expanduser("~/openlist/data/data.db")
QUARK_MOUNT = "/quark"
SHARE_DIR_NAME = "来自：分享"          # Quark's default folder for saved shares
QUARK_API = "https://drive-pc.quark.cn/1/clouddrive"
QUARK_Q = {"pr": "ucpro", "fr": "pc"}


def _req(url, data=None, headers=None, method=None, timeout=30, opener=None):
    h = {"User-Agent": UA}
    h.update(headers or {})
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    with (opener.open if opener else urllib.request.urlopen)(r, timeout=timeout) as resp:
        return resp.read()


def _json(url, **kw):
    return json.loads(_req(url, **kw))


# ---------------------------------------------------------------- version API / bilibili

def latest_pc_version():
    """Ask the game's own update endpoint (same request the game makes on startup)."""
    d = _json("https://api.pvzhe.com/new_version", headers={"X-PVZHE-Client-Version": "0.30.0.0"})
    pc = d["pc"]
    return ".".join(str(x) for x in pc["version"]), pc["url"], d


def windows_share_from_post(post_url, known_non_pc=()):
    """Return (share_id, passcode, post_text) for the Windows build linked in the author's bilibili post."""
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    _req("https://www.bilibili.com/", opener=opener)
    s = _req(post_url, headers={"Referer": "https://www.bilibili.com/"}, opener=opener).decode("utf-8", "replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    links = [(i, m.group(1)) for i, l in enumerate(lines) for m in [re.search(r"pan\.quark\.cn/s/([0-9a-zA-Z]+)", l)] if m]
    if not links:
        raise RuntimeError("no Quark link found in bilibili post")
    pick = None
    for i, sid in links:                          # prefer the link right after a "电脑/Windows/PC" label
        ctx = " ".join(lines[max(0, i - 2):i + 1])
        if re.search(r"电脑|Windows|PC", ctx, re.I) and sid not in known_non_pc:
            pick = (i, sid)
            break
    if pick is None:
        cands = [x for x in links if x[1] not in known_non_pc]
        if len(cands) != 1:
            raise RuntimeError(f"cannot decide which Quark link is the PC build: {[x[1] for x in links]}")
        pick = cands[0]
    i, sid = pick
    m = re.search(r"提取码[:：]?\s*([0-9a-zA-Z]{4})", " ".join(lines[i:i + 3]))
    return sid, (m.group(1) if m else ""), "\n".join(lines[:40])


# ---------------------------------------------------------------- Quark

def quark_cookie():
    db = sqlite3.connect(f"file:{OPENLIST_DB}?mode=ro", uri=True)
    try:
        row = db.execute("select addition from x_storages where mount_path=? and driver='Quark'", (QUARK_MOUNT,)).fetchone()
    finally:
        db.close()
    if not row:
        raise RuntimeError("OpenList has no Quark storage mounted at /quark")
    return json.loads(row[0])["cookie"]


def _quark(path, params=None, body=None, cookie=None, method=None):
    q = dict(QUARK_Q)
    q.update(params or {})
    h = {"Referer": "https://pan.quark.cn/", "Origin": "https://pan.quark.cn"}
    if cookie:
        h["Cookie"] = cookie
    d = _json(f"{QUARK_API}/{path}?{urllib.parse.urlencode(q)}", data=body, headers=h, method=method)
    if d.get("code") not in (0, None) or d.get("status") not in (200, None):
        raise RuntimeError(f"Quark API {path}: code={d.get('code')} message={d.get('message')}")
    return d


def share_list(share_id, passcode=""):
    """Anonymous listing of a share's top level. Returns (stoken, share_info, files)."""
    st = _quark("share/sharepage/token", body={"pwd_id": share_id, "passcode": passcode})["data"]["stoken"]
    d = _quark("share/sharepage/detail", params={"pwd_id": share_id, "stoken": st, "pdir_fid": "0", "force": "0",
                                                 "_page": "1", "_size": "100", "_fetch_banner": "0",
                                                 "_fetch_share": "1", "_fetch_total": "1",
                                                 "_sort": "file_type:asc,file_name:asc"})
    return st, d["data"].get("share", {}), d["data"]["list"]


def drive_list(cookie, pdir_fid="0"):
    out, page = [], 1
    while True:
        d = _quark("file/sort", params={"pdir_fid": pdir_fid, "_page": str(page), "_size": "100",
                                        "_fetch_total": "1", "_sort": "file_type:asc,updated_at:desc"}, cookie=cookie)
        out += d["data"]["list"]
        if len(out) >= d["metadata"].get("_total", 0) or not d["data"]["list"]:
            return out
        page += 1


def share_dir_fid(cookie):
    for f in drive_list(cookie):
        if f.get("dir") and f["file_name"] == SHARE_DIR_NAME:
            return f["fid"]
    raise RuntimeError(f"folder {SHARE_DIR_NAME} not found in Quark drive root")


def save_share(cookie, share_id, stoken, files, to_pdir_fid, timeout=300):
    """Save files from a share into the user's drive; waits for the async task to finish."""
    d = _quark("share/sharepage/save", body={
        "fid_list": [f["fid"] for f in files],
        "fid_token_list": [f["share_fid_token"] for f in files],
        "to_pdir_fid": to_pdir_fid, "pwd_id": share_id, "stoken": stoken, "pdir_fid": "0", "scene": "link",
    }, cookie=cookie, method="POST")
    task_id = d["data"]["task_id"]
    for i in range(timeout // 2):
        t = _quark("task", params={"task_id": task_id, "retry_index": str(i)}, cookie=cookie)["data"]
        if t.get("status") == 2:
            return t
        time.sleep(2)
    raise RuntimeError("Quark save task timed out")


# ---------------------------------------------------------------- OpenList

def openlist_token():
    out = subprocess.run(["docker", "exec", "-w", "/opt/openlist", "openlist", "./openlist", "admin", "token",
                          "--data", "/opt/openlist/data"], capture_output=True, text=True, check=True).stdout
    m = re.findall(r"openlist-[A-Za-z0-9-]+|[A-Za-z0-9._-]{40,}", out)
    if not m:
        raise RuntimeError("could not read OpenList admin token")
    return m[-1]


def ol(token, api, body):
    d = _json(OPENLIST + api, data=body, headers={"Authorization": token}, method="POST", timeout=120)
    if d.get("code") != 200:
        raise RuntimeError(f"OpenList {api}: {d.get('code')} {d.get('message')}")
    return d["data"]


def ol_list(token, path, refresh=True):
    return ol(token, "/api/fs/list", {"path": path, "page": 1, "per_page": 500, "refresh": refresh}).get("content") or []


def ol_download(token, path, dest, expected_size):
    info = ol(token, "/api/fs/get", {"path": path})
    url = f"{OPENLIST}/d{urllib.parse.quote(path)}"
    if info.get("sign"):
        url += "?sign=" + info["sign"]
    tmp = dest + ".part"
    subprocess.run(["curl", "-sS", "-L", "--fail", "--retry", "3", "-C", "-", "-o", tmp, url], check=True)
    size = os.path.getsize(tmp)
    if size != expected_size:
        raise RuntimeError(f"downloaded size {size} != expected {expected_size}")
    os.replace(tmp, dest)
    return dest
