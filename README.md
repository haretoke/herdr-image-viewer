# herdr-image-viewer

A [Herdr](https://herdr.dev) plugin that keeps a history of the images an agent
reads in each conversation and shows them in a pane next to the agent: the
selected image large, the others as small dimmed thumbnails with the selection
highlighted.

- One viewer pane per conversation, opened to the right of the agent's pane
  without taking focus, and reused for every image of that conversation.
- The viewer follows the newest image until you move away from it; new images
  are then marked in the title instead of taking over.
- Thumbnails sit below the image in a tall pane and to its right in a wide one.
- Images are copied into the plugin's state directory, so they stay viewable
  after the original file is gone.

Status: early (0.1.0). The tests run on macOS and in a Linux container; built
against Herdr 0.9.1.

## Requirements

- Herdr 0.9.1 or later
- Python 3.9 or later as `python3`
- macOS (uses `sips`) or Linux with ImageMagick (`magick` or `convert`);
  `ffmpeg` is used when present

## Install

```sh
herdr plugin install haretoke/herdr-image-viewer
```

### Claude Code

The viewer is fed by a `PostToolUse` hook on the `Read` tool. Point it at
`hooks/claude-read.sh` in the plugin root (`herdr plugin list --json` shows
`plugin_root`), for example in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Read",
        "hooks": [
          { "type": "command", "command": "sh /path/to/plugin-root/hooks/claude-read.sh", "timeout": 30 }
        ]
      }
    ]
  }
}
```

The hook does nothing outside Herdr or for files that are not images, never
prints anything, and always exits 0. Set `HERDR_IMAGE_VIEWER_HOOK=0` to turn it
off without editing the settings. Each Claude session (`session_id`) gets its
own history.

## Use

| Key | Action |
|---|---|
| `h` / `←` | left |
| `l` / `→` | right |
| `k` / `↑` | up |
| `j` / `↓` | down |
| `q` | close the viewer |

Moving past the visible thumbnails pages through the history. Closing the
viewer keeps the history; the next image reopens it. The **Open image viewer**
action reopens the viewer for the conversation last shown from the focused
pane.

Other agents or scripts can publish an image directly:

```sh
python3 -m herdr_image_viewer publish IMAGE --conversation KEY --caller-pane PANE_ID
```

Run it from the plugin root. `KEY` is `claude:<id>`, `codex:<id>`, or
`pane:<id>`.

## Storage and limits

History and copies live in
`${XDG_STATE_HOME:-~/.local/state}/herdr/plugins/haretoke.image-viewer`
(directories 0700, files 0600).

| | |
|---|---|
| images per conversation | 30 (the oldest drop out) |
| conversations kept | 14 days after their last image |
| total size | 500 MiB (least recently updated conversations go first) |
| input file | 50 MiB, 100 megapixels |

Only PNG, JPEG, GIF, WebP, BMP, TIFF, and HEIC files are accepted, recognized
by their content. Collection runs at most once an hour during a publish;
`python3 -m herdr_image_viewer gc` runs it now. Errors are logged to `hook.log`
and `viewer.log` in the same directory.

## Development

```sh
scripts/test.sh
```

runs the tests under `/usr/bin/python3` and the `python3` on `PATH`, plus
ruff's pyflakes rules when ruff (or uvx) is available. `plan.md` holds the
design, the Herdr behavior it relies on, and the test list.

## License

MIT
