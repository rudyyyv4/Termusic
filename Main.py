import os
import re
import sys
import json
import shutil
import random
import subprocess
import tomllib
import atexit
import urllib.request
import urllib.parse
from pathlib import Path
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Header, Footer, ListView, ListItem, Label, Input, Button
from textual.containers import Horizontal, Vertical, Grid
from textual.binding import Binding
from just_playback import Playback
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
from mutagen import File as MutagenFile

ALL_SONGS_PLAYLIST = "All Songs"
RANDOM_HISTORY_SIZE = 5
STATS_PATH = Path.home() / ".config" / "termusic" / "stats.json"


# Ścieżka do aktywnego motywu w Omarchy
OMARCHY_COLORS_PATH = Path.home() / ".config" / "omarchy" / "current" / "theme" / "colors.toml"

def load_strict_omarchy_colors():
    """Wczytuje kolory z Omarchy, a w razie braku pliku używa Twojej ziemistej palety wygenerowanej przez Aether."""
    fallback_colors = {
        "background": "transparent",
        "foreground": "#A8A890",
        "accent": "#F7DFB1",
        "color1": "#C3C67C",
        "color4": "#5E8B5A"
    }
    
    if not OMARCHY_COLORS_PATH.exists():
        return fallback_colors
        
    try:
        with open(OMARCHY_COLORS_PATH, "rb") as f:
            theme_data = tomllib.load(f)
            return {
                "background": "transparent",
                "foreground": theme_data.get("foreground", fallback_colors["foreground"]),
                "accent": theme_data.get("accent", fallback_colors["accent"]),
                "color1": theme_data.get("color1", theme_data.get("accent", fallback_colors["color1"])),
                "color4": theme_data.get("color4", theme_data.get("accent", fallback_colors["color4"]))
            }
    except Exception:
        return fallback_colors

COLORS = load_strict_omarchy_colors()


def clean_filename_for_search(filename: str) -> str:
    """Usuwa śmieci z nazw plików pobranych z YouTube przed wysłaniem zapytania do API."""
    name = filename
    name = re.sub(r"[\(\[].*?[\)\]]", "", name)
    name = re.sub(r"(?i)(snapyt\.app|y2mate\.com|official video|official audio|lyrics|hd|4k)", "", name)
    name = re.sub(r"[_-]+", " ", name)
    name = re.sub(r"(?<=[a-ząćęłńóśźż])(?=[A-ZĄĆĘŁŃÓŚŹŻ])", " ", name)
    return " ".join(name.split()).strip()


def fetch_deezer_metadata(query: str) -> dict | None:
    """Pyta darmowe API Deezera o metadane utworu."""
    if not query:
        return None
    try:
        encoded_q = urllib.parse.quote(query)
        url = f"https://api.deezer.com/search?q={encoded_q}&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Termusic/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            tracks = data.get("data", [])
            if tracks:
                track = tracks[0]
                return {
                    "title": track.get("title", ""),
                    "artist": track.get("artist", {}).get("name", ""),
                    "album": track.get("album", {}).get("title", "")
                }
    except Exception:
        pass
    return None


def parse_lrc(lrc_text: str) -> list[tuple[float, str]]:
    """Parsuje tekst w formacie LRC na listę krotek (czas_w_sekundach, linijka)."""
    lines = []
    pattern = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
    for raw_line in lrc_text.splitlines():
        match = pattern.match(raw_line.strip())
        if match:
            mins = int(match.group(1))
            secs = float(match.group(2))
            text = match.group(3).strip()
            if text:
                lines.append((mins * 60.0 + secs, text))
    return sorted(lines, key=lambda x: x[0])


def fetch_lrclib_lyrics(song_path: Path, artist: str, title: str) -> tuple[list[tuple[float, str]], list[str]]:
    """
    Szuka tekstu utworu: najpierw w lokalnym pliku .lrc, potem w LRCLIB API.
    Zwraca (synced_lines, plain_lines).
    """
    lrc_path = song_path.with_suffix(".lrc")
    if lrc_path.exists():
        try:
            content = lrc_path.read_text(encoding="utf-8")
            synced = parse_lrc(content)
            if synced:
                return synced, []
            return [], [l.strip() for l in content.splitlines() if l.strip()]
        except Exception:
            pass

    headers = {"User-Agent": "Termusic/1.0 (https://github.com/rudyyyv4/Termusic)"}
    data = None

    if artist and title:
        try:
            params = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
            url = f"https://lrclib.net/api/get?{params}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            data = None

    if not data:
        query = f"{artist} {title}".strip() if (artist or title) else clean_filename_for_search(song_path.stem)
        if query:
            try:
                params = urllib.parse.urlencode({"q": query})
                url = f"https://lrclib.net/api/search?{params}"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    results = json.loads(resp.read().decode("utf-8"))
                    if isinstance(results, list) and results:
                        data = next((r for r in results if r.get("syncedLyrics")), results[0])
            except Exception:
                data = None

    if not data:
        return [], []

    synced_raw = data.get("syncedLyrics")
    plain_raw = data.get("plainLyrics")

    if synced_raw:
        try:
            lrc_path.write_text(synced_raw, encoding="utf-8")
        except Exception:
            pass
        return parse_lrc(synced_raw), []

    if plain_raw:
        return [], [l.strip() for l in plain_raw.splitlines() if l.strip()]

    return [], []


def get_audio_duration(song_path: Path) -> float:
    """Zwraca długość utworu w sekundach."""
    try:
        audio = MutagenFile(str(song_path))
        if audio and getattr(audio, "info", None) and getattr(audio.info, "length", None):
            return float(audio.info.length)
    except Exception:
        pass
    return 0.0


