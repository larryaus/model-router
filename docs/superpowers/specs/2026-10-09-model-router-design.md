# Model Router: Design

Date: 2026-10-09
Status: Draft for review

## 1. Purpose

A personal model router that runs inside Claude Code and spreads delegated
work across two flat-rate subscriptions: a Claude plan (used through Claude
Code) and a ChatGPT plan (used through the Codex CLI).

It is for one person on one Mac. It exists to solve three problems, in this
priority order:

1. **Do not get blocked.** Shift work to a cheaper Claude model or to Codex
   before a usage window fills, instead of after.
2. **Best model per task.** Send each kind of task to the model the owner
   prefers for it, with no manual switching.
3. **Use both plans.** When one plan is heading for its limit and the other is
   going to waste, move work toward the underused one.

### Success criteria

- With the router enforcing, every subagent launch is logged with the target
  chosen and the reason.
- While all quota is normal, each launch goes to the first entry of its
  category's preference list.
- When a plan reaches `conserve` or worse, no launch is sent to a target that
  level disallows, unless the owner tagged the task with an explicit override.
- A routing decision adds under 100 ms to a launch in the warm case.
- No router failure of any kind blocks or alters a launch. A missing or
  invalid configuration counts as a failure for this purpose.
- A redirect to Codex never gives a task wider file access than the session
  that launched it had.

### What was stated and what is assumed

Stated by the owner: runs inside Claude Code; the two plans above; the
priority order; hook-enforced deterministic routing; Python; independent of the
Quota Glass repository.

Assumed, and open to correction: preferences live in a hand-edited config file;
routing is automatic with a logged reason and a per-task override; local-only
with no network calls.

## 2. Constraints and non-goals

**Constraints**

- Only the official clients are used, each with its own login. The router never
  reads, stores, or forwards credentials.
- Claude Code exposes subscription quota only through the status-line input.
  Hook inputs carry no quota fields.
- No hook can change the main session's model. The router enforces routing on
  subagent launches and can only recommend a change for the main conversation.
- If the Claude plan is fully exhausted, Claude Code stops and the router stops
  with it. The router's job is to shift load early enough to avoid that.
- No runtime dependency on the `just-for-fun` (Quota Glass) repository.

**Non-goals**

- Proxying or rewriting raw API calls. Pay-per-token API keys as a backend.
- Providers other than Claude and Codex.
- A classifier that calls a model to decide routing.
- A dashboard, a server, a daemon, or a database.
- Learning preferences from history.

## 3. Architecture

```
Claude Code session
├─ status line ──► statusline.py ──► claude-quota.json   (Claude 5h + weekly)
│                                    ~/.codex/sessions/  (Codex 5h + weekly)
│                                             │
├─ prompt submitted ──► prompt hook ──────────┤ one line of guidance on
│                                             ▼ a level change
│                                     ┌───────────────┐
└─ subagent launch ──► pre-launch ───►│   decide()    │◄── config.jsonc
                         hook         │ pure function │
     │                                └───────────────┘
     ├─ keep                                  │
     ├─ set model (opus / sonnet / haiku)     └──► decisions.jsonl
     ├─ redirect to a Codex worker ──► codex exec
     └─ deny with a reason
```

Six units. Each has one job and a narrow interface.

### 3.1 Quota readers (`model_router/quota/`)

Turn each plan's on-disk data into one shape:

```python
Window(used_pct: float, window_minutes: int, resets_at: int)
PlanQuota(plan: str, windows: List[Window], captured_at: Optional[int],
          known: bool)
```

- `claude.py` reads the router's own snapshot, `claude-quota.json`, written by
  the status-line script. Windows: `five_hour` (300 minutes) and `seven_day`
  (10080 minutes).
- `codex.py` finds the most recent `rate_limits` object in the Codex session
  logs under `~/.codex/sessions/`. It examines up to the 20 most recently
  modified `rollout-*.jsonl` files and reads only the last 256 KB of each.
  `primary` and `secondary` map to the two windows.

Rules shared by both readers:

- Usage only rises within a window, so an old reading is a valid lower bound.
  Readings are never discarded for age.
