# Mycelium Regression Patterns

Read this before changing Mycelium itself. These patterns were extracted from
cross-host migration work and repeated review rounds; each one is a defect class
to audit, not a claim that every nearby construct is wrong.

## 1. Source checkout is not the installed artifact

**Failure:** A real-host smoke passes or fails against an older plugin cache
while the source branch contains different code.

**Invariant:** Record the source commit and installed plugin version. Compare
hashes of every exercised packaged file. For Codex local development, use the
supported cachebuster/reinstall flow and launch a new task.

**Regression evidence:** Report plugin identity separately from hook behavior.
A mismatched artifact makes the behavioral result inconclusive.

## 2. Validation occurs after mutation begins

**Failure:** Valid JSON with malformed nested types, a late symlink, or an
unsafe destination crashes after guidance or runtime state has already changed.

**Invariant:** A multi-file operation preflights every source, destination, and
traversed schema before its first write. Validation permits unknown user fields
but constrains every type the implementation will dereference.

**Regression evidence:** Seed the unsafe condition in the last logical action;
assert all earlier files remain byte-identical and no new managed file exists.

## 2a. Host process observes a changing artifact

**Failure:** A long-running Claude Code or Codex smoke loads some hooks before a
source edit and other hooks afterward. The resulting task no longer represents
any commit or installed build, and transient errors can be misdiagnosed as
product defects.

**Invariant:** Finish, install, identify, and hash the candidate before starting
the host process. Treat both source and installed artifacts as immutable until
natural Stop completes. Stop the process before making another fix.

**Regression evidence:** Record artifact hashes before and after the run. If
they differ, classify the run as inconclusive and repeat it against a frozen
artifact; do not blend its observations into release evidence.

## 3. Writers and readers disagree on a state format

**Failure:** One hook writes a multi-line active marker while another treats the
whole file as a path, or a new sentinel format is parsed only by one consumer.

**Invariant:** Document each state format and centralize parsing where possible.
Every reader validates types, field count, containment, ownership, and staleness
before use.

**Regression evidence:** Exercise every consumer with valid, stale, truncated,
extra-line, escaping, and symlinked state.

## 4. Host wire data is encoded or decoded at the wrong layer

**Failure:** Codex model-facing tool responses contain nested or serialized
result JSON, so exit status is lost; a failed command becomes `null`, or a failed
edit counts as activity. In the opposite direction, a context-bearing hook emits
`additionalContext` at the top level even though the current host requires the
event-specific output envelope, so dispatch succeeds but the model never sees
the context.

**Invariant:** Normalize host wire formats at the dispatcher boundary. Emit the
current event-specific envelope with its matching event name, keep universal or
decision fields at their documented level, prefer structured exit evidence over
prose, and propagate success/failure explicitly to shared hooks.

**Regression evidence:** Cover canonical host payloads plus nested `input_text`,
serialized JSON, missing status, conflicting prose, and nonzero exit cases.
Exercise real SessionStart and PostToolUse hooks for both hosts and assert the
exact context-output schema, then confirm context reaches each model in a fresh
CLI task.

## 5. Shell text is mistaken for executed argv

**Failure:** Interpreter-looking text inside quotes, comments, wrapper options,
or `env -S` produces false lineage; concatenated shell words or cwd-changing
wrappers lose real executions.

**Invariant:** Decode complete shell words conservatively, track proven
execution and cwd in order, and reject unsupported argv rewriting. A regex match
inside arbitrary command text is not execution evidence.

**Regression evidence:** Pair each positive with a near-neighbor negative.
Cover quotes, escapes, comments, operators, terminal help flags, nested wrappers,
cwd options, word boundaries, and nonzero exits.

## 6. Retry state is cleaned before acceptance

**Failure:** A blocked or partially failed Stop removes the active log, baseline,
raw lineage events, or session ID, making the next Stop unable to finish the
same transaction.

**Invariant:** Mark completion and clean active state only after log, registry,
lineage, and handoff publication all succeed. Resume and compaction must retain
the same unfinalized transaction.

**Regression evidence:** Inject failure at each publication boundary, resume,
perform more work, retry, and assert one final log, footer, registry row, lineage
manifest, and cleanup.

