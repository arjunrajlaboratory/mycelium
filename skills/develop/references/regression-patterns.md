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

**Why Claude goes stale specifically:** the Claude plugin cache lives at
`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` and is keyed on the
**version string**. If repository content changes while the version stays the
same — every commit between a version bump and its release — the installed tree
never refreshes, and `claude plugin list` keeps reporting the version it was
told. `claude plugin marketplace update <name>` refreshes marketplace metadata
but **not** the content cache. Only removing the version directory and
reinstalling refetches:

```bash
mv ~/.claude/plugins/cache/mycelium/mycelium/<version>{,.stale-backup}
claude plugin install mycelium@mycelium
```

Codex is immune to this because its version carries `+codex.<token>`, which
changes with the build. That asymmetry is the argument for never reusing a
version number once content under it has changed.

**Regression evidence:** Report plugin identity separately from hook behavior.
A mismatched artifact makes the behavioral result inconclusive. Hash before
auditing rather than trusting the version label:

```bash
git ls-files skills hooks .claude-plugin .codex-plugin   # then sha256 each
```


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
sessions. Once an identified transaction's marker is gone, a later identified
PostToolUse payload never writes into that transaction or falls back to
markerless legacy mode; if it carries work, it reopens a fresh transaction for
its own host session (pattern 44).

**Regression evidence:** Start a new root owner while the prior task remains
unfinalized, then deliver late tool and Stop events from the old owner. Require
the current root's state to remain byte-identical. Also deliver Bash/edit/read
events after an accepted Stop removed the marker: reads and non-work commands
recreate nothing, and work opens only a fresh transaction of its own; only
identity-free legacy payloads may use markerless behavior.

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
plugin-root accessor spellings. The accessor exception trusts only commands
that actually read this project's pointer: substitutions that merely mention
it (`basename`, `echo`, `dirname`) or read a nested repository's pointer
must remain untrusted. The accessor must also anchor the entire shell word
and be followed by a registry-normalizable helper path: a user component
before a genuine pointer read (`foo/$(cat .mycelium/plugin-root)/…`)
resolves under the cwd, and traversal after the accessor escapes the managed
tree, so both stay eligible analysis. Finally, the pointer itself is
repository-controlled state: the accessor is trusted only after
dereferencing `.mycelium/plugin-root` under the command's effective working
directory (non-symlink regular file, absolute-path value) and verifying
that its target is a real Mycelium plugin root — a nested repo's pointer
aimed at an unverified user tree stays eligible analysis. Verification must
emulate the exact substitution semantics of the matched reader with no
normalization: `cat` strips only trailing newlines and fails closed on
embedded ones, `sed -n '1p'` yields the exact first line, so a
trailing-space pointer verifies the space-suffixed directory the shell
actually executes from, never a stripped variant of it — read pointer bytes
without universal-newline decoding, since a CRLF pointer leaves a CR in the
executed path. Quoting context gates expansion: a word-initial `$(` is
expanded when the authored word is bare, fully double-quoted, or quoted
only around the accessor component; single-quoted or escaped spellings are
literal paths. The robust structure is one trust gate: resolve the accessor
to the exact executed path, then let the same managed-path verification
that judges directly written paths make the only suppression decision — and
that gate must judge the canonical filesystem path of the uncollapsed word,
because lexical `abspath` folds `symlink/..` differently than the shell
resolves it. Bash also removes NUL bytes from substitution output, so the
emulation must strip them too — and no decoding artifact may raise out of
the detector, because an unhandled exception silently kills the hook and
costs both the reminder and the lineage event.

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

