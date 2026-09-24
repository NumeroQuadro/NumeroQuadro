# Local Automation

This repo refreshes Dmitriy's public vibecoding dashboard from local AI transcript metadata.

The generator publishes aggregate counts only:

- daily sessions
- prompts
- assistant messages
- tool calls
- recorded LLM request totals across sources
- source totals

It does not publish prompt text, local paths, command names, workspace names, or credentials.

An LLM request is a model invocation, which can differ from a user prompt. Codex uses rising token-count events as a call marker, Claude uses unique assistant message IDs, Kimi uses `llm.request` events, and Gemini CLI uses model messages. Cursor does not record every call directly, so its count uses nonempty assistant responses as an estimate. Antigravity task metadata contains no call count, so each task counts as one estimated request. The total is therefore a count of recorded or estimated requests from the available local stores, not a complete provider billing count.

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