def format_duration_hms(total_seconds: float) -> str:
    """Formatuje sekundy jako HH:MM:SS lub MM:SS."""
    sec = max(0, int(round(total_seconds)))
    hours, rem = divmod(sec, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def load_album_play_stats() -> dict:
    """Wczytuje licznik odtworzeń dla albumów."""
    try:
        if STATS_PATH.exists():
            data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def save_album_play_stats(stats: dict) -> None:
    """Zapisuje licznik odtworzeń dla albumów."""
    try:
        STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def read_audio_tags(song_path: Path) -> dict:
    """Odczytuje tagi (title, artist, album) z MP3 oraz FLAC."""
    try:
        audio = MutagenFile(str(song_path), easy=True)
        if not audio:
            return {"title": "", "artist": "", "album": ""}
        title = (audio.get("title") or [""])[0].strip()
        artist = (audio.get("artist") or [""])[0].strip()
        album = (audio.get("album") or [""])[0].strip()
        return {"title": title, "artist": artist, "album": album}
    except Exception:
        return {"title": "", "artist": "", "album": ""}


def write_audio_tags(song_path: Path, title: str, artist: str, album: str) -> bool:
    """Zapisuje tagi do MP3 oraz FLAC."""
    try:
        audio = MutagenFile(str(song_path), easy=True)
        if audio is None:
            if song_path.suffix.lower() == ".mp3":
                try:
                    audio = EasyID3(str(song_path))
                except ID3NoHeaderError:
                    audio = EasyID3()
                    audio.save(str(song_path))
                    audio = EasyID3(str(song_path))
            else:
                return False
        if title:
            audio["title"] = [title] if isinstance(audio.get("title"), list) else title
        if artist:
            audio["artist"] = [artist] if isinstance(audio.get("artist"), list) else artist
        if album:
            audio["album"] = [album] if isinstance(audio.get("album"), list) else album
        audio.save()
        return True
    except Exception:
        return False

class SearchAlbumModal(ModalScreen[tuple[str | None, Path | None]]):
    """Wyszukuje album po nazwie albumu albo po utworze (tytuł / wykonawca / plik)."""
    CSS = f"""
    SearchAlbumModal {{ align: center middle; background: rgba(0, 0, 0, 0.75); }}
    #search-dialog {{ width: 62; height: 18; background: #000000; border: solid {COLORS.get('color4')}; padding: 1; }}
    #search-dialog > Label {{ text-style: bold; color: {COLORS.get('accent')}; margin-bottom: 1; }}
    Input {{ border: tall {COLORS.get('color4')}; background: transparent; color: {COLORS.get('foreground')}; margin-bottom: 1; }}
    #search-results {{ height: 1fr; border: none; background: transparent; }}
    #search-results > ListItem {{ padding: 0 1; color: {COLORS.get('foreground')}; }}
    #search-results > ListItem.--highlight {{ background: transparent; color: {COLORS.get('accent')}; text-style: bold; }}
    """

    def __init__(self, playlists: dict):
        super().__init__()
        self.playlists = playlists
        self.matches: list[tuple[str, Path | None, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="search-dialog"):
            yield Label("Search album or song inside album:")
            yield Input(placeholder="Type album name or song title...", id="search-input")
            yield ListView(id="search-results")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()
        self.update_matches("")

    def on_input_changed(self, event: Input.Changed) -> None:
        self.update_matches(event.value.strip().lower())

    def update_matches(self, query: str) -> None:
        results_view = self.query_one("#search-results", ListView)
        results_view.clear()
        self.matches.clear()

        for album_name, songs in self.playlists.items():
            if album_name == ALL_SONGS_PLAYLIST:
                continue

            album_match = not query or (query in album_name.lower())
            matched_song = None

            if query:
                for song in songs:
                    haystack = f"{song.get('display', '')} {song.get('title', '')} {song.get('artist', '')} {song['path'].stem}".lower()
                    if query in haystack:
                        matched_song = song
                        break

            if album_match or matched_song:
                if matched_song and not album_match:
                    label = f"{album_name}  [song: {matched_song['display']}]"
                    song_path = matched_song["path"]
                else:
                    label = f"{album_name} ({len(songs)} tracks)"
                    song_path = matched_song["path"] if matched_song else None

                self.matches.append((album_name, song_path, label))
                results_view.append(ListItem(Label(escape(label))))

        if results_view.children:
            results_view.index = 0

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.matches:
            idx = self.query_one("#search-results", ListView).index or 0
            if 0 <= idx < len(self.matches):
                album_name, song_path, _ = self.matches[idx]
                self.dismiss((album_name, song_path))
                return
        self.dismiss((None, None))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self.matches):
            album_name, song_path, _ = self.matches[idx]
            self.dismiss((album_name, song_path))

    def key_escape(self) -> None:
        self.dismiss((None, None))


class InfoModal(ModalScreen[None]):
    CSS = f"""
    InfoModal {{ align: center middle; background: rgba(0, 0, 0, 0.78); }}
    #info-dialog {{ width: 64; height: auto; max-height: 24; background: #000000; border: solid {COLORS.get('color4')}; padding: 1 2; }}
    #info-title {{ text-style: bold; color: {COLORS.get('accent')}; margin-bottom: 1; }}
    #info-body {{ color: {COLORS.get('foreground')}; margin-bottom: 1; }}
    #info-hint {{ color: {COLORS.get('color1')}; text-align: right; }}
    """

    def __init__(self, title: str, body: str):
        super().__init__()
        self.info_title = title
        self.info_body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="info-dialog"):
            yield Label(escape(self.info_title), id="info-title")
            yield Label(self.info_body, id="info-body")
            yield Label("[Enter / Esc / q] Close", id="info-hint")

    def on_key(self, event) -> None:
        if event.key in ("enter", "escape", "q"):
            self.dismiss(None)


class AddPlaylistModal(ModalScreen[str]):
    CSS = f"""
    AddPlaylistModal {{ align: center middle; background: rgba(0, 0, 0, 0.7); }}
    #dialog {{ width: 45; height: 7; background: #000000; border: solid {COLORS.get('color4')}; padding: 1; align: center middle; }}
    #dialog > Label {{ text-style: bold; color: {COLORS.get('accent')}; margin-bottom: 1; }}
    Input {{ border: tall {COLORS.get('color4')}; background: transparent; color: {COLORS.get('foreground')}; }}
    """
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Enter playlist name:")
            yield Input(placeholder="My Awesome Playlist", id="playlist-input")
    def on_mount(self) -> None: self.query_one("#playlist-input").focus()
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() if event.value.strip() else "")


class DownloadYoutubeModal(ModalScreen[str]):
    CSS = f"""
    DownloadYoutubeModal {{ align: center middle; background: rgba(0, 0, 0, 0.7); }}
    #dialog-yt {{ width: 60; height: 7; background: #000000; border: solid {COLORS.get('color4')}; padding: 1; align: center middle; }}
    #dialog-yt > Label {{ text-style: bold; color: {COLORS.get('accent')}; margin-bottom: 1; }}
    Input {{ border: tall {COLORS.get('color4')}; background: transparent; color: {COLORS.get('foreground')}; }}
    """
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-yt"):
            yield Label("Enter YouTube Link:")
            yield Input(placeholder="https://www.youtube.com/watch?v=...", id="yt-input")
    def on_mount(self) -> None: self.query_one("#yt-input").focus()
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() if event.value.strip() else "")


