# Wii Banner Sound MP3 Extractor

A small Python tool for extracting the Wii menu/banner sound from Wii game images and saving it as an MP3.

When you hover over a Wii game in the Wii System Menu, the game can play a short banner animation and sound. This tool extracts that sound from the game's `opening.bnr` file and converts it to:

```text
ROM Name_banner_sound.mp3
```

Example output:

```text
Wii Banner Sounds/
  Super Mario Galaxy (Europe, Australia) (En,Fr,De,Es,It)_banner_sound.mp3
  Mario Kart Wii (Europe, Australia) (En,Fr,De,Es,It)_banner_sound.mp3
```

The tool has been tested on Windows and macOS with a small Wii ISO set. It may not work with every Wii game or every system setup. If a game fails, keep the log and report the issue.

---

## Where to put the script

Copy the script into the same folder as your Wii games.

Example:

```text
Nintendo - Nintendo Wii/
  extract_wii_banner_mp3_auto_v0_3_0.py
  Big Beach Sports (Europe) (En,Fr,De,Es,It,Nl).iso
  Mario Kart Wii (Europe, Australia) (En,Fr,De,Es,It).iso
  Super Mario Galaxy (Europe, Australia) (En,Fr,De,Es,It).iso
```

The script will create an output folder beside itself:

```text
Wii Banner Sounds/
```

By default, all MP3 files are saved directly into that one folder. It does not create one subfolder per game.

---

## Supported input files

The tool is intended for Wii disc images and standalone Wii banner files.

Supported input extensions:

```text
.iso
.wbfs
.wdf
.ciso
opening.bnr
.bnr
```

For disc images, the script extracts `opening.bnr` using WIT. For standalone `.bnr` files, it reads the banner file directly.

---

## Requirements

You need:

- Python 3
- WIT / Wiimms ISO Tools
- vgmstream-cli
- FFmpeg

The script tries to handle these automatically where possible:

| Tool | Windows | macOS | Linux |
|---|---|---|---|
| WIT | Auto-downloads locally | Auto-downloads locally | Auto-downloads locally |
| vgmstream-cli | Auto-downloads locally | Auto-downloads locally | Auto-downloads locally |
| FFmpeg | Auto-downloads locally if missing | Uses existing FFmpeg, or offers Homebrew install if missing | Uses existing FFmpeg; manual install may be needed |

Downloaded helper tools are placed in:

```text
_wii_banner_tools/
```

They are not installed system-wide by the script, except for the optional macOS Homebrew FFmpeg prompt.

---

## If automatic download/install does not work

### Windows

Install Python first if needed:

```powershell
winget install Python.Python.3
```

Install FFmpeg manually if automatic download fails:

```powershell
winget install Gyan.FFmpeg
```

WIT and vgmstream are normally downloaded locally by the script. If that fails, download them manually and place the extracted tools somewhere in `_wii_banner_tools/`, or add their folders to your `PATH`.

### macOS

Install Python and FFmpeg with Homebrew:

```bash
brew install python ffmpeg
```

If Python fails with a certificate error when downloading tools, run:

```bash
/Applications/Python\ 3.8/Install\ Certificates.command
```

The exact Python version folder may be different on your Mac. For example:

```bash
/Applications/Python\ 3.12/Install\ Certificates.command
```

WIT and vgmstream are normally downloaded locally by the script. If that fails, install or download them manually and make sure `wit` and `vgmstream-cli` are available on your `PATH`, or placed under `_wii_banner_tools/`.

### Linux

Install Python and FFmpeg using your distro package manager.

Debian / Ubuntu:

```bash
sudo apt update
sudo apt install python3 ffmpeg
```

Fedora:

```bash
sudo dnf install python3 ffmpeg
```

Arch Linux / Manjaro:

```bash
sudo pacman -S python ffmpeg
```

WIT and vgmstream should auto-download locally on Linux. If that fails, download/install them manually and make sure `wit` and `vgmstream-cli` are available on your `PATH`, or placed under `_wii_banner_tools/`.

