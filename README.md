# Bookbind

Point it at a folder of loose audio files — a book ripped into twelve mp3s, say — and it
merges them into one chaptered `.m4b`, checks the result, then moves the originals into
`_originals/`. It never deletes anything.

## Download

Two builds, same app — take whichever suits the machine, from
[Releases](https://github.com/aghamorad/bookbind/releases). Unzip either one and put
`Bookbind.app` wherever you like.

| Asset | Size | Needs |
| --- | --- | --- |
| `Bookbind-<version>-macos.zip` | ~2 MB | Python 3 with `mutagen`, and ffmpeg |
| `Bookbind-<version>-macos-intel.zip` | ~100 MB | nothing — both are inside it |

The small one starts instantly and is not architecture-specific; the launcher is a shell
script around a Python server, so the same zip runs on Intel and Apple Silicon.

```bash
brew install python3 ffmpeg
```

```bash
python3 -m pip install mutagen
```

If one of those is missing the app names it and tells you the command. The large one is
frozen with PyInstaller and carries a static ffmpeg, built for Intel Macs.

**First launch.** The app is not signed or notarized, so macOS refuses a plain
double-click. Right-click `Bookbind.app` → **Open** → **Open**. After that it launches
normally.

## Running from source

```bash
python3 app.py
```

and open <http://127.0.0.1:8765>. Needs `python3`, `mutagen`, and `ffmpeg`/`ffprobe` on
`PATH`.

## What it does

1. **Point it at a folder.** It lists the audio it found, with count, total running time
   and how many files already carry tags, and takes a guess at the author and title from
   the folder name.
2. **Search for the metadata.** Author + Title hits the iTunes Search API, which returns
   the audiobook release year, narrator and cover art. Pick a candidate and the fields
   fill themselves; every field stays editable, and the cover can be dropped entirely.
3. **Merge.** Lossless by default — the streams are copied, so nothing is re-encoded and
   quality cannot regress. Accepts mp3, m4a, m4b, aac, flac, opus, wav, ogg and wma, and
   will mix formats in one book if it has to. Chapters come from the track/disc tags when
   every file carries a number, and from natural path order otherwise (`2.mp3` before
   `10.mp3`), with the running time of each source file as its chapter length.
4. **Verify.** Six checks run against the finished file before anything is touched:
   duration in versus out, one chapter per source file, the first and last seconds
   actually decode, the tags landed, and the cover art is really embedded. If any of them
   disagrees with the plan, the output is discarded and the originals stay where they are.
5. **Undo.** A manifest records every move. One click puts the originals back and removes
   the merged file.

Cover art is written as an `attached_pic` mjpeg stream, which is what Apple Books,
iTunes and most players read. Chapters are written as MP4 chapter tracks — in these files
they appear as a `bin_data` stream alongside the audio, which is normal.

## Notes

- `build/make_icon.py` regenerates the app icon and the header png from a flat source
  image on a solid page: it keys the page out, sizes the shape to Apple's icon grid
  (824 of a 1024 canvas) and writes both the `.icns` and `static/icon.png`.
- The server holds jobs in memory, so an Undo offered by a job that was running before
  the app was restarted will report that the manifest is gone. The manifest file itself
  is still on disk next to the book.