class DeleteConfirmModal(ModalScreen[bool]):
    CSS = f"""
    DeleteConfirmModal {{ align: center middle; background: rgba(0, 0, 0, 0.7); }}
    #confirm-dialog {{ width: 50; height: 8; background: #000000; border: solid {COLORS.get('color4')}; padding: 1; align: center middle; }}
    #confirm-dialog > Label {{ text-align: center; text-style: bold; color: {COLORS.get('foreground')}; margin-bottom: 1; }}
    #button-row {{ height: 3; align: center middle; }}
    Button {{ margin: 0 1; width: 12; }}
    #btn-yes {{ background: {COLORS.get('color1')}; color: #000000; }}
    #btn-no {{ background: {COLORS.get('color4')}; color: #000000; }}
    """
    def __init__(self, item_name: str, is_song: bool = False):
        super().__init__()
        self.item_name = item_name
        self.is_song = is_song
    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            item_type = "song" if self.is_song else "album/playlist"
            yield Label(f"Are you sure you want to delete\n{item_type} '{self.item_name}'?")
            with Horizontal(id="button-row"):
                yield Button("Yes", id="btn-yes")
                yield Button("No", id="btn-no")
    def on_mount(self) -> None: self.query_one("#btn-no").focus()
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-yes": self.dismiss(True)
        elif event.button.id == "btn-no": self.dismiss(False)