---

## How to run

### Windows PowerShell

Open PowerShell in the Wii ROM folder.

Check tools/platform info:

```powershell
python .\extract_wii_banner_mp3_auto_v0_3_0.py --platform-info
```

Test one game:

```powershell
python .\extract_wii_banner_mp3_auto_v0_3_0.py --limit 1 --force
```

Process games in the current folder:

```powershell
python .\extract_wii_banner_mp3_auto_v0_3_0.py --force
```

Process games in the current folder and subfolders:

```powershell
python .\extract_wii_banner_mp3_auto_v0_3_0.py -r --force
```

Save a batch log:

```powershell
python .\extract_wii_banner_mp3_auto_v0_3_0.py --force 2>&1 | Tee-Object -FilePath ".\wii_banner_batch_test.log"
```

### macOS Terminal

Open Terminal in the Wii ROM folder.

Check tools/platform info:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --platform-info
```

Test one game:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --limit 1 --force
```

Process games in the current folder:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --force
```

Process games in the current folder and subfolders:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py -r --force
```

Save a batch log:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --force 2>&1 | tee ./wii_banner_batch_test.log
```

If FFmpeg is missing and Homebrew is installed, the script may ask whether to install FFmpeg. To approve automatically:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --force --install-ffmpeg yes
```

### Linux Terminal

Open a terminal in the Wii ROM folder.

Check tools/platform info:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --platform-info
```

Test one game:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --limit 1 --force
```

Process games in the current folder:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --force
```

Process games in the current folder and subfolders:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py -r --force
```

Save a batch log:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --force 2>&1 | tee ./wii_banner_batch_test.log
```

---

## Useful options

Show platform/tool information:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --platform-info
```

Only process one game:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --limit 1
```

Overwrite existing MP3s:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --force
```

Scan subfolders:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py -r
```

Keep intermediate files for debugging:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --keep-intermediate
```

Disable automatic downloads:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --no-download
```

Choose a custom output folder:

```bash
python3 ./extract_wii_banner_mp3_auto_v0_3_0.py --output "My Wii Banner Sounds"
```

---

## Output format

Output files are named after the ROM file:

```text
<ROM filename without extension>_banner_sound.mp3
```

Example:

```text
Mario Kart Wii (Europe, Australia) (En,Fr,De,Es,It)_banner_sound.mp3
```

---

## Notes and limitations

- This tool extracts the Wii banner sound, not the full game soundtrack.
- Some Wii games may use unusual banner audio formats.
- If a game fails, rerun with `--keep-intermediate` and save the log.
- Red Steel 2-style compressed banner WAV data is supported in the current version.
- The script was tested on Windows and macOS with a small Wii ISO set, but it has not been exhaustively tested with every Wii title or every Linux distribution.

---

## Licensing

This repository/package contains the Python script only, unless you choose to include additional files yourself.

Suggested license for this script:

```text
MIT License
```

You may include a separate `LICENSE` file with the MIT License text for the Python script.

External tools are not authored by this project and keep their own licences:

- Python is licensed under the Python Software Foundation License.
- WIT / Wiimms ISO Tools is GPL-2.0-or-later.
- vgmstream has its own open-source licence terms from the vgmstream project.
- FFmpeg is primarily LGPL-2.1-or-later, but GPL terms may apply depending on how a particular FFmpeg build is configured.

The script may download or call these external tools, but they are separate projects. If you redistribute any third-party binaries with your release package, include their licences and comply with their redistribution requirements.

For the cleanest release package, distribute only:

```text
extract_wii_banner_mp3_auto_v0_3_0.py
README.md
LICENSE
```

Do not bundle WIT, vgmstream, or FFmpeg unless you also include and follow their licence terms.

---

## Disclaimer

Use this only with games you own and have legally dumped. This tool is intended for personal preservation and library organisation workflows.
