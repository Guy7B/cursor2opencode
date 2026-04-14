#!/usr/bin/env python3
"""
cursor2opencode — Import Cursor agent transcripts into OpenCode.

Discovers Cursor workspaces automatically and imports conversation history
into OpenCode sessions.

Usage:
    # Preview what will be imported:
    cursor2opencode --discover

    # Auto-discover and import all Cursor conversations:
    cursor2opencode

    # Import all conversations from a specific workspace:
    cursor2opencode ~/.cursor/projects/.../agent-transcripts/ --all

    # Import a single conversation:
    cursor2opencode ~/.cursor/projects/.../agent-transcripts/<uuid>/

    # Convert only (save JSON files, don't import):
    cursor2opencode --no-import --output ./converted

Requires: Python 3.7+, opencode CLI (for import)
"""

__version__ = "0.1.0"

import json
import os
import sys
import re
import argparse
import hashlib
import tempfile
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, List

# ---------------------------------------------------------------------------
# Cursor transcript format constants
# ---------------------------------------------------------------------------

CURSOR_PROJECTS_DIR = Path.home() / ".cursor" / "projects"
AGENT_TRANSCRIPTS_DIRNAME = "agent-transcripts"

# Map Cursor tool names to OpenCode equivalents
TOOL_NAME_MAP = {
    "Shell": "bash",
    "Write": "write",
    "Read": "read",
    "Grep": "grep",
    "Glob": "glob",
    "WebFetch": "webfetch",
    "WebSearch": "webfetch",
    "AskQuestion": "question",
    "TodoWrite": "todowrite",
    "Task": "task",
    "Edit": "edit",
    "ApplyPatch": "edit",
}

# Fields to try (in order) when building a tool input preview string
TOOL_PREVIEW_FIELDS = [
    "command", "url", "search_term", "path", "filePath",
    "description", "contents", "raw",
]


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def generate_id(prefix: str, seed: str) -> str:
    """Generate a deterministic ID with a given prefix, seeded by input string."""
    h = hashlib.sha256(seed.encode()).hexdigest()[:24]
    return "{}_{}".format(prefix, h)


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------

def parse_jsonl(path: Path) -> List[dict]:
    """Parse a JSONL file, skipping malformed lines."""
    lines = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if raw:
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    return lines


# ---------------------------------------------------------------------------
# Cursor content extraction
# ---------------------------------------------------------------------------

def extract_user_text(content_parts: list) -> str:
    """Extract the user's query text, stripping Cursor's <user_query> wrapper."""
    texts = []
    for part in content_parts:
        if part.get("type") == "text":
            text = part["text"]
            if "<user_query>" in text:
                start = text.find("<user_query>") + len("<user_query>")
                end = text.find("</user_query>")
                if end != -1:
                    text = text[start:end].strip()
                else:
                    text = text[start:].strip()
            texts.append(text)
    return "\n".join(texts)


def extract_assistant_text(content_parts: list) -> str:
    """Extract all text content from Cursor assistant content parts."""
    texts = []
    for part in content_parts:
        if part.get("type") == "text":
            texts.append(part["text"])
    return "\n".join(texts)


def extract_tool_calls(content_parts: list) -> list:
    """Extract tool_use entries from Cursor content parts."""
    return [p for p in content_parts if p.get("type") == "tool_use"]


def map_tool_name(cursor_name: str) -> str:
    """Map a Cursor tool name to its OpenCode equivalent."""
    return TOOL_NAME_MAP.get(cursor_name, cursor_name.lower())


# ---------------------------------------------------------------------------
# Working directory resolution
# ---------------------------------------------------------------------------

def resolve_working_dir(cursor_project_name: str) -> str:
    """
    Attempt to recover the original filesystem path from a Cursor project name.

    Cursor encodes paths by replacing '/' with '-', which is lossy for paths
    containing actual dashes. We resolve ambiguity by greedily testing from
    the left: at each dash, try '/' first (directory must exist), otherwise
    keep the dash literal.

    Returns the resolved path string, or empty string if unresolvable.
    """
    # Skip non-path project names (timestamps, temp dirs, etc.)
    if not cursor_project_name or cursor_project_name[0].isdigit():
        return ""
    if cursor_project_name in ("empty-window",):
        return ""

    parts = cursor_project_name.split("-")
    if not parts:
        return ""

    # Greedy left-to-right resolution
    resolved = "/" + parts[0]
    for segment in parts[1:]:
        candidate = resolved + "/" + segment
        if os.path.isdir(candidate):
            resolved = candidate
        else:
            # Try keeping it as a dash (part of the directory name)
            resolved = resolved + "-" + segment

    return resolved if os.path.isdir(resolved) else ""


