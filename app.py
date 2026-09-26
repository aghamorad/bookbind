#!/usr/bin/env python3
"""Bookbind -- merge a folder of loose audio files into one chaptered .m4b.

Local web app. Nothing is ever deleted: originals are moved into an `_originals`
subfolder only after the output passes verification, and can be restored with one
click. Run with:  python3 app.py
"""
import json
import os
import plistlib
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
PORT = 8765
ORIG = "_originals"
REPO = "aghamorad/bookbind"

# What a bare checkout reports. A released .app carries an Info.plist and that
# number wins, because it is the one this copy was actually cut at.
VERSION = "1.1.0"


def version():
    """The version of the copy that is running, and never a guess.

    The bundle's Info.plist is the authority, and it sits in a different place
    depending on how this copy was put together: above the server inside a
    self-contained build (the server is copied into `Contents/Resources`),
    beside it in a release, and inside the .app in a checkout. A copy with no
    plist anywhere is the bare source, and the constant above is its answer.
    """
    for candidate in (os.path.join(HERE, os.pardir, "Info.plist"),
                      os.path.join(HERE, "Info.plist"),
                      os.path.join(HERE, "Bookbind.app", "Contents", "Info.plist")):
        try:
            with open(candidate, "rb") as fh:
                found = plistlib.load(fh).get("CFBundleShortVersionString")
        except Exception:
            continue
        if found and str(found).strip():
            return str(found).strip()
    return VERSION

AUDIO_EXT = {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".opus", ".wav", ".ogg", ".wma"}
AAC_EXT = {".m4a", ".m4b", ".aac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
SKIP_EXT = {".aa"}                     # Audible-encrypted, needs keys we do not have

from mutagen import File as MFile


# ---------------------------------------------------------------- audio helpers

def natkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def scan_audio(root):
    """Every audio file under root, ignoring mac metadata and our own folders."""
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".") and d != ORIG
                  and d != "__MACOSX"]
        for f in sorted(fns):
            if f.startswith("._") or f.startswith("."):
                continue
            if os.path.splitext(f)[1].lower() in AUDIO_EXT:
                out.append(os.path.join(dp, f))
    return sorted(out, key=lambda p: natkey(os.path.relpath(p, root)))


def probe(fp):
    """(seconds, bitrate, sample_rate, channels) -- None entries when unknown."""
    try:
        i = MFile(fp).info
        return (getattr(i, "length", None), getattr(i, "bitrate", None),
                getattr(i, "sample_rate", None), getattr(i, "channels", None))
    except Exception:
        return (None, None, None, None)


def duration(fp):
    return probe(fp)[0]


def order_files(files, root, method="auto"):
    """Track/disc tags win when every file carries a number; else natural path order."""
    if method == "path":
        return files, "path"
    tagged = []
    for f in files:
        t = d = None
        try:
            a = MFile(f, easy=True)
            t = (a.get("tracknumber") or [None])[0]
            d = (a.get("discnumber") or [None])[0]
            t = int(str(t).split("/")[0]) if t else None
            d = int(str(d).split("/")[0]) if d else 1
        except Exception:
            t = None
        tagged.append((d or 1, t, f))
    if all(t is not None for _, t, _ in tagged):
        if len({t for _, t, _ in tagged}) == len(tagged):
            return [f for _, _, f in sorted(tagged, key=lambda r: (r[0], r[1]))], "tags"
    return files, "path"


def first_tag(fp, key):
    try:
        v = (MFile(fp, easy=True).get(key) or [None])[0]
    except Exception:
        return None
    if not v or re.search(r"unknown|various|^[\W_]*$", str(v), re.I):
        return None
    return str(v).strip()


# ------------------------------------------------------------- catalogue lookup