**Recurrence:** The knowledge-log parsers had the same defect after
`validate_structure` was fixed. `generate_index` (counts, `collect_entries`,
snippets, `## F-` totals), `detect_recurrence`, `recall_lessons`, the
SessionStart summary's `grep -c`, and the knowledge-map extractors all counted a
fenced `### [date] Example` as an entry. `crystallize_findings` then did the same
with findings topics: a fenced `## F-999` became a `FINDINGS_REGISTRY.md` row
and a fenced `**Status:** robust` raised the cross-project INDEX's strongest
status. Fixing one reader of a format does not fix its siblings: sweep every
reader, and route each through `markdown_fences.mark_fenced` rather than a local
fence loop or a bare grep. A reader that skips fenced lines must also report an
unclosed fence (`unclosed_fence_line`), or real structure after it disappears
without a trace, and a derived file must be rewritten (not left alone) when
the corrected parse finds nothing, or the old false rows persist.

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
a decoy whose basename was never read. Path division and `os.path.join` do the
same: `Path(sys.argv[1]) / "input.csv"` names a file outside the repository,
yet dropping the runtime prefix matched the repository's own `input.csv`.

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
Join a command-line, `argparse`, or environment directory to a literal filename
that also exists in the repository, by division, `joinpath`, `os.path.join`,
and through assignments; require no lineage, while a `__file__`-anchored path
to the same filename is still recovered.
Repeat with the directory passed in as a function parameter (directly, through
`self`, a local alias, or a static default such as `root=ROOT`); require no
lineage, while a static path beside the parameterised one is still recovered.
Parameters seed the same file-wide runtime taint as command-line values, so
aliases are caught by the existing propagation.

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
section after obsolete lifecycle prose is removed. A subheading is structure,
not body: a section holding only `###` subsections is empty. A fresh handoff
that fails this check first gets one Stop block naming its problems (see
pattern 52); an absent or stale handoff, or one still failing after that
block, receives the deterministic complete fallback before accepted
publication. The finalizer enforces the same contract before its first write.

**Regression evidence:** Exercise canonical and legacy complete handoffs as
preservation positives. Exercise an empty body, a body of subheadings only, reordered
headings, a duplicate heading, and a section whose only body line is removed by
lifecycle cleanup as negatives; require one block, then one ordered, nonempty canonical set in the accepted durable
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
and body content appended between attempts to survive. Machine-block removal
must match only the exact emitted syntax (HH:MM time, numeric duration and
file count) outside Markdown code fences: an authored heading that merely
resembles the footer, and a machine-shaped example inside a fence, must
survive finalization byte-identical.

## 33. An emulated interpreter diverges from the real one

**Failure:** A hook decides trust or ownership by predicting how another
interpreter will treat text — bash expanding a command substitution, or
Markdown structuring a session log — but the emulation is approximate. Each
approximation is a defect: stripping whitespace the shell preserves, keeping
NUL bytes bash removes, universal-newline decoding that hides a CR, honoring
a `$(` that quoting disabled, trusting an unquoted substitution that field
splitting will rewrite, toggling fences on any delimiter line, or consuming
authored lines because they match a generated shape. PR #70 accumulated
seven review rounds of exactly these gaps.

**Invariant:** When a decision depends on another interpreter's semantics,
either reproduce those semantics byte-exactly (quoting context, expansion
rules, byte filtering, delimiter matching, generator-recorded counts) or
refuse conservatively. Every residual divergence must fail toward detection
and preservation — a wrong guess may cost a redundant reminder or a stale
example, never a suppressed lineage event, a silently killed hook, or
deleted authored content. Prefer resolving text to the exact value the real
interpreter would produce and passing it through the one shared trust gate,
over predicting the interpreter inside the gate.

**Regression evidence:** For each emulated rule, pin both directions with a
matched pair: the divergent input stays detected/preserved, and the
canonical input still verifies/collapses. Cover quoting variants, trailing
and embedded whitespace, CRLF and NUL bytes, field-splitting inputs,
mismatched fence delimiters, flagged builtin forms (`cd -P` resolves
physically; unmodeled `pushd`/`source`/`eval` prefixes make a dependent
trust decision fail closed), and authored content adjacent to generated
blocks, and require unparseable words to be skipped without raising out of
the hook.

## 34. A version-keyed artifact is audited before it is refreshed

**Failure:** A real-host audit runs against the cache that happened to be on
disk, passes, and certifies a tree that was never exercised. The audit reads as
evidence for the candidate while testing something else entirely.

**Invariant:** Refresh and then hash-verify the installed artifact **before**
launching any host process, and re-verify after any reinstall. An audit whose
artifact was not verified first is inconclusive, not passing — the same standing
as pattern 1, applied to sequencing rather than identity.

**Regression evidence:** Record the file count compared and the missing/differing
counts, not a version string. `154 packaged files, 0 missing, 0 differing` is
evidence; `Version: 0.7.0` is not.

## 35. A stacked pull request merges into a base that is about to disappear

