# Clipper (NeonClipping)

> **Turn long videos and streams into viral, captioned 9:16 vertical clips with one click, then post directly to YouTube Shorts, TikTok, Facebook Pages/Reels, and Instagram.**

Clipper processes long YouTube, Twitch, Kick replays, or your local MP4 files entirely on your computer with zero subscription fees, automatic AI highlight detection, animated subtitles, smart gaming layouts, and direct multi-platform social media publishing.

---

## ⚡ Key Features

- **⚡ Fast Moment Finder:** Downloads audio first to analyze speech, shouting, loudness jumps, and excitement—extracting high-impact moments without downloading multi-gigabyte video files.
- **📱 Direct Social Media Posting:** Post your generated clips directly to:
  - **YouTube Shorts** (via YouTube Data API v3 resumable upload)
  - **TikTok** (via TikTok Content Posting API)
  - **Facebook Pages & Reels** (via Meta Graph API)
  - **Instagram Reels** (via Instagram Graph API)
  - Supports both **1-Click OAuth login** and **Direct Access / Page Token input** for immediate testing without app review hurdles.
- **🎨 11 Native Video Layouts:** Facecam + Gameplay stacking, Blurred Fill, Split-screen, Picture-in-Picture, Circular Facecam, and Center Crops.
- **💬 Auto Captions & Animations:** 5 caption styles (GTA Neon, Impact, Clean, Hormozi, MrBeast), Hinglish transliteration, and support for 52 spoken languages.
- **🔒 100% Private & Local:** All video rendering, transcription, and layout composition happen locally on your computer.

---

## 🚀 Quick Start & Installation

### Prerequisites

1. **Python 3.10 to 3.14** installed on your system.
2. **FFmpeg** installed and accessible in your system `PATH`.
   - *Windows (via winget):*
     ```powershell
     winget install Gyan.FFmpeg
     ```
   - *macOS (via brew):*
     ```bash
     brew install ffmpeg
     ```
   - *Ubuntu / Debian:*
     ```bash
     sudo apt update && sudo apt install ffmpeg
     ```
3. *(Optional)* **Ollama** with `llama3.2:3b` for AI-generated titles, hooks, and sentiment scoring:
   ```bash
   ollama pull llama3.2:3b
   ```

---

### Running on Windows

1. Clone or download the repository:
   ```powershell
   git clone https://github.com/RamKoirala1123/clipper.git
   cd clipper
   ```

2. Run the launcher script:
   ```powershell
   .\run.ps1
   ```
   *Or install dependencies manually and start:*
   ```powershell
   pip install -r requirements.txt
   python app.py
   ```

3. Open your browser at:
   ```
   http://127.0.0.1:8770
   ```

---

### Running on Linux / macOS

1. Clone the repository:
   ```bash
   git clone https://github.com/RamKoirala1123/clipper.git
   cd clipper
   ```

2. Run the setup script:
   ```bash
   chmod +x run.sh
   ./run.sh
   ```
   *Or manually:*
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python3 app.py
   ```

3. Open your browser at `http://127.0.0.1:8770`.

---

## 📤 Social Media Direct Posting Guide

You can publish any generated or saved clip directly to your social channels.

### 1. Connecting Your Accounts

Navigate to **🌐 Social accounts** in the sidebar. You can connect your accounts in two ways:

#### Option A: Direct Token Input (Fastest & Recommended for Testing)
Click **+ Add with Token / Key**:
- **Facebook:** Paste your **Facebook Page Access Token** and **Page ID**. (Generated instantly via [Meta Graph API Explorer](https://developers.facebook.com/tools/explorer/)).
- **YouTube:** Paste your Google OAuth Access Token.
- **TikTok:** Paste your TikTok developer token.
- **Instagram:** Paste your Instagram Graph API token.

#### Option B: 1-Click Browser OAuth
1. Expand the **⚙️ App API Keys (OAuth Client Setup)** section.
2. Enter your App Client ID & Secret from:
   - **Google Cloud Console:** Enable YouTube Data API v3. Add redirect URI `http://127.0.0.1:8770/oauth/youtube/callback`.
   - **TikTok for Developers:** Add redirect URI `http://127.0.0.1:8770/oauth/tiktok/callback`.
   - **Meta for Developers:** Add redirect URI `http://127.0.0.1:8770/oauth/facebook/callback`.
3. Click **Connect** on any platform card to sign in with your account.

---

### 2. Posting Generated Clips

1. In **⚡ Make clips**, click **Make clips** or export an individual clip from **Export clip**.
2. On any clip card, click **📤 Post to Social** (or **📤 Post** in *My clips*).
3. The post dialog will open with a live video preview:
   - Review or edit the auto-generated **Title**, **Description**, and **Hashtags**.
   - Select your desired **Privacy** (Public, Unlisted, Private).
   - Check the boxes for the accounts you want to post to (YouTube, TikTok, Facebook, Instagram).
4. Click **⚡ Post Now** to upload immediately, or **Add to Queue** to schedule.
5. The post status updates live with a clickable link to view your published video once ready!

---

## 🛠️ Project Structure

| File / Folder | Description |
|---|---|
| `app.py` | FastAPI application server and route orchestrator |
| `social_accounts.py` | Social media OAuth, token management, and platform upload handlers |
| `posting.py` | Post queue dispatcher, retry mechanisms, and background scheduler |
| `smart.py` | Audio excitement, laughter, and transcript AI moment scoring |
| `highlights.py` | Audio and optical-flow visual scene cut detection |
| `captions.py` | Speech transcription and subtitle burning (SRT, ASS, VTT) |
| `layouts.py` | Multi-crop geometry engines (Facecam + Gameplay, Blur Fill, etc.) |
| `index.html` | Clean web dashboard and UI layout |
| `app.js` | Interactive frontend logic, clip editors, and social dialogs |
| `style.css` | Neon dark UI design system |

---

## 📄 License

Open-source and free under the MIT License.
