# 🦦 ferret

A small CLI that ferrets out the state of your GitHub repos and posts a daily report to a Discord channel.

For each repo it tracks:

- Local working tree status — uncommitted changes, branch
- Local vs. remote — commits ahead / behind upstream
- Open PRs (with titles for the top ones)
- Open issue count
- Latest CI run status

Designed to be **single-file, dependency-light, and personal-data-free in the repo** — all your tokens and paths live in a gitignored `.env` and `repos.yaml`.

---

## Why "ferret"?

A ferret is a small forest mammal. The verb *to ferret out* means to discover by careful searching — which is exactly what this tool does for your repos.

---

## Requirements

- Python 3.10+
- [`gh`](https://cli.github.com/) authenticated (`gh auth login`)
- `git`
- A Discord bot in the target server with permission to `Send Messages`

---

## Setup

```bash
git clone https://github.com/<you>/ferret.git
cd ferret

python3 -m pip install -r requirements.txt

cp .env.example .env           # then fill in DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID
cp repos.yaml.example repos.yaml   # then list the repos you want to watch
```

### Create a Discord bot

1. Go to https://discord.com/developers/applications → **New Application**.
2. **Bot** sidebar → reveal **Token** → paste it into `.env` as `DISCORD_BOT_TOKEN`.
3. **OAuth2 → URL Generator** → scopes: `bot` → permissions: `Send Messages`. Open the URL and add the bot to your server.
4. In Discord, enable **Developer Mode** (User Settings → Advanced), right-click the target channel → **Copy Channel ID** → paste into `.env` as `DISCORD_CHANNEL_ID`.

---

## Usage

### One-off (sends to Discord)

```bash
python3 ferret.py
```

### Dry run (prints to stdout, doesn't send)

```bash
FERRET_DRY_RUN=1 python3 ferret.py
```

---

## Schedule it

### macOS — launchd

`scripts/com.ferret.daily.plist.example` is a template that runs every day at 12:00 noon. Edit the paths, drop it in `~/Library/LaunchAgents/`, and:

```bash
launchctl load ~/Library/LaunchAgents/com.ferret.daily.plist
```

### Linux — cron

```cron
0 12 * * * cd /path/to/ferret && /usr/bin/python3 ferret.py >> ferret.log 2>&1
```

---

## Skip selected checks per repo

```yaml
repos:
  - name: noisy-monorepo
    path: /Users/you/code/noisy-monorepo
    skip: [issue, ci]   # only show local status + PRs
```

Supported skips: `local`, `pr`, `issue`, `ci`.

---

## License

MIT
