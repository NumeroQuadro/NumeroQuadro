#!/usr/bin/env python3
"""Generate the public vibecoding dashboard from local AI transcript metadata.

The published artifacts intentionally contain aggregate counts only. This
script reads local transcript files, but it never writes prompt text, local
paths, command names, or credentials to the generated HTML/SVG/README files.
"""

from __future__ import annotations

import calendar
import csv
import html
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
TZ = ZoneInfo("Europe/Moscow")
RANGE_START = date(2025, 6, 1)

HTML_PATH = ROOT / "vibecoding-heatmap-interactive.html"
SVG_PATH = ROOT / "vibecoding-heatmap-2026.svg"
README_PATH = ROOT / "README.md"

PUBLIC_SOURCE_ORDER = [
    "Codex",
    "Codex via custom setup",
    "Claude",
    "Claude via custom setup",
    "Gemini CLI",
]


@dataclass
class SessionStats:
    public_source: str
    started_at: datetime
    prompts: int = 0
    assistant: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    tokens: int = 0
    web: int = 0

    @property
    def day(self) -> date:
        return self.started_at.astimezone(TZ).date()


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).astimezone(TZ)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def glob_existing(patterns: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in HOME.glob(pattern):
            if path.is_file() and path not in seen:
                paths.append(path)
                seen.add(path)
    return sorted(paths)


def usage_total(usage: dict[str, Any] | None) -> int:
    if not isinstance(usage, dict):
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, (int, float)):
        return int(total)
    keys = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    return int(sum(v for key in keys if isinstance((v := usage.get(key)), (int, float))))


def is_web_tool(name: str | None) -> bool:
    if not name:
        return False
    needle = name.lower()
    return any(part in needle for part in ("web", "fetch", "browser", "google_search"))


def codex_tool_name(obj: dict[str, Any]) -> str | None:
    payload = obj.get("payload")
    candidates = [obj, payload] if isinstance(payload, dict) else [obj]
    for candidate in candidates:
        for key in ("name", "tool_name", "recipient_name"):
            value = candidate.get(key)
            if isinstance(value, str):
                return value
        call = candidate.get("call")
        if isinstance(call, dict) and isinstance(call.get("name"), str):
            return call["name"]
    return None


def parse_codex_session(path: Path, source: str) -> SessionStats | None:
    started_at: datetime | None = None
    prompts = assistant = tool_calls = tool_results = web = 0
    token_total = 0

    for obj in iter_jsonl(path):
        ts = parse_dt(obj.get("timestamp"))
        if started_at is None and ts:
            started_at = ts

        obj_type = obj.get("type")
        payload = obj.get("payload")
        payload_type = payload.get("type") if isinstance(payload, dict) else None

        if obj_type == "session_meta" and isinstance(payload, dict):
            started_at = parse_dt(payload.get("timestamp")) or started_at

        role = obj.get("role")
        if obj_type == "message" and role == "user":
            prompts += 1
        elif obj_type == "message" and role == "assistant":
            assistant += 1
        elif obj_type == "response_item" and isinstance(payload, dict):
            if payload_type == "message" and payload.get("role") == "user":
                prompts += 1
            elif payload_type == "message" and payload.get("role") == "assistant":
                assistant += 1
            elif payload_type == "function_call":
                tool_calls += 1
                web += int(is_web_tool(codex_tool_name(payload)))
            elif payload_type == "function_call_output":
                tool_results += 1
        elif obj_type == "function_call":
            tool_calls += 1
            web += int(is_web_tool(codex_tool_name(obj)))
        elif obj_type == "function_call_output":
            tool_results += 1
        elif obj_type == "event_msg" and isinstance(payload, dict):
            if payload_type == "token_count":
                info = payload.get("info")
                if isinstance(info, dict):
                    total_usage = info.get("total_token_usage")
                    token_total = max(token_total, usage_total(total_usage))
            elif payload_type == "task_started" and started_at is None:
                started_at = parse_dt(payload.get("started_at"))

    if started_at is None:
        started_at = parse_codex_filename_date(path)
    if started_at is None:
        return None

    return SessionStats(
        public_source=source,
        started_at=started_at,
        prompts=prompts,
        assistant=assistant,
        tool_calls=tool_calls,
        tool_results=tool_results,
        tokens=token_total,
        web=web,
    )


