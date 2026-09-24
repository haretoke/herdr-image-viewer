# herdr-image-viewer plan

Herdr plugin that keeps a per-conversation history of the images an agent reads
and shows them in a viewer pane: the selected image large, the others as tiny
dimmed thumbnails with the selection highlighted, hjkl / arrow keys to move. A
rewrite of the idea behind zbyhoo/herdr-image-gallery with a smaller, testable
design, building on what devcon-herdr's herdr-image-preview learned about
Herdr 0.9.1.

Design reviewed twice on 2026-09-24 by Codex (gpt-6-astra, xhigh) and Claude
Fable; this revision applies the adopted findings and the owner's decisions.

## Decisions

- Plugin id `haretoke.image-viewer`, public GitHub repository
  `haretoke/herdr-image-viewer`, MIT license. Code, tests, and history contain
  no personal paths, hostnames, or secrets. The repository is created on GitHub
  when the first release is ready.
- Distribution uses devcon-herdr's existing plugin flow, like herdr-file-viewer
  and reviewr: `devcon-herdr plugins update` resolves the latest GitHub release
  into the plugin lock (release tag + commit), and the Mac and containers run
  `herdr plugin install haretoke/herdr-image-viewer --ref <commit>`. No
  credentials are needed. During development the Mac uses
  `herdr plugin link` on the local clone instead; each distributed change needs
  a push and a release.
- History key is the conversation, not the pane: `claude:<session_id>` from the
  Claude hook payload, `codex:<id>` once a Codex conversation id source is
  verified, otherwise `pane:<socket + caller pane>` as a documented fallback.
  Keys are validated and hashed before they become directory names. The caller
  pane only decides where the viewer opens.
- One viewer per conversation, opened to the right of the caller pane (no
  automatic split ratio in the first version).
- History: at most 30 entries, newest last, each with an explicit `updated_at`.
  A duplicate is the same content hash (moved to newest); the same path with new
  content is a new entry.
- Archive: per conversation, content-addressed inside the conversation
  directory. GC (see Limits) removes conversations untouched for 14 days and
  trims the total to 500 MiB, least recently updated first. It never deletes a
  conversation whose store lock is held.
- Storage root: `HERDR_PLUGIN_STATE_DIR` when it belongs to this plugin
  (`HERDR_PLUGIN_ID` matches); otherwise the path Herdr uses,
  `${XDG_STATE_HOME:-$HOME/.local/state}/herdr/plugins/haretoke.image-viewer`.
  Never inside the plugin root. `publish` passes the root it resolved to the
  viewer through the open request's env, so both always use the same store.
  Viewer liveness locks and open reservations live in a `run/` directory
  outside the GC-managed history tree.
- Rendering uses two stream layers per viewer: one `pane.graphics.stream` for
  the main image and one for a composite of the visible thumbnails (unselected
  dimmed, selection highlighted, baked into pixels). Streams remove their layers
  when the viewer exits or crashes. Text is used only for the title line.
- Layout by pixel aspect ratio of the pane (tall: grid below, wide: columns on
  the right); thumbnails are hidden below a minimum pane size.
- Navigation moves over a logical grid of the whole history; the visible page is
  a window onto it. h/← left, j/↓ down, k/↑ up, l/→ right; movement stops only at
  the ends of the history; on a ragged last row it lands on the nearest existing
  cell. With thumbnails hidden the grid is one virtual row: h/l previous/next,
  j/k do nothing. The selection is held by content hash, not by index.
- New images: the viewer has a `follow_latest` state. While it is on, a new
  image becomes the selection. Moving to an older image turns it off and the
  title shows a "new" marker when images arrive; moving back to the newest image
  turns it on again and clears the marker. If the selected entry falls out of
  the history, the nearest remaining entry is selected.
- Claude hook: one shim, `~/.claude/hooks/herdr-image-viewer-hook.sh`,
  registered once by devcon-herdr. The Mac and WSL2 share nothing, but WSL2 and
  every container share one `~/.claude` (bind mount), so the shim decides at run
  time: if this environment has the plugin (its `plugin_root` from
  `herdr plugin list --json`), it calls the plugin's `publish`; otherwise it
  falls back to the old herdr-image-preview follower path. No
  environment-specific path goes into settings.json.