**Failure:** A child PR is opened against a parent branch on the assumption the
host will retarget it when the parent merges. It does not: GitHub retargets a
child when the base branch is **deleted**, and it refuses to delete a branch
that is still the base of an open PR. The parent merges, the base survives, and
the child merges into an already-merged branch — its content never reaches the
default branch while the PR reads MERGED.

**Invariant:** Either branch from the default branch and accept the noisier diff
until the parent lands, or merge the stack bottom-up, retargeting each child to
the default branch before merging its parent. Do not rely on auto-retarget.

**Regression evidence:** After merging a stack, verify content reachability from
the default branch rather than PR state. `git ls-tree <default> --name-only`
for a file the child added, or `git log --oneline --merges <default>` for the
child's merge commit. A MERGED badge is not evidence.

## 36. A pattern matcher stands in for a parser

**Failure:** A tool locates structure in a markup or code language with
regular expressions — `<figure\b[^>]*>`, `\bwidth=`, "the first `<script
id=…>`", "an array of numbers" — and each divergence from the real grammar
becomes its own bug: example markup inside a comment is treated as live, a
nested comment ends early, a quoted `>` truncates a tag and the rewrite
splices an attribute into a value, `width` matches the tail of
`stroke-width`, markup inside a JavaScript string is synced, and index
expressions count as data. The HTML report tools accumulated nine such
defects across three review rounds before the scanning moved to a parser.

**Invariant:** Locate structure with the language's parser (`html.parser`
reports source offsets; tokenize JavaScript strings and comments before
looking at brackets). Rebuild rewritten tags from parsed attributes rather
than splicing text. Keep regular expressions for flat token classes inside
a region the parser has already delimited. When two tools read the same
format, they share one reader or one contract test, so the writer and the
checker cannot disagree about what a region is.

**Regression evidence:** For each located structure, pin the adversarial
neighbors: the same markup inside a comment, inside a script string, and
inside a quoted attribute value; a quoted `>`; a hyphenated attribute
suffix; nested comments; and a second copy of a "first-only" element. Each
must be ignored, preserved byte-for-byte, or rejected — never rewritten.

**Recurrence (#129):** the `ACTIVE_CONVENTIONS.yaml` repair edited YAML line
by line, and four review rounds each found new valid inputs it corrupted:
block-scalar content read as comments, `|+` trailing blanks stripped, anchors
broken by removing an entry, a quoted multi-line scalar mistaken for the
legacy key, U+2028 split by `splitlines`. Each fix that widened what the
editor accepted invited the next case. What converged was the opposite:
accept exactly the grammar the machine writers emit, refuse everything else
before any mutation, rewrite only files carrying the defect's marker, and let
the real parser (when installed) veto any rewrite that changes the parsed
value. Keep every reviewer's adversarial input in one refusal table that runs
with and without the optional parser.

## 37. An exemption is wider than the evidence that justifies it

**Failure:** A checker exempts a region because something vouches for it,
but the exemption covers more than the voucher does, or the checker accepts
a looser spelling than the consumer it protects. The HTML report linter
exempted a whole registered `<figure>` from prose checks while the content
hash covered only the media between its markers, so a hand-typed paragraph
or image beside the media shipped unchecked. The slide gate accepted any
`data-fig-ref` while the runtime fills only `.slide-figure[data-fig-ref]`,
and the data gate accepted any `<script data-sci-data>` while the runtime
reads only `type="application/json"` — both passed while the page rendered
blank figures.

**Invariant:** An exemption's scope is exactly the scope of its evidence
(the hashed span, the signed file, the tested path) — never the enclosing
element. A gate's notion of "valid" is the consumer's selector, not a
superset: pin the two together with a contract test, and make near-miss
spellings a finding rather than silently accepted.

**Regression evidence:** For each exemption, place unvouched content just
outside the vouched span but inside the exempt container, and require a
finding. For each consumer selector, feed the gate a near-miss (missing
class, missing type) and require a finding, plus a test that asserts the
consumer's selector strings still match what the gate enforces.

## 38. A directive delegates a keyed write without supplying the key

**Failure:** A hook tells the agent to update shared state keyed by an
identity — "update the current session row" in `LOG_REGISTRY.md` — but never
names that identity. The agent infers one (`001-audit-codex` from the log
file name), and the finalizer, keyed by the real ID, appends a second row:
one session, two registry rows. Found by the 0.8.2 Codex lifecycle audit;
Claude's agent skipped the optional step, so only one host showed it.

**Invariant:** A directive that asks for a keyed write supplies the exact key
and, where practical, the exact command; when the key is unresolvable it
tells the agent to leave the write to the lifecycle owner. The owner also
reconciles on a second, unforgeable identity (the session log's unique file
name) so a wrongly keyed agent row folds into the canonical one, keeping the
agent's authored cells.

**Regression evidence:** Assert the injected directive names the exact key;
run the directive's command as written, then the finalizer, and require one
row with the authored cells; seed a row under a guessed key that links the
same log (bare and markdown-link forms) and require it to fold; require a
row for a different log, including a suffix-sharing name, to survive.

## 39. A missing timestamp is read as an ancient one

**Failure:** A lock waiter stats the lock directory in the instant between one
owner's `rmdir` and the next owner's `mkdir`. The mtime helper reports a
missing path as `0`, so the age guard sees a 56-year-old lock and the waiter
plants its reap claim inside the successor's fresh lock. That claim makes the
successor's release `rmdir` fail, which leaves an ownerless lock with a fresh
mtime; every remaining waiter spins out its retry budget and the fail-open
PostToolUse hook drops its event with no stderr. Under heavy load the parallel
child-event test lost paths this way.

**Invariant:** A sentinel for "unknown" never satisfies a staleness threshold:
an age guard acts only on a timestamp it actually read, and the generation it
claims is the one whose owner and age it observed. Anything a contender places
inside a guarded resource must not stop the owner from releasing it: release
keeps its owner record until a transient claim drains, so the claimant
withdraws, and only then removes the directory.

**Regression evidence:** Force the mtime helper to report `0` against a recent
ownerless lock and require that it is neither reaped nor claimed. Plant a reap
claim that drains shortly after release begins and require the lock to be
gone afterward, with the owner record still present while the claim was
held. Under load, instrument reap and release paths and require no
premature reap and no leaked lock across repeated parallel child-event runs.

## 40. A utility's exit status stands in for a kernel guarantee

**Failure:** Directory locks treat `mkdir`'s exit status as an atomic
test-and-set. That holds only while the utility reports `mkdir(2)` faithfully.
uutils coreutils 0.10.0, the default `/usr/bin/mkdir` on Ubuntu 26.04 (GitHub's
`ubuntu-latest` from late October 2026), pre-checks the path and then accepts
any failed create when the path is a directory afterwards, even without `-p`.
Two SessionStarts both "acquired" the repository lock and allocated the same
session log; two dead-owner reapers could both claim one lock. macOS and GNU
Linux CI kept passing, and the Linux job began failing on an unchanged commit
when the runner image moved.

**Invariant:** Where correctness depends on a kernel primitive's exclusivity
(`mkdir`, `O_EXCL`, `link`, `rename` without replacement), invoke the
primitive directly rather than through a userland utility whose error handling
can soften it. Keep the guarantee in one helper (`mycelium_mkdir_exclusive`)
and route every mutex-style call through it; the state runner's one
uncontended `lock:*` attempt is the same bare `os.mkdir`. A plain create-if-missing call
that is not a mutex may keep using the utility.

**Regression evidence:** Put a `mkdir` shim on `PATH` that reproduces the
non-exclusive behavior with a widened race window, then require mutually
exclusive critical sections for concurrent lock holders (free lock and
dead-owner reap) and distinct logs for concurrent distinct-root
SessionStarts. When CI breaks on an unchanged commit, compare the runner image
and the provenance of `mkdir`, `stat`, `date`, and `mktemp` (`--version`,
`dpkg -S`) between the last green and first red runs before suspecting
Mycelium.

## 41. A policy or grammar is implemented for one member of its class

**Failure:** The post-action check exempted "inline one-liners" by skipping
Python `-c` only, so `R -e` opened a bookkeeping cycle. The R `-e` regex
borrowed Rscript's long-option-only grammar, so `R -q -e` never matched at
all: it looked exempt, but only because lineage could not see it either. Two
defects in opposite directions made the behavior depend on which startup flag
the agent happened to type.

**Invariant:** State a policy over the class the documentation names (every
inline form, every interpreter) and enumerate that class in one place. Parse
each interpreter with its own argv grammar — short flags, separated values,
end-of-options markers, and options it honors after the payload — rather than
reusing a sibling's. An exemption must come from the policy, never from a
parse failure that also drops the event elsewhere.

**Regression evidence:** For each member of the class, pair the exempt or
detected form with option variants before and after the payload, and assert
both consumers (post-action and lineage) separately, so a form that vanishes
from both cannot pass as exempt.

## 42. A hot-path scan repeats linear work per start or per match

**Failure:** The lineage detector runs on every shell command with no hook
timeout. Its shell-word regexes begin with a variable-length repetition that
a quoted component can stretch across whitespace, so `finditer` re-walked a
run of quoted words from every start position: `R -f a.R --args` plus 5,000
`"$x"` words took about 58 s. Separately, each candidate match re-tokenized
the whole command before it (and, for AND/OR lists, after it), so 800
`R -q -e` lines or a 1,000-command `&&` chain took seconds. Each scan was
correct; only its repetition was quadratic, and short test commands never
showed it. PR #117 had already fixed an exponential case in the same regex.

**Invariant:** In a hook path, every scan is linear in the command, or its
cost is bounded with a documented fallback. A per-start or per-match helper
must not re-read text an earlier start or match already read: skip starts
that provably cannot match, and resume prefix folds from cached checkpoints.
Each fast path must be exactly equivalent to the plain computation it
replaces, not an approximation. A cache of per-checkpoint states must stay
linear in memory too: share stacks between states and keep summaries rather
than copying histories. The first version of this fix copied the operator
history into every checkpoint, so a 6,000-command `&&` chain held about
150 MB. Pruning start positions does not help inside one `match()` call, so
check each pattern's own repetitions as well: two alternatives that accept
the same word (`--output=x` as a known value option and as a generic
`--name=value`) double the work per repetition, and a lazy word loop that
retries a path at every whitespace split re-reads a word containing escaped
blanks once per split.

**Regression evidence:** Timing tests with generous bounds on long
quoted-word runs, quote and escape runs, many-line commands, and long
`&&`/`||` chains; a `tracemalloc` peak bound on long chains and deep
nesting; and deterministic fuzz tests that compare each
fast path with the plain computation (pruned scan against `finditer`,
cached prefix folds against whole-prefix tokenization, each rewritten
regex against its previous version).

## 43. A shell fast path imitates the interpreter it skips

**Failure:** Moving work out of a `python3` start into bash looks like a pure
speedup, but bash and Python disagree in places no ordinary test reaches.
bash 3.2 keeps double quotes inside a `${v//x/"y"}` replacement while 5.x
removes them, and bash 5.2's default `patsub_replacement` eats backslashes in
an unquoted one. `local LC_ALL=C` is exported to every child when `LC_ALL`
was already exported, so an interpreter started inside that scope decodes
differently. Python decodes argv with the filesystem encoding and stdin with
the locale encoding plus universal newlines, so the same bytes can encode to
two different JSON strings, and `PYTHONIOENCODING` or `PYTHONUTF8` changes
both. Each gap makes the "same" output differ on one host and not another.

**Invariant:** A bash path that replaces an interpreter covers only a class
of inputs whose output it can prove identical, and hands everything else to
the interpreter. Decide the class on bytes in a C-locale scope that starts
no process, pin shell options that change substitution, and gate on the
encodings the interpreter actually reports (the payload preload reports
them) rather than on environment variables. When a whole helper moves into
Python instead, port each check in order, keep the variables bash assigned
before a later check failed, and refuse inputs the old shell code read
inconsistently (NUL bytes) rather than guess.

**Regression evidence:** Differential tests that run both paths on the same
bytes: control characters, CR and CRLF, invalid and partial UTF-8 around the
covered sequences, long strings, across every installed bash, several
locales, `PYTHONIOENCODING`, `PYTHONUTF8`, and `patsub_replacement` on. For a
ported helper, keep a frozen copy of the code it replaced and compare
statuses, variables, output, and filesystem effects on adversarial state
(symlinks, malformed markers and owners, invalid IDs, held locks). Mutate the
port and confirm the comparison fails.

## 44. A per-turn host event is treated as the end of the session

**Failure:** Claude Code and Codex run Stop after every turn, but the
lifecycle treated an accepted Stop as the end of the host session: it removed
the transaction, and every later PostToolUse from that still-live session was
classified as a late delivery and dropped without output. A session that
accepted one idle turn recorded nothing more (no lineage, no log prompt, no
Stop enforcement) until the next resume or compaction. An SNP-tree Claude
session ran about 200 detectable analysis commands and recorded one lineage
event; replaying the same commands through the same hooks one at a time
recorded them all, so unit tests never saw it.

**Invariant:** Model the host's real event cadence, not the name of the event.
An accepted turn closes its transaction; later work in the same host session
opens a new one through the same locked setup a start uses, and never revives
the accepted one. Only events that carry work reopen. Every hook path that
declines a detected command leaves a reasoned record, so silence cannot pass
for "nothing happened".

**Regression evidence:** Accept an idle turn, then run analysis, a data
command, and a project edit; require events, a fresh log, and a blocking Stop.
Make the first command after an idle turn an inline write and require Stop to
block on its file (the reopen must not snapshot after the command it serves).
Publish a valid marker between a hook's failed validation and its reopen
check and require the hook to record; plant a corrupt marker while a reopen
waits and require it byte-identical afterward. Commit in the reopening command
and require Stop to count it; age the carried baseline past any plausible
cleanup threshold and require it still used. Report unowned gaps behind a
corrupt marker and a foreign owner, and keep every record when the shared
file's rewrite fails. Make a turn's only work a shell edit and require Stop to
enforce it; record a script that deleted itself as a skip, never an event
(file existence is part of the execution trust gate); count a repeated skip
once; put U+2028 in a skipped command and require one intact record. Reopen
after a long idle and require a resume to keep the transaction (accounting
time is not the liveness clock), and require a `.living`-only turn to refresh
the carried baseline so its note cannot excuse later work.
Accept a working turn and require its log byte-identical while the next turn
gets a second log. Race parallel hooks through a reopen (one transaction),
keep another live root byte-identical, refuse a symlinked run tree, and require
reads and non-work commands to recreate nothing. Replay a real transcript
through the hooks in order, Stops included, and compare recorded events with
detected commands.

## 45. A recorder that writes only what it can prove leaves the rest silent

**Failure:** The lineage detector records an interpreter run only when it can
prove the run executed, which is the right bar for claiming an analysis. But
every Bash call below that bar left nothing: `head data/x.csv`, `zcat | cut`,
`cp a.tsv outputs/`, a heredoc-fed `python3 - <<'PY'` reading a CSV, an
`Rscript` inside a `for` loop. Replaying the SNP-tree and AP1 transcripts,
19.6% of data-touching commands left an event; the rest were
indistinguishable from "nothing happened", which undercounts every look at the
data. Raising the detector's recall instead would have broken patterns 5, 13,
15, and 33 by claiming executions from text.

**Invariant:** Separate "this ran, with this I/O" from "this call touched
data". The second gets its own record kind that no consumer can mistake for
the first: mentions without direction, a label for unproven runs, and
direction only where the shell's own grammar proves it (redirections and
modelled `cp`/`mv` in an exit-0 `&&` list of literal words). The weaker record
must not drive lifecycle decisions meant for the stronger one (reopening,
post-action prompts, session-work accounting), and a record that waits for a
home is not a gap.