def itunes_search(author, title, limit=12):
    terms = [t for t in (author, title) if t]
    if not terms:
        return []
    q = urllib.parse.urlencode({"term": " ".join(terms), "media": "audiobook",
                                "entity": "audiobook", "limit": limit, "country": "us"})
    req = urllib.request.Request("https://itunes.apple.com/search?" + q,
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    out = []
    for c in data.get("results", []):
        art = (c.get("artistName") or "").strip()
        parts = [p.strip() for p in re.split(r"[,/&]| - ", art) if p.strip()]
        out.append({
            "title": c.get("collectionName") or "",
            "author": parts[0] if parts else art,
            "narrator": ", ".join(parts[1:]) if len(parts) > 1 else "",
            "year": (c.get("releaseDate") or "")[:4],
            "cover": (c.get("artworkUrl100") or "").replace("100x100bb", "1200x1200bb"),
            "narrator_note": "catalogue lists", "source": "iTunes",
        })
    return out


def fetch_cover(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        blob = r.read()
    tmp = dest + ".dl"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    # normalise to jpeg -- an h264 or odd png in attached_pic trips the muxer
    r = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", tmp,
                        "-frames:v", "1", "-q:v", "3", dest],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(dest):
        os.replace(tmp, dest)
    elif os.path.exists(tmp):
        os.remove(tmp)
    return dest


# ------------------------------------------------------------------ build steps

def build_plan(folder, author, title, year, narrator, cover_path, order_method="auto",
               bitrate_kbps=None):
    """Everything the encoder needs, or an error string explaining what is wrong."""
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        return None, "that folder does not exist"
    files = scan_audio(folder)
    if not files:
        return None, "no audio files in that folder"

    exts = {os.path.splitext(f)[1].lower() for f in files}
    if exts & SKIP_EXT:
        return None, f"encrypted Audible source ({', '.join(sorted(exts & SKIP_EXT))}) needs keys"
    if len(files) < 2:
        return None, "only one audio file -- nothing to merge"

    # a partial output from an earlier attempt is an audio file sitting in the same
    # folder; it must never be picked up as one of its own sources
    out_dir = folder
    out_name = safe_name(author, title, year) + ".m4b"
    out = os.path.join(out_dir, out_name)
    files = [f for f in files if os.path.abspath(f) != os.path.abspath(out)]
    if len(files) < 2:
        return None, "only one audio file -- nothing to merge"

    if not author or not title:
        return None, "author and title are both required to name the file"

    # every source must be readable before we commit to a long encode
    bad, total, params = [], 0.0, set()
    for f in files:
        secs, br, sr, ch = probe(f)
        if not secs or secs <= 0:
            bad.append(os.path.basename(f))
            continue
        total += secs
        params.add((sr, ch))
    if bad:
        return None, ("these files could not be read: " + ", ".join(bad[:4])
                      + (f" (+{len(bad)-4} more)" if len(bad) > 4 else ""))

    ordered, how = order_files(files, folder, order_method)

    lossless = exts <= AAC_EXT and len(params) == 1
    br = None
    if not lossless:
        if bitrate_kbps:
            br = int(bitrate_kbps) * 1000
        else:
            brs = [b for b in (probe(f)[1] for f in ordered) if b]
            br = max(brs) if brs else 64000
        br = max(64000, min(br, 160000))

    if not cover_path:
        for c in ("cover.jpg", "folder.jpg", "Cover.jpg", "cover.png", "folder.png"):
            p = os.path.join(folder, c)
            if os.path.exists(p):
                cover_path = p
                break
    if not cover_path:
        imgs = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                if os.path.splitext(f)[1].lower() in IMAGE_EXT]
        cover_path = imgs[0] if imgs else None

    free = shutil.disk_usage(out_dir).free
    need = total * ((br or 128000) / 8) * 1.15
    if free < need:
        return None, (f"not enough space: need about {need/1e9:.1f} GB, "
                      f"{free/1e9:.1f} GB free")

    return dict(folder=folder, files=ordered, out=out, out_name=out_name,
                total_secs=total, lossless=lossless, bitrate=br, order=how,
                author=author, title=title, year=str(year or ""),
                narrator=narrator or "", cover=cover_path,
                n=len(ordered), est_bytes=int(need)), None


