# Termusic

A minimalist, terminal-based music player (TUI) written in Python. Built with a dark, transparent aesthetic, live CAVA audio spectrum, synced lyrics, YouTube downloading, and automatic album organization.

## Features

* Automatic Album Organization & All Songs Playlist: Recursively scans ~/Music for .mp3 and .flac files, groups tracks by their metadata Album tags, and provides an All Songs playlist at the top for quick access to your entire library.
* Album & Track Search (/): Search albums by album name or by a song title/artist inside them, jumping straight to the matching album and track.
* Deezer Auto-Tagging: Automatically cleans filenames and fetches missing Artist, Title, and Album metadata via the Deezer API, saving tags directly to your audio files.
* Live Audio Spectrum (CAVA): Creates an isolated PulseAudio/PipeWire virtual sink so the visualizer reacts only to Termusic's audio.
* Synced Lyrics (LRCLIB): Fetches time-synced lyrics on the fly and caches them locally as .lrc files for offline use. Toggle between the visualizer and lyrics anytime with Ctrl+L.
* Volume Boost, Visual Bar & Milestone Beeps: Adjust volume from 0% up to 200% (boosted above 100% via PulseAudio/PipeWire), complete with a visual volume bar and audio confirmation beeps at 100% (1 beep), 150% (2 beeps), and 200% (3 beeps).
* Built-in YouTube Downloader: Download .mp3 tracks directly from YouTube using yt-dlp and ffmpeg, with automatic Deezer tagging right after download.
* Theme Integration: Automatically reads colors from Omarchy (~/.config/omarchy/current/theme/colors.toml) with a built-in earth-toned fallback palette.

## Dependencies & Requirements

System packages (Arch Linux):
* python
* yt-dlp
* ffmpeg (includes ffplay for volume milestone beeps)
* cava
* libpulse
* git

Python packages (requirements.txt):
* textual
* just_playback
* mutagen

## Installation & Setup

Install system packages:
`
  sudo pacman -S python yt-dlp ffmpeg cava libpulse git
`

Clone repository:
`
  git clone https://github.com/rudyyyv4/Termusic.git ~/TerminalMusic
  cd ~/TerminalMusic
`

Create a virtual environment and install Python packages:
`
  python -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
`

Run:
`
  python Main.py
`

Global command (run `termusic` from anywhere):
`
  mkdir -p ~/.local/bin
  printf '#!/bin/bash\ncd ~/TerminalMusic\nsource venv/bin/activate\nexec python Main.py "$@"\n' > ~/.local/bin/termusic
  chmod +x ~/.local/bin/termusic
`

## Keybindings

* Space — Play / Pause
* Enter — Select Album / Play Track
* Esc — Focus back to Albums / Playlists
* / — Search Albums or Tracks
* < / >— Next / Previous Track
* } / { — Volume +5% / -5% (up to 200% boost)
* L — Toggle Loop
* R — Toggle Random (Shuffle)
* Ctrl+L — Toggle Live Audio Spectrum / Synced Lyrics
* s — Auto-tag untagged audio files via Deezer API
* a — Add Playlist (in Albums) / Download from YouTube (in Tracks)
* d — Delete selected Album or Track
* q — Quit