- A window whose `resets_at` is in the past counts as 0% used.
- `known` is false only when no reading exists or the source cannot be parsed.

Readers take their source paths as arguments. They do no other I/O.

### 3.2 Classifier (`model_router/classify.py`)

`classify(tool_input, config) -> Classification(category, source, override)`.
Pure. Rules apply in this order and the first match wins:

1. A tag in the task prompt: `[route:<target-name>]` or `[route:keep]`. This
   sets `override`.
2. The agent type, looked up in the config's `agent_categories` map.
3. Keywords from the config's `keywords` map, matched case-insensitively as
   whole words or phrases against the launch `description`, then the first 500
   characters of the prompt. Categories are tested in the order review, debug,
   plan, explore, implement.
4. Otherwise `default`.

Categories: `plan`, `implement`, `review`, `explore`, `debug`, `default`.

### 3.3 Policy engine (`model_router/policy.py`)

`decide(classification, launch, quotas, config, now) -> Decision`.
Pure: no I/O, and time is passed in. Section 4 defines its behaviour.

```python
Launch(agent_type: str,
       permission_mode: Optional[str],   # from the hook input; None if absent
       nested: bool,                     # launched from inside a subagent
       file_deny_rules: Optional[bool])  # None if settings were unreadable
```

```python
Decision(action: str,            # keep | set_model | redirect_codex | deny
         target: Optional[str],  # target name from config
         reason: str,            # one human-readable line
         levels: Dict[str, str]) # plan -> level at decision time
```

### 3.4 Hook adapters (`model_router/hooks/`)

Thin glue. Each reads hook JSON on stdin, calls the units above, and prints a
hook response.

- `pre_agent.py`: the `PreToolUse` hook, matched on the `Agent` tool, whose
  input carries `subagent_type`, `model`, `description`, and `prompt`. It
  applies the decision through `hookSpecificOutput.updatedInput`, which it
  always sends as the complete tool input with its changes applied, or through
  a `deny` permission decision. It builds the `Launch` value from the hook
  input: `permission_mode`, whether `agent_id` is present (a nested launch),
  and `cwd`.
- `permissions.py`: a helper for `pre_agent.py`. Given `cwd`, it reports
  whether any `permissions.deny` rule scoped to `Read`, `Edit`, or `Write`
  exists in the user, project, project-local, or managed Claude Code settings
  files. It returns `None` if any of those files exists but cannot be parsed.
- `prompt.py`: the `UserPromptSubmit` hook. It injects one line through
  `additionalContext` when a plan's level differs from the level last announced
  in this session.
- `statusline.py`: the status-line command. It saves the quota fields and then
  runs the configured passthrough command.

### 3.5 Codex workers (`agents/`)

Two forwarding agents shipped with the plugin: `codex-read` (read-only sandbox)
and `codex-write` (workspace-write sandbox). Each writes the task to a prompt
file, runs one `codex exec` in the foreground, and returns Codex's final
message verbatim. Both declare `model: haiku` so the wrapper itself costs
almost no Claude quota.

The pre-launch hook passes the reasoning effort to the worker as a first line
of the prompt, `ROUTER_EFFORT: <effort>`, which the worker strips and passes to
Codex.

Each worker fixes its own boundary and does not inherit one from Codex's
configuration:

- It passes `--sandbox` explicitly on every run: `read-only` for `codex-read`,
  `workspace-write` for `codex-write`.
- It passes `--cd` with the session's working directory, so writes are confined
  to that workspace.
- It never passes a flag that widens or bypasses the sandbox, and never adds
  extra writable directories.
- Its `codex exec` call is an ordinary Bash call. It goes through Claude Code's
  permission system like any other command, so the session's allow, ask, and
  deny rules for Bash still apply to it.

### 3.6 CLI (`bin/router`)

- `router status`: both plans' windows, level, and projection; config
  validation result; current mode.
- `router explain "<task text>" [--agent <type>]`: dry-run a decision and print
  the reasoning. Changes nothing.
- `router log [-n N]`: recent decisions.

## 4. Routing policy

### 4.1 Level per plan