def safe_name(author, title, year):
    def clean(s):
        s = (s or "").strip()
        # a colon is nearly always "Title: Subtitle", so keep the space the
        # colon was holding instead of collapsing it into a bare dash
        s = re.sub(r":\s+", " - ", s)
        s = re.sub(r"[/\\:*?\"<>|]", "-", s)
        return re.sub(r"\s+", " ", s).strip(" .")
    base = f"{clean(author)}, {clean(title)}"
    if str(year or "").strip():
        base += f" ({clean(str(year))})"
    return base


def write_concat(files, path):
    with open(path, "w", encoding="utf-8") as fh:
        for f in files:
            fh.write("file '" + f.replace("'", "'\\''") + "'\n")


def write_chapters(plan, path):
    meta = [";FFMETADATA1",
            f"title={plan['title']}",
            f"artist={plan['author']}",
            f"album={plan['title']}"]
    if plan["year"]:
        meta.append(f"date={plan['year']}")
    if plan["narrator"]:
        meta.append(f"composer={plan['narrator']}")
        meta.append(f"comment=Narrated by {plan['narrator']}")
    meta.append("genre=Audiobook")
    meta.append(f"media_type=2")
    cur = 0.0
    for i, f in enumerate(plan["files"], 1):
        d = duration(f) or 0.0
        label = os.path.splitext(os.path.basename(f))[0]
        label = re.sub(r"^\s*\d{1,3}\s*[-_.)\]]\s*", "", label).strip() or label
        meta += ["[CHAPTER]", "TIMEBASE=1/1000",
                 f"START={int(cur * 1000)}", f"END={int((cur + d) * 1000)}",
                 f"title={label}"]
        cur += d
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(meta) + "\n")


def ffmpeg_command(plan, concat, chapters, out):
    # every -i must come before the output options: an -i placed after -map_metadata
    # makes ffmpeg bind that option to the wrong input and it refuses to run
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
           "-f", "concat", "-safe", "0", "-i", concat, "-i", chapters]
    if plan["cover"]:
        cmd += ["-i", plan["cover"]]
    cmd += ["-map_metadata", "1", "-map_chapters", "1", "-dn", "-sn", "-map", "0:a"]
    if plan["cover"]:
        cmd += ["-map", "2:v:0"]
    else:
        cmd += ["-map", "0:v:0?"]           # keep cover art already in the sources
    cmd += ["-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
    cmd += ["-c:a", "copy"] if plan["lossless"] else ["-c:a", "aac", "-b:a", str(plan["bitrate"])]
    cmd += ["-movflags", "+faststart", out]
    return cmd


# ------------------------------------------------------------------ verification

def ffprobe_duration(fp):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", fp], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def count_chapters(fp):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", fp],
                       capture_output=True, text=True)
    try:
        return len(json.loads(r.stdout).get("chapters", []))
    except Exception:
        return 0


def has_attached_pic(fp):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v",
                        "-show_entries", "stream_disposition=attached_pic",
                        "-of", "csv=p=0", fp], capture_output=True, text=True)
    return "1" in r.stdout


