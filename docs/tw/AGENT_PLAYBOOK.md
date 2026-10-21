# TW Agent Playbook — how a model team works this repo

Operating model for AI-driven sessions (Cowork / Claude Code / any agent harness). Written for a
world where **no single context window can hold this repo** (~5k lines TW code on a huge upstream,
GB-scale logs/data). The design principle: *expensive models judge and plan; mid models build;
cheap models read and grind; nobody reads everything.*

A solo session (one model, no subagents) plays these roles **sequentially** — the role boundaries
and reading budgets still apply. They exist to protect the context window, not to require
multiple processes.

## 1. Team

| Role | Runs as | Model | Owns | Hard limits |
|---|---|---|---|---|
| **PM / Orchestrator** | main session | Fable 5 (best available) | task graph, user dialogue, merge decisions, doc updates, git handoffs | never bulk-reads; any file >300 lines goes through a Scout |
| **Scout** | `Explore` subagent (read-only) | Haiku 4.5 | "where is X / what does Y do / what's in this log" | returns ≤300-token digest + `path:line` pointers; never full dumps; never edits |
| **Architect** | `Plan` subagent | Opus 4.8 | risky designs: upstream syncs, bin-schema changes, deadlock-class bugs | outputs a plan + file list + risks; writes no code |
| **Implementer** | `general-purpose` subagent (worktree if parallel) | Sonnet 5 | one brief = one TODO node; code in `*_tw` files | may not touch upstream-patched files (CLAUDE.md list) without an Architect plan |
| **Verifier** | `general-purpose`, **fresh context** | Sonnet 5 (Opus after upstream merges) | diff review vs golden rules; patch-list audit | sees only: the diff, CLAUDE.md, touched files. Independence from Implementer is the point |
| **Scribe** | subagent or PM inline | Haiku 4.5 | FINDINGS/PROJECT_STATE drafts from templates | writes only under `docs/tw/` |
| **Jans (human)** | — | — | everything on the real machine: run loops/smokes, git push, spend approval, API keys, `factor_ideas.md` | the only member who spends money or touches production |

**Not a team member but a contractor:** the RD-loop's inner LLM (hypothesis-gen + factor/model
coding at hundreds of calls per session) stays `deepseek-chat` via LiteLLM — chosen for cost
(D12). Optional experiment: route only hypothesis-gen to a stronger model via `LITELLM_CHAT_MODEL_MAP`
and measure IC delta vs. cost. Needs Jans's spend approval.

## 2. Model routing

| Task class | Model | Rationale |
|---|---|---|
| Orchestration, ambiguous trade-offs, final acceptance | Fable 5 | judgment over long horizons |
| Architecture, merge-conflict strategy, "impossible" bugs | Opus 4.8 | deepest reasoning; used sparingly |
| Scoped implementation, tests, code review | Sonnet 5 | best capability/cost for competent coding |
| Search, log triage, summarization, doc upkeep | Haiku 4.5 | cheap, fast, disposable, parallel |
| Inner R&D loop (bulk generation) | deepseek-chat | volume pricing; already wired |

**Escalation ladder:** two failed tries at one tier → escalate one tier with a *written summary of
what failed* (not the raw transcript). Ambiguity about *intent* escalates to the PM/user, never to
a bigger model.

## 3. Context economy (the token budget)

| Role | Mandatory reads | Allowed | Forbidden |
|---|---|---|---|
| PM | CLAUDE.md, PROJECT_STATE, TODO_GRAPH | DECISIONS/RUNBOOK on demand | logs, data files, any source file >300 lines (delegate) |
| Scout | the brief | anything, via grep/head/tail | returning >300 tokens; reading whole large files when grep answers |
| Architect | CLAUDE.md + brief excerpts | targeted source reads | repo-wide sweeps (request Scout digests) |
| Implementer | CLAUDE.md + brief + files named in brief | tests it writes | exploratory reading (ask PM → Scout) |
| Verifier | CLAUDE.md + the diff | touched files | implementer's reasoning/transcript |
| Scribe | templates + inputs handed over | — | everything else |

Principles:

1. **CLAUDE.md is the constitution** — the only universal read (~2k tokens). Everything else is
   role-scoped.
2. **Ask, don't read.** Before opening any file >300 lines, ask a Scout the specific question.
3. **Logs are radioactive** (multi-GB). Only Scouts touch them, only with `grep/tail`, returning
   digests.
4. **Compression at every boundary.** Worker → PM reports are bounded (§4). Session → future
   session knowledge lands in the tracked docs. *If it isn't written into a tracked doc, it
   didn't happen.*
5. **Warm-start briefs.** The PM inlines the exact excerpts (5–20 lines) a worker needs, so most
   workers read almost nothing beyond the brief.

## 4. Communication protocol

**Topology: hub-and-spoke.** Workers never talk to each other — all traffic goes through the PM.
Cross-session traffic goes through the tracked docs only (PROJECT_STATE = state register,
DECISIONS = why-log, TODO_GRAPH = what's next, FINDINGS = results).

**Brief (PM → worker):**

```
NODE:   <TODO_GRAPH id, e.g. S1>
GOAL:   <one sentence>
DONE =  <objectively checkable condition(s)>
INPUTS: <paths + line ranges + inlined excerpts>
RULES:  <golden-rule numbers that bind, e.g. "CLAUDE.md rules 1,2,3">
BUDGET: <max files to read / max output tokens>
RETURN: <report format below>
```

**Report (worker → PM), ≤400 tokens prose:**

```
STATUS:    done | partial | blocked
CHANGED:   <file list, or none>
EVIDENCE:  <test/smoke output snippet or "needs Jans to run X">
SURPRISES: <anything contradicting the docs — triggers doc fix>
NEXT:      <suggestion or "none">
```

**Stop-and-return triggers** (worker aborts immediately, reports `blocked`):
needs to modify an upstream file beyond its documented patch · discovers a golden-rule conflict ·
needs the real machine / keys / money (→ Jans) · 80% of budget burned without `DONE` in sight ·
evidence that the brief's premise is wrong.

## 5. Session choreography

1. PM reads CLAUDE.md → PROJECT_STATE → TODO_GRAPH; evaluates jump rules; picks a node.
2. Scouts refresh the facts that node needs (cheap, parallel).
3. Architect engaged **only** if the node is flagged `risky` in TODO_GRAPH.
4. Implementer executes the brief → Verifier reviews the diff → PM accepts/rejects.
5. Scribe updates PROJECT_STATE (+FINDINGS/DECISIONS as applicable); PM mirrors TODO_GRAPH
   markers and hands the commit/push commands to Jans (sandboxes may lack git-write ability).

Anti-patterns: two Implementers on overlapping files without worktrees · Verifier = Implementer ·
briefs that say "look around and improve things" · pasting raw logs between roles.