class Termusic(App):
    CSS = f"""
    App, Screen {{ background: transparent; }}
    * {{ scrollbar-size: 0 0; background: transparent; }} 
    
    #layout {{ height: 1fr; background: transparent; }}
    #left-sidebar {{ width: 1fr; height: 100%; grid-size: 1 2; grid-rows: 1fr 1fr; grid-gutter: 0; background: transparent; }}
    
    /* Ujednolicone ramki i ciasne marginesy w lewej kolumnie */
    .column {{ border: solid {COLORS.get('color4')}; background: transparent; margin: 1; padding: 1; }}
    #playlists-container {{ margin: 1 1 0 1; }}
    #visualizer-container {{ margin: 0 1 1 1; align: center bottom; background: transparent; }}
    #songs-container {{ width: 1fr; height: 100%; background: transparent; }}
    
    /* Kontener dolny */
    #now-playing-container {{ height: 5; border: solid {COLORS.get('color4')}; background: transparent; margin: 0 1 1 1; padding: 0 1; }}
    
    /* Słupki audio i tryb Lyrics */
    #visualizer-display {{ color: {COLORS.get('color1')}; text-align: center; width: 100%; height: 100%; content-align: center bottom; background: transparent; }}
    #visualizer-display.lyrics-mode {{ color: {COLORS.get('foreground')}; content-align: center middle; }}
    
    #top-meta-row {{ layout: grid; grid-size: 2 1; grid-columns: 1fr auto; width: 100%; height: 1; background: transparent; }}
    #modes-row {{ height: 1; width: 100%; text-align: left; text-style: bold; color: {COLORS.get('foreground')}; background: transparent; }}
    #volume-box {{ layout: horizontal; width: auto; height: 1; align: right middle; background: transparent; }}
    #volume-bar {{ width: 22; height: 1; text-align: right; color: {COLORS.get('foreground')}; background: transparent; }}
    #volume-display {{ width: auto; height: 1; margin-left: 1; text-align: right; text-style: bold; color: {COLORS.get('accent')}; background: transparent; }}
    
    #status-row {{ layout: grid; grid-size: 1 1; width: 100%; height: 1; background: transparent; }}
    #now-playing {{ text-align: center; text-style: bold; color: {COLORS.get('accent')}; width: 100%; background: transparent; }}
    
    #custom-progress-bar {{ width: 100%; height: 1; background: transparent; color: {COLORS.get('foreground')}; text-align: center; }}
    
    ListView {{ background: transparent; }}
    ListView > ListItem {{ padding: 0 1; border-left: tall transparent; background: transparent; color: {COLORS.get('foreground')}; }}
    ListView > ListItem.--highlight {{ background: transparent; color: {COLORS.get('foreground')}; text-style: bold; }}
    
    .active-item {{ 
        background: transparent !important; 
        border-left: tall {COLORS.get('color1')} !important; 
        color: {COLORS.get('foreground')} !important; 
        text-style: bold !important; 
    }}
    
    Header {{ background: transparent; color: {COLORS.get('accent')}; }}
    Footer {{ background: transparent; color: {COLORS.get('foreground')}; }}
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "toggle_pause", "Pause/Play"),
        Binding("escape", "back_to_playlists", "Back"),
        Binding("{", "volume_down", "Vol -5%"),
        Binding("}", "volume_up", "Vol +5%"),
        Binding("a", "add_or_download", "Add/YT"),
        Binding("d", "delete_item", "Delete"),
        Binding("s", "auto_tag_deezer", "Auto-Tag"),
        Binding("slash", "search_albums", "Search"),
        Binding("ctrl+l", "toggle_visualizer_mode", "Lyrics/Spec"),
        Binding("I", "show_albums_info", "Albums Info"),
        Binding("ctrl+i", "show_playlist_info", "Playlist Info"),
        Binding(">", "next_song", "Next"),
        Binding("<", "prev_song", "Prev"),
        Binding("L", "toggle_loop", "Loop"),
        Binding("R", "toggle_random", "Random"),
    ]

    def __init__(self):
        super().__init__()
        self.music_dir = Path.home() / "Music"
        self.playlists = {}
        self.active_playlist = None
        self.current_song_index = -1
        self.active_song_name = "music"
        self.active_song_path = None
        
        self.show_lyrics = False
        self.synced_lyrics = []
        self.plain_lyrics = []
        self.lyrics_status = "Play a song to load lyrics"
        
        self.setup_isolated_audio()
        
        self.playback = Playback()
        self.volume = 1.0
        self.max_volume = 2.0
        self.last_beep_pct = None
        self.is_loop_active = False
        self.is_random_active = False
        self.random_history_size = RANDOM_HISTORY_SIZE
        self.recent_random_paths: list[Path] = []
        self.album_play_stats = load_album_play_stats()
        self.spectrum_buffer = []
        self.cava_process = None
        self.scan_music()

    def setup_isolated_audio(self) -> None:
        self.pa_sink_id = None
        self.pa_loopback_id = None
        try:
            sink_proc = subprocess.run(
                ["pactl", "load-module", "module-null-sink", "sink_name=TerminalMusicSink", "sink_properties=device.description=TerminalMusic"],
                capture_output=True, text=True, check=True
            )
            self.pa_sink_id = sink_proc.stdout.strip()

            loop_proc = subprocess.run(
                ["pactl", "load-module", "module-loopback", "source=TerminalMusicSink.monitor"],
                capture_output=True, text=True, check=True
            )
            self.pa_loopback_id = loop_proc.stdout.strip()

            os.environ["PULSE_SINK"] = "TerminalMusicSink"
            atexit.register(self.cleanup_isolated_audio)
        except Exception:
            pass

    def cleanup_isolated_audio(self) -> None:
        if getattr(self, 'pa_sink_id', None):
            subprocess.run(["pactl", "unload-module", self.pa_sink_id], stderr=subprocess.DEVNULL)
            self.pa_sink_id = None
        if getattr(self, 'pa_loopback_id', None):
            subprocess.run(["pactl", "unload-module", self.pa_loopback_id], stderr=subprocess.DEVNULL)
            self.pa_loopback_id = None

    def scan_music(self) -> None:
        if not self.music_dir.exists():
            self.music_dir.mkdir(parents=True, exist_ok=True)
            
        grouped = {}
        all_tracks = []

        for path in sorted(self.music_dir.iterdir()):
            if path.is_dir() and not any(x.suffix.lower() in {".mp3", ".flac"} for x in path.glob("*")):
                grouped[path.name] = []

        for mp3_path in sorted(x for x in self.music_dir.rglob("*") if x.suffix.lower() in {".mp3", ".flac"}):
            tags = read_audio_tags(mp3_path)
            album = tags["album"]
            artist = tags["artist"]
            title = tags["title"]
            
            if not album:
                album = mp3_path.parent.name if mp3_path.parent != self.music_dir else "Unsorted"
                
            if artist and title:
                display = f"{artist} - {title}"
            elif title:
                display = title
            else:
                display = mp3_path.stem
                
            track_entry = {
                "path": mp3_path,
                "display": display,
                "title": title,
                "artist": artist,
                "album": album
            }
            grouped.setdefault(album, []).append(track_entry)
            all_tracks.append(track_entry)

        sorted_albums = {
            k: sorted(v, key=lambda x: x["display"].lower())
            for k, v in sorted(grouped.items(), key=lambda item: item[0].lower())
        }

        ordered_playlists = {}
        if all_tracks:
            ordered_playlists[ALL_SONGS_PLAYLIST] = sorted(all_tracks, key=lambda x: x["display"].lower())
        ordered_playlists.update(sorted_albums)
        self.playlists = ordered_playlists

    def action_search_albums(self) -> None:
        """Otwiera wyszukiwarkę albumów po nazwie albumu lub po utworze."""
        def on_search_done(result: tuple[str | None, Path | None]) -> None:
            album_name, song_path = result
            if not album_name or album_name not in self.playlists:
                return

            playlist_names = list(self.playlists.keys())
            self.active_playlist = album_name
            self.load_songs_to_ui(album_name)

            playlist_list = self.query_one("#playlist-list", ListView)
            if album_name in playlist_names:
                playlist_list.index = playlist_names.index(album_name)

            songs = self.playlists.get(album_name, [])
            target_song_idx = 0
            if song_path is not None:
                for idx, s in enumerate(songs):
                    if s["path"] == song_path:
                        target_song_idx = idx
                        break

            self.update_active_highlight()
            song_list = self.query_one("#song-list", ListView)
            if songs:
                song_list.index = target_song_idx
                song_list.focus()

        self.push_screen(SearchAlbumModal(self.playlists), on_search_done)

    def compose(self) -> ComposeResult:
        title_color = COLORS.get('accent')
        hint_color = COLORS.get('foreground')
        
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Grid(id="left-sidebar"):
                with Vertical(classes="column", id="playlists-container"):
                    yield Label(f"[bold {title_color}]ALBUMS / PLAYLISTS[/]\n[{hint_color}]('/' Search | 's' Tag | 'a' Add | 'd' Del)[/]")
                    yield ListView(id="playlist-list")
                with Vertical(classes="column", id="visualizer-container"):
                    yield Label(f"[bold {title_color}]LIVE AUDIO SPECTRUM[/] [{hint_color}](Ctrl+L: Lyrics)[/]", id="visualizer-title")
                    yield Label("", id="visualizer-display")
            with Vertical(classes="column", id="songs-container"):
                yield Label(f"[bold {title_color}]TRACKS[/]\n[{hint_color}](Press 'a' Download YT | 'd' Delete)[/]")
                yield ListView(id="song-list")
        with Vertical(id="now-playing-container"):
            with Horizontal(id="top-meta-row"):
                yield Label("", id="modes-row")
                with Horizontal(id="volume-box"):
                    yield Label("██████████──────────", id="volume-bar")
                    yield Label("[100%]", id="volume-display")
            with Horizontal(id="status-row"):
                yield Label("Status: Not playing", id="now-playing")
            yield Label("00:00 ──────────────────────────────────────── 00:00", id="custom-progress-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_playlists_ui()
        self.query_one("#playlist-list").focus()
        self.set_interval(0.04, self.update_playback_and_visualizer)
        self.update_modes_ui()
        self.run_worker(self.cava_listener_worker, thread=True)

    def action_toggle_visualizer_mode(self) -> None:
        self.show_lyrics = not self.show_lyrics
        title_color = COLORS.get('accent')
        hint_color = COLORS.get('foreground')
        title_label = self.query_one("#visualizer-title", Label)
        display_label = self.query_one("#visualizer-display", Label)

        if self.show_lyrics:
            title_label.update(f"[bold {title_color}]LYRICS[/] [{hint_color}](Ctrl+L: Spectrum)[/]")
            display_label.add_class("lyrics-mode")
            self.render_lyrics()
        else:
            title_label.update(f"[bold {title_color}]LIVE AUDIO SPECTRUM[/] [{hint_color}](Ctrl+L: Lyrics)[/]")
            display_label.remove_class("lyrics-mode")

    def load_lyrics_for_song(self, song_info: dict) -> None:
        target_path = song_info["path"]
        self.synced_lyrics = []
        self.plain_lyrics = []
        self.lyrics_status = "⏳ Searching lyrics..."

        def worker():
            synced, plain = fetch_lrclib_lyrics(target_path, song_info["artist"], song_info["title"])
            if self.active_song_path == target_path:
                self.synced_lyrics = synced
                self.plain_lyrics = plain
                if not synced and not plain:
                    self.lyrics_status = "No lyrics found"
                else:
                    self.lyrics_status = ""

        self.run_worker(worker, thread=True)

    def render_lyrics(self) -> None:
        try:
            display_label = self.query_one("#visualizer-display", Label)
            height = display_label.size.height
            if height <= 0:
                return

            accent = COLORS.get("accent", "#F7DFB1")
            fg = COLORS.get("foreground", "#A8A890")

            if self.synced_lyrics:
                curr_pos = self.playback.curr_pos if self.playback.active else 0.0
                active_idx = 0
                for i, (ts, _) in enumerate(self.synced_lyrics):
                    if ts <= curr_pos + 0.3:
                        active_idx = i
                    else:
                        break

                half = height // 2
                start = max(0, active_idx - half)
                end = min(len(self.synced_lyrics), start + height)
                if end - start < height:
                    start = max(0, end - height)

                rendered = []
                for i in range(start, end):
                    text = escape(self.synced_lyrics[i][1])
                    if i == active_idx:
                        rendered.append(f"[bold {accent}]{text}[/]")
                    else:
                        rendered.append(f"[{fg}]{text}[/]")
                display_label.update("\n".join(rendered))

            elif self.plain_lyrics:
                total_lines = len(self.plain_lyrics)
                if total_lines <= height:
                    start = 0
                else:
                    pct = (self.playback.curr_pos / self.playback.duration) if (self.playback.active and self.playback.duration > 0) else 0.0
                    start = int(pct * (total_lines - height))
                chunk = [f"[{fg}]{escape(l)}[/]" for l in self.plain_lyrics[start:start + height]]
                display_label.update("\n".join(chunk))

            else:
                display_label.update(f"[{fg}]{escape(self.lyrics_status)}[/]")
        except Exception:
            pass

    def update_active_highlight(self) -> None:
        playlist_names = list(self.playlists.keys())
        try:
            active_idx = playlist_names.index(self.active_playlist)
        except ValueError:
            active_idx = -1

        for i, item in enumerate(self.query("#playlist-list ListItem")):
            item.remove_class("active-item")
            if i == active_idx:
                item.add_class("active-item")
        
        for i, item in enumerate(self.query("#song-list ListItem")):
            item.remove_class("active-item")
            if i == self.current_song_index:
                item.add_class("active-item")

    def action_auto_tag_deezer(self) -> None:
        self.query_one("#now-playing", Label).update("🔍 Scanning library with Deezer API...")
        
        def tagger_worker():
            all_mp3s = [x for x in self.music_dir.rglob("*") if x.suffix.lower() in {".mp3", ".flac"}]
            to_tag = []
            for p in all_mp3s:
                tags = read_audio_tags(p)
                if not tags["album"] or not tags["artist"] or not tags["title"]:
                    to_tag.append(p)
            
            if not to_tag:
                self.call_from_thread(self.on_tagging_finished, 0, 0)
                return
                
            tagged_count = 0
            total = len(to_tag)
            for idx, mp3_path in enumerate(to_tag, start=1):
                self.call_from_thread(
                    self.update_status_text,
                    f"🔍 Deezer ({idx}/{total}): {mp3_path.stem[:35]}..."
                )
                query = clean_filename_for_search(mp3_path.stem)
                meta = fetch_deezer_metadata(query)
                if meta and meta["album"]:
                    if write_audio_tags(mp3_path, meta["title"], meta["artist"], meta["album"]):
                        tagged_count += 1
                        
            self.call_from_thread(self.on_tagging_finished, tagged_count, total)
            
        self.run_worker(tagger_worker, thread=True)

    def update_status_text(self, text: str) -> None:
        self.query_one("#now-playing", Label).update(text)

    def on_tagging_finished(self, tagged_count: int, total: int) -> None:
        self.scan_music()
        self.refresh_playlists_ui()
        if self.active_playlist and self.active_playlist in self.playlists:
            self.load_songs_to_ui(self.active_playlist)
        else:
            self.query_one("#song-list", ListView).clear()
        self.update_active_highlight()
        
        if total == 0:
            self.query_one("#now-playing", Label).update("✅ All tracks are already tagged!")
        else:
            self.query_one("#now-playing", Label).update(f"✅ Auto-tagged {tagged_count}/{total} tracks via Deezer!")

    def cava_listener_worker(self) -> None:
        bars_count = 200 
        self.spectrum_buffer = [0.0] * bars_count
        
        cava_conf = f"""