def decodes(fp, start=None, length=12):
    """Decode a slice to nowhere -- catches a truncated or malformed file."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-xerror"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-t", str(length), "-i", fp, "-f", "null", "-"]
    return subprocess.run(cmd, capture_output=True, text=True).returncode == 0


def verify(plan, out):
    """Read the finished file back and prove it against what went in."""
    checks, dur = [], ffprobe_duration(out)
    if dur <= 0:
        return False, [("readable", False, "the file could not be opened")]
    checks.append(("duration", abs(dur - plan["total_secs"]) <= max(5, plan["total_secs"] * 0.01),
                   f"{dur/3600:.2f}h out vs {plan['total_secs']/3600:.2f}h in"))
    ch = count_chapters(out)
    checks.append(("chapters", ch == plan["n"], f"{ch} chapters for {plan['n']} files"))
    checks.append(("starts cleanly", decodes(out, 0), "first 12s decode"))
    if dur > 30:
        checks.append(("ends cleanly", decodes(out, max(0, dur - 12)),
                       "last 12s decode"))
    try:
        a = MFile(out, easy=True)
        got = (a.get("artist") or [""])[0]
        checks.append(("tags", plan["author"].lower() in (got or "").lower()
                       or bool(got), f"artist tag = {got!r}"))
    except Exception:
        checks.append(("tags", False, "no readable tags"))
    # cover art is only a promise when we had one to embed -- a book with no
    # cover anywhere is a perfectly good book and must not fail the gate
    try:
        art = has_attached_pic(out)
        checks.append(("cover art", art or not plan["cover"],
                       "embedded" if art else
                       ("MISSING" if plan["cover"] else "none available")))
    except Exception:
        pass
    return all(c[1] for c in checks), checks


# ------------------------------------------------------------------- job engine

JOBS, JOBS_LOCK = {}, threading.Lock()
QUEUE = queue.Queue()
WORKERS = 2


class Job:
    def __init__(self, plan):
        self.id = uuid.uuid4().hex[:12]
        self.plan = plan
        self.state = "queued"
        self.pct = 0.0
        self.note = ""
        self.log = []
        self.checks = []
        self.out = plan["out"]
        self.manifest = None
        self.cancel = threading.Event()
        self.proc = None
        self.t0 = None
        self.t1 = None

    def say(self, msg):
        self.log.append(msg)
        del self.log[:-200]


def move_originals(plan):
    """Move sources into an _originals subfolder. Never deletes, never overwrites."""
    dest = os.path.join(plan["folder"], ORIG)
    os.makedirs(dest, exist_ok=True)
    mapping = []
    for f in plan["files"]:
        rel = os.path.relpath(f, plan["folder"])
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target):
            stem, ext = os.path.splitext(target)
            target = f"{stem} (2){ext}"
        shutil.move(f, target)
        mapping.append({"from": rel, "to": os.path.relpath(target, plan["folder"])})
    # tidy up disc subfolders that are now empty
    for dp, dns, fns in os.walk(plan["folder"], topdown=False):
        if dp != plan["folder"] and dp != dest and not os.listdir(dp):
            try:
                os.rmdir(dp)
            except OSError:
                pass
    return mapping


def run_job(job):
    plan = job.plan
    job.state, job.t0 = "encoding", time.time()
    tmpdir = os.path.join(tempfile.gettempdir(), "bookbind")
    os.makedirs(tmpdir, exist_ok=True)
    concat = os.path.join(tmpdir, f"{job.id}.concat")
    chapters = os.path.join(tmpdir, f"{job.id}.ffmeta")
    partial = plan["out"] + ".partial.m4b"
    cpath = None

    try:
        if plan["cover"]:
            cpath = os.path.join(tmpdir, f"{job.id}.cover.jpg")
            try:
                fetch_cover(plan["cover"], cpath) if plan["cover"].startswith("http") \
                    else shutil.copyfile(plan["cover"], cpath)
                plan = dict(plan, cover=cpath)
            except Exception as e:
                job.say(f"cover art failed ({e}); continuing without it")
                plan = dict(plan, cover=None)

        write_concat(plan["files"], concat)
        write_chapters(plan, chapters)
        job.say(f"encoding {plan['n']} files, {plan['total_secs']/3600:.2f} h "
                f"({plan['order']} order, {'copy' if plan['lossless'] else str(plan['bitrate']//1000)+'k aac'})")

        if os.path.exists(partial):
            os.remove(partial)
        cmd = ffmpeg_command(plan, concat, chapters, partial)
        job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, bufsize=1)
        for line in job.proc.stdout:
            if job.cancel.is_set():
                job.proc.kill()
                break
            k, _, v = line.strip().partition("=")
            if k == "out_time_us":
                try:
                    done = int(v) / 1e6
                    job.pct = min(99.0, done / plan["total_secs"] * 100)
                except ValueError:
                    pass
        job.proc.wait()
        err = (job.proc.stderr.read() or "").strip()
        if job.cancel.is_set():
            if os.path.exists(partial):
                os.remove(partial)
            job.state, job.note = "cancelled", "stopped; originals untouched"
            return
        if job.proc.returncode != 0:
            if os.path.exists(partial):
                os.remove(partial)
            job.state, job.note = "failed", err[:600] or "ffmpeg failed"
            return

        job.state, job.pct = "verifying", 100.0
        ok, checks = verify(plan, partial)
        job.checks = [{"name": c[0], "ok": c[1], "detail": c[2]} for c in checks]
        if not ok:
            os.remove(partial)
            job.state, job.note = "failed", "output did not pass verification"
            return

        if os.path.exists(plan["out"]):
            os.replace(plan["out"], plan["out"] + ".rejected")
            job.say("an existing file was set aside as .rejected")
        os.replace(partial, plan["out"])

        job.state = "moving originals"
        mapping = move_originals(plan)
        job.manifest = os.path.join(plan["folder"], f".bookbind-{job.id}.json")
        with open(job.manifest, "w", encoding="utf-8") as fh:
            json.dump({"id": job.id, "output": plan["out_name"], "moved": mapping,
                       "checks": job.checks, "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      fh, indent=1)
        job.state, job.note = "done", f"{len(mapping)} originals moved to {ORIG}/"
    except Exception as e:
        job.state, job.note = "failed", f"{type(e).__name__}: {e}"
        if os.path.exists(partial):
            try:
                os.remove(partial)
            except OSError:
                pass
    finally:
        job.t1 = time.time()
        for p in filter(None, (concat, chapters, cpath)):
            try:
                os.remove(p)
            except OSError:
                pass


def worker():
    while True:
        job = QUEUE.get()
        if job is None:
            return
        with JOBS_LOCK:
            if job.state == "queued":
                run_job(job)
        QUEUE.task_done()


for _ in range(WORKERS):
    threading.Thread(target=worker, daemon=True).start()


def restore(job_id):
    """Put the originals back and remove the merged file."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.manifest or not os.path.exists(job.manifest):
        return False, "no manifest for that job"
    man = json.load(open(job.manifest, encoding="utf-8"))
    base = job.plan["folder"]
    back = 0
    for m in man["moved"]:
        src, dst = os.path.join(base, m["to"]), os.path.join(base, m["from"])
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            back += 1
    out = os.path.join(base, man["output"])
    if os.path.exists(out):
        os.remove(out)
    d = os.path.join(base, ORIG)
    if os.path.isdir(d) and not os.listdir(d):
        os.rmdir(d)
    os.remove(job.manifest)
    return True, f"{back} files restored"