For each window: `elapsed = 1 - (resets_at - now) / window_seconds`, clamped to
0..1. `projected = used_pct / elapsed` when `elapsed >= warmup` (default 0.15),
otherwise undefined.

Each window gets a level, and the plan's level is the worst of its windows:

| Level | Condition | Target weights allowed on the plan |
|---|---|---|
| normal | none of the below | heavy, medium, light |
| conserve | `used >= 70`, or `projected >= 100` | medium, light |
| critical | `used >= 90` | light |
| exhausted | `used >= 98` | none |

A plan whose quota is not `known` is treated as `normal`, and the decision's
reason says so.

### 4.2 Preference list

Each category has an ordered list of target names. A target names a plan, a
model or effort, and a weight. The engine walks the list and picks the first
target that passes both checks:

- its weight is allowed at its plan's current level;
- if it is on the Codex plan, the launch is eligible for redirect (see 4.4).

A Codex target that fails the second check is skipped, never downgraded. The
engine moves on to the next entry, so the task stays on Claude with its
original permissions and only its model can change.

If no entry passes, the engine considers every defined target subject to the
same two checks and picks the one with the lightest weight, breaking ties by
the order targets appear in the config. If nothing passes, the action is
`deny`, and
the reason carries the earliest reset time so the main agent can do the work
inline or wait.

### 4.3 Balance

Applied only after 4.2 has chosen a target, and only using the weekly windows.
If the chosen target's plan has `projected > overused` (default 85) and a later
entry in the same list passes both checks and sits on a plan with
`projected < underused` (default 60), the engine promotes that entry. Both
projections must be defined. Balance never selects a target that 4.1 or 4.2
would reject.

### 4.4 Rules that protect correctness

- **Passthrough.** Agent types listed in `passthrough`, the router's own
  workers, and `fork` launches are never altered. This also prevents redirect
  loops.
- **Which worker a launch needs.** A launch needs `codex-write` when its
  category is in `write_categories` (default: `implement`, `debug`, `default`)
  and its agent type is not in `readonly_agents` (default: `Explore`, `Plan`,
  `explorer`). Otherwise it needs `codex-read`. A read-only agent type never
  gets the write worker.
- **Redirect eligibility.** A redirect moves a task out of Claude Code's
  permission system and into Codex's sandbox, so it is allowed only when the
  sandbox's file access is no wider than what the session already permits. A
  launch is eligible only if all of these hold:
  1. *Agent type.* It is listed in `redirectable` (default: `general-purpose`,
     `claude`, `Explore`, `Plan`, `explorer`). A custom agent's behaviour and
     tool limits come from its own definition, which a redirect would discard.
  2. *Not nested.* It was launched by the main conversation. A launch made
     from inside another subagent inherits that subagent's tool limits, which
     the hook cannot see.
  3. *Permission mode.* The session's `permission_mode` is listed in
     `read_redirect_modes` if the launch needs `codex-read`, or in
     `write_redirect_modes` if it needs `codex-write`. See the table below.
  4. *No file-scoped deny rules.* No `Read`, `Edit`, or `Write` deny rule
     exists in the Claude Code settings that apply to the session. Codex
     cannot honour per-path rules, so their presence rules out any redirect.

  Anything unknown fails the check: a missing or unrecognised
  `permission_mode`, or settings that could not be read. A launch that fails
  any check still receives Claude model changes.

  | `permission_mode` | `codex-read` | `codex-write` |
  |---|---|---|
  | `plan` | no | no |
  | `default`, `auto` | yes | no |
  | `acceptEdits`, `bypassPermissions` | yes | yes |
  | `dontAsk`, missing, or unrecognised | no | no |

  These are the defaults of `read_redirect_modes` and `write_redirect_modes`.
  `plan` may not be added to either list. Adding a mode to
  `write_redirect_modes` is the owner stating that unattended edits inside the
  workspace sandbox are acceptable in that mode.
- **Overrides.** `[route:<target>]` replaces the preference list with that one
  target and skips balance. It ignores `conserve` and `critical`, because the
  owner asked for that target explicitly. It still obeys `exhausted` and
  redirect eligibility; if either rejects it, routing falls back to the normal
  preference list and the reason says why. `[route:keep]` leaves the launch
  untouched.

