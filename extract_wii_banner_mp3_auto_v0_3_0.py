#!/usr/bin/env python3
"""
extract_wii_banner_mp3_auto_v0_3_0.py

Extract Wii Disc Channel/banner audio from Wii disc images or opening.bnr files,
then convert it to MP3.

Cross-platform automatic workflow:
  - Finds or locally downloads WIT for extracting opening.bnr from ISO/WBFS/etc.
  - Finds or locally downloads vgmstream-cli for decoding BNS audio.
  - Finds FFmpeg on PATH. On Windows it can locally download FFmpeg; on macOS it can offer Homebrew installation.
  - Extracts /meta/sound.bin from opening.bnr's U8 archive.
  - Strips the IMD5 wrapper when present.
  - Decompresses Wii banner LZ77/LZ10-wrapped audio when present.
  - Converts final audio to MP3.
  - Falls back through vgmstream guessed extensions for unusual/unknown banner audio.
  - If a RIFF/AIFF banner fails direct FFmpeg conversion, falls back to vgmstream decoding first.

Default output:
  Wii Banner Sounds/<ROM filename>_banner_sound.mp3

Use --keep-intermediate to also keep prefixed intermediate files in the same output folder.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tarfile
import urllib.request
import wave
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

VERSION = "0.3.0"

IMAGE_EXTS = {".iso", ".wbfs", ".wdf", ".ciso", ".wia", ".gcz"}
DEFAULT_OUTPUT_DIR = "Wii Banner Sounds"
DEFAULT_TOOLS_DIR = "_wii_banner_tools"

# Helper tool URLs. These are only used when the tool is not already found.
WIT_WIN_URL = "https://wit.wiimm.de/download/wit-v3.05a-r8638-cygwin64.zip"
WIT_MAC_URL = "https://wit.wiimm.de/download/wit-v3.05a-r8638-mac.tar.gz"
WIT_LINUX_X64_URL = "https://wit.wiimm.de/download/wit-v3.05a-r8638-x86_64.tar.gz"
WIT_LINUX_I386_URL = "https://wit.wiimm.de/download/wit-v3.05a-r8638-i386.tar.gz"

VGMSTREAM_WIN_URL = "https://github.com/vgmstream/vgmstream-releases/releases/download/nightly/vgmstream-win64.zip"
VGMSTREAM_MAC_URL = "https://github.com/vgmstream/vgmstream-releases/releases/download/nightly/vgmstream-mac-cli.tar.gz"
VGMSTREAM_LINUX_URL = "https://github.com/vgmstream/vgmstream-releases/releases/download/nightly/vgmstream-linux-cli.tar.gz"

FFMPEG_WIN_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


@dataclass
class ToolPaths:
    wit: Optional[Path]
    vgmstream: Optional[Path]
    ffmpeg: Optional[Path]


class BannerExtractError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


def is_windows() -> bool:
    return platform.system().lower() == "windows"


def is_macos() -> bool:
    return platform.system().lower() == "darwin"


def is_linux() -> bool:
    return platform.system().lower() == "linux"


def exe_name(base: str) -> str:
    return f"{base}.exe" if is_windows() else base


def sanitize_name(name: str, fallback: str = "Unknown Game") -> str:
    name = re.sub(r"[<>:\"/\\|?*]", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    # Leave room for nested output path on Windows.
    return name[:180]


def which_path(command: str) -> Optional[Path]:
    found = shutil.which(command)
    return Path(found) if found else None


def find_local_exe(tools_dir: Path, executable: str) -> Optional[Path]:
    if not tools_dir.exists():
        return None
    matches = list(tools_dir.rglob(executable))
    if not matches:
        return None
    # Prefer bin folders when available.
    matches.sort(key=lambda p: ("bin" not in [part.lower() for part in p.parts], len(str(p))))
    return matches[0]


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    log(f"Downloading: {url}")
    with urllib.request.urlopen(url) as response, tmp.open("wb") as out:
        total_header = response.headers.get("Content-Length")
        total = int(total_header) if total_header and total_header.isdigit() else None
        downloaded = 0
        next_report = 5 * 1024 * 1024
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)
            if downloaded >= next_report:
                if total:
                    pct = downloaded / total * 100
                    log(f"  downloaded {downloaded / 1024 / 1024:.1f} MB / {total / 1024 / 1024:.1f} MB ({pct:.0f}%)")
                else:
                    log(f"  downloaded {downloaded / 1024 / 1024:.1f} MB")
                next_report += 5 * 1024 * 1024

    if dest.exists():
        dest.unlink()
    tmp.rename(dest)


def extract_zip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    log(f"Extracting: {zip_path.name}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)


def _safe_extract_tar(tar: tarfile.TarFile, dest: Path) -> None:
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest_resolved)):
            raise RuntimeError(f"Unsafe path in tar archive: {member.name}")
    tar.extractall(dest)


def extract_tar_gz(tar_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    log(f"Extracting: {tar_path.name}")
    with tarfile.open(tar_path, "r:gz") as tf:
        _safe_extract_tar(tf, dest)


def chmod_executable(path: Path) -> None:
    if is_windows():
        return
    try:
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)
    except OSError:
        pass


def current_platform_tool_url(name: str) -> tuple[str, str]:
    """Return (url, archive_extension_label) for the current platform."""
    machine = platform.machine().lower()

    if name == "wit":
        if is_windows():
            return WIT_WIN_URL, ".zip"
        if is_macos():
            return WIT_MAC_URL, ".tar.gz"
        if is_linux():
            if machine in {"x86_64", "amd64"}:
                return WIT_LINUX_X64_URL, ".tar.gz"
            if machine in {"i386", "i686", "x86"}:
                return WIT_LINUX_I386_URL, ".tar.gz"
            raise FileNotFoundError(f"No automatic WIT download configured for Linux CPU: {platform.machine()}")

    if name == "vgmstream":
        if is_windows():
            return VGMSTREAM_WIN_URL, ".zip"
        if is_macos():
            return VGMSTREAM_MAC_URL, ".tar.gz"
        if is_linux():
            if machine not in {"x86_64", "amd64"}:
                raise FileNotFoundError(f"No automatic vgmstream download configured for Linux CPU: {platform.machine()}")
            return VGMSTREAM_LINUX_URL, ".tar.gz"

    if name == "ffmpeg":
        if is_windows():
            return FFMPEG_WIN_URL, ".zip"
        raise FileNotFoundError("FFmpeg local auto-download is only configured for Windows")

    raise FileNotFoundError(f"Unknown helper tool: {name}")


def extract_archive(archive_path: Path, dest: Path) -> None:
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        extract_zip(archive_path, dest)
    elif name.endswith(".tar.gz") or name.endswith(".tgz"):
        extract_tar_gz(archive_path, dest)
    else:
        raise RuntimeError(f"Unsupported archive type: {archive_path}")


def ensure_helper_tool(tools_dir: Path, name: str, executable: str, no_download: bool) -> Path:
    # Check PATH first.
    path_exe = which_path(executable)
    if path_exe:
        chmod_executable(path_exe)
        return path_exe

    local = find_local_exe(tools_dir, executable)
    if local:
        chmod_executable(local)
        return local

    if no_download:
        raise FileNotFoundError(f"{executable} not found and --no-download was used")

    url, archive_ext = current_platform_tool_url(name)
    downloads = tools_dir / "downloads"
    target_dir = tools_dir / name
    archive_path = downloads / f"{name}{archive_ext}"

    if not archive_path.exists():
        download_file(url, archive_path)
    else:
        log(f"Using cached download: {archive_path}")

    extract_archive(archive_path, target_dir)

    local = find_local_exe(tools_dir, executable)
    if not local:
        raise FileNotFoundError(f"Downloaded {name}, but could not find {executable}")

    chmod_executable(local)
    return local


def prompt_yes_no(message: str, default_no: bool = True) -> bool:
    suffix = "[y/N]" if default_no else "[Y/n]"
    try:
        answer = input(f"{message} {suffix} ").strip().lower()
    except EOFError:
        return not default_no
    if not answer:
        return not default_no
    return answer in {"y", "yes"}


def ensure_ffmpeg_tool(tools_dir: Path, no_download: bool, install_ffmpeg: str) -> Path:
    executable = exe_name("ffmpeg")

    path_exe = which_path(executable)
    if path_exe:
        return path_exe

    local = find_local_exe(tools_dir, executable)
    if local:
        chmod_executable(local)
        return local

    if no_download:
        raise FileNotFoundError(f"{executable} not found and --no-download was used")

    if is_windows():
        return ensure_helper_tool(tools_dir, "ffmpeg", executable, no_download)

    if is_macos():
        brew = which_path("brew")
        if not brew:
            raise FileNotFoundError(
                "ffmpeg was not found, and Homebrew was not found. Install Homebrew first, then run: brew install ffmpeg"
            )

        should_install = False
        if install_ffmpeg == "yes":
            should_install = True
        elif install_ffmpeg == "ask":
            should_install = prompt_yes_no("ffmpeg was not found. Install it now using Homebrew: brew install ffmpeg?", default_no=True)

        if should_install:
            result = run_command([brew, "install", "ffmpeg"])
            if result.returncode != 0:
                raise FileNotFoundError(f"Homebrew failed to install ffmpeg. Output was:\n{result.stdout.strip()}")
            path_exe = which_path(executable)
            if path_exe:
                return path_exe
            raise FileNotFoundError("Homebrew finished, but ffmpeg was still not found on PATH")

        raise FileNotFoundError("ffmpeg is required for MP3 output. On macOS, install it with: brew install ffmpeg")

    if is_linux():
        brew = which_path("brew")
        if brew and install_ffmpeg in {"yes", "ask"}:
            should_install = install_ffmpeg == "yes" or prompt_yes_no(
                "ffmpeg was not found. Install it now using Homebrew/Linuxbrew: brew install ffmpeg?",
                default_no=True,
            )
            if should_install:
                result = run_command([brew, "install", "ffmpeg"])
                if result.returncode != 0:
                    raise FileNotFoundError(f"Homebrew/Linuxbrew failed to install ffmpeg. Output was:\n{result.stdout.strip()}")
                path_exe = which_path(executable)
                if path_exe:
                    return path_exe

        raise FileNotFoundError(
            "ffmpeg is required for MP3 output. Install it with your package manager, for example: "
            "sudo apt install ffmpeg, sudo dnf install ffmpeg, or sudo pacman -S ffmpeg"
        )

    raise FileNotFoundError(f"ffmpeg not found. Unsupported auto-install platform: {platform.system()}")

def resolve_tools(
    tools_dir: Path,
    no_download: bool,
    install_ffmpeg: str,
    need_wit: bool = True,
    need_vgmstream: bool = True,
    need_ffmpeg: bool = True,
) -> ToolPaths:
    tools_dir.mkdir(parents=True, exist_ok=True)

    wit = None
    vgm = None
    ffmpeg = None

    if need_wit:
        wit = ensure_helper_tool(tools_dir, "wit", exe_name("wit"), no_download)
    if need_vgmstream:
        vgm = ensure_helper_tool(tools_dir, "vgmstream", exe_name("vgmstream-cli"), no_download)
    if need_ffmpeg:
        ffmpeg = ensure_ffmpeg_tool(tools_dir, no_download, install_ffmpeg)

    return ToolPaths(wit=wit, vgmstream=vgm, ffmpeg=ffmpeg)


def run_command(args: list[str | os.PathLike[str]], *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = [str(a) for a in args]
    if not quiet:
        log("Running: " + " ".join(f'\"{c}\"' if " " in c else c for c in cmd))
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def extract_opening_bnr_with_wit(wit: Path, image_path: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Proven WIT filter. Full virtual path filter may fail; bare +opening.bnr works.
    result = run_command([
        wit,
        "EXTRACT",
        image_path,
        out_dir,
        "--files",
        "+opening.bnr",
        "--flat",
        "--overwrite",
        "--force",
    ])

    bnr = out_dir / "opening.bnr"
    if not bnr.exists():
        raise BannerExtractError(
            f"WIT did not produce opening.bnr for {image_path.name}. Output was:\n{result.stdout.strip()}"
        )
    return bnr


def parse_u8_files(u8: bytes) -> list[tuple[str, int, int]]:
    if len(u8) < 0x20:
        raise BannerExtractError("U8 archive is too small")

    magic, root_off, header_size, data_off = struct.unpack_from(">IIII", u8, 0)
    if magic != 0x55AA382D:
        raise BannerExtractError("Bad U8 magic")

    nodes_start = root_off

    def read_node(index: int) -> tuple[int, int, int, int]:
        off = nodes_start + index * 12
        if off + 12 > len(u8):
            raise BannerExtractError("U8 node table exceeds file size")
        type_name, file_off, size = struct.unpack_from(">III", u8, off)
        node_type = type_name >> 24
        name_off = type_name & 0x00FFFFFF
        return node_type, name_off, file_off, size

    root_type, root_name, root_parent, node_count = read_node(0)
    if root_type != 1 or node_count <= 0:
        raise BannerExtractError("Invalid U8 root node")

    names_start = nodes_start + node_count * 12
    if names_start >= len(u8):
        raise BannerExtractError("U8 string table starts beyond file size")

    def read_name(name_off: int) -> str:
        start = names_start + name_off
        if start >= len(u8):
            raise BannerExtractError("U8 filename offset exceeds file size")
        try:
            end = u8.index(b"\x00", start)
        except ValueError as exc:
            raise BannerExtractError("Unterminated U8 filename") from exc
        return u8[start:end].decode("utf-8", errors="replace")

    files: list[tuple[str, int, int]] = []

    def walk(index: int, path: str) -> None:
        node_type, name_off, file_off, size = read_node(index)
        name = read_name(name_off) if index != 0 else ""
        current = f"{path}/{name}" if name else path

        if node_type == 1:
            child = index + 1
            while child < size:
                child_type, child_name_off, child_file_off, child_size = read_node(child)
                child_name = read_name(child_name_off)
                child_path = f"{current}/{child_name}" if current else f"/{child_name}"

                if child_type == 1:
                    walk(child, current)
                    child = child_size
                else:
                    if child_file_off + child_size > len(u8):
                        raise BannerExtractError(f"U8 file entry exceeds file size: {child_path}")
                    files.append((child_path, child_file_off, child_size))
                    child += 1

    walk(0, "")
    return files



def decompress_lz10(comp: bytes) -> bytes:
    """Decompress Nintendo LZ10 data.

    Wii banner sounds can appear as:
      IMD5 -> LZ77 -> LZ10-compressed WAV

    The LZ10 stream starts with 0x10 followed by a 24-bit little-endian
    uncompressed size.
    """
    if len(comp) < 4 or comp[0] != 0x10:
        raise BannerExtractError("Not Nintendo LZ10-compressed data")

    out_size = comp[1] | (comp[2] << 8) | (comp[3] << 16)
    src = 4
    out = bytearray()

    while len(out) < out_size:
        if src >= len(comp):
            raise BannerExtractError("Unexpected end of LZ10 stream while reading flags")

        flags = comp[src]
        src += 1

        for bit in range(7, -1, -1):
            if len(out) >= out_size:
                break

            if flags & (1 << bit):
                if src + 2 > len(comp):
                    raise BannerExtractError("Unexpected end of LZ10 stream while reading back-reference")

                b1 = comp[src]
                b2 = comp[src + 1]
                src += 2

                length = (b1 >> 4) + 3
                disp = ((b1 & 0x0F) << 8) | b2
                copy_src = len(out) - disp - 1

                if copy_src < 0:
                    raise BannerExtractError("Invalid LZ10 back-reference")

                for _ in range(length):
                    out.append(out[copy_src])
                    copy_src += 1
                    if len(out) >= out_size:
                        break
            else:
                if src >= len(comp):
                    raise BannerExtractError("Unexpected end of LZ10 stream while reading literal")
                out.append(comp[src])
                src += 1

    return bytes(out)


def maybe_decompress_banner_lz77_audio(audio: bytes) -> bytes:
    """Return decompressed audio when sound.bin contains a Wii LZ77/LZ10 wrapper.

    Red Steel 2 is a confirmed case:
      IMD5 -> b"LZ77" + LZ10 stream -> valid RIFF/WAVE
    """
    if audio.startswith(b"LZ77") and len(audio) > 8 and audio[4] == 0x10:
        return decompress_lz10(audio[4:])

    if audio.startswith(b"\x10") and len(audio) > 4:
        # Some tools/files may expose only the raw LZ10 stream without the
        # ASCII "LZ77" prefix.
        try:
            decompressed = decompress_lz10(audio)
        except BannerExtractError:
            return audio
        if decompressed.startswith((b"BNS ", b"RIFF", b"FORM")):
            return decompressed

    return audio


def extract_sound_from_bnr(bnr_path: Path, work_dir: Path, keep_intermediate: bool) -> Path:
    data = bnr_path.read_bytes()
    u8_magic = b"\x55\xAA\x38\x2D"
    u8_start = data.find(u8_magic)
    if u8_start < 0:
        raise BannerExtractError("No U8 archive magic found in opening.bnr")

    u8 = data[u8_start:]
    files = parse_u8_files(u8)

    sound_entry = None
    for path, offset, size in files:
        if path.lower().endswith("/sound.bin"):
            sound_entry = (path, offset, size)
            break

    if sound_entry is None:
        file_list = "\n".join(f"  {p}" for p, _, _ in files)
        raise BannerExtractError(f"No /meta/sound.bin found. Files were:\n{file_list}")

    _, offset, size = sound_entry
    sound_bin = u8[offset:offset + size]
    sound_bin_path = work_dir / "sound.bin"
    sound_bin_path.write_bytes(sound_bin)

    # sound.bin normally wraps the real audio in a 0x20-byte IMD5 header.
    if sound_bin.startswith(b"IMD5") and len(sound_bin) > 0x20:
        audio = sound_bin[0x20:]
    else:
        audio = sound_bin

    # Some banners wrap audio as IMD5 -> LZ77/LZ10 -> real WAV/BNS/AIFF.
    # Decompress before looking for RIFF/BNS magic. Red Steel 2 is one known
    # example; trimming straight to RIFF before decompression creates static.
    audio = maybe_decompress_banner_lz77_audio(audio)

    # Some banners have minor padding before the real audio magic. Trim only
    # after LZ77/LZ10 handling so compressed payloads are not misdetected.
    known_magics = (b"BNS ", b"RIFF", b"FORM")
    if not audio.startswith(known_magics):
        search_window = audio[:0x1000]
        candidates = [pos for sig in known_magics if (pos := search_window.find(sig)) > 0]
        if candidates:
            audio = audio[min(candidates):]

    magic = audio[:4]
    if magic == b"BNS ":
        audio_path = work_dir / "sound.bns"
    elif magic == b"RIFF":
        audio_path = work_dir / "sound.wav"
    elif magic in {b"FORM", b"AIFF"}:
        audio_path = work_dir / "sound.aiff"
    else:
        audio_path = work_dir / "sound.audio"

    audio_path.write_bytes(audio)

    if not keep_intermediate and sound_bin_path.exists():
        sound_bin_path.unlink(missing_ok=True)

    return audio_path


def decode_to_wav(vgmstream: Path, audio_path: Path, wav_path: Path) -> Path:
    result = run_command([vgmstream, "-o", wav_path, audio_path])
    if result.returncode != 0 or not wav_path.exists():
        # vgmstream sometimes writes default_name.ext.wav. Use it if it exists.
        default_wav = audio_path.with_name(audio_path.name + ".wav")
        if default_wav.exists():
            shutil.move(str(default_wav), str(wav_path))
        else:
            raise BannerExtractError(
                f"vgmstream failed to decode {audio_path.name}. Output was:\n{result.stdout.strip()}"
            )

    # Delete extra default output if vgmstream made both target and source.ext.wav.
    extra = audio_path.with_name(audio_path.name + ".wav")
    if extra.exists() and extra.resolve() != wav_path.resolve():
        extra.unlink(missing_ok=True)

    return wav_path


def decode_unknown_to_wav(vgmstream: Path, audio_path: Path, wav_path: Path) -> Path:
    """Try vgmstream on unusual Wii banner audio by testing likely extensions."""
    errors: list[str] = []

    trial_paths = [audio_path]
    for ext in [".bns", ".wav", ".aiff", ".brstm", ".dsp"]:
        trial = audio_path.with_name(f"sound_guess{ext}")
        if trial not in trial_paths:
            shutil.copy2(audio_path, trial)
            trial_paths.append(trial)

    for trial in trial_paths:
        try:
            return decode_to_wav(vgmstream, trial, wav_path)
        except BannerExtractError as exc:
            errors.append(f"{trial.name}: {exc}")

    head = audio_path.read_bytes()[:32]
    hex_head = " ".join(f"{b:02X}" for b in head)
    raise BannerExtractError(
        f"Could not decode unknown banner audio with vgmstream. First 32 bytes: {hex_head}\n"
        + "\n".join(errors[-3:])
    )



def guess_malformed_wav_params(data: bytes) -> tuple[int, int, int, int]:
    """Guess raw PCM parameters for malformed Wii banner WAV-like files.

    Some banners contain RIFF/WAVE-ish audio where the chunk header is padded or
    shifted, so strict RIFF parsers reject it. Red Steel 2 is one confirmed case.
    The sample data is still plain little-endian PCM after the data marker.
    Returns: (data_start, sample_rate, channels, bits_per_sample).
    """
    data_marker = data.find(b"data")
    if data_marker < 0:
        raise BannerExtractError("Malformed RIFF-like audio has no data marker")

    data_start = data_marker + 8
    if data_start >= len(data):
        raise BannerExtractError("Malformed RIFF-like audio has no sample data after data marker")

    # Look for a plausible PCM sample rate near the broken fmt header.
    common_rates = [8000, 11025, 16000, 22050, 24000, 32000, 33075, 44100, 48000]
    head = data[:96]
    rate_candidates: list[tuple[int, int]] = []
    for rate in common_rates:
        needle = struct.pack("<I", rate)
        pos = head.find(needle)
        if pos >= 0:
            rate_candidates.append((pos, rate))

    channels = 1
    bits_per_sample = 16
    sample_rate = 22050

    # Prefer a rate whose matching byte-rate appears shortly afterwards.
    for pos, rate in rate_candidates:
        for ch in (1, 2):
            byte_rate = rate * ch * (bits_per_sample // 8)
            byte_rate_pos = head.find(struct.pack("<I", byte_rate), pos + 4, min(len(head), pos + 24))
            if byte_rate_pos >= 0:
                sample_rate = rate
                channels = ch
                return data_start, sample_rate, channels, bits_per_sample

    if rate_candidates:
        sample_rate = rate_candidates[0][1]
        # Try to infer mono/stereo from a nearby small channel byte before the rate.
        pos = rate_candidates[0][0]
        nearby = data[max(0, pos - 6):pos]
        if b"\x02\x00" in nearby or b"\x02" in nearby:
            channels = 2
        else:
            channels = 1

    return data_start, sample_rate, channels, bits_per_sample


def repair_malformed_wav_like_audio(source_audio: Path, repaired_wav: Path) -> Path:
    """Rewrite malformed RIFF/WAV-like banner audio as a normal PCM WAV."""
    data = source_audio.read_bytes()
    if not data.startswith(b"RIFF") or b"WAVE" not in data[:32]:
        raise BannerExtractError("Not a RIFF/WAVE-like file")

    data_start, sample_rate, channels, bits_per_sample = guess_malformed_wav_params(data)
    pcm = data[data_start:]

    # Keep 16-bit PCM aligned. This avoids a trailing odd byte confusing WAV readers.
    bytes_per_sample_frame = channels * (bits_per_sample // 8)
    usable = len(pcm) - (len(pcm) % bytes_per_sample_frame)
    pcm = pcm[:usable]
    if not pcm:
        raise BannerExtractError("No aligned PCM data available after repairing WAV-like file")

    # Red Steel 2 exposes its banner sound as a RIFF/WAVE-like file, but the
    # sample payload is big-endian 16-bit PCM. Python's wave module writes
    # normal little-endian WAV, so byteswap the 16-bit sample pairs before
    # writing the repaired WAV. Without this, the output is technically valid
    # but sounds like static.
    if bits_per_sample == 16:
        pcm = b"".join(pcm[i + 1:i + 2] + pcm[i:i + 1] for i in range(0, len(pcm), 2))

    repaired_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(repaired_wav), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(bits_per_sample // 8)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)

    return repaired_wav

def convert_to_mp3(ffmpeg: Path, input_audio: Path, mp3_path: Path, quality: int) -> Path:
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    result = run_command([
        ffmpeg,
        "-y",
        "-i",
        input_audio,
        "-codec:a",
        "libmp3lame",
        "-q:a",
        str(quality),
        mp3_path,
    ])
    if result.returncode != 0 or not mp3_path.exists():
        # Remove partial/invalid output if FFmpeg created one.
        mp3_path.unlink(missing_ok=True)
        raise BannerExtractError(
            f"FFmpeg failed to create MP3 for {input_audio.name}. Output was:\n{result.stdout.strip()}"
        )
    return mp3_path


def convert_to_mp3_with_vgmstream_fallback(
    tools: ToolPaths,
    source_audio: Path,
    final_mp3: Path,
    quality: int,
    work_dir: Path,
    keep_intermediate: bool,
    safe_name: str,
    output_root: Path,
) -> Path:
    """Convert source audio to MP3, falling back to vgmstream when FFmpeg cannot read it.

    Some Wii banners identify as RIFF/WAV or AIFF, but the codec/layout is still
    Nintendo-specific enough that FFmpeg may reject it. In that case, vgmstream
    gets a second chance to decode it to a normal PCM WAV before FFmpeg makes MP3.
    """
    if not tools.ffmpeg:
        raise BannerExtractError("ffmpeg is required for MP3 output")

    try:
        return convert_to_mp3(tools.ffmpeg, source_audio, final_mp3, quality)
    except BannerExtractError as ffmpeg_exc:
        source_head = source_audio.read_bytes()[:64]

        # Some Wii banners contain WAV-like PCM with a malformed RIFF header.
        # FFmpeg and vgmstream both reject the header, but the PCM data can be
        # repaired into a normal WAV and then converted. Red Steel 2 is a
        # confirmed example.
        if source_head.startswith(b"RIFF") and b"WAVE" in source_head:
            repaired_wav = work_dir / "banner_repaired.wav"
            try:
                log(f"  Direct FFmpeg conversion failed for {source_audio.name}; trying WAV-header repair...")
                repair_malformed_wav_like_audio(source_audio, repaired_wav)
                if keep_intermediate:
                    shutil.copy2(repaired_wav, output_root / f"{safe_name}_banner_repaired.wav")
                return convert_to_mp3(tools.ffmpeg, repaired_wav, final_mp3, quality)
            except BannerExtractError as repair_exc:
                log(f"  WAV-header repair failed for {source_audio.name}; trying vgmstream fallback...")
                ffmpeg_exc = BannerExtractError(str(ffmpeg_exc) + "\n\nWAV repair error:\n" + str(repair_exc))

        if not tools.vgmstream:
            raise

        log(f"  Direct FFmpeg conversion failed for {source_audio.name}; trying vgmstream fallback...")
        wav_path = work_dir / "banner_tmp.wav"
        try:
            decode_unknown_to_wav(tools.vgmstream, source_audio, wav_path)
        except BannerExtractError as vgm_exc:
            head = source_audio.read_bytes()[:64]
            hex_head = " ".join(f"{b:02X}" for b in head)
            raise BannerExtractError(
                f"Direct FFmpeg conversion failed, and vgmstream fallback also failed for {source_audio.name}.\n"
                f"First 64 bytes: {hex_head}\n"
                f"FFmpeg error:\n{ffmpeg_exc}\n\nvgmstream error:\n{vgm_exc}"
            ) from vgm_exc

        if keep_intermediate:
            shutil.copy2(wav_path, output_root / f"{safe_name}_banner_tmp.wav")

        return convert_to_mp3(tools.ffmpeg, wav_path, final_mp3, quality)


def process_bnr_file(
    bnr_path: Path,
    game_name: str,
    output_root: Path,
    tools: ToolPaths,
    quality: int,
    keep_intermediate: bool,
    force: bool,
) -> Path:
    safe_name = sanitize_name(game_name)
    final_mp3 = output_root / f"{safe_name}_banner_sound.mp3"

    if final_mp3.exists() and not force:
        log(f"SKIP existing: {final_mp3}")
        return final_mp3

    output_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="wii_banner_") as tmp_name:
        tmp = Path(tmp_name)
        work_bnr = tmp / "opening.bnr"
        shutil.copy2(bnr_path, work_bnr)

        if keep_intermediate:
            shutil.copy2(work_bnr, output_root / f"{safe_name}_opening.bnr")

        audio_path = extract_sound_from_bnr(work_bnr, tmp, keep_intermediate=True)

        # Copy source audio to output only if requested. Prefix filenames so flat output does not collide.
        if keep_intermediate:
            shutil.copy2(tmp / "sound.bin", output_root / f"{safe_name}_sound.bin") if (tmp / "sound.bin").exists() else None
            shutil.copy2(audio_path, output_root / f"{safe_name}_{audio_path.name}")

        # BNS should always go through vgmstream first. RIFF/AIFF normally can
        # go straight through FFmpeg, but if FFmpeg rejects them we now fall
        # back to vgmstream, because some Wii banner WAV-like files use layouts
        # that FFmpeg does not understand.
        suffix = audio_path.suffix.lower()
        if suffix == ".bns":
            if not tools.vgmstream:
                raise BannerExtractError("vgmstream-cli is required for BNS banner audio")
            wav_path = tmp / "banner_tmp.wav"
            decode_to_wav(tools.vgmstream, audio_path, wav_path)
            source_for_mp3 = wav_path
            if keep_intermediate:
                shutil.copy2(wav_path, output_root / f"{safe_name}_banner_tmp.wav")
            convert_to_mp3_with_vgmstream_fallback(
                tools=tools,
                source_audio=source_for_mp3,
                final_mp3=final_mp3,
                quality=quality,
                work_dir=tmp,
                keep_intermediate=keep_intermediate,
                safe_name=safe_name,
                output_root=output_root,
            )
        else:
            convert_to_mp3_with_vgmstream_fallback(
                tools=tools,
                source_audio=audio_path,
                final_mp3=final_mp3,
                quality=quality,
                work_dir=tmp,
                keep_intermediate=keep_intermediate,
                safe_name=safe_name,
                output_root=output_root,
            )

    return final_mp3


def process_image_file(
    image_path: Path,
    output_root: Path,
    tools: ToolPaths,
    quality: int,
    keep_intermediate: bool,
    force: bool,
) -> Path:
    if not tools.wit:
        raise BannerExtractError("WIT is required for Wii image extraction")

    safe_name = sanitize_name(image_path.stem)
    final_mp3 = output_root / f"{safe_name}_banner_sound.mp3"
    if final_mp3.exists() and not force:
        log(f"SKIP existing: {final_mp3}")
        return final_mp3

    with tempfile.TemporaryDirectory(prefix="wii_bnr_extract_") as tmp_name:
        tmp = Path(tmp_name)
        bnr_path = extract_opening_bnr_with_wit(tools.wit, image_path, tmp)
        return process_bnr_file(
            bnr_path=bnr_path,
            game_name=safe_name,
            output_root=output_root,
            tools=tools,
            quality=quality,
            keep_intermediate=keep_intermediate,
            force=force,
        )


def should_exclude(path: Path, root: Path, output_root: Path, tools_dir: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    parts = {p.lower() for p in rel.parts}
    excluded_names = {
        output_root.name.lower(),
        tools_dir.name.lower(),
        "_wii_banner_test_out",
        "__pycache__",
    }
    return bool(parts & excluded_names)


def discover_inputs(input_path: Path, recursive: bool, output_root: Path, tools_dir: Path) -> list[Path]:
    input_path = input_path.resolve()

    if input_path.is_file():
        return [input_path]

    if not input_path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    pattern_iter: Iterable[Path]
    if recursive:
        pattern_iter = input_path.rglob("*")
    else:
        pattern_iter = input_path.glob("*")

    found: list[Path] = []
    for p in pattern_iter:
        if not p.is_file():
            continue
        if should_exclude(p, input_path, output_root.resolve(), tools_dir.resolve()):
            continue
        name_lower = p.name.lower()
        if name_lower == "opening.bnr" or p.suffix.lower() in IMAGE_EXTS:
            found.append(p)

    found.sort(key=lambda p: str(p).lower())
    return found


def print_platform_info(tools_dir: Path, no_download: bool) -> int:
    log(f"Wii Banner MP3 Extractor v{VERSION}")
    log(f"Platform: {platform.platform()}")
    log(f"Python: {sys.version.split()[0]}")
    log(f"Tools dir: {tools_dir.resolve()}")
    log("")

    for label, executable in [
        ("WIT", exe_name("wit")),
        ("vgmstream", exe_name("vgmstream-cli")),
        ("FFmpeg", exe_name("ffmpeg")),
    ]:
        found = which_path(executable) or find_local_exe(tools_dir, executable)
        log(f"{label}: {found if found else 'not found'}")

    if no_download:
        log("\nAuto-download: disabled")
    elif is_windows():
        log("\nAuto-download: available for WIT, vgmstream, and FFmpeg on Windows")
    elif is_macos():
        log("\nAuto-download: available for WIT and vgmstream on macOS; FFmpeg uses PATH or an optional Homebrew prompt")
    elif is_linux():
        log("\nAuto-download: available for WIT and vgmstream on Linux x86_64; FFmpeg uses PATH or Linuxbrew if available")
    else:
        log(f"\nAuto-download: limited/unsupported on {platform.system()}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract Wii opening.bnr banner audio and convert it to MP3.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", nargs="?", default=".", help="Input Wii image, opening.bnr, or folder to scan")
    parser.add_argument("-r", "--recursive", action="store_true", help="Scan folders recursively")
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT_DIR, help="Output folder")
    parser.add_argument("--tools-dir", default=DEFAULT_TOOLS_DIR, help="Local helper tools folder")
    parser.add_argument("--keep-intermediate", action="store_true", help="Keep opening.bnr, sound.bin, source audio, and temp WAV")
    parser.add_argument("--force", action="store_true", help="Overwrite existing MP3 outputs")
    parser.add_argument("--no-download", action="store_true", help="Do not automatically download missing tools")
    parser.add_argument("--install-ffmpeg", choices=["ask", "yes", "no"], default="ask", help="When FFmpeg is missing on macOS/Linux with Homebrew available: ask, install automatically, or do not install")
    parser.add_argument("--mp3-quality", type=int, default=2, choices=range(0, 10), metavar="0-9", help="LAME VBR quality: 0 highest, 9 lowest")
    parser.add_argument("--platform-info", action="store_true", help="Show detected tools and exit")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N inputs; useful for testing")

    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_root = Path(args.output)
    tools_dir = Path(args.tools_dir)

    if args.platform_info:
        return print_platform_info(tools_dir, args.no_download)

    output_root.mkdir(parents=True, exist_ok=True)

    inputs = discover_inputs(input_path, args.recursive, output_root, tools_dir)
    if args.limit and args.limit > 0:
        inputs = inputs[: args.limit]

    if not inputs:
        log("No Wii images or opening.bnr files found.")
        log(f"Supported image extensions: {', '.join(sorted(IMAGE_EXTS))}")
        return 1

    need_wit = any(p.name.lower() != "opening.bnr" for p in inputs)
    # Need vgmstream for common BNS case. We resolve it upfront for simpler automation.
    tools = resolve_tools(tools_dir, args.no_download, args.install_ffmpeg, need_wit=need_wit, need_vgmstream=True, need_ffmpeg=True)

    log("")
    log(f"Found {len(inputs)} input(s).")
    log(f"Output: {output_root.resolve()}")
    log("")

    ok = 0
    fail = 0

    for idx, src in enumerate(inputs, start=1):
        log(f"[{idx}/{len(inputs)}] {src.name}")
        try:
            if src.name.lower() == "opening.bnr":
                # If opening.bnr is inside a folder, use the parent folder as game name.
                game_name = src.parent.name if src.parent.name else src.stem
                mp3 = process_bnr_file(
                    bnr_path=src,
                    game_name=game_name,
                    output_root=output_root,
                    tools=tools,
                    quality=args.mp3_quality,
                    keep_intermediate=args.keep_intermediate,
                    force=args.force,
                )
            else:
                mp3 = process_image_file(
                    image_path=src,
                    output_root=output_root,
                    tools=tools,
                    quality=args.mp3_quality,
                    keep_intermediate=args.keep_intermediate,
                    force=args.force,
                )
            ok += 1
            log(f"  OK: {mp3}")
        except Exception as exc:
            fail += 1
            log(f"  FAIL: {exc}")
        log("")

    log(f"Complete. OK={ok} FAIL={fail}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