- The plugin owns the hook logic and all rendering (single source of truth).
  The old herdr-image-preview is frozen and kept as the fallback and for Codex
  until the Codex skill uses `publish` and its other uses (`--clear`, `--info`)
  are replaced.

## Limits (initial values, tuned after the real-device checks)

| Name | Value |
|---|---|
| history entries per conversation | 30 |
| GC age | 14 days since `updated_at` |
| GC total | 500 MiB of archive + history files, temp files older than 1 h included |
| GC frequency | at most once per hour, run by `publish` |
| input file size | 50 MiB |
| input pixels | 100 megapixels |
| external conversion timeout | 20 s |
| stream frame | 16 MiB (Herdr limit) |
| `set` data | 512 KiB (Herdr limit, not used by the viewer) |
| composite canvas budget | 196,608 pixels (about 590 KB of RGB per selection move; the composite is streamed, so the 512 KiB `set` limit no longer applies), reduced page capacity if exceeded |
| thumbnail cache | 32 MiB of RGBA |
| open reservation | 15 s |
| stream reconnect | backoff 1 s → 30 s, then stop and report |

When GC cannot free enough space for a new image, publish fails with a capacity
error and keeps the existing history.

## Herdr facts this design relies on (0.9.1, source 065ef9d6)

- `pane.graphics.set` draws pixels 1:1 and crops to the placement; it never
  scales. It accepts at most 512 KiB of decoded image data.
- `pane.graphics.stream` replies `ok` when opened; frames get no reply unless
  rejected, after which Herdr sends an error line and closes the stream. Up to
  16 MiB per frame. Idle time between frames has no timeout, but a frame's
  header and body have read timeouts once started. Closing the stream removes
  its layer; `set` layers stay until cleared or the pane closes.
- Layer limits: 16 per pane and 64 across the whole server (shared with other
  plugins); 64 MiB of inline data in total.
- `pane.layout` is computed from the server's terminal area. The pane's pty size
  is what the client renders. With a single attached client every tab is
  resized; otherwise a hidden tab may keep a stale size (seen: 140x40) until it
  is shown, so the viewer re-fits on the SIGWINCH that follows.