### 4.5 Applying a decision

| Action | Effect on the launch |
|---|---|
| keep | No output. The launch proceeds unchanged. Produced by passthrough, `[route:keep]`, `"mode": "off"`, and an invalid config. |
| set_model | `model` is set to the target's model. |
| redirect_codex | `subagent_type` becomes the plugin's `codex-read` or `codex-write`, `model` is removed, and the effort line is prepended to the prompt. |
| deny | The launch is denied with the reason. |

In `shadow` mode the decision is computed and logged, and the launch always
proceeds unchanged.

### 4.6 Main-session guidance

The prompt hook keeps the last announced level per plan, per session, in
`state.json`. When a level differs from the announced one, it injects one line
and records the new level. Example:

```
Router: Claude 5h 74% (resets 14:20), weekly 41%. Codex 5h 12%. Claude is in
conserve: heavy Claude targets are paused. Consider /model sonnet.
```

Nothing is injected while levels are unchanged.

## 5. Configuration

File: `~/.config/model-router/config.jsonc`. JSON with `//` and `/* */`
comments.

The router alters launches only when this file exists, passes validation, and
sets `"mode": "enforce"`. The three possible states:

| Config file | Behaviour |
|---|---|
| Valid | As written. Keys left out take the defaults shown below, except `mode`, whose default is `shadow`. |
| Absent | Built-in defaults in `shadow` mode: decisions are logged, nothing is altered. |
| Invalid (syntax error, failed validation, or unknown key) | Inert. Every launch is kept unchanged, whatever `mode` the file asks for. See section 7. |

The example below shows every key with its default value, apart from `mode`,
which is set to `enforce` here to show a working configuration.

```jsonc
{
  "mode": "enforce",            // "enforce" | "shadow" | "off"; default "shadow"
  "targets": {
    "opus":       { "plan": "claude", "model": "opus",   "weight": "heavy"  },
    "sonnet":     { "plan": "claude", "model": "sonnet", "weight": "medium" },
    "haiku":      { "plan": "claude", "model": "haiku",  "weight": "light"  },
    "codex-deep": { "plan": "codex",  "effort": "xhigh",  "weight": "heavy"  },
    "codex":      { "plan": "codex",  "effort": "medium", "weight": "medium" }
  },
  "preferences": {
    "plan":      ["opus", "codex-deep", "sonnet"],
    "implement": ["codex-deep", "sonnet", "codex"],
    "review":    ["codex-deep", "opus", "sonnet"],
    "explore":   ["haiku", "codex", "sonnet"],
    "debug":     ["opus", "codex-deep", "sonnet"],
    "default":   ["sonnet", "codex"]
  },
  "thresholds": { "conserve": 70, "critical": 90, "exhausted": 98 },
  "projection": { "warmup": 0.15 },
  "balance":    { "overused": 85, "underused": 60 },
  "agent_categories": { "Explore": "explore", "explorer": "explore",
                        "Plan": "plan" },
  "keywords": {
    "review":    ["review", "audit", "critique"],
    "debug":     ["debug", "root cause", "failing", "flaky", "stack trace"],
    "plan":      ["plan", "design", "architecture"],
    "explore":   ["find", "locate", "search", "where is", "trace how"],
    "implement": ["implement", "add", "build", "refactor", "fix", "write"]
  },
  "passthrough": ["codex-review", "design-review", "implementer",
                  "statusline-setup", "claude-code-guide"],
  "redirectable": ["general-purpose", "claude", "Explore", "Plan", "explorer"],
  "readonly_agents": ["Explore", "Plan", "explorer"],
  "write_categories": ["implement", "debug", "default"],
  "read_redirect_modes":  ["default", "auto", "acceptEdits", "bypassPermissions"],
  "write_redirect_modes": ["acceptEdits", "bypassPermissions"],
  "statusline": { "passthrough": null }  // command to run after capture
}
```