# ---------------------------------------------------------------------------
# OpenCode message construction helpers
# ---------------------------------------------------------------------------

def get_file_creation_time_ms(filepath: Path) -> int:
    """Get file creation time in milliseconds (uses birthtime on macOS)."""
    try:
        stat = filepath.stat()
        ctime = getattr(stat, "st_birthtime", stat.st_ctime)
        return int(ctime * 1000)
    except Exception:
        return int(datetime.now().timestamp() * 1000)


def get_opencode_version() -> str:
    """Detect installed OpenCode version, falling back to 'unknown'."""
    try:
        result = subprocess.run(
            ["opencode", "--version"], capture_output=True, text=True, timeout=5
        )
        version = result.stdout.strip()
        if version:
            return version
    except Exception:
        pass
    return "unknown"


def make_user_msg(
    session_id: str, msg_id: str, text: str, time_ms: int,
) -> dict:
    """Build an OpenCode user message."""
    part_id = generate_id("prt", "{}-text".format(msg_id))
    return {
        "info": {
            "role": "user",
            "time": {"created": time_ms},
            "agent": "build",
            "model": {"providerID": "cursor", "modelID": "cursor-agent"},
            "summary": {"diffs": []},
            "id": msg_id,
            "sessionID": session_id,
        },
        "parts": [{
            "type": "text",
            "text": text,
            "id": part_id,
            "sessionID": session_id,
            "messageID": msg_id,
        }],
    }


def make_asst_info(
    msg_id: str,
    session_id: str,
    parent_msg_id: str,
    time_ms: int,
    finish: str = "stop",
    working_dir: str = "",
) -> dict:
    """Build an OpenCode assistant message info dict."""
    return {
        "parentID": parent_msg_id,
        "role": "assistant",
        "mode": "build",
        "agent": "build",
        "path": {"cwd": working_dir, "root": "/"},
        "cost": 0,
        "tokens": {
            "total": 0, "input": 0, "output": 0, "reasoning": 0,
            "cache": {"write": 0, "read": 0},
        },
        "modelID": "cursor-agent",
        "providerID": "cursor",
        "time": {"created": time_ms, "completed": time_ms + 100},
        "finish": finish,
        "id": msg_id,
        "sessionID": session_id,
    }


def make_text_separator(
    session_id: str, msg_id: str, parent_msg_id: str,
    text: str, time_ms: int, working_dir: str = "",
) -> dict:
    """Build a separator assistant message (used for subagent boundaries)."""
    return {
        "info": make_asst_info(msg_id, session_id, parent_msg_id, time_ms, working_dir=working_dir),
        "parts": [
            {
                "type": "step-start",
                "id": generate_id("prt", "{}-start".format(msg_id)),
                "sessionID": session_id,
                "messageID": msg_id,
            },
            {
                "type": "text",
                "text": text,
                "time": {"start": time_ms, "end": time_ms + 100},
                "id": generate_id("prt", "{}-text".format(msg_id)),
                "sessionID": session_id,
                "messageID": msg_id,
            },
            {
                "reason": "stop",
                "type": "step-finish",
                "tokens": {
                    "total": 0, "input": 0, "output": 0, "reasoning": 0,
                    "cache": {"write": 0, "read": 0},
                },
                "cost": 0,
                "id": generate_id("prt", "{}-finish".format(msg_id)),
                "sessionID": session_id,
                "messageID": msg_id,
            },
        ],
    }


def build_tool_preview(tool_input: dict) -> str:
    """Build a short preview string for a tool call's input."""
    for key in TOOL_PREVIEW_FIELDS:
        if key in tool_input:
            return str(tool_input[key])[:200]
    return json.dumps(tool_input)[:200]


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------