def parse_codex_filename_date(path: Path) -> datetime | None:
    match = re.search(r"rollout-(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})", path.name)
    if not match:
        return None
    day, hour, minute, second = match.groups()
    try:
        return datetime.fromisoformat(f"{day}T{hour}:{minute}:{second}").replace(tzinfo=TZ)
    except ValueError:
        return None


def claude_content_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


def parse_claude_session(path: Path, source: str) -> SessionStats | None:
    started_at: datetime | None = None
    prompts = assistant = tool_calls = tool_results = tokens = web = 0

    for obj in iter_jsonl(path):
        ts = parse_dt(obj.get("timestamp"))
        if started_at is None and ts:
            started_at = ts

        obj_type = obj.get("type")
        message = obj.get("message")
        if not isinstance(message, dict):
            continue

        role = message.get("role")
        if obj_type == "user" or role == "user":
            prompts += 1
            for item in claude_content_items(message):
                if item.get("type") == "tool_result":
                    tool_results += 1
        elif obj_type == "assistant" or role == "assistant":
            assistant += 1
            tokens += usage_total(message.get("usage"))
            for item in claude_content_items(message):
                if item.get("type") == "tool_use":
                    tool_calls += 1
                    web += int(is_web_tool(item.get("name")))

    if started_at is None:
        return None
    return SessionStats(
        public_source=source,
        started_at=started_at,
        prompts=prompts,
        assistant=assistant,
        tool_calls=tool_calls,
        tool_results=tool_results,
        tokens=tokens,
        web=web,
    )


