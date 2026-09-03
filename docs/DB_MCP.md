# Postgres MCP server (dbhub) — setup note

## Why

From a Claude Code insights report: I was seeding a fake competition into production
Postgres and debugging against real Supabase, but doing all of it through Bash calls —
739 of them. Every schema lookup and every query went out through a shell command.

A Postgres MCP server gives Claude direct access to the database instead: it can inspect
the schema and run queries as first-class tool calls, rather than shelling out blindly and
parsing whatever comes back.

## Setup

### 1. Connection string in `~/.bashrc`

dbhub is a Node program and expects a plain `postgresql://` scheme. If `DATABASE_URL` uses
SQLAlchemy's dialect syntax (`postgresql+psycopg://`) for the Python app, derive a second
variable for dbhub rather than changing the original:

```bash
export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/db"
export DBHUB_DSN="${DATABASE_URL/postgresql+psycopg:/postgresql:}"
```

Open a new shell afterwards. Claude Code reads these from the environment it is launched
in, so they have to be exported in `.bashrc`, not just set in the current session.

### 2. Add the server at project scope

```bash
cd ~/projects/livescoring
claude mcp add --scope project --transport stdio db \
  -- npx -y @bytebase/dbhub --dsn '${DBHUB_DSN}'
```

Single quotes matter — they keep `${DBHUB_DSN}` as a placeholder in the config instead of
letting bash expand it. Claude Code resolves it at server startup.

`--scope project` writes `.mcp.json` in the project root. Other scopes:

| Scope | Loads in | Stored in |
|---|---|---|
| `local` (default) | current project only, private | `~/.claude.json` |
| `project` | current project, shareable | `.mcp.json` in project root |
| `user` | all projects, private | `~/.claude.json` |

### 3. Verify

```bash
claude mcp get db     # should show Status: ✔ Connected
claude mcp list       # flags unresolved ${VAR} references
```

Inside a session, `/mcp` confirms the tools loaded. First run in the project prompts for
approval of the `.mcp.json` server — expected.

To test dbhub on its own, outside Claude Code:

```bash
npx -y @bytebase/dbhub --dsn "$DBHUB_DSN"
```

No output and a hung terminal means it connected — that's a stdio server waiting for a
client. Ctrl-C out.

## Notes for next time

- Use a read-only Postgres role in the DSN. Whatever Claude runs through the MCP server
  executes with those privileges, and this points at production.
- `.mcp.json` is safe to commit because it holds `${DBHUB_DSN}`, not the resolved string.
  Each machine supplies its own value.
- Removing: `claude mcp remove db -s project` (match the scope it was added at).
- `claude mcp get` prints args exactly as stored, so seeing `${DBHUB_DSN}` there says
  nothing about whether it resolved. `claude mcp list` is what warns about missing
  variables.