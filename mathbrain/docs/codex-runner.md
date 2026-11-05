# Blinded Codex runner

The evaluation transport starts one intended-fresh Codex app-server process and one
ephemeral thread per solver. It requests the pre-registered model and reasoning
effort with provider fallback disabled, uses an empty working directory, and
sets read-only sandboxing with approvals disabled. Shell, web, browser, plugin,
memory, hook, app, environment, and multi-agent capabilities are disabled.
Project-instruction discovery has a zero-byte budget with no fallback filenames,
so neither user-level nor ancestor `AGENTS.md` content enters a solver turn. Local
history persistence is disabled as well.

On Windows the child starts with `CREATE_NO_WINDOW` and BelowNormal priority.
The call is synchronous: stdin is closed after the completed turn and the child
is reaped before the runner returns. A forced termination invalidates the run.
Production execution also requires an explicit ChatGPT `auth.json`. The runner copies that
bounded regular file into a unique per-process `CODEX_HOME`, verifies that authentication is the
only initial entry, and launches with an eight-name environment allowlist rather than inheriting
the evaluator's environment. It scans the transcript and stderr for literal credential values,
then deletes the credential-bearing home and verifies its absence before returning. Artifacts
retain only the public isolation-policy digest, directory identity, SHA-256 of the temporary home
path, and boolean verification facts; they never retain the auth path, auth digest, environment
values, account id, or tokens.

The first command element is the native Codex binary itself. An npm `.cmd`,
PowerShell, shell, or JavaScript launcher is not accepted as executable evidence.
An explicit path must be absolute and resolve to a regular native binary (PE,
ELF, or Mach-O; executable on POSIX). The runner hashes that resolved file before
`Popen`, passes the same canonical path as command element zero, then hashes and
stats it again after the child is reaped. On Windows it holds a `CreateFileW`
handle sharing read access only from before the first hash through the second;
later write and delete opens, including swap-and-restore attempts, are denied.
A path, file identity, size, timestamp, or SHA-256 change invalidates the run.
POSIX cannot claim the same mandatory pathname protection from an open descriptor,
so its artifact records the lock as unenforced and cannot pass the strict gate.
This is not code signing or hostile-kernel attestation.

## Evidence boundary

The exact launch command, canonical executable path, executable size and SHA-256, domain-separated
command-tail policy digest, initialize messages, thread/start request, turn/start request, prompt
text, and canonical output-schema bytes are retained with the run result. Command element zero must
equal the retained executable path. The tail must contain the complete no-tool disable list, and the
requests must match the locked instructions, sandbox,
approval, fallback, environment, model, effort, and timeout policy. The resolved runtime
directory's device/file identity is also retained for the attempt ledger, so lexical path aliases
cannot count as fresh sandboxes. Every JSONL message is byte-bounded, retained in order, and hashed.
The run is
ineligible if the stream contains a command, file operation, MCP call, dynamic
tool, web request, subagent event, hook, model reroute, server request, unknown
raw response item, malformed completion, or anything other than one final
assistant message. Raw transcripts belong under ignored `runs/` storage and
must pass privacy review before any summary is published.

`rawResponseItem/completed` messages are also inspected as model-visible Responses API context.
The legacy test profile requires the exact base instruction, developer instruction, and sealed
prompt. Production desktop runs use a separately pinned profile for Codex 0.144.0-alpha.4. It
requires five input messages in one exact order and grouping: three ambient desktop blocks, one
environment block, and the sealed prompt. Every wrapper, role, content-part field, and hidden turn
identifier is checked. Ambient preimages remain private: tracked source contains only SHA-256
commitments. The host-local date must equal the calendar date derived from the accepted thread
timestamp, and exactly five dynamic capability paths must share the sterile `codexHome`; both
values are normalized only after independent validation. The accepted thread, sandbox,
instruction-source,
workspace-root, provider, and multi-agent fields are exact. The five inputs must be contiguous
between `turn/started` and the exact public user lifecycle, and the raw assistant output must match
the one public assistant lifecycle. Added, removed, regrouped, reordered, or altered context fails
closed. A production run cannot fall back to the looser legacy profile.