## 7. Concurrency is tested only as sequential idempotency

**Failure:** Two SessionStart or Stop processes both pass a check-before-write
and create duplicate logs, registry rows, lineage archives, or truncated state.

**Invariant:** Serialize the full transaction, not merely individual writes.
Atomic replacement prevents torn files but does not replace transaction locks.

**Regression evidence:** Start real concurrent processes at the contested
boundary and assert exactly-once effects plus valid final bytes.

## 8. A manual hook test is presented as host dispatch evidence

**Failure:** A hook works when invoked directly but the host never loads,
approves, or dispatches it.

**Invariant:** Unit/harness tests and black-box host tests are separate evidence.
A dispatch audit launches a fresh host task and never calls the hook manually.

**Regression evidence:** Capture exact SessionStart/PostToolUse/Stop context and
filesystem side effects from the host process, then classify dispatch, hook
logic, environment, and agent-compliance failures independently.

## 9. Compatibility cleanup deletes user state

**Failure:** Migration replaces an entire hook event or guidance file to remove
one obsolete Mycelium entry, dropping unrelated user configuration.

**Invariant:** Identify Mycelium-owned entries unambiguously, remove or repair
only those entries, and preserve unknown fields and byte-stable no-op behavior.

**Regression evidence:** Seed mixed old Mycelium and custom entries, migrate
twice, and verify custom bytes/semantics survive while the second run performs
no structural rewrite.

## 10. Cross-host hook discovery invokes the wrong adapter

**Failure:** A conventional plugin path is recognized by more than one host.
Claude Code, for example, discovers `hooks/hooks.json`, so a Codex adapter in
that location can run alongside the repository's native Claude hooks and
duplicate SessionStart, PostToolUse, or Stop effects.

**Invariant:** Every host-facing adapter positively identifies or safely
excludes the other host before touching repository state. Shared hook logic may
remain provider-neutral, but discovery entrypoints must not double-dispatch it.

**Regression evidence:** Load the real plugin through both CLIs. Count automatic
hook events and assert the non-native adapter exits silently, while the native
registration still produces exactly one lifecycle effect.

## 11. Host payload omits result metadata

**Failure:** A test fixture supplies exit status or elapsed time that the real
host does not provide at that hook boundary. Production then records `null`, or
a later patch invents zero and mislabels failed analysis as successful.

**Invariant:** Capture the automatic hook stdin from the current host before
defining its wire contract. Preserve unknown fields as unknown. An exit event
visible later in a CLI stream cannot be attributed by a hook that ran earlier.
Unknownness must survive aggregation: a consolidated summary sums only known
values, reports null (or omits the total) when nothing was measured, and
publishes explicit coverage counts instead of letting missing telemetry read
as a measured zero or an implicit success.

**Regression evidence:** Retain a sanitized canonical payload fixture. Current
Codex Bash PostToolUse supplies an empty `tool_response`; require lineage to
record the execution with `bash_exit` and `bash_wall_s` as null, while retaining
decoders for richer structured payloads a host may provide later. Consolidate
events whose telemetry is entirely null and require a null total with zero
coverage; consolidate mixed events and require both the partial sum and its
coverage counts.

## 12. Lock recovery waits longer than acquisition

**Failure:** A lock owned by a terminated process is considered recoverable only
after an age threshold that exceeds the caller's acquisition timeout. Every
retry times out during that gap, and a fail-open hook can silently skip its
transaction.

**Invariant:** Reclaim a lock immediately when a recorded owner PID is confirmed
dead. Preserve an age guard for ownerless or malformed locks because another
process may be between atomic directory creation and owner publication. Lock
failure at a safety boundary must fail closed.

**Regression evidence:** Create a recent lock with a dead recorded owner and
require acquisition before the normal retry timeout. Separately verify that an
empty recent lock is not stolen and an old empty lock is recoverable.

## 13. Option values are mistaken for positional inputs

**Failure:** A regex skips an option name without consuming its separate value,
then classifies an input-looking value such as `converted.ipynb` as the executed
script or notebook. An option whose value does not resemble an input can instead
hide the real positional path.

**Invariant:** Parse option arity before selecting positional inputs. Model
known flags and value-bearing options, accept assignment forms as one argv
element, and reject unknown separated-value options conservatively.