def convert_line(
    line_data: dict,
    session_id: str,
    parent_msg_id: str,
    line_index: int,
    base_time_ms: int,
) -> list:
    """
    Convert a single Cursor JSONL line into OpenCode messages.

    Returns a list of message dicts (usually one, but may be empty if the
    line has no meaningful content).
    """
    role = line_data["role"]
    content_parts = line_data.get("message", {}).get("content", [])

    msg_time_ms = base_time_ms + (line_index * 1000)
    msg_id = generate_id("msg", "{}-{}".format(session_id, line_index))

    if role == "user":
        user_text = extract_user_text(content_parts)
        if not user_text.strip():
            return []
        return [make_user_msg(session_id, msg_id, user_text, msg_time_ms)]

    # role == "assistant"
    assistant_text = extract_assistant_text(content_parts)
    tool_calls = extract_tool_calls(content_parts)

    parts = []  # type: List[dict]

    # Step start
    parts.append({
        "type": "step-start",
        "id": generate_id("prt", "{}-{}-start".format(session_id, line_index)),
        "sessionID": session_id,
        "messageID": msg_id,
    })

    # Text part
    if assistant_text.strip():
        parts.append({
            "type": "text",
            "text": assistant_text,
            "time": {"start": msg_time_ms, "end": msg_time_ms + 500},
            "id": generate_id("prt", "{}-{}-text".format(session_id, line_index)),
            "sessionID": session_id,
            "messageID": msg_id,
        })

    # Tool parts
    for ti, tool in enumerate(tool_calls):
        tool_name = map_tool_name(tool.get("name", "unknown"))
        tool_input = tool.get("input", {})

        # OpenCode requires tool input to be a dict (Zod record validation)
        if not isinstance(tool_input, dict):
            tool_input = {"raw": str(tool_input)}

        preview = build_tool_preview(tool_input)
        call_id = generate_id("call", "{}-{}-tool-{}".format(session_id, line_index, ti))

        parts.append({
            "type": "tool",
            "tool": tool_name,
            "callID": call_id,
            "state": {
                "status": "completed",
                "input": tool_input,
                "output": "[Imported from Cursor -- tool output not captured in transcript]",
                "metadata": {"preview": preview, "truncated": False, "loaded": []},
                "title": "{}: {}".format(tool.get("name", "unknown"), preview[:80]),
                "time": {"start": msg_time_ms + 100, "end": msg_time_ms + 500},
            },
            "metadata": {"cursor": {"original_tool": tool.get("name", "unknown")}},
            "id": generate_id("prt", "{}-{}-tool-{}".format(session_id, line_index, ti)),
            "sessionID": session_id,
            "messageID": msg_id,
        })

    # Step finish
    finish_reason = "tool-calls" if tool_calls else "stop"
    parts.append({
        "reason": finish_reason,
        "type": "step-finish",
        "tokens": {
            "total": 0, "input": 0, "output": 0, "reasoning": 0,
            "cache": {"write": 0, "read": 0},
        },
        "cost": 0,
        "id": generate_id("prt", "{}-{}-finish".format(session_id, line_index)),
        "sessionID": session_id,
        "messageID": msg_id,
    })

    return [{
        "info": make_asst_info(
            msg_id, session_id, parent_msg_id, msg_time_ms, finish=finish_reason,
        ),
        "parts": parts,
    }]


def derive_title(lines: list) -> str:
    """Derive a session title from the first user message."""
    for line in lines:
        if line.get("role") == "user":
            text = extract_user_text(line.get("message", {}).get("content", []))
            if text.strip():
                title = text.strip().replace("\n", " ")[:80]
                if len(text.strip()) > 80:
                    title += "..."
                return title
    return "Imported Cursor conversation"