This desktop profile exposes two limits rather than hiding them. The outbound `thread/start`
request contains `baseInstructions`, but the current raw input events do not show them, so their
model visibility is unattested. Symmetric ambient product context is present even though its
capabilities are disabled. A no-brain arm therefore means no supplied experimental brain packet,
not an empty product context.

The event audit is stateful. Protocol responses preserve request causality; thread, turn, and item
starts precede their completions; deltas require an active item; turn-bound events remain inside
the turn; and only token-usage updates or an idle status may trail completion. A missing lifecycle
event is not interpreted as zero tool use or zero follow-ups.

Codex CLI 0.144 can emit three inert status methods during a no-tool turn:
`remoteControl/status/changed`, `mcpServer/startupStatus/updated`, and
`account/rateLimits/updated`. The locked initializer asks app-server to suppress exactly these
notifications using `optOutNotificationMethods`. Because suppression is a client capability rather
than proof that a notification cannot arrive, the auditor still accepts only these exact methods
as a defense-in-depth fallback. They are not broad MCP or remote-control exceptions. The auditor
accepts only the exact methods and payload shapes observed in a disabled remote-control session;
remote control must remain `disabled`, MCP startup may only move through non-error `starting` and
`ready` states bound to the expected thread, and the rate-limit event must contain only a status
mapping. An MCP tool call, another `mcpServer/*` method, an enabled remote-control status, a foreign
thread id, an error state, or an extra field still fails closed.

The same build may emit one turn-bound `model/verification` event. Its complete compact-JSON vector
is accepted only under a pinned SHA-256 commitment, and only after the user item completes and
before the assistant item starts. Any other value, shape, field, binding, placement, or duplicate
fails closed. The vector is treated as opaque access-related metadata, not served-model attestation.

The `thread/start` response establishes only that the requested model, provider,
and effort were accepted by the local app server. The current protocol has no
positive response-side attestation of the model that served the turn. Therefore
`model_identity_verified` remains false even for an otherwise eligible run. A
`model/rerouted` event proves a mismatch and invalidates the run; the absence of
that event does not prove serving identity, and `model/verification` does not change that result.

The constrained output schema is defense in depth. Evaluation code must still
parse the final text independently, enforce exact assignment completeness, and
replay repairs and witnesses without trusting solver-supplied booleans.
Runtime and isolation receipts are derived from the self-checking run artifact;
caller-asserted prompt, transcript, response, thread, turn, or treatment digests
cannot satisfy the benefit gate. The evaluator rebinds the retained prompt and
schema to the sealed batch and the exact output text to the scored observations.
Opaque run-spec, batch, and dispatch commitments prevent replay from an otherwise
identical-looking plan or slot.

The content-derived `protocol_execution_id` binds the server thread/turn, transcript, prompt,
schema, and sent request policy. It deliberately excludes launcher paths, timeouts, directory
identities, stderr, local timing, and the locally generated nonce: none of those fields proves a
new server turn. Consequently the protocol ID is not a freshness certificate. A durable
first-attempt ledger must bind those local facts before any real evaluation launch, and local
elapsed time remains diagnostic until that ledger attests its start and completion markers.

`CodexRunResult` proves internal consistency inside the trusted evaluator process; it is not a
signature or external attestation. A caller that can synthesize arbitrary Python objects and raw
transcripts can synthesize a self-consistent result. Evidence crossing a persistence, API, or
untrusted-process boundary therefore needs authenticated append-only capture. The current app
server also exposes no enforced `max_output_tokens` request field, so that pre-registered budget is
reported as unenforced and blocks a strict benefit claim.

The evaluation plan may precommit an `executable_provenance` object under `[runtime]` with exact
`canonical_path`, `size_bytes`, and `sha256_digest` fields. The sealed runtime-policy digest also
commits the command-tail digest derived from the requested model, effort, and full fixed argument
list. Omitting executable provenance is allowed for descriptive or portable configurations, but
`executable_provenance_unsealed` then blocks a strict benefit claim. A present commitment must match
every artifact exactly; a mismatch is rejected before it becomes runtime evidence. Strict evidence
also requires the production `subprocess.Popen` factory and the enforced executable lease. The
injectable process factory exists for deterministic transport tests, but artifacts made through it
are marked nonstandard and cannot support a benefit claim.