**Regression evidence:** Pair a normal value such as `--to notebook` with an
input-looking value such as `--output converted.ipynb`, plus assignment and
unknown-option neighbors. Require the true positional input or no attribution;
never accept the option value.

## 14. Repository state is mistaken for invocation identity

**Failure:** Repository timestamps are treated as proof that a host event owns
the active transaction, so a late event from a superseded root task finalizes,
deletes, or appends to the current root task's state.

**Invariant:** Persist the root session identity supplied by the host and compare
it before any shared PostToolUse or Stop-side mutation. Current subagents use a
dedicated lifecycle and retain that root ID. Publish identity before the active
marker, encode the ownership format in that marker, and fail closed when a
required companion identity is corrupt, multiline, or missing. Retain shared
timestamps only as an upgrade fallback for markers that identify legacy
sessions. Once an identified transaction's marker is gone, later identified
PostToolUse payloads are stale and must not fall back to markerless legacy mode.

**Regression evidence:** Start a new root owner while the prior task remains
unfinalized, then deliver late tool and Stop events from the old owner. Also
deliver Bash/edit/read events after an accepted Stop removed the marker. Require
the current state to remain byte-identical and no transaction state to be
recreated; only identity-free legacy payloads may use markerless behavior.

## 15. Terminal-only mode is treated as execution

**Failure:** A parser accepts a flag that prints or generates configuration and
exits, then attributes a later script-looking argument as executed. Checking
only options before the positional path misses terminal flags placed afterward.

**Invariant:** Classify terminal modes separately from ordinary flags and scan
the complete parsed simple-command argv, not raw text or only the prefix before
the apparent input. Continue across redirections in that simple command, but
consume redirection targets as shell syntax rather than interpreter argv. Scope
the decision to that command so a later command or terminal-looking text inside
an option value cannot hide real execution.

**Regression evidence:** Cover each terminal flag before and after an apparent
input, with ordinary flags, assignment forms, quoted values containing similar
text, redirections before the terminal option, option-looking redirection
targets, and a neighboring command that really executes.

## 16. A fallback is appended to partial stdout

**Failure:** `value=$(command || echo fallback)` assumes failure produced no
stdout. A command such as Git on an unborn branch prints a partial value and
then exits nonzero, so the substitution contains two lines and corrupts a
scalar, path, or generated document.

**Invariant:** Capture the primary command separately, branch on its status,
and overwrite—not append—with a fallback or secondary query. Validate the final
value's shape before embedding it, and encode it for the destination format.

**Regression evidence:** Exercise a command that emits stdout and fails. Require
one typed scalar in the generated artifact, then round-trip it through every
consumer. Include destination-sensitive values such as YAML-looking Git branch
names.

## 17. A new root task is mistaken for a nested child

**Failure:** SessionStart treats every different root identity as proof that the
current owner is abandoned, so a concurrent live task loses its marker,
baselines, reminders, activity, and raw lineage to the newcomer.

**Invariant:** Use the host lifecycle contract: root SessionStart and
SubagentStart are distinct, and subagents retain their parent's session ID. A
different root startup may supersede an old repository transaction only after
the same owner-age and activity/reminder liveness checks prove it inactive.
Live owners remain intact and the competing task receives an explicit warning.
Cleanup must also preserve both liveness sentinels while that owner remains
active; an old reminder cannot authorize deleting a fresh activity signal.
Every shared PostToolUse writer checks ownership before mutation.

**Regression evidence:** First attempt to supersede a live owner and require its
marker, owner, log, raw lineage, reminder, activity, and baselines to remain
byte-identical, including when the reminder is old but activity is fresh. Then
age both the owner and its liveness signals beyond the threshold, retry, and
require a fresh log/baseline plus archived old evidence.

## 18. Control-plane work is classified as analysis work

**Failure:** Index, registry, handoff, validation, or session-accounting helpers
open a new analysis reminder after the agent has already completed reflection,
creating a Stop loop that the lifecycle itself continually refreshes.

