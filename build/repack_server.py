#!/usr/bin/env python3
"""Put a fresh app.py into an existing PyInstaller BookbindServer.

The server Housekeeping ships is a frozen PyInstaller build. Rebuilding it from
scratch needs PyInstaller and the exact Python 3.15 it was cut with, which is a
lot of machinery for a one-file change. The archive is a simple container
though: a bootloader, the entry blobs, a table of contents, a cookie, the
interpreter name, and a code signature. This replaces the `app` entry, rewrites
the offsets, drops the old signature command and pads the file back to the
length its __LINKEDIT segment claims. `codesign --force --sign -` finishes it.

Usage:
    python3 build/repack_server.py --source old/BookbindServer \
        --app app.py --out new/BookbindServer
    codesign --force --sign - new/BookbindServer

The Python that runs this must have the same minor version as the frozen build.
"""

import argparse
import marshal
import os
import struct
import zlib

MAGIC = b"MEI\014\013\012\013\016"
LC_CODE_SIGNATURE = 0x1D


def read_entries(data):
    pos = data.rfind(MAGIC)
    if pos < 0:
        raise SystemExit("not a PyInstaller archive: no cookie")
    pkg_len, toc_off, toc_len, pyver = struct.unpack("!iiii", data[pos + 8:pos + 24])
    toc_start = pos - toc_len
    entries = []
    i = toc_start
    while i < pos:
        elen, off, clen, ulen = struct.unpack("!iiii", data[i:i + 16])
        entries.append({
            "elen": elen, "off": off, "clen": clen, "ulen": ulen,
            "flag": data[i + 16], "typ": chr(data[i + 17]),
            "name": data[i + 18:i + elen].rstrip(b"\0").decode(),
        })
        i += elen
    data_len = max(e["off"] + e["clen"] for e in entries if e["typ"] != "o")
    return pos, pkg_len, toc_len, pyver, toc_start, entries, toc_start - data_len


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="the frozen server to patch")
    ap.add_argument("--app", required=True, help="the app.py to put inside it")
    ap.add_argument("--out", required=True, help="where to write the new server")
    args = ap.parse_args()

    data = open(args.source, "rb").read()
    pos, pkg_len, toc_len, pyver, toc_start, entries, arch = read_entries(data)
    if arch < 0 or data[arch:arch + 2] != b"\x78\xda":
        raise SystemExit("could not find the archive start")
    prefix = data[:arch]
    pylib = data[pos + 24:pos + 24 + 64]

    code = compile(open(args.app, "rb").read(), "app.py", "exec")
    blob = zlib.compress(marshal.dumps(code), 9)

    body = bytearray()
    for e in entries:
        if e["typ"] == "o":
            e["newoff"] = len(body)
            e["blob"] = b""
            continue
        e["newoff"] = len(body)
        if e["name"] == "app":
            e["blob"] = blob
            e["clen"] = len(blob)
            e["ulen"] = len(marshal.dumps(code))
        else:
            e["blob"] = data[arch + e["off"]:arch + e["off"] + e["clen"]]
        body += e["blob"]

    toc = bytearray()
    for e in entries:
        entry = struct.pack("!iiii", e["elen"], e["newoff"], e["clen"], e["ulen"])
        entry += bytes([e["flag"]]) + e["typ"].encode() + e["name"].encode()
        entry = entry.ljust(e["elen"], b"\0")
        if len(entry) != e["elen"]:
            raise SystemExit("entry %r no longer fits its slot" % e["name"])
        toc += entry
    if len(toc) != toc_len:
        raise SystemExit("table of contents changed size")

    pkg = len(body) + toc_len + 24 + 64
    cookie = MAGIC + struct.pack("!iiii", pkg, len(body), toc_len, pyver)
    out = bytearray(prefix + bytes(body) + bytes(toc) + cookie + pylib)

    magic, _, _, _, ncmds, sizeofcmds, _, _ = struct.unpack("<IiiIIIII", out[:32])
    if magic != 0xFEEDFACF:
        raise SystemExit("not a 64-bit Mach-O")
    off = 32
    for _ in range(ncmds):
        cmd, size = struct.unpack("<II", out[off:off + 8])
        if cmd == LC_CODE_SIGNATURE:
            struct.pack_into("<II", out, 16, ncmds - 1, sizeofcmds - 16)
            break
        off += size

    if len(out) < len(data):
        out += b"\0" * (len(data) - len(out))
    open(args.out, "wb").write(out)
    os.chmod(args.out, 0o755)
    print("wrote %s (%d bytes, app %d bytes)" % (args.out, len(out), len(blob)))


if __name__ == "__main__":
    main()