def convert_conversation(
    jsonl_path: Optional[Path],
    subagent_paths: Optional[List[Path]] = None,
    working_dir: str = "",
    conversation_id: str = "",
    opencode_version: str = "unknown",
) -> Optional[dict]:
    """
    Convert a full Cursor conversation (main + subagents) to OpenCode format.

    Args:
        jsonl_path: Path to the main .jsonl transcript (None for subagent-only).
        subagent_paths: Subagent .jsonl files to append.
        working_dir: Working directory for session metadata.
        conversation_id: Conversation UUID (required when jsonl_path is None).
        opencode_version: OpenCode version string for the export header.
    """
    if jsonl_path is not None:
        conversation_id = conversation_id or jsonl_path.stem
    session_id = generate_id("ses", "cursor-{}".format(conversation_id))

    # Parse main transcript
    lines = parse_jsonl(jsonl_path) if jsonl_path is not None else []

    if not lines and not subagent_paths:
        return None

    # Derive timestamps
    if jsonl_path is not None:
        base_time_ms = get_file_creation_time_ms(jsonl_path)
    elif subagent_paths:
        base_time_ms = get_file_creation_time_ms(subagent_paths[0])
    else:
        base_time_ms = int(datetime.now().timestamp() * 1000)

    title = derive_title(lines) if lines else "Subagent-only conversation"

    # --- Build messages ---
    all_messages = []  # type: List[dict]
    last_user_msg_id = None  # type: Optional[str]

    # OpenCode requires every assistant message to have a parentID pointing to
    # a user message. If the conversation starts with an assistant turn (or is
    # subagent-only), synthesize a placeholder user message.
    first_role = lines[0]["role"] if lines else "assistant"
    if first_role != "user":
        synth_id = generate_id("msg", "{}-synthetic-user".format(session_id))
        all_messages.append(
            make_user_msg(session_id, synth_id, "[Background agent tasks]", base_time_ms - 1)
        )
        last_user_msg_id = synth_id

    for i, line in enumerate(lines):
        msgs = convert_line(line, session_id, last_user_msg_id, i, base_time_ms)
        for m in msgs:
            if m["info"]["role"] == "user":
                last_user_msg_id = m["info"]["id"]
        all_messages.extend(msgs)

    # --- Append subagent transcripts ---
    if subagent_paths:
        for sa_path in subagent_paths:
            sa_lines = parse_jsonl(sa_path)
            if not sa_lines:
                continue

            sa_id = sa_path.stem
            sa_title = derive_title(sa_lines)

            # Offset line indices to avoid ID collisions with main conversation
            offset = len(all_messages) + 1000
            sep_time = base_time_ms + (offset * 1000)

            # Start separator
            sep_msg_id = generate_id("msg", "{}-subagent-{}-sep".format(session_id, sa_id))
            all_messages.append(make_text_separator(
                session_id, sep_msg_id, last_user_msg_id,
                "---\n\n**[Subagent: {}]** (ID: `{}`)\n\n---".format(sa_title, sa_id),
                sep_time, working_dir,
            ))

            # Subagent messages
            sa_last_user_id = last_user_msg_id
            for j, sa_line in enumerate(sa_lines):
                sa_msgs = convert_line(
                    sa_line, session_id, sa_last_user_id, offset + j + 1, base_time_ms,
                )
                for m in sa_msgs:
                    if m["info"]["role"] == "user":
                        sa_last_user_id = m["info"]["id"]
                all_messages.extend(sa_msgs)

            # End separator
            end_offset = offset + len(sa_lines) + 2
            end_time = base_time_ms + (end_offset * 1000)
            end_msg_id = generate_id("msg", "{}-subagent-{}-end".format(session_id, sa_id))
            all_messages.append(make_text_separator(
                session_id, end_msg_id, sa_last_user_id,
                "---\n\n**[End of subagent: {}]**\n\n---".format(sa_title),
                end_time, working_dir,
            ))

    end_time_ms = base_time_ms + (max(len(lines), len(all_messages)) * 1000)

    return {
        "info": {
            "id": session_id,
            "slug": "cursor-{}".format(conversation_id[:8]),
            "projectID": "global",
            "directory": working_dir,
            "title": "[Cursor] {}".format(title),
            "version": opencode_version,
            "summary": {"additions": 0, "deletions": 0, "files": 0},
            "time": {"created": base_time_ms, "updated": end_time_ms},
        },
        "messages": all_messages,
    }


# ---------------------------------------------------------------------------
# Conversation directory processing
# ---------------------------------------------------------------------------

def process_conversation_dir(
    conv_dir: Path, working_dir: str = "", opencode_version: str = "unknown",
) -> Optional[dict]:
    """Process a single Cursor conversation directory."""
    conv_id = conv_dir.name

    # Find subagent transcripts
    subagent_paths = []  # type: List[Path]
    subagents_dir = conv_dir / "subagents"
    if subagents_dir.exists():
        subagent_paths = sorted(subagents_dir.glob("*.jsonl"))

    # Find main transcript
    jsonl_path = conv_dir / "{}.jsonl".format(conv_id)
    if not jsonl_path.exists():
        jsonl_files = list(conv_dir.glob("*.jsonl"))
        if not jsonl_files:
            if not subagent_paths:
                print("  Warning: No .jsonl files found in {}".format(conv_dir), file=sys.stderr)
                return None
            jsonl_path = None
        else:
            jsonl_path = jsonl_files[0]

    return convert_conversation(
        jsonl_path, subagent_paths, working_dir, conv_id, opencode_version,
    )


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

