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
    bin/router stats [--days 7]     routing and verified results, summarized

The decision log keeps its live file and five rotated ones, at most about
30 MB in all. `stats` reads all six; `log` reads the live file.

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

## Verification

Set `verify.command` in the config, for example `"python -m pytest -q"`,
and the router checks each routed write task (by default `implement` and
`debug`) once it ends. The command runs in the background in the session's
directory, so it never holds up the session. Exit 0 is a pass, any other
exit a fail, and a timeout or a command the shell cannot run is unknown.

![When a subagent finishes in the foreground, the hook finds its decision in decisions.jsonl. If verify.command is set and the launch was a routed write task by an agent that can write, it runs the command, reduces the exit to pass, fail, or unknown, asks outcome.judge for the next step, and appends an outcome record that router stats reads.](docs/diagrams/verify.png)

The result, and what the router would do next, is logged as an `outcome`
record: `pass`, `retry` the same target, `escalate` to the next heavier
target allowed at the current quota levels, or `human` when none is left.
This step only records. Nothing is retried, and no launch or answer is
changed, in any mode. `bin/router log` shows these records as
`verify pass`, `verify fail`, or `verify unknown`, and `bin/router stats`
gives the pass rate and median task time for each category and model, and
how often each next step would have been taken.

In shadow mode the logged target is the one the router would have used,
and `resolved_model` is the model that actually ran; `stats` counts the
model that ran, marked `*`. Tests run on the
working tree as it is when the task ends, so other work in progress can
affect the result.

## Architecture

The router is four thin hook adapters around a decision core of pure
functions. The adapters do the reading and writing; the core takes plain
values and returns a decision, so it can be tested without Claude Code.

![Each Claude Code event reaches one hook adapter: the status line saves quota, a prompt can get one line of guidance, a subagent launch is classified and routed, and a finished subagent is verified. Launch decisions and verified results go to decisions.jsonl, which router stats and router log read.](docs/diagrams/architecture.png)

Read it top to bottom, one box per Claude Code event. At a launch, the core
picks a target from the config, both plans' quota, and the permission rules,
and the hook applies it unless the mode is shadow. When a subagent finishes,
the post-launch hook runs your verify command in the background and logs
what should come next. Both write to `decisions.jsonl`, which `router stats`
and `router log` read. The status line takes no decisions: it saves the
quota reading the next decision will use, and passes its input on to your
previous status-line command.

| Part | Files | Job |
|---|---|---|
| Entry | `bin/router`, `entry.py` | Sends hooks and the status line straight to their handlers, loading as little as possible. Everything else goes to `cli.py`. |
| Hook adapters | `hooks/pre_agent.py`, `hooks/prompt.py`, `hooks/statusline.py`, `hooks/post_agent.py` | Read the hook input, call the core, print the hook response. Any failure prints nothing, so the launch proceeds untouched. `post_agent` runs in the background and only logs. |
| Inputs | `config.py`, `quota/`, `hooks/permissions.py` | The config (valid, absent, or invalid), both plans' usage windows, and whether file-scoped deny or ask rules exist. |
| Decision core | `classify.py`, `policy.py`, `outcome.py` | Name the task's category, set each plan's level, check redirect eligibility, and pick a target; after a verified launch, pick what comes next. No I/O. |
| Codex path | `grants.py`, `codex_run.py`, `agents/` | Carry a redirected task to Codex inside a sandbox the hook fixed. |
| Records | `decision_log.py`, `state.py`, `stats.py` | One log line per decision or verified result, what each session has already been told, and the summary `router stats` prints. |

### How one launch is routed

`policy.decide()` applies these steps in order. A Codex target that is not
eligible is skipped, never downgraded to a weaker sandbox.

![The routing steps: keep a passthrough or route:keep launch; set each plan's level; use an allowed tagged target; otherwise the first preference entry its level allows, promoting the other plan's later entry when this plan is over its week and the other underused; if none, the lightest allowed target or a deny. A Claude target sets the model, a Codex target redirects to a worker.](docs/diagrams/routing.png)

A plan's level is the worst of its 5-hour and weekly windows. Heavy targets
stop at conserve, medium at critical, and light at exhausted. In shadow mode
the decision is logged and nothing is changed.

### How a Codex redirect stays inside its sandbox

Eligibility is checked once, in the hook. A one-time grant carries that
approval to the point where Codex starts, so neither the prompt text nor the
worker can ask for a wider sandbox, and a worker launched directly has
nothing to run with.

![The pre_agent hook decides, writes a one-time grant, and rewrites the launch so the prompt carries only the nonce. The worker runs codex-run, which redeems the grant: if it is missing, used, stale, or for another mode, it refuses and Codex never starts; otherwise codex exec runs with the grant's sandbox and directory and the answer comes back verbatim.](docs/diagrams/codex-redirect.png)

A launch is eligible for redirect only when its agent type is on the
redirectable list, it was not launched from inside another subagent, the
session's permission mode allows that sandbox, and no file-scoped deny or ask
rules exist. Anything unknown blocks the redirect; the task then stays on
Claude and only its model can change.

## Limits

- It routes subagent launches. It cannot change the main conversation's
  model; it suggests a `/model` switch when a plan's level changes.
- A task goes to Codex only when your session's permission mode allows it
  and no file-scoped deny rules exist. See `read_redirect_modes` and
  `write_redirect_modes`.
- Inside a Codex run, Claude Code's Bash rules do not apply to the commands
  Codex runs. The Codex sandbox is the boundary.
- That sandbox does not protect the paths Claude Code guards in every mode,
  such as `.claude/` and `.mcp.json`. A write redirect could edit them where
  Claude Code would have asked first. Be deliberate about which modes you
  list in `write_redirect_modes`.
- Routed Codex runs ignore `~/.codex/config.toml`, so its plugins and
  settings do not apply to them. To choose the Codex model, add `"model"` to
  a Codex target; otherwise Codex uses its built-in default.
- `verify.command` runs from a hook, so Claude Code's permission prompts
  and Bash rules do not apply to it. It runs in every project where a
  routed write task ends, and a test suite runs that project's code. Set it
  only if you would let every such project run that command.
- `router codex-run` is internal. It starts Codex only for a launch the hook
  redirected, once, within 30 minutes; run by hand it refuses.
- Codex's quota on disk updates only when Codex runs.

## Development

    /usr/bin/python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
    .venv/bin/python -m pytest

Runtime code uses the Python 3.9 standard library only.

The diagrams are Mermaid sources in `docs/diagrams/*.mmd`, shown in this
README as PNGs because GitHub's mobile app does not render Mermaid. After
editing a source, regenerate the images with `docs/diagrams/render.js`; the
steps are at the top of that file.