[general]
bars = {bars_count}
framerate = 30

[input]
method = pulse
source = TerminalMusicSink.monitor

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 1000
        """
        conf_path = Path.home() / ".config" / "cava" / "player_tui_auto_conf"
        conf_path.parent.mkdir(parents=True, exist_ok=True)
        conf_path.write_text(cava_conf.strip())

        self.cava_process = subprocess.Popen(
            ["cava", "-p", str(conf_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1 
        )

        while self.cava_process and self.cava_process.poll() is None:
            line = self.cava_process.stdout.readline()
            if not line: continue
            try:
                values = [int(v) for v in line.strip().split(';') if v]
                for i, val in enumerate(values):
                    if i < bars_count:
                        self.spectrum_buffer[i] = val / 1000.0
            except Exception:
                pass

    def refresh_playlists_ui(self) -> None:
        playlist_list = self.query_one("#playlist-list", ListView)
        playlist_list.clear()
        for name in self.playlists.keys():
            playlist_list.append(ListItem(Label(escape(name))))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        list_id = event.list_view.id
        if list_id == "playlist-list":
            playlist_names = list(self.playlists.keys())
            playlist_index = event.list_view.index
            if playlist_index is not None and 0 <= playlist_index < len(playlist_names):
                self.active_playlist = playlist_names[playlist_index]
                self.load_songs_to_ui(self.active_playlist)
                self.update_active_highlight()
                self.query_one("#song-list").focus()
        elif list_id == "song-list":
            song_index = event.list_view.index
            if self.active_playlist and song_index is not None:
                songs = self.playlists.get(self.active_playlist, [])
                if 0 <= song_index < len(songs):
                    self.play_song(songs[song_index], song_index)

    def action_add_or_download(self) -> None:
        playlist_list = self.query_one("#playlist-list", ListView)
        song_list = self.query_one("#song-list", ListView)
        if playlist_list.has_focus:
            def handle_modal_result(playlist_name: str) -> None:
                if playlist_name and playlist_name != ALL_SONGS_PLAYLIST:
                    new_dir = self.music_dir / playlist_name
                    try:
                        new_dir.mkdir(exist_ok=False)
                        self.scan_music()
                        self.refresh_playlists_ui()
                    except FileExistsError: pass
            self.push_screen(AddPlaylistModal(), handle_modal_result)
        elif song_list.has_focus:
            def handle_yt_result(url: str) -> None:
                if url: self.download_youtube_song(url)
            self.push_screen(DownloadYoutubeModal(), handle_yt_result)

    def download_youtube_song(self, url: str) -> None:
        if self.active_playlist and self.active_playlist != ALL_SONGS_PLAYLIST and (self.music_dir / self.active_playlist).is_dir():
            target_dir = self.music_dir / self.active_playlist
        else:
            target_dir = self.music_dir / "Downloads"
            target_dir.mkdir(parents=True, exist_ok=True)
            
        self.query_one("#now-playing", Label).update("⏳ Downloading from YouTube... Please wait")
        
        before_files = set(target_dir.glob("*.mp3"))
        command = [
            "/usr/bin/yt-dlp",
            "--ffmpeg-location", "/usr/bin/ffmpeg",
            "-x",
            "--audio-format", "mp3",
            "-o", f"{target_dir}/%(title)s.%(ext)s",
            url
        ]
        
        def run_downloader():
            try:
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if result.returncode == 0:
                    after_files = set(target_dir.glob("*.mp3"))
                    new_files = after_files - before_files
                    for new_mp3 in new_files:
                        self.call_from_thread(self.update_status_text, f"🔍 Tagging via Deezer: {new_mp3.stem[:30]}...")
                        query = clean_filename_for_search(new_mp3.stem)
                        meta = fetch_deezer_metadata(query)
                        if meta and meta["album"]:
                            write_audio_tags(new_mp3, meta["title"], meta["artist"], meta["album"])
                    self.call_from_thread(self.on_download_success)
                else:
                    self.call_from_thread(self.on_download_error, "❌ Download failed (yt-dlp error)")
            except Exception:
                self.call_from_thread(self.on_download_error, "❌ System error")
        self.run_worker(run_downloader, thread=True)

    def on_download_success(self) -> None:
        self.scan_music()
        self.refresh_playlists_ui()
        if self.active_playlist and self.active_playlist in self.playlists:
            self.load_songs_to_ui(self.active_playlist)
        self.query_one("#now-playing", Label).update("✅ Download & auto-tag complete!")

    def on_download_error(self, message: str) -> None:
        self.query_one("#now-playing", Label).update(message)

    def action_delete_item(self) -> None:
        playlist_list = self.query_one("#playlist-list", ListView)
        song_list = self.query_one("#song-list", ListView)
        if playlist_list.has_focus:
            playlist_names = list(self.playlists.keys())
            playlist_index = playlist_list.index
            if playlist_index is not None and 0 <= playlist_index < len(playlist_names):
                playlist_to_delete = playlist_names[playlist_index]
                if playlist_to_delete == ALL_SONGS_PLAYLIST:
                    self.query_one("#now-playing", Label).update("ℹ️ 'All Songs' cannot be deleted")
                    return
                def handle_delete_confirm(confirmed: bool) -> None:
                    if confirmed:
                        try:
                            self.playback.stop()
                            folder_path = self.music_dir / playlist_to_delete
                            if folder_path.is_dir():
                                shutil.rmtree(folder_path)
                            else:
                                for song in self.playlists.get(playlist_to_delete, []):
                                    if song["path"].exists():
                                        os.remove(song["path"])
                            self.scan_music()
                            self.refresh_playlists_ui()
                            self.query_one("#song-list", ListView).clear()
                        except Exception: pass
                self.push_screen(DeleteConfirmModal(playlist_to_delete, is_song=False), handle_delete_confirm)
        elif song_list.has_focus:
            if not self.active_playlist: return
            songs = self.playlists.get(self.active_playlist, [])
            song_index = song_list.index
            if song_index is not None and 0 <= song_index < len(songs):
                song_to_delete = songs[song_index]
                def handle_song_delete_confirm(confirmed: bool) -> None:
                    if confirmed:
                        try:
                            self.playback.stop()
                            os.remove(song_to_delete["path"])
                            self.scan_music()
                            self.refresh_playlists_ui()
                            if self.active_playlist in self.playlists:
                                self.load_songs_to_ui(self.active_playlist)
                            else:
                                self.query_one("#song-list", ListView).clear()
                            self.query_one("#now-playing", Label).update("Status: Not playing")
                        except Exception: pass
                self.push_screen(DeleteConfirmModal(song_to_delete["display"], is_song=True), handle_song_delete_confirm)

    def load_songs_to_ui(self, playlist_name: str) -> None:
        song_list_view = self.query_one("#song-list", ListView)
        song_list_view.clear()
        songs = self.playlists.get(playlist_name, [])
        for i, song in enumerate(songs):
            song_list_view.append(ListItem(Label(escape(f"{i+1}. {song['display']}"))))

    def play_song(self, song_info: dict, index: int = -1) -> None:
        song_path = song_info["path"]
        self.playback.load_file(str(song_path))
        self.apply_volume_with_boost()
        self.playback.play()
        
        self.active_song_path = song_path
        album_key = song_info.get("album") or self.active_playlist
        if album_key and album_key != ALL_SONGS_PLAYLIST:
            self.album_play_stats[album_key] = self.album_play_stats.get(album_key, 0) + 1
            save_album_play_stats(self.album_play_stats)
        self.active_song_name = song_info["display"]
        self.query_one("#now-playing", Label).update(f"🎶 Playing: {escape(self.active_song_name)}")
        self.update_progress_ui(0)
        self.load_lyrics_for_song(song_info)
        
        if index == -1:
            songs = self.playlists.get(self.active_playlist, [])
            for idx, s in enumerate(songs):
                if s["path"] == song_path:
                    self.current_song_index = idx
                    break
        else:
            self.current_song_index = index

        self.update_active_highlight()
        
        song_list = self.query_one("#song-list", ListView)
        if 0 <= self.current_song_index < len(song_list.children):
            song_list.index = self.current_song_index

    def action_next_song(self) -> None:
        if not self.active_playlist: return
        songs = self.playlists.get(self.active_playlist, [])
        if not songs: return
        
        if self.is_random_active:
            history_limit = max(0, int(self.random_history_size))
            blocked_paths = set(self.recent_random_paths[-history_limit:]) if history_limit > 0 else set()
            if self.active_song_path and len(songs) > 1:
                blocked_paths.add(self.active_song_path)

            candidates = [idx for idx, s in enumerate(songs) if s["path"] not in blocked_paths]
            if not candidates:
                # Jeśli wszystkie utwory są na liście historii, wyczyść historię poza bieżącym utworem
                self.recent_random_paths = [self.active_song_path] if self.active_song_path else []
                candidates = [idx for idx, s in enumerate(songs) if s["path"] != self.active_song_path] or list(range(len(songs)))

            chosen_idx = random.choice(candidates)
            chosen_song = songs[chosen_idx]
            self.current_song_index = chosen_idx
            if history_limit > 0:
                self.recent_random_paths = [p for p in self.recent_random_paths if p != chosen_song["path"]]
                self.recent_random_paths.append(chosen_song["path"])
                self.recent_random_paths = self.recent_random_paths[-history_limit:]
        else:
            self.current_song_index += 1
            if self.current_song_index >= len(songs):
                if self.is_loop_active:
                    self.current_song_index = 0
                else:
                    self.current_song_index = len(songs) - 1
                    return
        self.play_song(songs[self.current_song_index], self.current_song_index)

    def action_prev_song(self) -> None:
        if not self.active_playlist: return
        songs = self.playlists.get(self.active_playlist, [])
        if not songs: return
        
        self.current_song_index -= 1
        if self.current_song_index < 0:
            if self.is_loop_active:
                self.current_song_index = len(songs) - 1
            else:
                self.current_song_index = 0
        self.play_song(songs[self.current_song_index], self.current_song_index)

    def action_toggle_loop(self) -> None:
        self.is_loop_active = not self.is_loop_active
        self.update_modes_ui()

    def action_toggle_random(self) -> None:
        self.is_random_active = not self.is_random_active
        self.update_modes_ui()

    def update_modes_ui(self) -> None:
        active_modes = []
        if self.is_loop_active: active_modes.append("[L] (Loop)")
        if self.is_random_active: active_modes.append("[R] (Random)")
        self.query_one("#modes-row", Label).update(" • ".join(active_modes) if active_modes else "")

    def format_time(self, seconds: float) -> str:
        if seconds is None or seconds < 0: return "00:00"
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def update_playback_and_visualizer(self) -> None:
        if self.playback.active and self.playback.playing and self.playback.duration > 0:
            if self.playback.curr_pos >= self.playback.duration - 0.2:
                self.action_next_song()
                return
            self.update_progress_ui(self.playback.curr_pos / self.playback.duration)
            if self.show_lyrics:
                self.render_lyrics()
            else:
                self.render_live_audio_spectrum(is_playing=True)
        else:
            self.update_progress_ui(0.0)
            if self.show_lyrics:
                self.render_lyrics()
            else:
                self.render_live_audio_spectrum(is_playing=False)

    def render_live_audio_spectrum(self, is_playing: bool) -> None:
        try:
            display_label = self.query_one("#visualizer-display", Label)
            width = display_label.size.width - 2
            total_height = display_label.size.height
            
            if width <= 0 or total_height <= 0 or not self.spectrum_buffer:
                return

            bars_to_draw = min(width // 2, len(self.spectrum_buffer))
            lines = []
            
            for y in reversed(range(total_height)):
                line_chars = []
                for x in range(width):
                    bar_idx = x // 2
                    
                    if x % 2 == 1 or bar_idx >= bars_to_draw:
                        line_chars.append(" ") 
                    else:
                        base_val = self.spectrum_buffer[bar_idx]
                        boosted_val = (base_val ** 0.6) if base_val > 0 else 0
                        raw_val = boosted_val * total_height * 2.4
                        
                        if raw_val >= y + 1:
                            line_chars.append("█")
                        elif raw_val > y:
                            fraction = raw_val - y
                            block_idx = int(fraction * 8)
                            blocks = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇"]
                            line_chars.append(blocks[block_idx] if block_idx < 8 else "▇")
                        else:
                            line_chars.append(" ")
                            
                lines.append("".join(line_chars))

            display_label.update("\n".join(lines))
        except Exception:
            pass

    def update_progress_ui(self, percentage: float) -> None:
        try:
            bar_label = self.query_one("#custom-progress-bar", Label)
            
            width = bar_label.size.width
            if width <= 0: width = self.console.width - 6
            if width <= 0: width = 80
                
            curr_pos = self.playback.curr_pos if self.playback.active else 0.0
            duration = self.playback.duration if self.playback.active else 0.0
            elapsed_str = self.format_time(curr_pos)
            duration_str = self.format_time(duration)
            
            overhead = len(elapsed_str) + len(duration_str) + 2
            bar_width = width - overhead
            
            if bar_width > 0:
                filled_length = int(round(bar_width * min(1.0, max(0.0, percentage))))
                empty_length = bar_width - filled_length
                bar_blocks = "█" * filled_length + "─" * empty_length
                bar_label.update(f"{elapsed_str} {bar_blocks} {duration_str}")
        except Exception:
            pass

    def play_volume_beep(self, count: int) -> None:
        def _worker():
            try:
                single_beep = (
                    "sine=frequency=980:duration=0.07,"
                    "afade=t=in:d=0.008,afade=t=out:st=0.05:d=0.02,"
                    "apad=pad_dur=0.06"
                )
                chain = ",".join([single_beep] * count)
                proc = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", chain,
                        "-f", "wav", "-"
                    ],
                    capture_output=True,
                    timeout=3,
                )
                if proc.returncode == 0 and proc.stdout:
                    player = shutil.which("ffplay") or shutil.which("pw-play") or shutil.which("paplay")
                    if player and player.endswith("ffplay"):
                        subprocess.run(
                            [player, "-nodisp", "-autoexit", "-loglevel", "quiet", "-i", "-"],
                            input=proc.stdout,
                            timeout=3,
                        )
                    elif player and player.endswith("pw-play"):
                        subprocess.run([player, "-"], input=proc.stdout, timeout=3)
                    elif player and player.endswith("paplay"):
                        subprocess.run([player], input=proc.stdout, timeout=3)
            except Exception:
                pass

        self.run_worker(_worker, thread=True)

    def apply_volume_with_boost(self) -> None:
        pct = int(round(self.volume * 100))
        base_vol = min(1.0, self.volume)
        self.playback.set_volume(base_vol)

        # Podbicie powyżej 100% przez PulseAudio / PipeWire sink
        try:
            if pct > 100:
                subprocess.run(
                    ["pactl", "set-sink-volume", "TerminalMusicSink", f"{pct}%"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                subprocess.run(
                    ["pactl", "set-sink-volume", "TerminalMusicSink", "100%"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
        except Exception:
            pass

        vol_label = self.query_one("#volume-display", Label)
        bar_len = 20
        ratio = min(1.0, max(0.0, pct / 200.0))
        filled = int(round(bar_len * ratio))
        bar_str = "█" * filled + "─" * (bar_len - filled)
        self.query_one("#volume-bar", Label).update(bar_str)

        if pct > 100:
            vol_label.update(f"[BOOST {pct}%]")
        else:
            vol_label.update(f"[{pct}%]")

        milestone_beeps = {100: 1, 150: 2, 200: 3}
        if pct in milestone_beeps:
            if self.last_beep_pct != pct:
                self.last_beep_pct = pct
                self.play_volume_beep(milestone_beeps[pct])
        else:
            self.last_beep_pct = None

    def action_volume_up(self) -> None:
        self.volume = min(self.max_volume, round(self.volume + 0.05, 4))
        self.apply_volume_with_boost()

    def action_volume_down(self) -> None:
        self.volume = max(0.0, round(self.volume - 0.05, 4))
        self.apply_volume_with_boost()

    def action_toggle_pause(self) -> None:
        if self.playback.active:
            if self.playback.playing:
                self.playback.pause()
                self.query_one("#now-playing", Label).update("⏸️ Paused")
            else:
                self.playback.resume()
                song_name = getattr(self, 'active_song_name', 'music')
                self.query_one("#now-playing", Label).update(f"🎶 Playing: {escape(song_name)}")

    def action_show_albums_info(self) -> None:
        """Wyświetla informacje o albumach: liczba, łączny czas odsłuchu i najczęściej słuchany album."""
        accent = COLORS.get("accent", "#F7DFB1")
        fg = COLORS.get("foreground", "#A8A890")

        album_items = [(name, tracks) for name, tracks in self.playlists.items() if name != ALL_SONGS_PLAYLIST]
        albums_count = len(album_items)

        unique_paths = {}
        for _, tracks in album_items:
            for t in tracks:
                unique_paths[t["path"]] = t

        total_seconds = sum(get_audio_duration(path) for path in unique_paths.keys())
        total_time_str = format_duration_hms(total_seconds)

        most_played_text = "No play history yet"
        if self.album_play_stats:
            existing_stats = {k: v for k, v in self.album_play_stats.items() if v > 0}
            if existing_stats:
                top_album, top_count = max(existing_stats.items(), key=lambda item: item[1])
                most_played_text = f"{escape(top_album)} ({top_count} plays)"

        body = (
            f"[{fg}]Total albums:[/{fg}] [bold {accent}]{albums_count}[/bold {accent}]\n"
            f"[{fg}]Total listening time:[/{fg}] [bold {accent}]{escape(total_time_str)}[/bold {accent}]\n"
            f"[{fg}]Most played album:[/{fg}] [bold {accent}]{most_played_text}[/bold {accent}]"
        )
        self.push_screen(InfoModal("Albums Information", body))

    def action_show_playlist_info(self) -> None:
        """Wyświetla informacje o aktualnej playliście / albumie: tytuł, wykonawca, czas i liczba piosenek."""
        accent = COLORS.get("accent", "#F7DFB1")
        fg = COLORS.get("foreground", "#A8A890")

        target_name = self.active_playlist
        if not target_name:
            playlist_names = list(self.playlists.keys())
            idx = self.query_one("#playlist-list", ListView).index
            if idx is not None and 0 <= idx < len(playlist_names):
                target_name = playlist_names[idx]

        if not target_name or target_name not in self.playlists:
            self.push_screen(InfoModal("Playlist / Album Information", f"[{fg}]No album or playlist selected.[/{fg}]"))
            return

        tracks = self.playlists.get(target_name, [])
        song_count = len(tracks)
        total_seconds = sum(get_audio_duration(t["path"]) for t in tracks)
        total_time_str = format_duration_hms(total_seconds)

        artists = []
        for t in tracks:
            artist = (t.get("artist") or "").strip()
            if not artist and " - " in t.get("display", ""):
                artist = t["display"].split(" - ", 1)[0].strip()
            if artist and artist not in artists:
                artists.append(artist)

        if not artists:
            artists_str = "Unknown Artist"
        elif len(artists) <= 4:
            artists_str = ", ".join(artists)
        else:
            artists_str = ", ".join(artists[:4]) + f" (+{len(artists) - 4} more)"

        body = (
            f"[{fg}]Title:[/{fg}] [bold {accent}]{escape(target_name)}[/bold {accent}]\n"
            f"[{fg}]Artist(s):[/{fg}] [bold {accent}]{escape(artists_str)}[/bold {accent}]\n"
            f"[{fg}]Duration:[/{fg}] [bold {accent}]{escape(total_time_str)}[/bold {accent}]\n"
            f"[{fg}]Songs count:[/{fg}] [bold {accent}]{song_count}[/bold {accent}]"
        )
        self.push_screen(InfoModal("Playlist / Album Information", body))

    def action_back_to_playlists(self) -> None:
        self.query_one("#playlist-list").focus()

    def on_unmount(self) -> None:
        self.playback.stop()
        if self.cava_process:
            self.cava_process.terminate()
        self.cleanup_isolated_audio()

if __name__ == "__main__":
    Termusic().run()
