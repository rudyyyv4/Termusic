# Termusic

A minimalist, terminal-based music player (TUI) written in Python. Built with a dark, transparent aesthetic, live CAVA audio spectrum, synced lyrics, YouTube downloading, and automatic album organization.

## Features

* **Automatic Album Organization:** Recursively scans `~/Music` and groups tracks by their ID3 `Album` tags (similar to Musicolet).
* **Deezer Auto-Tagging:** Automatically cleans filenames and fetches missing Artist, Title, and Album metadata via the Deezer API, saving tags directly to your `.mp3` files.
* **Live Audio Spectrum (CAVA):** Creates an isolated PulseAudio/PipeWire virtual sink so the visualizer reacts *only* to Termusic's audio.
* **Synced Lyrics (LRCLIB):** Fetches time-synced lyrics on the fly and caches them locally as `.lrc` files for offline use. Toggle between the visualizer and lyrics anytime with `Ctrl+L`.
* **Built-in YouTube Downloader:** Download `.mp3` tracks directly from YouTube using `yt-dlp` and `ffmpeg`, with automatic Deezer tagging right after download.
* **Theme Integration:** Automatically reads colors from Omarchy (`~/.config/omarchy/current/theme/colors.toml`) with a built-in earth-toned fallback palette.

## Requirements

### System Packages (Arch Linux)
* `python`
* `yt-dlp`
* `ffmpeg`
* `cava`
* `libpulse`
* `git`

Install them with:
```bash
sudo pacman -S python yt-dlp ffmpeg cava libpulse git
```

### Python Dependencies (installed via `requirements.txt`)
* `textual`
* `just_playback`
* `mutagen`

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/rudyyyv4/Termusic.git](https://github.com/rudyyyv4/Termusic.git) ~/TerminalMusic
   cd ~/TerminalMusic
   ```

2. **Create a virtual environment and install Python packages:**
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Run the player:**
   ```bash
   python Main.py
   ```

### Optional: Run as a global `termusic` command
To launch the player from anywhere by typing `termusic`:

```bash
mkdir -p ~/.local/bin
cat << 'SCRIPT' > ~/.local/bin/termusic
#!/bin/bash
cd ~/TerminalMusic
source venv/bin/activate
exec python Main.py "$@"
SCRIPT
chmod +x ~/.local/bin/termusic
```

## Keybindings

| Key | Action |
| :--- | :--- |
| `Space` | Play / Pause |
| `Enter` | Select Album / Play Track |
| `Esc` | Focus back to Albums / Playlists |
| `>` / `<` | Next / Previous Track |
| `}` / `{` | Volume +5% / -5% |
| `L` | Toggle Loop |
| `R` | Toggle Random (Shuffle) |
| `Ctrl+L` | Toggle Live Audio Spectrum / Synced Lyrics |
| `s` | Auto-tag untagged `.mp3` files via Deezer API |
| `a` | Add Playlist (in Albums) / Download from YouTube (in Tracks) |
| `d` | Delete selected Album or Track |
| `q` | Quit |
