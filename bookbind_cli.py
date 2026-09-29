#!/usr/bin/env python3
"""Bookbind from the command line -- the same engine the app runs, headless.

Nothing here re-implements anything: the plan, the encode, the verification and
the moving of originals are all app.py's own functions, so a book made here is
byte-for-byte the book the window makes.

The report is one JSON object on stdout. Progress -- the part a person reads --
goes to stderr, so `bookbind_cli.py ... | jq` stays clean.

  bookbind_cli.py "/path/to/folder" --author "Paul Auster" --title "The Invention of Solitude"
  bookbind_cli.py "/path/to/folder" --dry-run
  bookbind_cli.py --search --author "Paul Auster" --title "Solitude"
  bookbind_cli.py --restore "/path/to/folder"
"""
import argparse
import json
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import app  # noqa: E402  -- the engine itself


def human(msg):
    print(msg, file=sys.stderr, flush=True)


def emit(obj, code=0):
    print(json.dumps(obj, indent=1, ensure_ascii=False))
    sys.exit(code)


def watch(job):
    """Say where the encode has got to, on stderr, while it runs."""
    last = -1
    while job.state in ("queued", "encoding") and not job.cancel.is_set():
        pct = int(job.pct)
        if pct != last and pct % 5 == 0:
            last = pct
            human(f"  {job.state} {pct}%")
        time.sleep(1)


def do_search(args):
    results = app.itunes_search(args.author, args.title)
    emit({"results": results, "count": len(results)})


def do_restore(folder):
    """Undo the most recent job in a folder, using the manifest it left behind."""
    if os.path.isfile(folder):
        manifests = [folder]
    else:
        if not os.path.isdir(folder):
            emit({"ok": False, "error": "no such folder"}, 1)
        manifests = sorted(
            (os.path.join(folder, f) for f in os.listdir(folder)
             if f.startswith(".bookbind-") and f.endswith(".json")),
            key=os.path.getmtime, reverse=True)
    if not manifests:
        emit({"ok": False, "error": "no manifest in that folder -- nothing to undo"}, 1)

    path = manifests[0]
    man = json.load(open(path, encoding="utf-8"))
    base = os.path.dirname(path)
    back = 0
    for m in man.get("moved", []):
        src, dst = os.path.join(base, m["to"]), os.path.join(base, m["from"])
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                stem, ext = os.path.splitext(dst)
                n = 2
                while os.path.exists(f"{stem} ({n}){ext}"):
                    n += 1
                dst = f"{stem} ({n}){ext}"
            import shutil
            shutil.move(src, dst)
            back += 1
    out = os.path.join(base, man["output"])
    removed = None
    if os.path.exists(out):
        removed = man["output"]
        os.remove(out)
    d = os.path.join(base, app.ORIG)
    if os.path.isdir(d):
        for dp, _, _ in os.walk(d, topdown=False):
            try:
                os.rmdir(dp)
            except OSError:
                pass
    os.remove(path)
    emit({"ok": True, "restored": back, "removed_output": removed,
          "was": man.get("output"), "at": man.get("at")})