def walk_values(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


def parse_gemini_session(path: Path) -> SessionStats | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None

    timestamps: list[datetime] = []
    prompts = assistant = tool_calls = tool_results = tokens = web = 0
    models: Counter[str] = Counter()

    for value in walk_values(data):
        if not isinstance(value, dict):
            continue
        for key in ("timestamp", "createdAt", "created_at", "startTime", "time"):
            parsed = parse_dt(value.get(key))
            if parsed:
                timestamps.append(parsed)

        role = value.get("role") or value.get("author")
        if role == "user":
            prompts += 1
        elif role in {"model", "assistant"}:
            assistant += 1

        name = value.get("name") or value.get("toolName") or value.get("tool_name")
        if isinstance(name, str) and (
            value.get("type") in {"function_call", "tool_call"}
            or "tool" in str(value.get("type", "")).lower()
            or "function" in str(value.get("type", "")).lower()
        ):
            tool_calls += 1
            web += int(is_web_tool(name))

        if value.get("type") in {"function_response", "tool_result"}:
            tool_results += 1

        usage = value.get("usageMetadata") or value.get("usage") or value.get("tokenUsage")
        if isinstance(usage, dict):
            tokens += usage_total(
                {
                    "input_tokens": usage.get("promptTokenCount") or usage.get("input_tokens"),
                    "output_tokens": usage.get("candidatesTokenCount") or usage.get("output_tokens"),
                    "total_tokens": usage.get("totalTokenCount") or usage.get("total_tokens"),
                }
            )

        model = value.get("model")
        if isinstance(model, str):
            models[model] += 1

    if not timestamps:
        match = re.search(r"session-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2})", path.name)
        if match:
            try:
                timestamps.append(
                    datetime.strptime(match.group(1), "%Y-%m-%dT%H-%M").replace(tzinfo=TZ)
                )
            except ValueError:
                pass
    if not timestamps:
        return None

    # Some Gemini session files are nested structures; if role detection misses
    # them, still count the file as a stored activity session.
    return SessionStats(
        public_source="Gemini CLI",
        started_at=min(timestamps),
        prompts=prompts,
        assistant=assistant,
        tool_calls=tool_calls,
        tool_results=tool_results,
        tokens=tokens,
        web=web,
    )


def parse_gemini_brain_metadata() -> list[SessionStats]:
    """Best-effort fallback for Antigravity/Gemini task metadata.

    These files do not expose prompt/response counters, so each task folder is
    treated as one low-intensity session on its latest update day.
    """

    roots = [
        HOME / ".gemini/antigravity-backup/brain",
        HOME / ".gemini/antigravity/brain",
        HOME / ".gemini/antigravity-ide/brain",
    ]
    sessions: list[SessionStats] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name in seen:
                continue
            seen.add(folder.name)
            latest: datetime | None = None
            artifact_count = 0
            for metadata in folder.glob("*.metadata.json"):
                try:
                    obj = json.loads(metadata.read_text(encoding="utf-8", errors="ignore"))
                except (OSError, json.JSONDecodeError):
                    continue
                parsed = parse_dt(obj.get("updatedAt"))
                if parsed:
                    latest = max(latest, parsed) if latest else parsed
                    artifact_count += 1
            if latest:
                sessions.append(
                    SessionStats(
                        public_source="Gemini CLI",
                        started_at=latest,
                        prompts=1,
                        assistant=max(1, artifact_count),
                    )
                )
    return sessions


def collect_sessions() -> list[SessionStats]:
    configs: list[tuple[str, str, list[str]]] = [
        ("codex", "Codex", [".codex/sessions/**/*.jsonl", ".codex/archived_sessions/**/*.jsonl"]),
        (
            "codex",
            "Codex via custom setup",
            [
                ".codex-omniroute/sessions/**/*.jsonl",
                ".codex-omniroute/archived_sessions/**/*.jsonl",
                ".codex-omniroute-app/sessions/**/*.jsonl",
                ".codex-omniroute-app/archived_sessions/**/*.jsonl",
            ],
        ),
        ("claude", "Claude", [".claude/projects/**/*.jsonl"]),
        ("claude", "Claude via custom setup", [".claudeee/projects/**/*.jsonl"]),
    ]

    sessions: list[SessionStats] = []
    for kind, source, patterns in configs:
        for path in glob_existing(patterns):
            parsed = parse_codex_session(path, source) if kind == "codex" else parse_claude_session(path, source)
            if parsed:
                sessions.append(parsed)

    for path in glob_existing([".gemini/tmp/**/chats/session-*.json"]):
        parsed = parse_gemini_session(path)
        if parsed:
            sessions.append(parsed)
    sessions.extend(parse_gemini_brain_metadata())

    return [session for session in sessions if session.day >= RANGE_START]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = round((len(values) - 1) * fraction)
    return values[max(0, min(index, len(values) - 1))]


def day_score(day: dict[str, Any]) -> float:
    if day["sessions"] <= 0:
        return 0.0
    token_score = math.log10(max(1, day["tokens"])) * 2.0 if day["tokens"] else 0.0
    score = (
        day["sessions"] * 4.0
        + day["prompts"] * 1.1
        + day["assistant"] * 0.12
        + day["toolCalls"] * 0.12
        + day["web"] * 0.5
        + token_score
    )
    return round(score, 2)


def level_for(score: float, thresholds: list[float]) -> int:
    if score <= 0:
        return 0
    if score <= thresholds[0]:
        return 1
    if score <= thresholds[1]:
        return 2
    if score <= thresholds[2]:
        return 3
    return 4


def streaks(days: list[dict[str, Any]]) -> tuple[int, int]:
    longest = current = 0
    for day in days:
        current = current + 1 if day["sessions"] else 0
        longest = max(longest, current)

    closing = 0
    for day in reversed(days):
        if not day["sessions"]:
            break
        closing += 1
    return longest, closing


def build_data(sessions: list[SessionStats], today: date) -> dict[str, Any]:
    end = max(today, max((session.day for session in sessions), default=RANGE_START))
    week_start = RANGE_START - timedelta(days=(RANGE_START.weekday() + 1) % 7)
    week_end = end + timedelta(days=(5 - end.weekday()) % 7)
    total_weeks = ((week_end - week_start).days // 7) + 1

    by_day: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_source: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    source_days: dict[str, set[date]] = defaultdict(set)

    for session in sessions:
        day = session.day
        daily = by_day[day]
        daily["sessions"] += 1
        daily["prompts"] += session.prompts
        daily["assistant"] += session.assistant
        daily["toolCalls"] += session.tool_calls
        daily["toolResults"] += session.tool_results
        daily["tokens"] += session.tokens
        daily["web"] += session.web

        source = by_source[session.public_source]
        source["sessions"] += 1
        source["prompts"] += session.prompts
        source["assistant"] += session.assistant
        source["toolCalls"] += session.tool_calls
        source["tokens"] += session.tokens
        source_days[session.public_source].add(day)

    days: list[dict[str, Any]] = []
    cursor = week_start
    while cursor <= week_end:
        raw = by_day[cursor]
        col = (cursor - week_start).days // 7
        row = (cursor - week_start).days % 7
        day = {
            "date": cursor.isoformat(),
            "sessions": int(raw["sessions"]),
            "prompts": int(raw["prompts"]),
            "assistant": int(raw["assistant"]),
            "toolCalls": int(raw["toolCalls"]),
            "tokens": int(raw["tokens"]),
            "web": int(raw["web"]),
            "score": 0.0,
            "level": 0,
            "inRange": RANGE_START <= cursor <= end,
            "row": row,
            "col": col,
        }
        day["score"] = day_score(day)
        days.append(day)
        cursor += timedelta(days=1)

    in_range_days = [day for day in days if day["inRange"]]
    active_days = [day for day in in_range_days if day["sessions"] > 0]
    score_values = [float(day["score"]) for day in active_days]
    thresholds = [percentile(score_values, p) for p in (0.2, 0.4, 0.65, 0.85)]
    for day in days:
        day["level"] = level_for(float(day["score"]), thresholds) if day["inRange"] else 0

    longest, closing = streaks(in_range_days)
    peak_day = max(active_days, key=lambda item: item["score"], default=None)
    first_day = active_days[0]["date"] if active_days else None

    summary = {
        "activeDays": len(active_days),
        "sessions": sum(day["sessions"] for day in in_range_days),
        "prompts": sum(day["prompts"] for day in in_range_days),
        "assistant": sum(day["assistant"] for day in in_range_days),
        "toolCalls": sum(day["toolCalls"] for day in in_range_days),
        "tokens": sum(day["tokens"] for day in in_range_days),
        "web": sum(day["web"] for day in in_range_days),
        "firstActivityDate": first_day,
        "peakDate": peak_day["date"] if peak_day else None,
        "longestStreak": longest,
        "closingStreak": closing,
    }

    sources = []
    for name in PUBLIC_SOURCE_ORDER:
        raw = by_source.get(name, {})
        sessions_count = int(raw.get("sessions", 0))
        if not sessions_count:
            continue
        sources.append(
            {
                "name": name,
                "shortName": {
                    "Codex": "Codex",
                    "Codex via custom setup": "Codex custom",
                    "Claude": "Claude",
                    "Claude via custom setup": "Claude custom",
                    "Gemini CLI": "Gemini CLI",
                }[name],
                "sessions": sessions_count,
                "activeDays": len(source_days[name]),
                "prompts": int(raw.get("prompts", 0)),
                "assistant": int(raw.get("assistant", 0)),
                "toolCalls": int(raw.get("toolCalls", 0)),
                "tokens": int(raw.get("tokens", 0)),
            }
        )

    return {
        "generatedAt": today.isoformat(),
        "range": {"start": RANGE_START.isoformat(), "end": end.isoformat()},
        "weekStart": week_start.isoformat(),
        "weeks": total_weeks,
        "privacy": "public aggregate counts only",
        "summary": summary,
        "sources": sources,
        "thresholds": thresholds,
        "days": days,
    }


def short_number(value: int | float) -> str:
    value = float(value)
    for suffix, divisor in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if abs(value) >= divisor:
            return f"{value / divisor:.2f}{suffix}".replace(".00", "")
    return str(int(value))


def fmt_int(value: int | float) -> str:
    return f"{int(value):,}"


def fmt_range(data: dict[str, Any]) -> str:
    start = date.fromisoformat(data["range"]["start"])
    end = date.fromisoformat(data["range"]["end"])
    if start.year == end.year:
        return f"{calendar.month_abbr[start.month]} - {calendar.month_abbr[end.month]} {end.year}"
    return f"{calendar.month_abbr[start.month]} {start.year} - {calendar.month_abbr[end.month]} {end.year}"


def fmt_date(iso: str | None, *, with_year: bool = True) -> str:
    if not iso:
        return "None"
    value = date.fromisoformat(iso)
    if with_year:
        return f"{calendar.month_abbr[value.month]} {value.day}, {value.year}"
    return f"{calendar.month_abbr[value.month]} {value.day}"


def update_html(data: dict[str, Any]) -> None:
    payload = json.dumps(data, separators=(",", ":"))
    if HTML_PATH.exists():
        text = HTML_PATH.read_text(encoding="utf-8")
    else:
        raise FileNotFoundError(f"Expected existing HTML template at {HTML_PATH}")

    text = re.sub(
        r'(<script id="vibe-data" type="application/json">).*?(</script>)',
        lambda match: match.group(1) + payload + match.group(2),
        text,
        flags=re.S,
    )
    text = re.sub(r'<strong id="totalActive">[^<]*</strong>', f'<strong id="totalActive">{fmt_int(data["summary"]["activeDays"])}</strong>', text)
    text = re.sub(r'<strong id="totalSessions">[^<]*</strong>', f'<strong id="totalSessions">{fmt_int(data["summary"]["sessions"])}</strong>', text)
    text = re.sub(r'<strong id="totalPrompts">[^<]*</strong>', f'<strong id="totalPrompts">{fmt_int(data["summary"]["prompts"])}</strong>', text)
    text = re.sub(r'<strong id="totalAssistant">[^<]*</strong>', f'<strong id="totalAssistant">{fmt_int(data["summary"]["assistant"])}</strong>', text)
    text = re.sub(r'<strong id="totalTools">[^<]*</strong>', f'<strong id="totalTools">{fmt_int(data["summary"]["toolCalls"])}</strong>', text)
    text = re.sub(r'<strong id="totalTokens">[^<]*</strong>', f'<strong id="totalTokens">{short_number(data["summary"]["tokens"])}</strong>', text)
    badge_values = iter([html.escape(fmt_range(data)), f'{len(data["sources"])} sources'])

    def replace_badge(match: re.Match[str]) -> str:
        return f'<span class="year-badge">{next(badge_values, match.group(1))}</span>'

    text = re.sub(r'<span class="year-badge">([^<]*)</span>', replace_badge, text)
    text = re.sub(r'<dd id="generatedAt">[^<]*</dd>', f'<dd id="generatedAt">{fmt_date(data["generatedAt"])}</dd>', text)
    HTML_PATH.write_text(text, encoding="utf-8")


def render_svg(data: dict[str, Any]) -> str:
    summary = data["summary"]
    days = data["days"]
    range_label = fmt_range(data)
    in_range_count = sum(1 for item in days if item["inRange"])
    active_share = summary["activeDays"] / in_range_count * 100 if in_range_count else 0
    title = f"{fmt_int(summary['activeDays'])} vibe-coding days"
    desc = (
        f"Public aggregate of Dmitriy's AI coding activity from {fmt_date(data['range']['start'])} "
        f"through {fmt_date(data['range']['end'])}: {fmt_int(summary['activeDays'])} active days, "
        f"{fmt_int(summary['sessions'])} sessions, {fmt_int(summary['prompts'])} prompts, "
        f"{fmt_int(summary['toolCalls'])} tool calls, and {short_number(summary['tokens'])} tokens."
    )

    graph_x = 78
    graph_y = 105
    cell = 10
    gap = 3
    lines: list[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 332" width="1120" height="332" role="img" aria-labelledby="title desc">',
        f'<title id="title">{html.escape(title)}, Gruvbox theme</title>',
        f'<desc id="desc">{html.escape(desc)}</desc>',
        "<style>",
        ':root{color-scheme:light dark;--bg:#1d2021;--surface:#282828;--fg:#ebdbb2;--muted:#a89984;--border:#504945;--empty:#3c3836;--l1:#689d6a;--l2:#98971a;--l3:#d79921;--l4:#d65d0e;--accent:#fabd2f}',
        '@media(prefers-color-scheme:light){:root{--bg:#f2e5bc;--surface:#fbf1c7;--fg:#3c3836;--muted:#7c6f64;--border:#d5c4a1;--empty:#d5c4a1;--l1:#689d6a;--l2:#98971a;--l3:#d79921;--l4:#d65d0e;--accent:#b57614}}',
        'text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;fill:var(--fg)}',
        ".muted{fill:var(--muted)}.accent{fill:var(--accent)}.small{font-size:13px}.micro{font-size:10px}.month{font-size:12px}.title{font-size:22px;font-weight:600}.value{font-size:14px;font-weight:600}.label{font-size:9px;fill:var(--muted)}.card{fill:var(--surface);stroke:var(--border);stroke-width:1}.day{stroke-width:.5}.outside{fill:var(--bg);stroke:none}.l0{fill:var(--empty);stroke:var(--border)}.l1{fill:var(--l1);stroke:none}.l2{fill:var(--l2);stroke:none}.l3{fill:var(--l3);stroke:none}.l4{fill:var(--l4);stroke:none}",
        "</style>",
        '<rect width="100%" height="100%" fill="var(--bg)"/>',
        f'<text x="22" y="38" class="title">{html.escape(title)}</text>',
        f'<text x="681" y="39" class="muted small">{html.escape(range_label)} / public aggregate</text>',
        '<rect class="card" x="22" y="62" width="875" height="248" rx="6"/>',
    ]

    first = date.fromisoformat(data["weekStart"])
    cursor = date.fromisoformat(data["range"]["start"]).replace(day=1)
    end_month = date.fromisoformat(data["range"]["end"]).replace(day=1)
    while cursor <= end_month:
        col = (cursor - first).days // 7
        x = graph_x + col * (cell + gap)
        lines.append(f'<text x="{x}" y="90" class="month">{calendar.month_abbr[cursor.month]}</text>')
        year = cursor.year + (cursor.month // 12)
        month = (cursor.month % 12) + 1
        cursor = date(year, month, 1)

    lines.extend(
        [
            '<text x="36" y="130" class="muted month">Mon</text>',
            '<text x="36" y="156" class="muted month">Wed</text>',
            '<text x="36" y="182" class="muted month">Fri</text>',
        ]
    )

    for day in days:
        x = graph_x + day["col"] * (cell + gap)
        y = graph_y + day["row"] * (cell + gap)
        cls = "outside" if not day["inRange"] else f"l{day['level']}"
        if not day["inRange"]:
            label = f"{day['date']}: outside snapshot range"
        elif day["sessions"]:
            label = (
                f"{day['date']}: {fmt_int(day['sessions'])} sessions, "
                f"{fmt_int(day['prompts'])} prompts, {fmt_int(day['toolCalls'])} tool calls, "
                f"{short_number(day['tokens'])} tokens"
            )
        else:
            label = f"{day['date']}: no stored session activity"
        lines.append(f'<rect class="day {cls}" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2">')
        lines.append(f"  <title>{html.escape(label)}</title>")
        lines.append("</rect>")

    lines.extend(
        [
            f'<text x="78" y="226" class="muted small">{fmt_int(summary["sessions"])} sessions - {fmt_int(summary["prompts"])} prompts - {fmt_int(summary["toolCalls"])} tool calls - {short_number(summary["tokens"])} tokens</text>',
            '<text x="698" y="226" class="muted small">Less</text>',
        ]
    )
    for idx, cls in enumerate(("l0", "l1", "l2", "l3", "l4")):
        lines.append(f'<rect class="day {cls}" x="{738 + idx * 17}" y="215" width="10" height="10" rx="2"/>')
    lines.extend(
        [
            '<text x="829" y="226" class="muted small">More</text>',
            '<line x1="78" y1="246" x2="875" y2="246" stroke="var(--border)"/>',
            f'<text x="78" y="274" class="muted small">{fmt_int(summary["activeDays"])} of {fmt_int(in_range_count)} days active  /  first stored {fmt_date(summary["firstActivityDate"], with_year=False)}  /  longest streak {fmt_int(summary["longestStreak"])} days</text>',
            '<rect class="card" x="926" y="22" width="172" height="288" rx="6"/>',
            '<text x="944" y="47" class="value">Snapshot</text>',
            f'<text x="944" y="64" class="muted micro">{html.escape(range_label)}</text>',
            '<line x1="944" y1="78" x2="1080" y2="78" stroke="var(--border)"/>',
            '<text x="944" y="98" class="label">ACTIVE SHARE</text>',
            f'<text x="944" y="117" class="value">{active_share:.1f}%</text>',
            '<text x="1020" y="98" class="label">LONGEST</text>',
            f'<text x="1020" y="117" class="value">{fmt_int(summary["longestStreak"])} days</text>',
            '<text x="944" y="143" class="label">CLOSING</text>',
            f'<text x="944" y="162" class="value">{fmt_int(summary["closingStreak"])} days</text>',
            '<text x="1020" y="143" class="label">PEAK DAY</text>',
            f'<text x="1020" y="162" class="value">{fmt_date(summary["peakDate"], with_year=False)}</text>',
            '<line x1="944" y1="176" x2="1080" y2="176" stroke="var(--border)"/>',
            '<text x="944" y="193" class="label">SESSION SOURCES</text>',
        ]
    )

    y = 210
    for source in data["sources"][:5]:
        lines.append(f'<text x="944" y="{y}" class="muted micro">{html.escape(source["shortName"])}</text>')
        lines.append(f'<text x="1080" y="{y}" class="value" text-anchor="end">{fmt_int(source["sessions"])}</text>')
        y += 15

    lines.extend(
        [
            '<line x1="944" y1="276" x2="1080" y2="276" stroke="var(--border)"/>',
            '<text x="944" y="291" class="label">GENERATED</text>',
            f'<text x="944" y="307" class="value">{fmt_date(data["generatedAt"])}</text>',
            "<!-- Public daily and source-level aggregate counts only. -->",
            "</svg>",
            "",
        ]
    )
    return "\n".join(lines)


def update_svg(data: dict[str, Any]) -> None:
    SVG_PATH.write_text(render_svg(data), encoding="utf-8")


def update_readme(data: dict[str, Any]) -> None:
    summary = data["summary"]
    alt = (
        f"{fmt_int(summary['activeDays'])} vibe-coding days from "
        f"{fmt_range(data)}, shown in a Gruvbox activity dashboard"
    )
    text = f"""# Dmitriy / NumeroQuadro

<a href="https://numeroquadro.github.io/NumeroQuadro/vibecoding-heatmap-interactive.html">
  <img alt="{html.escape(alt)}" src="./vibecoding-heatmap-2026.svg" width="100%">
</a>

<p align="center">
  <a href="https://numeroquadro.github.io/NumeroQuadro/vibecoding-heatmap-interactive.html">Open interactive vibecoding diary</a>
</p>
"""
    README_PATH.write_text(text, encoding="utf-8")


def write_debug_csv(sessions: list[SessionStats]) -> None:
    debug_dir = Path(os.environ.get("VIBECODING_DEBUG_DIR", HOME / ".cache/numeroquadro-vibecoding"))
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / "sessions-public-aggregate.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source", "day", "prompts", "assistant", "tool_calls", "tokens", "web"])
        for session in sorted(sessions, key=lambda item: (item.started_at, item.public_source)):
            writer.writerow(
                [
                    session.public_source,
                    session.day.isoformat(),
                    session.prompts,
                    session.assistant,
                    session.tool_calls,
                    session.tokens,
                    session.web,
                ]
            )


def main() -> None:
    today = datetime.now(TZ).date()
    sessions = collect_sessions()
    data = build_data(sessions, today)
    update_html(data)
    update_svg(data)
    update_readme(data)
    write_debug_csv(sessions)
    print(
        f"Generated {data['summary']['activeDays']} active days, "
        f"{data['summary']['sessions']} sessions, {short_number(data['summary']['tokens'])} tokens."
    )


if __name__ == "__main__":
    main()
