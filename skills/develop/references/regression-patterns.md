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

## 4. Host output is decoded at the wrong layer

**Failure:** Codex model-facing tool responses contain nested or serialized
result JSON, so exit status is lost; a failed command becomes `null`, or a failed
edit counts as activity.

**Invariant:** Normalize host wire formats at the dispatcher boundary. Prefer
structured exit evidence over prose and propagate success/failure explicitly to
shared hooks.

**Regression evidence:** Cover canonical host payloads plus nested `input_text`,
serialized JSON, missing status, conflicting prose, and nonzero exit cases.

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

**Regression evidence:** Retain a sanitized canonical payload fixture. Current
Codex Bash PostToolUse supplies an empty `tool_response`; require lineage to
record the execution with `bash_exit` and `bash_wall_s` as null, while retaining
decoders for richer structured payloads a host may provide later.

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