**Regression evidence:** Pair every positive with its quoted, commented,
piped, `||`, `;`, backgrounded, expanded, globbed, heredoc, `cp -t`, and
unknown-exit neighbor; require mentions to stay directionless and analysis
runs to stay actions. Assert the record never reopens, never prompts, and is
not session work; park it without a transaction, keep it through a Stop with
none, and move it at the next owned Stop. Bound scan time on long words,
quoted runs, `&&` chains, many operands, and long heredocs, and replay real
transcripts against an independent classifier.

**Recurrence (#139 review, 16 Codex rounds):** two classes kept returning.
First, every "certain direction" rule was refuted by some way a zero exit
fails to prove it: `exit`/`set -n`/`!`/`time exit` before the command, a
later command that renames the destination or the working directory
(`/bin/mv`, `git mv`, `cp -P` over a symlink), `cp -n`/`-i`, `hash -p`,
`PATH=`, `CDPATH`, a no-op `mv`, a moved symlink, a FIFO swapped in after a
stat. What converged was an allowlist: claim only after commands known not
to move files or change lookup, and stop at anything else. Second, a
hand-written fallback grammar for labelling unproven runs diverged from the
detector's on every round (terminal flags, non-executing modes, payload
boundaries). It converged only when each segment was handed to the
detector's own grammar (pattern 36). Separately, a record that is not
session work must not be named by a session number that an idle turn
releases, or the next session overwrites it.