# ------------------------------------------------------------------- web server

def jsonable(job):
    return {"id": job.id, "state": job.state, "pct": round(job.pct, 1),
            "note": job.note, "log": job.log[-40:], "checks": job.checks,
            "name": job.plan["out_name"], "folder": job.plan["folder"],
            "n": job.plan["n"], "hours": round(job.plan["total_secs"] / 3600, 2),
            "elapsed": round((job.t1 or time.time()) - job.t0, 1) if job.t0 else 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        blob = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            with open(os.path.join(STATIC, "index.html"), "rb") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")
        if u.path.startswith("/static/"):
            p = os.path.join(STATIC, os.path.basename(u.path))
            if os.path.exists(p):
                ct = {"css": "text/css", "js": "text/javascript"}.get(
                    p.rsplit(".", 1)[-1], "application/octet-stream")
                with open(p, "rb") as fh:
                    return self._send(200, fh.read(), ct)
            return self._send(404, {"error": "not found"})

        if u.path == "/api/version":
            return self._send(200, {"version": version(), "repo": REPO})

        if u.path == "/api/home":
            return self._send(200, {"home": os.path.expanduser("~"),
                                    "desktop": os.path.expanduser("~/Desktop"),
                                    "volumes": ["/Volumes/" + v for v in
                                                sorted(os.listdir("/Volumes"))]})
        if u.path == "/api/browse":
            path = (q.get("path") or [os.path.expanduser("~")])[0]
            path = os.path.abspath(os.path.expanduser(path))
            if not os.path.isdir(path):
                return self._send(400, {"error": "not a folder"})
            dirs, audio = [], 0
            try:
                for name in sorted(os.listdir(path), key=natkey):
                    if name.startswith("."):
                        continue
                    full = os.path.join(path, name)
                    if os.path.isdir(full):
                        dirs.append({"name": name, "path": full})
                    elif os.path.splitext(name)[1].lower() in AUDIO_EXT:
                        audio += 1
            except PermissionError:
                return self._send(403, {"error": "no permission for that folder"})
            parent = os.path.dirname(path)
            return self._send(200, {"path": path, "dirs": dirs, "audio": audio,
                                    "parent": parent if parent != path else None})

        if u.path == "/api/scan":
            folder = (q.get("path") or [""])[0]
            folder = os.path.abspath(os.path.expanduser(folder))
            if not os.path.isdir(folder):
                return self._send(400, {"error": "that folder does not exist"})
            files = scan_audio(folder)
            files = [f for f in files if os.path.splitext(f)[1].lower() not in SKIP_EXT]
            total = sum((duration(f) or 0) for f in files)
            tagged = sum(1 for f in files if first_tag(f, "tracknumber"))
            return self._send(200, {
                "path": folder, "count": len(files), "hours": round(total / 3600, 2),
                "tagged": tagged, "size": sum(os.path.getsize(f) for f in files),
                "sample": [os.path.relpath(f, folder) for f in files[:6]],
                "orig": os.path.isdir(os.path.join(folder, ORIG)),
                "already": [f for f in os.listdir(folder) if f.endswith(".m4b")]})

        if u.path == "/api/search":
            try:
                res = itunes_search((q.get("author") or [""])[0], (q.get("title") or [""])[0])
                return self._send(200, {"results": res})
            except Exception as e:
                return self._send(200, {"results": [], "error": str(e)})

        if u.path == "/api/jobs":
            with JOBS_LOCK:
                jobs = sorted(JOBS.values(), key=lambda j: j.t0 or 0, reverse=True)
            return self._send(200, {"jobs": [jsonable(j) for j in jobs[:30]]})

        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad request body"})

        if u.path == "/api/start":
            plan, err = build_plan(
                body.get("folder", ""), body.get("author", "").strip(),
                body.get("title", "").strip(), body.get("year", "").strip(),
                body.get("narrator", "").strip(), body.get("cover") or None,
                body.get("order", "auto"), body.get("bitrate"))
            if err:
                return self._send(400, {"error": err})
            job = Job(plan)
            with JOBS_LOCK:
                JOBS[job.id] = job
            QUEUE.put(job)
            return self._send(200, {"job": jsonable(job), "est_bytes": plan["est_bytes"]})

        if u.path == "/api/cancel":
            with JOBS_LOCK:
                job = JOBS.get(body.get("id"))
            if not job:
                return self._send(404, {"error": "no such job"})
            job.cancel.set()
            if job.proc and job.proc.poll() is None:
                job.proc.kill()
            return self._send(200, {"ok": True})

        if u.path == "/api/restore":
            ok, msg = restore(body.get("id"))
            return self._send(200 if ok else 400, {"ok": ok, "message": msg})

        if u.path == "/api/reveal":
            folder = body.get("folder", "")
            if os.path.isdir(folder):
                subprocess.Popen(["open", folder])
                return self._send(200, {"ok": True})
            return self._send(400, {"error": "no such folder"})

        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Bookbind -> http://127.0.0.1:{PORT}   (ctrl-c to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
