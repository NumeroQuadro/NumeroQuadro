# Local Automation

This repo refreshes Dmitriy's public vibecoding dashboard from local AI transcript metadata.

The generator publishes aggregate counts only:

- daily sessions
- prompts
- assistant messages
- tool calls
- token totals
- source totals

It does not publish prompt text, local paths, command names, workspace names, or credentials.

## Manual Refresh

```bash
scripts/update_vibecoding_dashboard.sh
```

The script regenerates the dashboard, runs a privacy check, commits changed public artifacts, and pushes `main`.

## Install Daily Job

```bash
scripts/install_daily_update.sh
```

This installs a user `launchd` job:

- label: `com.numeroquadro.vibecoding-dashboard`
- schedule: daily at `04:10` local time
- log: `~/Library/Logs/NumeroQuadro/vibecoding-dashboard.log`

## Run Scheduled Job Now

```bash
launchctl kickstart -k "gui/$(id -u)/com.numeroquadro.vibecoding-dashboard"
```

## Check Job

```bash
launchctl print "gui/$(id -u)/com.numeroquadro.vibecoding-dashboard"
tail -n 80 ~/Library/Logs/NumeroQuadro/vibecoding-dashboard.log
```

## Uninstall

```bash
scripts/uninstall_daily_update.sh
```