`config.py` validates the file by hand and reports each problem with its key
path. Validation checks: no unknown keys at any level, so a mistyped key is an
error and not a silent default; `mode` is one of the three values; every
preference entry names a defined target; every target has a known plan and
weight; thresholds are ordered `conserve < critical < exhausted`;
`underused < overused`; both redirect-mode lists contain only known permission
modes and never `plan`; `write_redirect_modes` is a subset of
`read_redirect_modes`.

`config.py` returns one of three results, matching the table above: a valid
config, the built-in shadow config, or an invalid marker carrying the list of
errors. It never returns a partially applied file.

Environment: `MODEL_ROUTER=off` disables routing for a session.
`MODEL_ROUTER_CONFIG` and `MODEL_ROUTER_STATE_DIR` override the file locations,
which is how tests avoid touching real files.

## 6. Files and state

| Path | Contents |
|---|---|
| `~/.config/model-router/config.jsonc` | Configuration |
| `~/Library/Caches/model-router/claude-quota.json` | `captured_at` and the two Claude windows. Nothing else from the status-line input is stored. |
| `~/Library/Caches/model-router/state.json` | Last announced level per plan, per session |
| `~/Library/Caches/model-router/decisions.jsonl` | One line per decision |

A decision record holds: timestamp, session id, agent type, category, how it
was classified, action, target, reason, each plan's level, and mode. It never
holds prompt text. The log rotates to `decisions.jsonl.1` at 5 MB.

All writes are atomic: write a temporary file in the same directory, then
rename. Files are created with owner-only permissions.

## 7. Failure handling

- **Fail open.** Every hook entry point wraps its work. On any exception it
  appends the error to `decisions.jsonl`, prints nothing, and exits 0, so the
  launch or prompt proceeds unchanged. If writing the log also fails, the
  error is dropped and the hook still exits 0.
- **Unknown quota.** Treated as `normal`, stated in the reason.
- **Invalid config.** The router goes inert: the pre-launch hook keeps every
  launch unchanged and logs one `config_invalid` record per session. Built-in
  defaults are not applied, because they could redirect work that a broken
  `shadow` or `off` config was meant to leave alone. `router status` prints the
  validation errors, and the prompt hook announces the problem once per
  session. Quota capture by the status line keeps working.
- **Unknown permission state.** A missing or unrecognised `permission_mode`,
  or Claude Code settings that cannot be parsed, makes the launch ineligible
  for redirect. Claude model changes still apply.
- **Codex worker failure.** The worker returns Codex's error verbatim. The
  router does not retry on Claude, so no task is paid for twice silently.
- **Passthrough status line.** Run with a 2-second timeout. On failure or
  timeout the router prints an empty status line and still saves the snapshot.
- **Time budget.** Hooks declare a 5-second timeout. The decision path reads a
  handful of small files and the tail of at most 20 logs.

## 8. Testing

Runtime code uses the standard library only. Tests use `pytest` in a project
virtual environment.

- **Policy engine.** Table-driven tests over quota, config, and task. Cases:
  each threshold boundary, a passed reset, projection before and after warmup,
  fallback to the lightest target, deny, balance promotion and its guards, tag
  overrides, passthrough, and the read-only rule.
- **Redirect eligibility.** One case per row of the permission-mode table, for
  both workers. Nested launches, non-redirectable agent types, file-scoped deny
  rules present, and unreadable settings each block a redirect. In every
  blocked case the test asserts that the result is a Claude target or `keep`,
  never a read worker standing in for a write worker.
- **Permissions helper.** Settings fixtures with no deny rules, a `Bash`-only
  deny rule (does not block), a `Read` deny rule (blocks), and malformed JSON
  (returns `None`).
- **Classifier.** Tag, agent type, keyword order, and default.
- **Quota readers.** Fixture files for valid, empty, truncated, and malformed
  input. A large log file to confirm only the tail is read.
- **Config.** Comment stripping, defaults, and each validation error. The
  three load results: valid, absent (shadow), and invalid (inert).
- **Hook adapters.** Feed hook JSON on stdin and assert on stdout. Include a
  forced exception to prove fail-open, and `shadow` mode to prove nothing is
  rewritten. With a malformed config file, a config that fails validation, and
  a config with a mistyped `mode` key, assert that a launch which would
  otherwise be redirected produces no output at all. Repeat with no config
  file.