**Invariant:** Classify the executed script path, not its basename or argument
text. Mycelium-managed utility paths are silent; same-named user analysis
scripts elsewhere remain eligible. A hand-enumerated exclusion list goes stale
the moment a helper is added (issue #69 shipped with `recall_lessons.py`,
`detect_recurrence.py`, `upsert_table_row.py`, `crystallize_findings.py`, and
`extract_data_lineage.py` missing), so the registry must be derived from the
shipped scripts tree — or a regression test must walk that tree and fail when
a bundled helper is not excluded. The inverse trap is treating path shape as
identity: a registry suffix match must begin at a path-component boundary and
its candidate root must prove it is a real Mycelium plugin tree (the running
extractor's own install, a root with a Mycelium plugin manifest, or the
documented `.mycelium/plugin-root` accessor), or a user project that mimics
the conventional layout silently loses lineage and bookkeeping.

**Regression evidence:** Exercise every managed helper through both source and
versioned installed paths, plus user-script near neighbors. Require no reminder
or lineage event for the former and normal detection for the latter. Include a
sweep that enumerates every `.py` shipped under the managed scripts directory
so a newly added helper cannot reintroduce the gap. Require detection for a
colliding filename under an unverified `skills/core/scripts/` layout, for a
`myskills/`-style component-boundary neighbor, and for a non-Mycelium
manifest; require exclusion for verified installs and both documented
plugin-root accessor spellings.

## 19. Machine finalization overwrites authored semantics

**Failure:** Stop replaces a rich registry summary, outputs, tags, or handoff
with filenames and blank cells, or preserves a durable "Stop pending" statement
after Stop was accepted.

**Invariant:** Machine finalizers own factual lifecycle fields and an explicit
accepted-status block. Authored semantic fields and handoff body content remain
intact; obsolete lifecycle-status prose is removed atomically.

**Regression evidence:** Seed rich metadata and a complete handoff containing a
pending-Stop line, finalize twice, and require one row, one status block, no
pending contradiction, and unchanged authored semantic content.

## 20. Multi-agent review counts prose instead of root causes

**Failure:** The same defect is split across categories, draft counts survive
synthesis, or chat/handoff totals disagree with the rendered report.

**Invariant:** One remediation root gets one global finding ID. Derive category
and global tallies from the final headings and validate them mechanically before
reporting. Cross-input comparability is an explicit review axis.

**Regression evidence:** Reject duplicate/nonconsecutive IDs and mismatched
tallies; require an exact validator pass for a clean report fixture.

## 21. A Markdown validator parses example code as report structure

**Failure:** Headings, finding IDs, or table rows inside a fenced evidence
sample are treated as live report structure. A valid report then fails because
an illustrative `###` heading resets the category or a sample `##### F99`
becomes a real finding.

**Invariant:** Structural validation operates on Markdown outside fenced code
blocks. Section-local tables stop at the next peer heading, required categories
are checked even when they contain zero findings, and unexpected finding or
tally categories are rejected.

**Regression evidence:** Put category, finding, tally-heading, and table-shaped
examples inside both ordinary report prose and fenced blocks. Require fenced
content and later appendix tables to be ignored while wrong zero tallies and
unexpected live categories fail.

## 22. Review orchestration assumes unlimited subagent capacity

**Failure:** A review dispatches all specialist agents concurrently even when
the host exposes fewer slots. Some calls fail with `agent thread limit reached`,
and the final synthesis silently omits those review axes.

**Invariant:** Parallelism is bounded by observed host capacity. Specialists
run in waves, undispatched work is retried after a slot opens, and persistent
capacity failures fall back to the same checklist in-line. Every applicable
review axis runs exactly once.

**Regression evidence:** Exercise a host with fewer slots than specialists and
inject a capacity error. Require later waves or in-line completion and a final
report containing every applicable category without duplicate passes.

## 23. A development cachebuster is treated as a release-version mismatch

**Failure:** Codex appends its documented `+codex.<token>` cachebuster during a
local reinstall, but cross-host validation compares manifest strings byte for
byte and rejects the otherwise compatible plugin.

**Invariant:** Claude and Codex manifests share the same semantic base version.
Codex may carry one nonempty, correctly namespaced build-metadata cachebuster;
arbitrary suffixes and divergent base versions remain invalid.

**Regression evidence:** Validate the base version with and without a Codex
cachebuster, then reject an empty token, another namespace, or a changed base.