- `plugin.pane.open` starts the manifest's fixed argv; dynamic data goes through
  `env`. It takes `target_pane_id`, `direction`, and `focus` (target defaults to
  the current pane, so always pass it) and returns
  `result.plugin_pane.pane.pane_id`. The command starts in the plugin root.
  There is no split ratio, and `layout.set_split_ratio` addresses a split by
  tree path, not pane id. Plugin panes receive `HERDR_SOCKET_PATH`,
  `HERDR_PANE_ID` (the viewer's own pane), `HERDR_PLUGIN_ID`,
  `HERDR_PLUGIN_STATE_DIR`, and `HERDR_PLUGIN_CONTEXT_JSON`.
- `herdr plugin install` clones a public GitHub repository over HTTPS without
  credentials and records `requested_ref` / `resolved_commit`, which
  devcon-herdr compares with its lock. `herdr plugin link` (development only)
  canonicalizes the manifest path, registers that real path, and carries no
  `source`.

## Architecture

- `keys`: conversation key validation, namespacing, hashing to directory names.
- `store`: histories (JSON snapshot per conversation with a schema version) and
  archives. Publish order: copy the input to a temp file while hashing it,
  commit the archive file, atomically replace the history (fsync the file
  before the rename), then delete unreferenced archives. A corrupt history is
  moved aside and its archives are protected; an unknown schema version is left
  untouched and never collected. Lock order: global GC lock, then the
  conversation lock.
- `safety`: regular files only (opened once, copied from that descriptor), magic
  bytes, size and pixel caps, permissions (0700 directories, 0600 files, owner
  and symlink checks on existing storage), control-character stripping.
- `imaging`: PNG header parsing; conversion and exact resizing through sips /
  ImageMagick / ffmpeg with argv only and the time and resource limits; the
  first frame of multi-frame formats; EXIF orientation. Thumbnail pixels come
  as uncompressed data the standard library can read: BMP from sips on macOS,
  raw RGBA from ImageMagick on Linux, raw RGBA from ffmpeg when present. The
  length is checked against width x height x 4.
- `png`: minimal PNG encoder (8-bit, non-interlaced, filter 0, correct CRCs) on
  top of zlib.
- `composite`: thumbnails are flattened onto the background once and cached per
  (hash, size) in normal and dimmed variants; dimming uses `bytes.translate`,
  blitting and the highlight border use row-slice assignment (no per-pixel
  Python loops). The composite is rebuilt from the cache, so selection moves
  never start an external converter.
- `layout`: pure functions from pty size and cell pixel size to the main image
  rectangle, the thumbnail cells, the page window, and spatial moves.
- `herdr_api`: socket requests and a graphics stream that detects rejection or
  EOF even between frames.
- `launcher`: at most one live viewer per conversation: a liveness lock held by
  the running viewer, a time-limited open reservation, a registration with a
  start token so an old viewer never unregisters a newer one; a viewer that
  cannot take the liveness lock exits before drawing.
- `viewer`: the pane process. Input and drawing are separated; repeated keys and
  SIGWINCH bursts coalesce into one redraw of the latest state (debounce driven
  by an injectable clock). Identical frames are not re-sent. The viewer decides
  how to degrade (drop thumbnails, then report that the main image cannot be
  shown) and restores the current frame after a lost stream without waiting for
  input. It notices when its conversation was removed by GC and shows an empty
  view.
- `cli`: `publish <image> --conversation <key> --caller-pane <id>`, `viewer`,
  `gc`.
- `hooks/claude-read.sh`: the plugin side of the Claude hook (called by the shim).
- `herdr-plugin.toml`: `viewer` pane entrypoint (split), `open` action,
  `min_herdr_version = "0.9.1"`, platforms linux and macos.

## Tests

Mark a test `[x]` when it passes. Structural and behavioral changes are
committed separately. Mocked Herdr behavior never replaces the real-device
checks at the end.

### 0. spike (manual, record the findings here before the TDD steps)
- [x] a plugin pane opens right of a given pane without focus, gets the env passed in the request, and its `HERDR_PANE_ID` is its own pane
      (2026-09-24, Mac local and container session, `herdr plugin pane open
      --placement split --target-pane <p> --direction right --no-focus --env
      K=V`): the pane opened right of the target at a 50:50 split, the
      focused pane did not change and the result reported `focused: false`,
      the `--env` values arrived, `HERDR_PANE_ID` was the new pane and equal
      to `result.plugin_pane.pane.pane_id`, the working directory was the
      plugin root, and `HERDR_PLUGIN_STATE_DIR` was
      `~/.local/state/herdr/plugins/<plugin id>`. The pty size was known at
      start but differed from `pane.layout` for the new hidden tab (Mac
      74x31 vs 63x31; container 140x40 vs 70x40). `HERDR_PLUGIN_CONTEXT_JSON`
      named the target pane as `focused_pane_id`, not the pane the user had
      focused.
- [x] the plugin pane closes when its command exits (q and a crash)
      (2026-09-24, Mac local and container session): after `q` (exit 0), an
      uncaught exception (exit 1), and SIGKILL the pane closed within 4 s and
      the target pane took back the full width (63→126, 70→140). So q closes
      the viewer pane itself and the next publish reopens it, and graphics
      layers go away with the pane. A crash leaves nothing on screen, so the
      viewer must log errors to a file under the state directory.
- [x] two streams with different layer ids draw on one pane at the same time
      (2026-09-24): on the Mac a probe pane showed a red frame from stream
      layer `main` (z 10) and a blue one from layer `thumbs` (z 20) together;
      closing only the `thumbs` stream removed only the blue frame (screen
      captures). In the container session both streams opened with `ok` and
      both frames were accepted (display not captured there). Closing a
      stream means closing the socket and any `makefile()` reader: with the
      reader left open the connection, and the layer, stayed.
- [x] the computed state directory equals `HERDR_PLUGIN_STATE_DIR` inside a plugin pane
      (2026-09-24): equal on the Mac (`XDG_STATE_HOME` set to the default
      path) and in the container session (`XDG_STATE_HOME` unset). The hook
      runs with Claude's environment and the viewer with the Herdr server's,
      so they could still disagree if `XDG_STATE_HOME` differed; `publish`
      therefore passes the store root it resolved to the viewer through the
      open request's env, and the viewer uses that.
- [x] thumbnail RGBA can be obtained on the Mac (sips BMP) and in the container (magick RGBA)
      (2026-09-24, 200x100 RGBA source with a half-transparent quadrant
      resized to 40x20): `sips -s format bmp --resampleHeightWidth` wrote a
      BITMAPV5HEADER (124 bytes), 32 bpp, BI_BITFIELDS with masks R ff0000,
      G ff00, B ff, A ff000000 (BGRA bytes), stored top-down (negative
      height); colors matched within 1 and alpha 128 stayed straight (not
      premultiplied). `magick src -resize 40x20! -depth 8 RGBA:-` returned
      exactly 3200 bytes of straight RGBA with exact colors. The BMP reader
      must honor the row order sign and reject masks other than these.
- [x] `herdr plugin link` of the local clone works for development and survives a Herdr server restart
      (2026-09-24, Mac, the spike plugin directory standing in for the clone,
      which has no manifest yet): after linking, a throwaway named session
      listed the plugin and opened its pane; after `server stop` and a new
      server start the plugin was still listed and its pane opened again.
      Link registrations are global to the user, so the main server needs no
      restart to test this.

### conversation key
- [x] a Claude hook payload with session_id keys the history by that session
- [x] without a conversation id the key falls back to the caller pane
- [x] keys are validated and hashed before becoming directory names

### safety
- [x] symlinks, FIFOs, and directories are rejected; content is copied from the opened regular file
- [x] files whose magic bytes are not a supported image are rejected
- [x] files above the size or pixel caps are rejected
- [x] storage directories are 0700 and files 0600; foreign-owned or symlinked storage is refused
- [x] control characters in file names never reach the title

### store
- [x] publishing an image records it as the newest entry with an archived copy
- [x] publishing the same content again moves it to the newest position
- [x] publishing new content at an already published path adds a new entry
- [x] the history keeps at most 30 entries (29, 30, 31) and deletes archives no entry references
- [x] histories of different conversation keys are independent
- [x] concurrent publishes to one conversation do not lose entries
- [x] an entry stays viewable from the archive after its source is deleted
- [x] a crash after archiving but before replacing the history leaves the old history intact
- [x] a corrupt history is moved aside and its archives are kept
- [x] a history with an unknown schema version is neither modified nor collected
      (publish raises NewerSchema before touching anything; collection is
      covered by the GC test below)
- [x] GC removes conversations whose `updated_at` is older than 14 days (boundary ±1 s)
- [x] GC never collects a conversation with a newer-schema or set-aside corrupt history
- [x] GC trims the total below 500 MiB oldest first (boundary ±1 byte) and skips locked conversations
- [x] two GC runs at once and GC during a publish to another conversation stay consistent
- [x] publish fails with a capacity error when GC cannot free enough space, keeping the history
- [x] publish runs the age-based GC at most once an hour
- [x] GC removes unreferenced archive files and temp files older than an hour, except in protected conversations

### layout
- [x] a tall pane (by pixel aspect) places the grid below the main image
- [x] a wide pane places the grid in columns on the right
- [x] a pane below the minimum size shows no thumbnails
- [x] an empty history lays out a placeholder
- [x] unknown or zero pty or cell sizes produce no layout (wait for the next size)
- [x] h/j/k/l move left/down/up/right and stop only at the ends of the history
- [x] moving past the visible page shows the next or previous page
- [x] a move onto a ragged last row lands on the nearest existing cell
- [x] with thumbnails hidden h/l step through the history and j/k do nothing
- [x] the main image is fitted inside its area, centered, never upscaled

### png / composite (hand-made RGBA fixtures, no external tools)
- [x] the PNG encoder writes valid chunks and CRCs; an external decoder reads it back
- [x] thumbnails land in their cells with the highlight border on the selection (pixel assertions)
- [x] unselected thumbnails are dimmed once, not again on every move
- [x] semi-transparent thumbnails are flattened onto the background before dimming
- [x] a composite over the canvas budget reduces the page capacity

### imaging
- [x] PNG dimensions are read from the header; truncated PNGs are rejected
- [x] an image is resized to an exact pixel size with the available tool
- [x] resizing gives up after its time limit and reports an error
- [x] a GIF or TIFF converts its first frame
- [x] EXIF orientation is applied
- [x] thumbnail pixels are read as RGBA and a wrong length is rejected
      (a real-tool check found that sips writes 24 bpp BI_RGB with padded rows
      for opaque sources; spike 0-5 had only seen the 32 bpp bitfield layout
      it writes for sources with alpha)

### herdr_api
- [x] opening a stream waits for the ok reply
- [x] a rejected frame is reported and closes the stream
- [x] an EOF between frames is noticed without sending a frame
- [x] frames respect 16 MiB (limit -1/0/+1)

### viewer (fake clock, PTY)
- [x] on start the viewer shows the newest entry and a "n/N name WxH" title
- [x] with follow_latest a newly published image becomes the selection
- [x] after moving to an older image a new image keeps the selection and marks "new"
- [x] moving back to the newest image turns follow_latest on and clears "new"
- [x] the selection follows its content hash when entries move or the selected one is dropped
- [x] moving the selection re-sends the main image and the composite, converting only a newly selected image (thumbnails and revisited images come from caches)
- [x] the thumbnail cache stays within 32 MiB, dropping the least recently used
- [x] repeated keys coalesce into one redraw of the last selection
- [x] a move blocked at an end sends nothing
- [ ] SIGWINCH bursts are debounced (fake clock) and identical frames are not re-sent
- [ ] a viewer started with a stale pty size re-fits after the next SIGWINCH
- [ ] a lost stream is restored without input, with backoff, and gives up with a message
- [ ] a resource error drops the thumbnails first and then reports the main image as unavailable
- [ ] q, SIGTERM, SIGHUP, and EOF exit and restore the TTY
- [ ] arrow key escape sequences split across reads are parsed
- [ ] an entry whose archive is missing shows a placeholder
- [ ] a conversation removed by GC turns into an empty view
- [ ] an unexpected error is logged to a file under the state directory before the viewer exits

### launcher / cli
- [ ] `publish` opens the viewer next to the caller pane when the conversation has none
- [ ] concurrent publishes and a manual open leave at most one live viewer and no extra panes
- [ ] an open that timed out is not retried while its reservation is live
- [ ] a viewer launched after its reservation expired exits before drawing when another is live
- [ ] a registration left by a dead viewer is replaced
- [ ] an old viewer exiting does not remove a newer viewer's registration

### hook
- [ ] the plugin hook publishes image files and ignores other files
- [ ] malformed JSON, wrong types, and paths with spaces, quotes, or newlines are handled; relative paths resolve against the hook cwd
- [ ] the hook finishes within its time budget, always exits 0, and honors its disable variable

### integration (devcon-herdr)
- [ ] `devcon-herdr plugins update` locks the plugin's latest release, and an old lock without it gains the entry
- [ ] devcon-herdr installs the locked commit on the Mac and in a container and skips it when already current
- [ ] a failed install keeps the previous version
- [ ] the shared hook shim calls the plugin where it is linked and falls back to the old preview path where it is not (Mac, WSL2 host, container)
- [ ] registering the shim is idempotent, keeps other settings, and replaces the old preview hook entry
- [ ] real devices: Mac local, WSL2 thin client, publish into a hidden tab then show it, several conversations, reconnect, transfer volume while browsing

## Open items

- Where Codex exposes a conversation id (until then Codex uses the pane fallback).
