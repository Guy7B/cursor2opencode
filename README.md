# cursor2opencode

An [Agent Skill](https://agentskills.io) that imports [Cursor](https://cursor.sh) agent conversation history into [OpenCode](https://opencode.ai).

## What it does

- Auto-discovers all Cursor workspaces and agent transcripts
- Converts Cursor JSONL transcripts to OpenCode's session format
- Preserves user messages, assistant responses, tool calls (with inputs), and subagent conversations
- Imports directly via `opencode import` (temp files cleaned up automatically)

## Install

### npx (recommended)

```bash
npx skills add Guy7B/cursor2opencode -g
```

This works across all agents that support the [Agent Skills](https://agentskills.io) standard -- OpenCode, Claude Code, Gemini CLI, Cursor, VS Code Copilot, and more.

To install for a specific project instead of globally:

```bash
npx skills add Guy7B/cursor2opencode
```

### Manual install

Clone and copy the skill directory into your agent's skills folder:

```bash
# OpenCode
git clone https://github.com/Guy7B/cursor2opencode.git /tmp/cursor2opencode
cp -r /tmp/cursor2opencode/skills/cursor2opencode ~/.config/opencode/skills/

# Claude Code
git clone https://github.com/Guy7B/cursor2opencode.git /tmp/cursor2opencode
cp -r /tmp/cursor2opencode/skills/cursor2opencode ~/.claude/skills/

# Gemini CLI
git clone https://github.com/Guy7B/cursor2opencode.git /tmp/cursor2opencode
cp -r /tmp/cursor2opencode/skills/cursor2opencode ~/.gemini/skills/
```

## Usage

### Natural language

Just ask your agent:

```
import my cursor conversations
```

The agent will discover the skill and use it.

### Direct CLI

```bash
# Preview what's available:
python3 ~/.config/opencode/skills/cursor2opencode/scripts/cursor2opencode.py --discover

# Import everything:
python3 ~/.config/opencode/skills/cursor2opencode/scripts/cursor2opencode.py

# Import a specific workspace:
python3 ~/.config/opencode/skills/cursor2opencode/scripts/cursor2opencode.py \
  ~/.cursor/projects/<project>/agent-transcripts/ --all

# Convert only (save JSON files without importing):
python3 ~/.config/opencode/skills/cursor2opencode/scripts/cursor2opencode.py \
  --no-import --output ./converted
```

## Requirements

- Python 3.7+ (standard library only -- no pip install)
- [OpenCode](https://opencode.ai) CLI (`opencode import` must be available)
- Cursor IDE with agent transcripts at `~/.cursor/projects/`

## Project structure

```
cursor2opencode/
  skills/
    cursor2opencode/                  # The skill (Agent Skills standard)
      SKILL.md                        # Entry point
      scripts/
        cursor2opencode.py            # Main conversion script (Python)
      references/
        cursor-format.md              # Cursor transcript format documentation
  README.md
  LICENSE
```

This follows the [Agent Skills specification](https://agentskills.io/specification).

## Limitations

- **Tool outputs are not preserved.** Cursor transcripts only record tool names and inputs, not outputs. Imported tool calls show a placeholder.
- **Path resolution is best-effort.** Cursor encodes project paths by replacing `/` with `-`. The script resolves this by checking the filesystem, but can't recover deleted directories.
- **Re-importing is safe.** OpenCode deduplicates by session/message ID, so running the import again won't create duplicates.

## License

MIT