**Recurrence (#137):** the same silence one layer up. All four post-tool
hooks ran `mycelium_prepare_locked_post_tool_state ... || exit 0`, and that
helper returned 1 both for "this event is not ours" and for "this event is
ours but the session lock timed out". Under lock contention an edit or an
analysis run vanished, and Stop accepted a turn whose only work it never
heard of (a live `claude -p` with the lock held during an analysis run was
accepted with only a mislabeled lineage-gap note). The fix classifies the
failure where it is known (ownership proven, lock op failed), leaves a
work-of-unknown-extent marker Stop enforces, and exposes the outcome so the
data tracker does not then take its own no-transaction fallback for the same
event. Keep the fail-open exit status: evidence, not a blocked tool call.
Five Codex rounds then showed that an evidence channel written without the
session lock needs the whole lifecycle discipline itself: Stop takes it under
the file's own bounded flock and turns work into a fresh work cycle (an
earlier `.living/` update must not satisfy it), consumes only the generation
(inode) it took, rewrites by replace-and-rename, re-checks before every
acceptance, keeps report-only records until an accepting Stop has reported
them, and refuses anything but a single-link regular file reached through
`O_NOFOLLOW` directories.

## 46. Package identity omits file modes or the build recipe

Package content hashes omit executable permissions, while resource-only digests
omit the build recipe. Verify load-bearing modes before doctor/idempotent-install
success and include generator bytes and effective guidance in source identity.
Permission repair must be explicit, content-verified, staged and backed up; it
must not become an override for edited files, links or malformed ownership.

