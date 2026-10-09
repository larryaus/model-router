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
- No router failure of any kind blocks or alters a launch.

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

`decide(classification, agent_type, quotas, config, now) -> Decision`.
Pure: no I/O, and time is passed in. Section 4 defines its behaviour.

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
  a `deny` permission decision.
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
- if it is on the Codex plan, the launch's agent type is in `redirectable`
  (see 4.4).

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
- **Redirectable.** A custom agent's behaviour comes from its own system
  prompt, which a redirect to Codex would discard. Only agent types listed in
  `redirectable` may be sent to Codex. Default: `general-purpose`, `claude`,
  `Explore`, `Plan`, `explorer`. All other agent types receive Claude model
  changes only.
- **Read-only stays read-only.** A redirected launch uses `codex-read` unless
  its category is in `write_categories` (default: `implement`, `debug`,
  `default`) and its agent type is not in `readonly_agents` (default:
  `Explore`, `Plan`, `explorer`).
- **Overrides.** `[route:<target>]` replaces the preference list with that one
  target and skips balance. It ignores `conserve` and `critical`, because the
  owner asked for that target explicitly. It still obeys `exhausted` and the
  redirectable rule; if either rejects it, routing falls back to the normal
  preference list and the reason says why. `[route:keep]` leaves the launch
  untouched.

### 4.5 Applying a decision

| Action | Effect on the launch |
|---|---|
| keep | No output. The launch proceeds unchanged. Produced by passthrough, `[route:keep]`, and `mode: off`. |
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
comments. Missing keys take the defaults shown.

```jsonc
{
  "mode": "enforce",            // "enforce" | "shadow" | "off"
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
  "statusline": { "passthrough": null }  // command to run after capture
}
```

`config.py` validates the file by hand and reports each problem with its key
path. Validation checks: every preference entry names a defined target; every
target has a known plan and weight; thresholds are ordered
`conserve < critical < exhausted`; `underused < overused`.

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
- **Invalid config.** Built-in defaults are used. `router status` prints the
  validation errors, and the prompt hook announces the problem once per
  session.
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
  overrides, passthrough, redirectable, and the read-only rule.
- **Classifier.** Tag, agent type, keyword order, and default.
- **Quota readers.** Fixture files for valid, empty, truncated, and malformed
  input. A large log file to confirm only the tail is read.
- **Config.** Comment stripping, defaults, and each validation error.
- **Hook adapters.** Feed hook JSON on stdin and assert on stdout. Include a
  forced exception to prove fail-open, and `shadow` mode to prove nothing is
  rewritten.
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
│  └─ hooks/{pre_agent,prompt,statusline}.py
├─ tests/
└─ docs/superpowers/specs/
```

## 10. Build phases

Each phase leaves something usable.

1. **Quota readers and `router status`.** See both plans' windows and levels.
2. **Config, classifier, policy engine, `router explain`.** Dry-run decisions
   with no hooks installed.
3. **Status-line capture and the pre-launch hook in `shadow` mode.** Install
   the plugin, point the status line at `statusline.py` with the existing
   Quota Glass command as its passthrough, and collect real decisions. This
   phase confirms the launch tool's name and input fields against live hook
   payloads before anything is rewritten.
4. **Enforce Claude model changes.** `set_model` and `deny` go live.
5. **Codex workers and redirect.** `redirect_codex` goes live.
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
- Keyword classification is blunt. The `[route:...]` tag and the
  `agent_categories` map are the precise controls.
- Weekly limits that apply to one model family rather than the whole plan are
  not modelled. Only the two windows each client reports are used.
