## The catalogue search works again

**Search online catalogue** was failing on this Mac with

    CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate

which reads like a broken connection and is not one. A python.org Python on macOS
ships with **no root certificates** until its `Install Certificates.command` has
been run, and Python says nothing about that until the first https request: every
TLS connection then fails verification because there is nothing to verify against.
Bookbind's own catalogue search was the first thing on this machine to make one.

The trust store is now chosen rather than assumed — `certifi`'s bundle when it is
installed, macOS's own `/etc/ssl/cert.pem` when it is not — so the search works on
any Mac that has either, and continues to work on a Python that has been set up
properly. The failure message, if it ever comes back, now says the catalogue could
not be reached and that both fields can be filled by hand.

Verified on this Mac: `Greg Grandin` / `Empire's Workshop` returns the audiobook
from the iTunes catalogue, cover included.

### The bundle now says which version it is

Every bundled release so far reported **1.5.0** in its Info.plist, whatever it
actually carried, because the version was written by hand in the repository and the
release workflow copied that file as-is. The workflow now stamps the tag's version
into the bundle before it packages it, and the download check fails if the zip's
Info.plist disagrees with its tag.

## Drop a folder on it, and watch the run finish

Two things are new, and both are about not having to guess what Bookbind is doing.

### Drag and drop

Drag a folder of audio from the Finder onto the window and it becomes *the book's
folder* — the one the finished `.m4b` is written into, and the one the originals
are moved aside inside. Its files land in the chapter list in order.

Drag loose audio files instead and they are **added** to the list you already
have; if you have not chosen a folder yet, Bookbind works out which folder they
all live in and uses that. A dotted strip in the first card says the window takes
drops, and a veil comes up while you are holding files over it, so you can see it
has noticed you. A drop holding nothing playable says so instead of doing nothing.

The drop is caught by the app rather than by the page, because a web page is
deliberately never told where a dropped file lives — and without the real path a
dropped folder cannot become a real folder. Only file URLs are registered, so
dragging text into the title field still behaves as it always did.

### The run panel

**Make the audiobook** now opens a panel and keeps it for the life of that one run:

1. *Reading the files*, while it probes lengths and lays out the chapters.
2. *Binding the audio*, with a real percentage and the audio done out of the total.
3. *Checking the result*, as it compares the new file against what it asked for.
4. *Moving the originals aside* into `_originals`.
5. **Saved**, with the exact path the book was written to and **Show in Finder**.

**Stop** cancels the run and leaves the originals alone. **Keep running in the
background** closes the panel without stopping anything, and **Progress** on the
job in the list brings it back. **Try again** appears only after a failure or a
stop. Closing the panel never stops a run.

### Also in this version

A source that already carries a chapter track keeps it, so re-tagging a finished
`.m4b` no longer flattens thirty named chapters into one; a title typed against a
file still wins for that file, because that is a decision rather than a default.
The command line reports the real chapter count rather than the file count.

## Which build to take

Three builds of the same app. Take whichever suits the machine.

**`-macos.zip`** — 2 MB, starts instantly. Needs Python and ffmpeg installed:

    brew install python3 ffmpeg
    python3 -m pip install mutagen

**`-macos-silicon.zip`** — M-series Macs. **`-macos-intel.zip`** — everything
older. Both are around 70–100 MB and install nothing: Python and ffmpeg are
inside them, built for that architecture.

Either way: unzip, put `Bookbind.app` wherever you like, and double-click it.

**First launch.** The app is not signed or notarized, so macOS refuses a plain
double-click. Right-click `Bookbind.app` → **Open** → **Open**. After that it
launches normally.

If something is missing, the app names it and prints the command to fix it.