## 47. Initialization guidance sends the agent back into implementation discovery

A correct initializer can remain unexecuted when the skill describes its
outputs as separate tasks without an explicit native invocation. Give the agent
the installed package path, selected interpreter, explicit workspace and a
short fail-fast command sequence. Identify steps the initializer already owns.
Verify that exact documented sequence preserves user
files and creates a valid project. This proves the recipe works offline, not
that a selected model will follow it within a native invocation allowance.

## 48. Batched delivery loses mandatory reminders

A singleton pending reminder loses earlier mandatory guidance when a native
invocation completes several analysis commands. Persist reminders by event ID
until each is acknowledged, including restart and older singleton migration.
Exercise two positives before delivery, partial acknowledgement, duplicate tool
delivery and a negative neighbor; a one-positive-then-delivery test misses this.

## 49. Native asynchronous execution and finalization boundaries

**Failure:** An early RUNNING hook snapshots the previous output, or a lost
Stop response causes completed work to be dispatched again. Resolving a chosen
venv launcher to its base executable also changes the execution environment.

**Invariant:** Completion evidence, output observation, and durable finalization
acknowledgement are separate facts. Keep in-progress commands pending, correlate
terminal evidence to the same step, journal finalizer attempts before dispatch,
and reconcile identity-bound receipts after uncertain delivery. Preserve the
selected interpreter's invocation path while validating its executable target.

