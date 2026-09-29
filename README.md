# Bookbind

An audiobook is normally **one file**. A copy of one is often **a pile of files** — one per
CD track, or one per chapter, sitting in a folder with names like `01.mp3`, `02.mp3`.

Bookbind turns that pile into a single `.m4b`: the file type Apple Books, iPhones, iPods
and most audiobook players understand, with a real chapter list you can skip around in.
It checks its own work as it goes, and it never deletes anything.

You do not need to know what an `.m4b` is, or what "chapter list" means technically. Point
it at the folder, press the button, and you get one book.

## It is a real app

`Bookbind.app` is a Mac app with its own window and its own Dock icon. It does not open a
browser tab, and there is no address bar anywhere: the window you get is the whole program.

Folders and files are chosen in the normal Mac way — the **Choose folder** button opens a
Finder panel, and **Add files** opens the same panel for individual tracks. Nothing is
browsed by typing a path.

There are two lists, because they answer different questions:

- **1 · The folder** — one folder, and everything in it. This is the usual way: a folder of
  tracks becomes a book, in order.
- **2 · The chapters** — the actual list of files that will become chapters. It opens as the
  folder's contents, and each row can then be changed on its own: untick one to leave it
  out, move one with the arrows, drop one with **×**, or type in its box to give that
  chapter your own title. **Add files…** puts one in from anywhere else on the disk.

That second list is what you use when you want to match a track list you found online: type
each chapter's real name, put them in the right order, leave out the interviews, and the
book comes out named the way the publisher did it.

## The command line

The window and the command line are the **same engine**, so nothing here is a second
implementation that can drift: everything below calls the app's own `app.py`.

```bash
bookbind                       # open the window
bookbind ~/Books/somewhere     # open it already looking at that folder
bookbind --cli FOLDER …        # no window: bind it right here, print JSON
```

`bookbind` is a shorthand (`~/.local/bin/bookbind`). `--cli` passes the rest through to
`bookbind_cli.py`, which is in this folder and can also be called directly:

```bash
python3 bookbind_cli.py "/Books/Hammett, The Maltese Falcon (1930)" \
    --author "Dashiell Hammett" --title "The Maltese Falcon" --year 1930
```

It prints one JSON object on stdout and its progress on stderr, so `| jq` stays clean.
The folder's name is read the way the window reads it — `Hammett, The Maltese Falcon
(1930)` fills in all three — but an author is never guessed. Useful flags: `--dry-run`
(say what would happen, change nothing), `--lookup` (fill empty year, narrator and cover
from the top iTunes hit), `--titles "<path>=Opening Credits"` (name one chapter, can
repeat), `--order auto|path|tags`, `--keep-originals`, and `--restore FOLDER` to undo.
`--help` lists the lot.

If the app is already open, `bookbind FOLDER` cannot retarget it — macOS hands a running
app no arguments — so it says so rather than opening the wrong folder in silence.

## What it does, step by step

1. **Look.** You hand it a folder. It tells you what audio it found there, how long it
   runs in total, how many files already have tags, and guesses the author and title from
   the folder's name.
2. **Ask.** Type the author and title and it looks the book up online (iTunes). You get
   back the release year, the narrator, and a cover picture. Pick the right edition and the
   boxes fill themselves in — and every box stays editable, including "no cover at all".
3. **Bind.** It joins the files into one `.m4b`, with one chapter per source file, in the
   order the files should play (it understands that `2.mp3` comes before `10.mp3`, not
   after). By default it **copies** the audio rather than re-recording it, so the sound is
   exactly what you started with and cannot get worse.
4. **Check.** Before touching your originals it inspects the new file: is it the same
   length, does it have one chapter per file, do the very first and very last seconds
   actually play, did the tags land, is the cover really inside it. If any answer is wrong
   it throws its own output away and tells you why. Your files stay put.
5. **Tidy up.** The original files move into an `_originals` folder next to the new book —
   moved, never deleted. If you don't like the result, one click (**Undo**) puts them all
   back and removes the merged file.

It accepts mp3, m4a, m4b, aac, flac, opus, wav, ogg and wma, and will mix formats within
one book if it has to.

## Download

Three builds of the same app. Take whichever suits the machine, from
[Releases](https://github.com/aghamorad/bookbind/releases). Unzip it and put
`Bookbind.app` wherever you like — Applications, Desktop, anywhere.

| Asset | Size | Macs it runs on | Needs |
| --- | --- | --- | --- |
| `Bookbind-<version>-macos-silicon.zip` | ~70 MB | M1, M2, M3, M4 … | nothing — everything is inside |
| `Bookbind-<version>-macos-intel.zip` | ~72 MB | older Intel Macs | nothing — everything is inside |
| `Bookbind-<version>-macos.zip` | ~2 MB | either | Python 3 with `mutagen`, and ffmpeg |

Not sure which Mac you have? Apple menu → **About This Mac**. If the chip says **Apple M…**
take the *silicon* one; if it says **Intel** take the *intel* one.

The two big ones already contain the Python and the ffmpeg they need — that is the whole
point of them, and why they are large. The small one is for people who already have those
installed (or would rather have them):

```bash
brew install python3 ffmpeg
```

```bash
python3 -m pip install mutagen
```

If one of those is missing, the app tells you which and prints the command to fix it.

**First launch.** The app is not signed or notarized, so macOS refuses a plain
double-click. Right-click `Bookbind.app` → **Open** → **Open**. After that it launches
normally.

## Running from source

```bash
python3 app.py
```

and open <http://127.0.0.1:8765>. Needs `python3`, `mutagen`, and `ffmpeg`/`ffprobe` on
`PATH`.

In a browser there is no Finder panel to call, so the page offers a path box and its own
folder walker instead, and the window is a browser tab. The Mac app is the same page with
that one difference.

## Notes

- The app's window is `build/window.swift`, compiled to `Bookbind.app/Contents/MacOS/Bookbind`
  by `./build/make_window.sh`. That binary is committed on purpose: the small
  `-macos.zip` is assembled on a Linux runner that has no Swift compiler, and all three
  builds should ship the same window. It is compiled for both architectures at once, so it
  opens on Intel and Apple silicon alike. `window.swift` starts the Python server, points a
  web view at it, and answers three things the page asks for — pick a folder, pick files,
  show something in the Finder. Anything else the app does is still `app.py`.
- The server holds its jobs in memory, so an Undo offered by a job that was running before
  you quit the app will say the manifest is gone. The manifest file itself is still on disk
  next to the book, so nothing is lost.
- `build/make_icon.py` regenerates the app icon and the header png from a flat source image
  on a solid page: it keys the page out, sizes the shape to Apple's icon grid (824 of a
  1024 canvas) and writes both the `.icns` and `static/icon.png`.
- Cover art is written as an `attached_pic` mjpeg stream, which is what Apple Books, iTunes
  and most players read. Chapters are written as MP4 chapter tracks — in these files they
  appear as a `bin_data` stream alongside the audio, which is normal and not a problem.
