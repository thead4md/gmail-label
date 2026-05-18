# launchd Daemons — Mac Mini

These plists keep MailMind running permanently on the Mac Mini as background services.
They auto-start on login and restart automatically if they crash.

## Services

| Plist | What it does | Log |
|---|---|---|
| `com.mailmind.litestream.plist` | Replicates `~/.mailmind/mailmind.db` → Backblaze B2 every 10s | `/tmp/mailmind-litestream.log` |
| `com.mailmind.ingestion.plist` | Runs `mailmind run --watch` to fetch & process emails continuously | `/tmp/mailmind-ingestion.log` |

## Setup

### 1. Fill in credentials

Edit `com.mailmind.litestream.plist` and replace the three placeholder values:
- `REPLACE_WITH_B2_KEY_ID` → your Backblaze B2 keyID
- `REPLACE_WITH_B2_APPLICATION_KEY` → your B2 applicationKey
- `REPLACE_WITH_B2_ENDPOINT` → e.g. `s3.us-west-004.backblazeb2.com`

### 2. Copy plists to LaunchAgents

```bash
cp launchd/com.mailmind.litestream.plist ~/Library/LaunchAgents/
cp launchd/com.mailmind.ingestion.plist ~/Library/LaunchAgents/
```

### 3. Load both services

```bash
launchctl load ~/Library/LaunchAgents/com.mailmind.litestream.plist
launchctl load ~/Library/LaunchAgents/com.mailmind.ingestion.plist
```

### 4. Verify they are running

```bash
launchctl list | grep mailmind
# Should show both with PID (non-zero = running)
```

## Useful commands

```bash
# Tail live logs
tail -f /tmp/mailmind-litestream.log
tail -f /tmp/mailmind-ingestion.log

# Stop a service
launchctl unload ~/Library/LaunchAgents/com.mailmind.litestream.plist

# Restart a service
launchctl unload ~/Library/LaunchAgents/com.mailmind.litestream.plist
launchctl load   ~/Library/LaunchAgents/com.mailmind.litestream.plist

# Check last exit code (0 = running, non-zero = crashed)
launchctl list com.mailmind.litestream
launchctl list com.mailmind.ingestion
```