**Regression evidence:** Run actual base hooks with an async overwrite and
response loss after real Stop effects; assert final hashes, one action, and no
redispatch. Test the generated hook launcher and child Python against venv-only
packages. Retain unresolved outcomes when the required evidence is unavailable.

## 50. A hook resolves the repository from its own process directory

**Failure:** Claude Code runs a linked-worktree session's hooks in the main
checkout. Hooks that called a bare `git rev-parse --show-toplevel` judged the
session against the main checkout while others recorded its edits in the
worktree: Stop charged another session's dirty files to it, could not see
reflection in the worktree's `.living/`, and blocked until the agent wrote into
the main checkout (#144).

**Invariant:** One session, one root. Every lifecycle hook resolves the
repository from the payload's `cwd`, never a bare process-directory
`git rev-parse`. Stop, the lineage finalizer Stop runs in-process, and the
post-action hook all use `mycelium_session_repo_root`, so they agree:
the `cwd`'s own repository wins when opted in; after `cd` into a nested
repository without `.living/`, an enclosing opted-in repository is taken only
when it already holds `.mycelium/run/<host>/<session_id>/`, so the worktree
keeps the session and an unrelated enclosing project is never adopted.
Otherwise it falls back to the process directory.
Machine-local state that guidance dereferences, such as `.mycelium/plugin-root`,
must exist in whichever checkout the session uses.

**Regression evidence:** Run the real hooks with the process directory in the
main checkout and the payload `cwd` in a linked worktree; dirty the main
checkout from "another session"; assert Stop's reason names only worktree
files, accepts worktree reflection, and leaves the main checkout byte-identical.
Pair it with neighbors whose `cwd` is a nested non-opted repository inside the
main checkout and inside the worktree (the second must still block on the
worktree's changes and publish the worktree's lineage manifest), and one inside
an unrelated opted-in project, which must gain no `.mycelium/` or
`.living/log/`.

## 51. A session that changes checkout splits its transaction

**Failure:** A Claude Code session started in the main checkout, then moved
into a linked worktree (`cd .claude/worktrees/<name>`, or EnterWorktree).
SessionStart had reserved its log and run state in the main checkout; every
later hook resolved the worktree. The first work hook there opened a second
transaction, Stop finalized that one, and the main checkout kept an untracked
stub log, an INDEX count that included it, and stale
`.mycelium/run/<host>/<sid>/` state in another session's working tree. The
agent also saw two handoff paths.

**Invariant:** One session, one transaction. Whatever opens a transaction in
a checkout (SessionStart, or a work hook's reopen) first looks for this
session's reservation in the repository's other linked worktrees
(`git worktree list`, never an unrelated project) and takes it over: carry the
start time and a handoff written at the announced path, remove the stub log
and run state, and recount that checkout's INDEX. A Stop that finds no
transaction in its own checkout but a reservation in a sibling reopens here so
the takeover happens, then runs once more. Shell edits record no activity, so
files that changed in the old checkout since its baseline are carried into
the new transaction and enforced by its Stop; another session's concurrent
edits cost one triage, never silence. So is anything the old checkout still
owed (a carried record not yet enforced, a reminder its Stop started), so a
move back cannot erase it, and a handoff the destination already holds is
appended to, never dropped. Read the moved record without `| head` under
pipefail: an early-exiting consumer kills Stop with SIGPIPE (141) and fails
open on every retry. A reservation with recorded work
(activity, data or unrecorded events, a log the agent wrote into, a tracked
log) is never removed; it stays for its own checkout's Stop. Take that
checkout's lifecycle and session locks before checking and removing, and leave
it alone when either is busy.

**Regression evidence:** Run the real hooks with SessionStart in the main
checkout and later events in a linked worktree while "another session" owns
the main checkout; assert exactly one finalized log across both, the main
checkout byte-identical to its state before this session started, no
`.mycelium/run/<host>/<sid>/` left there, and the carried `started:` and
handoff. Pair it with an idle move (Stop only), a file changed in the main
checkout after the start (named in the worktree's Stop, left in place), a
main -> worktree -> main move with reflection still owed, a moved record larger
than a pipe buffer, an existing destination handoff, a relative
`MYCELIUM_STATE_DIR`, a malformed owner token (never taken), a move
after recorded work in the main checkout (kept and still judged there), and
an unrelated repository (never searched).

## 52. A validator silently replaces what the agent was told to write

**Failure:** The handoff instructions and the handoff validator drift apart.
The instructions said "five-section handoff" with loose section names; the
validator matches exact heading lines and strips Stop-status lines. An
Antigravity App agent wrote a detailed handoff under numbered headings, and
Stop replaced it with the generic fallback without telling the agent. Each
wording fix exposed another case the agent could not see (a subheading-only
section, a stripped "Stop pending" bullet).

**Invariant:** Every instruction that asks for the handoff names the exact
canonical headings in order, and a test checks them against
`finalize_handoff.HANDOFF_SCHEMAS`. A fresh handoff that the validator would
reject blocks Stop once per transaction, with a reason that names the missing,
duplicated, misordered and empty sections and any stripped Stop-status line.
The marker that spends that block holds the transaction's session ID. A retry
that still fails, an absent or stale handoff, and any validator error fall
back without blocking.

**Regression evidence:** Run Stop on a real numbered-heading handoff, a
subheading-only section and a section emptied by Stop-status cleanup: each
blocks once, then falls back on retry. A handoff repaired after the block is
preserved. A complete legacy-schema handoff is accepted without a block, a
validator error fails open, and a marker from another transaction does not
suppress the block.