## 24. Archive cleanup assumes runtime state is a regular file

**Failure:** Crash recovery checks only whether an event path has content, so a
repository-controlled directory, device, or FIFO at that path can be moved,
read, or blocked on as though it were a lineage log.

**Invariant:** Every archive source and optional identity file is a non-symlink
regular file, and every archive parent is a non-symlink directory, before
recovery mutates anything. Unsafe state leaves the prior transaction intact.

**Regression evidence:** Replace the raw-event file with a nonempty directory,
attempt root supersession, and require a quiet refusal with the directory,
owner, and active marker byte-stable.

## 25. Repository fallback duplicates or aliases literal lineage paths

**Failure:** A fallback searches by basename after static extraction, adds an
absolute copy of an already-recorded relative path, or maps an explicitly
external absolute literal onto a same-named in-repository file.

**Invariant:** Repository recovery handles only eligible relative literals and
merges by normalized path identity. Direct static attribution remains
authoritative; fallback status is reported only when it adds a new path.

**Regression evidence:** Cover a direct relative input that exists in the repo
and an absolute external input whose basename also exists locally. Require one
static record in each case and no `static+repository` claim.

## 26. Unrelated literals are fabricated as data lineage

**Failure:** Repository recovery treats every data-looking string constant as
provenance, so a docstring or display label such as `ghost.csv` fabricates an
input. It also guesses direction from directory names, misclassifying a read
from `results/` as an output. Recursively collecting a conditional path records
both branches, while leaf-name matching treats a user-defined `read_csv` helper
as real data I/O. Trusting a familiar alias after any rebinding has the same
effect, and retaining the literal half of runtime string concatenation can hash
a decoy whose basename was never read.

**Invariant:** Recovery candidates must data-flow into the path argument of a
recognized reader or writer expression. Direction is semantic evidence from
that call, never a directory-name heuristic. Runtime branch selection and
ambiguous assignment flow are left unresolved rather than guessed. Reader
aliases require an actual supported import and remain invalid after every AST
binding form. String concatenation contributes only when the entire expression
resolves statically; arbitrary functions and partial literals do not prove I/O.

**Regression evidence:** Place a uniquely named `ghost.csv` in the repository
and mention it only in a module docstring; require no lineage. Read a dynamic
path under `results/` and write one under `data/`; require input/output direction
to follow the calls. Pass a conditional path and a custom `read_csv` helper;
require unresolved lineage, while retaining a supported imported reader. Rebind
its conventional alias through assignment, loop, context manager, unpacking,
exception handling, walrus, comprehension, and pattern matching, and combine a
runtime prefix with a literal suffix; require every case to stay unresolved.

## 27. Resolved I/O triggers repository traversal

**Failure:** Every script containing a data-like literal recursively walks the
repository even when ordinary static extraction already resolved all I/O. Large
scientific data trees then delay or time out a synchronous PostToolUse hook.

**Invariant:** Remove statically resolved call-path candidates before repository
discovery and skip discovery when none remain. Dynamic discovery excludes
runtime/vendor trees and has a fixed entry bound; hitting the bound yields
unresolved lineage instead of an unbounded delay or partial guess.

**Regression evidence:** Replace the repository-query primitive with a failing
spy and process a direct `read_csv('data/raw/sample.csv')`; require the static
event to succeed without invoking the spy. Exercise the bound separately and
require an unresolved result.

## 28. Global safety serialization is mistaken for multi-session support

**Failure:** One repository-global owner and marker prevent cross-task
corruption, but a second live root is excluded from logging, activity,
lineage, Stop enforcement, and handoff. Replacing writes atomically avoids torn
bytes yet still loses updates when two shared writers derive from the same old
content.

**Invariant:** Every identified host root owns an exact, validated transaction
namespace. Repository lifecycle locking coordinates only shared allocation and
publication; a per-session lock protects private event state; stable durable
file locks cover the complete read/derive/write transaction for shared files.
Lock order is broad to narrow, and a queued event revalidates after acquiring
its lock. Identity-free and exact-owner pre-upgrade flat transactions remain
compatible without becoming a fallback for malformed identities.

