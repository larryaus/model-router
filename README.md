# Model Router

A Claude Code plugin that decides, for each subagent launch, which model
should do the work: a Claude model on your Claude plan, or Codex on your
ChatGPT plan. It decides from your preference list and how much of each
plan's allowance is left.

Design: `docs/superpowers/specs/2026-10-09-model-router-design.md`

## Commands

    bin/router status               quota, level per plan, config state
    bin/router explain "<task>"     dry-run a decision
    bin/router log -n 20            recent decisions and why

## Configuration

Copy `config.example.jsonc` to `~/.config/model-router/config.jsonc`.

The router changes launches only when that file exists, is valid, and sets
`"mode": "enforce"`. With no file it runs in shadow mode: it logs what it
would do and changes nothing. With an invalid file it does nothing at all;
`bin/router status` lists the errors.

## Overrides

- `[route:opus]`, `[route:codex]`, or any target name, in a task prompt:
  use that target. Beats conserve and critical, not exhausted.
- `[route:keep]` in a task prompt: leave this launch alone.
- `MODEL_ROUTER=off` in the environment: off for that session.
- `"mode": "shadow"` or `"off"` in the config: off everywhere.

## Limits

- It routes subagent launches. It cannot change the main conversation's
  model; it suggests a `/model` switch when a plan's level changes.
- A task goes to Codex only when your session's permission mode allows it
  and no file-scoped deny rules exist. See `read_redirect_modes` and
  `write_redirect_modes`.
- Inside a Codex run, Claude Code's Bash rules do not apply to the commands
  Codex runs. The Codex sandbox is the boundary.
- Codex's quota on disk updates only when Codex runs.

## Development

    /usr/bin/python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python -m pytest

Runtime code uses the Python 3.9 standard library only.
