---
name: cursor2opencode
description: >-
  Import Cursor IDE agent conversation history into OpenCode (or any compatible agent).
  Auto-discovers Cursor workspaces, converts JSONL transcripts to the agent's session format,
  and imports them. Use when the user wants to migrate, import, copy, or bring over their
  Cursor conversations, chat history, or agent transcripts.
license: MIT
compatibility: Requires Python 3.7+ and OpenCode CLI (opencode import)
metadata:
  author: Guy7B
  version: "0.1.0"
  category: migration
  source: cursor
---

# Import Cursor Conversations

This skill imports Cursor IDE agent conversation history into OpenCode.

## When to use

Activate this skill when the user mentions any of:
- Importing Cursor conversations, history, or sessions
- Migrating chat history from Cursor to OpenCode
- Bringing over old Cursor agent transcripts
- Switching from Cursor and wanting their history preserved

## Step 1: Preview available conversations

Run the discovery script to show the user what Cursor conversations are available:

```bash
python3 scripts/cursor2opencode.py --discover
```

Show the user the output. Ask if they want to import all conversations or only specific workspaces.

## Step 2: Import

To import all discovered conversations:

```bash
python3 scripts/cursor2opencode.py
```

To import a specific workspace:

```bash
python3 scripts/cursor2opencode.py ~/.cursor/projects/<project-name>/agent-transcripts/ --all
```

To import a single conversation:

```bash
python3 scripts/cursor2opencode.py ~/.cursor/projects/<project-name>/agent-transcripts/<uuid>/
```

To convert without importing (save JSON files only):

```bash
python3 scripts/cursor2opencode.py --no-import --output ./converted
```

## Step 3: Verify

After import, confirm the results by listing imported sessions:

```bash
opencode session list --format json 2>/dev/null | python3 -c "
import sys, json
sessions = json.load(sys.stdin)
cursor = [s for s in sessions if '[Cursor]' in s.get('title', '')]
print(f'{len(cursor)} Cursor sessions imported into OpenCode')
"
```

## How it works

- Cursor stores agent transcripts at `~/.cursor/projects/<project>/agent-transcripts/`
- Each conversation is a UUID directory with a `.jsonl` file (one JSON object per line)
- Subagent tasks are in a `subagents/` subdirectory
- The script converts these to OpenCode's session JSON format and calls `opencode import`
- Tool outputs are not captured in Cursor transcripts (only tool names and inputs)
- Re-importing is safe -- OpenCode deduplicates by session/message ID

See [references/cursor-format.md](references/cursor-format.md) for details on Cursor's transcript format.

## Troubleshooting

- If no workspaces are found, check that `~/.cursor/projects/` exists and has content
- If `opencode import` fails, verify OpenCode is installed and on your PATH
- The script requires only Python 3.7+ standard library -- no pip install needed
