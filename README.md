## Features

### Music Playback
| Command | Aliases | Description |
|---------|---------|-------------|
| `!play <query>` | `!p` | Play a YouTube URL or search query |
| `!pause` | - | Pause playback |
| `!resume` | - | Resume playback |
| `!skip` | `!s` | Skip current track |
| `!stop` | - | Stop playback and clear queue |
| `!queue` | `!q` | Show current queue (up to 10 tracks) |
| `!np` | `!nowplaying` | Show currently playing track |
| `!clear` | - | Clear queue and stop playback |
| `!join` | - | Join your voice channel |
| `!leave` | - | Leave voice channel |

### Autoplay
| Command | Aliases | Description |
|---------|---------|-------------|
| `!autoplay <artist>` | `!ap` | Start 2-hour autoplay for an artist (no duplicates) |
| `!stopautoplay` | `!sap` | Stop autoplay mode |
| `!autoplaystatus` | `!aps` | Check autoplay time remaining and songs played |

### Radio Mode
Freeform radio that uses YouTube's recommendation engine. Describe the sound you want and let it flow.

| Command | Aliases | Description |
|---------|---------|-------------|
| `!radio <description>` | `!r` | Start radio with any description ("late night jazz", "90s hip hop") |
| `!tune <new direction>` | - | Change the radio's direction mid-session |
| `!dial up` / `!dial down` | - | Adjust energy level (chill ↔ hype) |
| `!static` | - | Skip current track and avoid similar ones |
| `!signal` | - | Show current radio tuning and stats |
| `!stopradio` | `!sr` | Stop radio mode |

### Station Presets
Save and load your favorite radio tunings.

| Command | Description |
|---------|-------------|
| `!station save <name>` | Save current radio tuning as a preset |
| `!station <name>` | Load and play a saved station |
| `!stations` | List all saved stations |
| `!station delete <name>` | Delete a saved station |

**Example session:**
```
!radio chill lo-fi beats
  → starts playing lo-fi

!dial up
  → shifts to more energetic lo-fi

!tune jazz piano cafe
  → changes direction to jazz

!station save latenight
  → saves this tuning for later

!station latenight
  → loads saved station
```

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Install FFmpeg (required for audio playback)

3. Create a `.env` file:
   ```
   DISCORD_TOKEN=your_bot_token_here
   ```

4. Run the bot:
   ```bash
   python bot.py
   ```