def discover_cursor_workspaces() -> List[Path]:
    """
    Find all Cursor workspaces that contain agent transcripts.

    Returns a list of agent-transcripts directories.
    """
    if not CURSOR_PROJECTS_DIR.exists():
        return []

    results = []
    for project_dir in sorted(CURSOR_PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        transcripts_dir = project_dir / AGENT_TRANSCRIPTS_DIRNAME
        if transcripts_dir.exists():
            conv_dirs = [d for d in transcripts_dir.iterdir() if d.is_dir()]
            if conv_dirs:
                results.append(transcripts_dir)
    return results


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from a string."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_file(filepath: Path) -> bool:
    """Import a single JSON file into OpenCode. Returns True on success."""
    result = subprocess.run(
        ["opencode", "import", str(filepath)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("  Imported: {}".format(strip_ansi(result.stdout.strip())), file=sys.stderr)
        return True
    else:
        msg = strip_ansi(result.stderr.strip() or result.stdout.strip())
        # Compact the JSON error into a single line
        msg = " ".join(msg.split())
        print("  Failed: {}".format(msg[:200]), file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import Cursor agent transcripts into OpenCode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what will be imported:
  %(prog)s --discover

  # Auto-discover and import all Cursor conversations:
  %(prog)s

  # Import all conversations from a specific workspace:
  %(prog)s ~/.cursor/projects/.../agent-transcripts/ --all

  # Import a single conversation:
  %(prog)s ~/.cursor/projects/.../agent-transcripts/<uuid>/

  # Convert only (keep JSON files, don't import):
  %(prog)s --no-import --output ./converted
        """,
    )
    parser.add_argument(
        "path", nargs="?", default=None,
        help="Path to a conversation dir or agent-transcripts dir (with --all). "
             "Omit to auto-discover all Cursor workspaces.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Process all conversations in the given directory",
    )
    parser.add_argument(
        "--no-import", action="store_true",
        help="Convert only -- write JSON files but don't import into OpenCode",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output directory for converted JSON files (only useful with --no-import)",
    )
    parser.add_argument(
        "--working-dir", "-w", default=None,
        help="Override working directory in session metadata "
             "(auto-detected from Cursor project path by default)",
    )
    parser.add_argument(
        "--discover", "--list", action="store_true",
        help="List Cursor workspaces and conversations without importing anything",
    )
    parser.add_argument(
        "--version", action="version", version="cursor2opencode {}".format(__version__),
    )

    args = parser.parse_args()

    # --discover: dry-run listing
    if args.discover:
        workspaces = discover_cursor_workspaces()
        if not workspaces:
            print("No Cursor workspaces with transcripts found in {}".format(CURSOR_PROJECTS_DIR))
            sys.exit(0)

        total = 0
        for ws in workspaces:
            project_name = ws.parent.name
            working_dir = resolve_working_dir(project_name)
            conv_dirs = sorted(d for d in ws.iterdir() if d.is_dir())
            total += len(conv_dirs)

            wd_display = "  ({})".format(working_dir) if working_dir else ""
            print("{}{}".format(project_name, wd_display))

            for conv_dir in conv_dirs:
                conv_id = conv_dir.name
                jsonl_path = conv_dir / "{}.jsonl".format(conv_id)
                subagents_dir = conv_dir / "subagents"

                # Count lines and subagents
                line_count = 0
                if jsonl_path.exists():
                    with open(jsonl_path) as f:
                        line_count = sum(1 for l in f if l.strip())

                sa_count = 0
                if subagents_dir.exists():
                    sa_count = len(list(subagents_dir.glob("*.jsonl")))

                # Get title from first user message
                title = ""
                if jsonl_path.exists():
                    lines = parse_jsonl(jsonl_path)
                    title = derive_title(lines) if lines else ""
                elif sa_count > 0:
                    title = "(subagent-only)"

                sa_info = " + {} subagents".format(sa_count) if sa_count else ""
                print("  {} {} lines{}  {}".format(
                    conv_id[:8], line_count, sa_info, title[:60],
                ))

        print("\n{} conversations across {} workspaces".format(total, len(workspaces)))
        sys.exit(0)

    # Detect OpenCode version once
    opencode_version = get_opencode_version()

    # Determine output directory
    use_tempdir = args.output is None and not args.no_import
    if use_tempdir:
        tmpdir = tempfile.mkdtemp(prefix="cursor2opencode-")
        output_dir = Path(tmpdir)
    elif args.output:
        output_dir = Path(args.output).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = Path.cwd()

    try:
        if args.path is None:
            # Auto-discovery mode
            workspaces = discover_cursor_workspaces()
            if not workspaces:
                print(
                    "No Cursor workspaces with transcripts found in {}".format(CURSOR_PROJECTS_DIR),
                    file=sys.stderr,
                )
                sys.exit(1)

            total_imported = 0
            total_converted = 0
            for ws in workspaces:
                project_name = ws.parent.name
                working_dir = args.working_dir or resolve_working_dir(project_name)
                conv_dirs = sorted(d for d in ws.iterdir() if d.is_dir())

                print("=== {} ({} conversations) ===".format(
                    project_name, len(conv_dirs),
                ), file=sys.stderr)

                files = _convert_all(conv_dirs, working_dir, output_dir, opencode_version)
                total_converted += len(files)

                if not args.no_import and files:
                    imported = sum(1 for f in files if import_file(f))
                    total_imported += imported
                    print("  {}/{} imported".format(imported, len(files)), file=sys.stderr)
                print("", file=sys.stderr)

            if args.no_import:
                print("Converted {} conversations to {}/".format(total_converted, output_dir), file=sys.stderr)
            else:
                print("Done: {}/{} conversations imported.".format(total_imported, total_converted), file=sys.stderr)

        elif args.all:
            # Explicit --all on a directory
            input_path = Path(args.path).expanduser().resolve()
            if not input_path.is_dir():
                print("Error: {} is not a directory".format(input_path), file=sys.stderr)
                sys.exit(1)

            project_name = input_path.parent.name if input_path.name == AGENT_TRANSCRIPTS_DIRNAME else ""
            working_dir = args.working_dir or resolve_working_dir(project_name)
            conv_dirs = sorted(d for d in input_path.iterdir() if d.is_dir())

            if not conv_dirs:
                print("Error: No conversation directories found in {}".format(input_path), file=sys.stderr)
                sys.exit(1)

            files = _convert_all(conv_dirs, working_dir, output_dir, opencode_version)

            if not args.no_import and files:
                print("\nImporting {} conversations into OpenCode...".format(len(files)), file=sys.stderr)
                imported = sum(1 for f in files if import_file(f))
                print("\nDone: {}/{} imported.".format(imported, len(files)), file=sys.stderr)
            else:
                print("\nConverted {} conversations to {}/".format(len(files), output_dir), file=sys.stderr)

        else:
            # Single conversation
            input_path = Path(args.path).expanduser().resolve()
            if not input_path.is_dir():
                print("Error: {} is not a directory".format(input_path), file=sys.stderr)
                sys.exit(1)

            working_dir = args.working_dir or ""
            result = process_conversation_dir(input_path, working_dir, opencode_version)
            if result is None:
                print("Error: Could not convert conversation", file=sys.stderr)
                sys.exit(1)

            conv_id = input_path.name
            output_file = output_dir / "cursor-{}.json".format(conv_id[:8])
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            msg_count = len(result["messages"])

            if not args.no_import:
                print("Converted ({} messages), importing...".format(msg_count), file=sys.stderr)
                import_file(output_file)
            else:
                print("Converted: {} ({} messages)".format(output_file, msg_count), file=sys.stderr)

    finally:
        if use_tempdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _convert_all(
    conv_dirs: List[Path], working_dir: str, output_dir: Path, opencode_version: str,
) -> List[Path]:
    """Convert a list of conversation directories. Returns list of output files."""
    files = []
    for conv_dir in conv_dirs:
        conv_id = conv_dir.name
        print("  Converting: {}...".format(conv_id), file=sys.stderr)

        result = process_conversation_dir(conv_dir, working_dir, opencode_version)
        if result is None:
            continue

        output_file = output_dir / "cursor-{}.json".format(conv_id[:8])
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        msg_count = len(result["messages"])
        print("    {} messages".format(msg_count), file=sys.stderr)
        files.append(output_file)
    return files


if __name__ == "__main__":
    main()