def main():
    p = argparse.ArgumentParser(description="Merge loose audio into one chaptered .m4b.")
    p.add_argument("folder", nargs="?", help="the folder holding the tracks")
    p.add_argument("--author"), p.add_argument("--title")
    p.add_argument("--year"), p.add_argument("--narrator")
    p.add_argument("--cover", help="a file, a URL, or 'none' to embed nothing")
    p.add_argument("--order", default="auto", choices=["auto", "path", "tags"])
    p.add_argument("--bitrate", type=int, help="kbps, only for re-encoded (mixed/lossy) sets")
    p.add_argument("--files", action="append", help="an exact track, in order; repeatable")
    p.add_argument("--titles", action="append", metavar="PATH=TITLE",
                   help="name one chapter; repeatable")
    p.add_argument("--search", action="store_true", help="catalogue lookup only, then stop")
    p.add_argument("--lookup", action="store_true",
                   help="fill empty metadata from the top catalogue match")
    p.add_argument("--use-file-titles", action="store_true",
                   help="name each chapter from the file's own title tag when it has one")
    p.add_argument("--ignore-locked", action="store_true",
                   help="merge the readable files in a folder that also holds Audible .aa")
    p.add_argument("--dry-run", action="store_true", help="say what would happen, change nothing")
    p.add_argument("--keep-originals", action="store_true",
                   help="write the book but leave the sources where they are")
    p.add_argument("--restore", metavar="FOLDER", help="undo the last job in that folder")
    args = p.parse_args()

    if args.search:
        do_search(args)
    if args.restore:
        do_restore(args.restore)

    folder = os.path.abspath(os.path.expanduser(args.folder)) if args.folder else ""
    files = [os.path.abspath(os.path.expanduser(f)) for f in (args.files or [])]
    if not folder and not files:
        emit({"ok": False, "error": "give a folder, or --files"}, 2)

    author, title = (args.author or "").strip(), (args.title or "").strip()
    year, narrator = (args.year or "").strip(), (args.narrator or "").strip()
    cover = args.cover if args.cover != "none" else None

    # Nothing named yet? Read the folder's name the way the window does -- it
    # only ever guesses a title and a year off the folder, never an author.
    if not (author and title) and folder:
        base = os.path.basename(folder.rstrip(os.sep))
        m = re.match(r"^([^,]+),\s*(.+?)\s*\((\d{3,4})\)\s*$", base)
        if m:
            author, title, year = author or m[1], title or m[2], year or m[3]
        else:
            m = re.match(r"^(.*?)\s*\((\d{3,4})\)\s*$", base)
            if m:
                title, year = title or m[1], year or m[2]
            else:
                title = title or base
        human(f"guessed from the folder: {author!r} / {title!r}")

    if args.lookup and (author or title):
        try:
            hits = app.itunes_search(author, title)
        except Exception as e:
            hits, _ = [], human(f"catalogue lookup failed ({e}); carrying on")
        if hits:
            top = hits[0]
            year = year or top["year"]
            narrator = narrator or top["narrator"]
            if not cover and top["cover"]:
                cover = top["cover"]
            human(f"catalogue: {top['title']} -- {top['author']}"
                  + (f", {top['year']}" if top["year"] else ""))

    titles = {}
    for pair in (args.titles or []):
        k, _, v = pair.partition("=")
        titles[os.path.abspath(os.path.expanduser(k))] = v

    # The window shows an empty title box beside each track, so the file's name
    # is what a chapter gets called. A collection that is tagged properly can do
    # better, and only a caller can know it wants that.
    if args.use_file_titles:
        for f in app.source_files(folder, files):
            if f not in titles:
                tag = app.first_tag(f, "title")
                if tag:
                    titles[f] = tag

    plan, err = app.build_plan(folder, author, title, year, narrator, cover,
                               args.order, args.bitrate, files, titles,
                               args.ignore_locked)
    if err:
        emit({"ok": False, "error": err}, 1)

    if args.dry_run:
        emit({"ok": True, "dry_run": True, "output": plan["out_name"],
              "folder": plan["folder"], "chapters": plan["n"],
              "hours": round(plan["total_secs"] / 3600, 2),
              "seconds": round(plan["total_secs"], 2), "order": plan["order"],
              "lossless": plan["lossless"],
              "bitrate": (plan["bitrate"] // 1000) if plan["bitrate"] else None,
              "cover": plan["cover"], "est_mb": round(plan["est_bytes"] / 1e6, 1),
              "files": [os.path.basename(f) for f in plan["files"]]})

    job = app.Job(plan)
    if args.keep_originals:
        plan["move"] = False
    threading.Thread(target=watch, args=(job,), daemon=True).start()

    human(f"binding {plan['n']} files, {plan['total_secs']/3600:.2f} h -> {plan['out_name']}")
    app.run_job(job)

    result = {"ok": job.state == "done", "state": job.state, "note": job.note,
              "output": plan["out_name"], "path": plan["out"],
              "chapters": plan["n"], "seconds": round(plan["total_secs"], 2),
              "moved_to_originals": len(job.manifest and
                                        json.load(open(job.manifest, encoding="utf-8"))["moved"]
                                        or []) if job.manifest else 0,
              "manifest": job.manifest,
              "checks": job.checks, "log": job.log,
              "elapsed": round((job.t1 or time.time()) - (job.t0 or time.time()), 1)}
    if not result["ok"]:
        result["error"] = job.note
    emit(result, 0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