- **Isolation.** Tests never read `~/.claude`, `~/.codex`, or the real config.
  Paths are injected.
- **Shadow run.** Before enforcing, run in `shadow` mode on real work for
  several days and compare the log against the owner's own choices.

## 9. Technology

| Layer | Choice |
|---|---|
| Language | Python 3.9, standard library only at runtime |
| Interpreter | `/usr/bin/python3 -I` for every hook and the CLI. Isolated mode ignores environment variables and user site-packages, so no virtual environment can change what is imported. Each entry script adds the plugin root to `sys.path` itself. |
| Tests | `pytest`, development only |
| Config | JSONC, hand-written validator |
| Storage | Plain JSON and JSONL files |
| Integration | Claude Code plugin: `.claude-plugin/plugin.json`, `hooks/hooks.json`, `agents/`, `bin/router` |
| Backends | Claude Code subagents with a model override; `codex exec` |

Python 3.9 means no `X | Y` unions, no `tomllib`, and `typing.List`,
`Optional`, and `Dict` for annotations.

### Layout

```
model-router/
├─ .claude-plugin/plugin.json
├─ hooks/hooks.json
├─ agents/codex-read.md
├─ agents/codex-write.md
├─ bin/router
├─ model_router/
│  ├─ quota/{types,claude,codex}.py
│  ├─ classify.py
│  ├─ policy.py
│  ├─ config.py
│  ├─ decision_log.py
│  ├─ state.py
│  ├─ cli.py
│  └─ hooks/{pre_agent,prompt,statusline,permissions}.py
├─ config.example.jsonc
├─ tests/
└─ docs/superpowers/specs/
```

## 10. Build phases

Each phase leaves something usable.

1. **Quota readers and `router status`.** See both plans' windows and levels.
2. **Config, classifier, policy engine, `router explain`.** Dry-run decisions
   with no hooks installed. Includes redirect eligibility and the three config
   load results, and ships `config.example.jsonc`.
3. **Status-line capture and the pre-launch hook in `shadow` mode.** Install
   the plugin, point the status line at `statusline.py` with the existing
   Quota Glass command as its passthrough, and collect real decisions. This
   phase confirms, against live hook payloads and before anything is
   rewritten: the launch tool's name and input fields, that `permission_mode`
   is present on launch events, and that `agent_id` marks nested launches. If
   `permission_mode` turns out to be absent on this event, redirects stay
   disabled, which is what the eligibility rules already produce.
4. **Enforce Claude model changes.** `set_model` and `deny` go live. The owner
   creates the config file and sets `mode` to `enforce` at this point.
5. **Codex workers and redirect.** `redirect_codex` goes live, gated by
   redirect eligibility.
6. **Prompt-hook guidance, balance, and cleanup.** Remove the manual
   `~/.claude/.usage-exhausted` switch from the owner's existing `explorer`
   agent.

Changes to `~/.claude/settings.json` and to the owner's existing agents happen
in phases 3 and 6 and are confirmed with the owner at that point.

## 11. Known limits

- The main conversation's model is recommend-only.
- Codex quota on disk refreshes only when Codex runs. Between runs the reading
  is a lower bound, so Codex use outside this machine is invisible until the
  next run.
- Claude quota refreshes when the status line renders, which requires an open
  Claude Code session. That is always true when a hook fires.
- Redirect eligibility compares what the hook can see: permission mode,
  nesting, agent type, and file-scoped deny rules. It cannot see everything
  Claude Code enforces. Inside a Codex run, the session's Bash allow, ask, and
  deny rules, the `auto` mode's safety checks, and other plugins' hooks do not
  apply to the commands Codex itself runs. The Codex sandbox is the boundary
  there: read-only, or writes confined to the workspace.
- With the default lists, a session in `default` or `auto` mode never
  redirects a write task to Codex. Those tasks stay on Claude unless the owner
  adds the mode to `write_redirect_modes`.
- Keyword classification is blunt. The `[route:...]` tag and the
  `agent_categories` map are the precise controls.
- Weekly limits that apply to one model family rather than the whole plan are
  not modelled. Only the two windows each client reports are used.
