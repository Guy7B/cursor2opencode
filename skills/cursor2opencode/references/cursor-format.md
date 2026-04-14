# Cursor Transcript Format

## Directory structure

Cursor stores agent conversations under `~/.cursor/projects/`:

```
~/.cursor/projects/
  <project-name>/                    # dash-encoded path (e.g. Users-guy-myproject)
    agent-transcripts/
      <uuid>/                        # conversation directory
        <uuid>.jsonl                 # main transcript
        subagents/
          <uuid>.jsonl               # background agent transcripts
```

## Project name encoding

Cursor encodes the project filesystem path by replacing `/` with `-`. For example:
- `/Users/guy/projects/myapp` becomes `Users-guy-projects-myapp`

This is lossy for paths containing actual dashes. The script resolves ambiguity by greedy left-to-right filesystem checking.

## JSONL line format

Each line in a `.jsonl` file is a JSON object:

```json
{
  "role": "user" | "assistant",
  "message": {
    "content": [
      {"type": "text", "text": "..."},
      {"type": "tool_use", "name": "Shell", "input": {"command": "ls"}}
    ]
  }
}
```

## User messages

User text is wrapped in XML tags:

```
<user_query>
What the user actually typed
</user_query>
```

The rest of the content (before the `<user_query>` tag) is Cursor's injected context (file contents, selection, etc.) and is stripped during import.

## Tool calls

Tool use entries record the tool name and input, but **not the output**. Cursor tool names map to OpenCode equivalents:

| Cursor | OpenCode |
|--------|----------|
| Shell  | bash     |
| Write  | write    |
| Read   | read     |
| Grep   | grep     |
| Glob   | glob     |
| Edit   | edit     |
| ApplyPatch | edit |

## Subagents

Background agent tasks are stored in the `subagents/` subdirectory as separate `.jsonl` files. Each follows the same format as the main transcript.