**Regression evidence:** Start distinct Claude/Codex root IDs concurrently and
require distinct run directories, markers, and logs; start one ID twice and
require reuse. Race PostToolUse with accepted Stop and require no late state
write. Race distinct registry upserts and require every row plus the original
mode. Exercise exact matching and nonmatching pre-upgrade flat owners, invalid
and linked IDs, two independent accepted Stops, and one blocked root beside a
live sibling.

## 29. A syntactic identifier whitelist is mistaken for a safe path segment

**Failure:** A JSON number or Boolean is stringified into a host identity, while
an object or list disappears and silently selects legacy state. Even a value
that matches `[A-Za-z0-9._-]+` may be the special component `.` or `..`, which
collapses a supposedly private directory onto its parent.

**Invariant:** Decode filesystem identities as typed JSON strings at the hook
boundary. A missing or null optional identity may use documented compatibility
behavior; a present non-string value is invalid. In addition to the character
and length policy, reject `.` and `..` before any state preparation, and repeat
the path-component check at the filesystem boundary.

**Regression evidence:** Exercise numeric, Boolean, object, list, `.`, and `..`
session IDs. Require no runtime directory, log, shared pointer refresh, or
legacy fallback. Keep ordinary missing-ID compatibility as a paired positive.

## 30. A stale-lock reaper deletes a replacement owner's lock

**Failure:** Two contenders observe owner A as dead. One removes A's directory
and owner B acquires the same path; the delayed contender then removes that path
using only its stale observation. B loses its lock during the mkdir-to-owner
publication window, so a fail-silent hook drops an otherwise successful event.

**Invariant:** A reaper must atomically claim the observed lock generation and
revalidate both directory identity and owner while that claim excludes other
reapers. It may delete only the same dead or aged generation it inspected.
Ownerless recent directories remain protected as potential publication windows.

**Regression evidence:** Drive many concurrent child events through one parent
transaction and require every path exactly once with no hook stderr. Repeat the
contention test enough times to expose replacement-owner races, alongside dead-
owner recovery, recent-ownerless protection, and live-owner timeout cases.

## 31. Handoff headings are mistaken for complete handoff sections

**Failure:** Stop accepts a fresh handoff merely because all required heading
lines and bodies exist before finalization. An agent can leave a body empty,
duplicate or reorder headings, or use obsolete pending-Stop prose as a section's
only content; semantic cleanup then erases that content after validation and the
durable resume pointer advertises an incomplete handoff as accepted.

**Invariant:** Validate the exact normalized body that finalization will
publish, using one shared cleanup-and-validation implementation. A preserved
authored handoff contains every heading in one supported five-section schema
exactly once, in that schema's order, with at least one nonblank body line per
section after obsolete lifecycle prose is removed. Any absent, stale, empty,
duplicated, misordered, or cleanup-emptied structure receives the deterministic
complete fallback before accepted publication; the finalizer enforces the same
contract before its first write.

**Regression evidence:** Exercise canonical and legacy complete handoffs as
preservation positives. Exercise an empty body, reordered headings, a duplicate
heading, and a section whose only body line is removed by lifecycle cleanup as
negatives; require one ordered, nonempty canonical set in the accepted durable
handoff. Invoke finalization directly on the cleanup-emptied case and require
the private source and shared destination to remain byte-identical.

## 32. A retry refreshes one representation of a finalized fact but not its siblings

**Failure:** A Stop retry recomputes duration and changed files, rewrites the
session frontmatter and registry row with the new values, but skips the
existing human-readable `Session ended` footer and `Files Modified` list to
avoid duplicating them. The finalized log then disagrees with itself: three
representations of the same facts, two current and one stale.

**Invariant:** When one artifact stores the same fact in multiple
representations, every finalization attempt derives all of them from the same
canonical arguments in the same atomic replacement. Skipping a representation
because it already exists is only sound when its values are proven equal;
otherwise replace the machine-owned block wholesale. Deduplication is achieved
by removing prior machine-owned blocks, not by refusing to write current ones.

**Regression evidence:** Finalize a log once, retry with different duration,
file count, and file list, and require frontmatter, footer, `Modified`
summary, file list, and registry row to agree exactly with the retry values,
with exactly one footer. Also require duplicated legacy footers to collapse
and body content appended between attempts to survive.
