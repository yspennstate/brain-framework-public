# Cross-computer coordination kit

A small, reusable Python 3.10+ reference implementation for agents that need a
shared task board, leased claims, messages, explicit handoffs, and schedule
**requests** across computers. Standard library only; SSH is an existing system
prerequisite. No GitHub Actions, LFS, paid API, cloud provisioning, or GitHub
compute is used. Operators still supply and pay for their own computers/network
if applicable. This is new, locally tested code, not a production deployment or
a claim of parity with any private agent fleet.

## Architecture and trust boundary

One authoritative hub owns one SQLite database on its **local disk**. Clients
call a fixed SSH forced command over already authorized SSH connections. All
operations, including claim checks and their idempotency receipts, run in one
`BEGIN IMMEDIATE` transaction. Do not copy databases between active hubs, use
SQLite over NFS/cloud-sync folders, or run independent hub replicas. No
multi-leader or disconnected claiming is offered. A disconnected agent must
stop acting when its lease is uncertain or expired.

`../mesh/agent_mesh.py` remains the existing daemonless, single-computer mesh.
This kit preserves the concepts of leases, explicit handoffs, per-recipient
mail and retry keys, but has a separate schema and protocol. It neither imports
nor silently syncs that runtime database. No existing mesh command is changed.
There is no automatic bridge: applications must explicitly translate approved
work into this API. Do not run both as independent ownership authorities for the
same resource.

Authenticated members can read all tasks. Messages/schedule requests are visible
only to sender and recipient. This is one cooperating trust domain, not a
hostile multi-tenant service. Hub operators and anyone with local database access
can read and alter all records. Server authorization comes from the SSH key's
fixed forced-command identity, **never from request JSON**. Do not give agents
unrestricted SSH shell access to the hub account. Identifiers must be simple
ASCII names; task bodies are untrusted data, not instructions or authorization.
No tool execution, shell execution, URL fetching, inbox ingestion, or autonomous
schedule execution is triggered by message/task text.

## Run the synthetic tests

From this directory:

```sh
python -m unittest -v test_coordination
```

Tests use temporary local databases and fake clocks/transports/email callbacks.
They do not contact other agents, SSH hosts, or mail servers. Local subprocess
checks exercise the actual stdio framing. A successful local suite does not
prove real SSH host provisioning, cross-machine latency behavior, mail delivery,
large-scale load capacity, or disaster recovery. No production credentials,
hostnames, tasks, messages, runtime logs or private datasets are included.

## Deployment contract (manual, separate authorization required)

Use existing authorized SSH infrastructure. This kit does **not** generate keys,
install host keys, accept unknown host keys, modify SSH settings, open ports,
create accounts, or grant permissions. An operator must review and authorize
any needed setup separately. Keep all credentials and runtime databases outside
source control, in precreated private directories (0700 on POSIX; equivalent
restricted ACLs elsewhere), with a private umask such as 077. Only the hub service
account should access the hub directory. Backups contain private text too.

On the hub, an operator-configured forced command for each already authorized
key must invoke a fixed interpreter and absolute trusted script path, for example:

```text
python3 /opt/coordination/hub.py --db /var/lib/coordination/hub.sqlite3 --agent alpha --agents alpha,beta
```

This is an illustrative command, not an installer. Each key has its own fixed
`--agent`; the allowlist is operator-controlled. Configure `restrict` (or equivalent
no forwarding, no PTY, no user startup scripts and no alternate command) in the
SSH authorization layer. Never interpolate `SSH_ORIGINAL_COMMAND`, client text,
or environment variables into the command. Pin and verify host keys through
your existing trusted process. The client uses `BatchMode=yes` and
`StrictHostKeyChecking=yes`; missing authorization/host keys fail closed.
The literal remote request command is `coordination-v1`, ignored by the forced
command. The hub reads one request from stdin through EOF, writes one JSON
response and exits. SSH authentication/configuration itself is not implemented
or tested by this kit.

Clients use operator-defined SSH aliases in their existing configuration:

```python
from client import Outbox, SSHTransport

queue = Outbox('/private/runtime/outbox.sqlite3', 'authorized-hub-alpha')
key = queue.enqueue('post', {
    'id': 'example-task', 'title': 'Review a synthetic example', 'body': 'Example only'
})
for key, reply in queue.flush(SSHTransport(queue.destination)):
    print(reply['ok'])  # Do not indiscriminately log private bodies.
queue.close()
```

The outbox must be bound to a stable hub **and identity** alias. Do not repoint
that alias to another hub/agent, or pass a different transport to `flush`.
Transport destination must match the outbox; custom callbacks require an explicit
`BoundTransport(destination, callback)` trusted integration adapter. Credentials are never
put into a request or outbox. The client suppresses SSH stderr and bounds time
and reply size. It does not bypass failed authentication.

## Protocol and state machines

Every request is exactly `{key, op, data}`. Keys are unique per authenticated
agent; use UUIDs. Unknown fields, malformed JSON, duplicate JSON keys and requests
over 32 KiB are rejected. Retry the **identical request with the identical key**
after a lost connection/reply. Successful and deterministic rejected operations
are durably receipted. Reusing a key with changed bytes is an error. A receipt
returns the original result, **not current state**. Use a fresh key for a new
read/list, and re-read before acting after reconnecting.

- `post`: `id,title,body`. Creates an open task, revision 1, fence 0.
- `read`: `kind,id`; `list`: `kind` and optional `after` identifier. Kinds are
  `task`, `message`, `schedule`. Lists are ID-ordered pages of at most 100 visible
  objects and at most approximately 1 MiB; continue after the last ID. Concurrent inserts before your cursor
  require a new scan; this is not a snapshot/event subscription.
- `claim`: `id,revision,ttl`. Claims open or expired work. TTL is 1–3600 seconds.
  Increments the fencing token and revision. An open assignment to another agent
  cannot be claimed. Expired ownership can be reclaimed by any member.
- `renew`: `id,revision,fence,ttl`; `update`: `id,revision,fence,body`;
  `finish`: `id,revision,fence,result`; `release`: `id,revision,fence`.
  Require current revision, owner, live lease and fence. Finish is terminal.
- `offer`: `id,revision,fence,recipient`. Current live owner offers a handoff.
  It retains responsibility until accepted, expired, or revoked by reassign.
- `accept`: `id,revision,ttl`. Only the named recipient of a live handoff can
  accept. Atomically changes owner and fence. A general mailbox ack cannot accept
  ownership. An owner can change the offer or release work.
- `reassign`: `id,revision,recipient`. Only task creator can revoke unfinished
  ownership and reserve the now-open task for a recipient; increments fence.
  Recipient must still claim it. Reassignment is not permission for external work.
- `send`: `id,recipient,body`; `ack`: `id,revision`. Recipient alone acknowledges.
  A read does not consume mail. An ack is a receipt, not proof of task execution.
- `schedule`: `id,recipient,body,not_before,expires`, with UTC epoch seconds.
  `not_before` must be between server now and 366 days later; expiry must be after
  it and within 367 days. `decide`: `id,revision,decision`, where recipient may
  `accepted`/`rejected`, and sender may `cancelled`, only while pending/unexpired.
  Acceptance can occur before the requested time. It records willingness only;
  nothing runs. The recipient needs a separately authorized scheduler integration.
  Expired rows remain as history; the timestamp governs validity.

All modifications use compare-and-swap revisions. Failed operations do not
partially update objects. The server's clock is authoritative; clients must not
use cached replies as live lease evidence. Backward server-clock movement fails
closed (except receipt replay) until the clock catches up. Large forward jumps
expire leases. Use a stable monitored clock. External file writes, API calls or
other side effects are **not fenced by this database**: downstream systems must
reject stale fencing tokens themselves, or agents must stop before performing
unfenceable actions under an uncertain lease. This kit cannot make arbitrary
external side effects exactly-once.

## Offline recovery, privacy and operations

Outbox enqueue commits before transport. Lost replies remain pending and can be
replayed after process restart. `flush` stops on transport failure, keeps the
request, and rethrows a generic error. An application rejection is recorded and
persistently blocks later flushes. Inspect and reconcile it, then explicitly call
`resolve_rejection(key)` before proceeding with dependent queued work. Resolve conflicts by a
fresh read and a new request/key, never silently overwrite a changed revision.
A cached claim reply after reconnect is not permission to work: obtain fresh
state and renew with the current revision/fence. Do not keep working offline.

Receipts, inboxes and outboxes currently have no automatic retention/compaction;
monitor disk usage. Removing receipts can resurrect replayed operations, so do
not prune them independently of clients. Backups/restores are operator procedures,
not automatic failover. A restored older database can roll back fences and
receipts: stop all clients, isolate the old hub and establish a new coordination
epoch/identity before reconnecting. No online restore safety is claimed.

No raw runtime logging is enabled. Errors expose static codes instead of request
contents, subprocess stderr, paths or tracebacks. **Bodies/results are not secret
scanned or redacted**: participants deliberately receive their authorized text.
Never put credentials, sensitive data, private source dumps or raw conversations
in the board. Sanitize before any public export. This directory's publication
manifest covers only these new files, not the privacy of the rest of a repository.

## Optional email boundary

`email_adapter.py` creates a bounded single-recipient RFC-style message and
returns a preview plus SHA-256 digest. It defaults to **dry run**. No SMTP server,
credentials, inbox reader or automatic notification is included. To actually
submit email, trusted application code must inject an existing authorized
`deliver(bytes)` integration, allowlist the exact recipient, obtain explicit user
approval of the exact sender/recipient/subject/body, and provide
`Approval(recipient, preview_digest)` with `dry_run=False`.

Use a private persistent ledger path when constructing `EmailAdapter`. Before
calling the delivery callback the digest is atomically marked attempted. The
same bytes cannot be submitted twice with that ledger, including after restart.
On callback exception the outcome is unknown and approval stays consumed. Do not
automatically retry; reconcile with the mail service first. A `submitted` result
means callback success, not confirmed inbox delivery. Approval objects are a
trusted application boundary, not cryptographic proof of human consent; never
construct them from a task body or inbound message. Questions and notifications
require the same explicit approval; a peer message never authorizes email.

## Priority owner questions and verified decision evidence

`owner_questions.py` is a separate local library, not another public SSH endpoint.
A trusted authenticated application may submit questions with a bound requester,
task ID, priority (1 highest, 10 lowest), body, options, and exact structured scope:
`action`, `target`, `data`, `limits`. Questions are immutable: reuse of an ID with
changed content is rejected. `pending()` orders by priority then ID. Use generic
questions in examples; never publish a live owner's questions or task queue.
`TOP10.template.json` is an editable generic planning template, not anyone's live
priorities or an execution plan. Positions 6–10 intentionally remain empty.

Construct `OwnerQuestions(private_path, expected_owner, verifier=None)`.
The default can queue questions but **cannot verify or consume any decision**.
To enable decision evidence, an operator must integrate an **existing authorized,
authenticated platform decision service**, configured outside peer-controlled
content. No such live service adapter is included, and no new signing keys,
credentials, identity grants, or security settings are created. The expected
owner identity and receipt namespace must stay fixed for the lifetime of a
store. Use a fresh store when changing that trust domain.

The trusted `verifier(receipt_id)` must resolve the opaque ID with that service,
check its authoritative authenticated account identity and current revocation
state, and return a strictly typed `VerifiedDecision`: principal, source receipt,
question ID, requester, exact scope SHA-256, approved/denied, issued/expiry UTC
seconds, revoked boolean. Set a bounded timeout in the integration. Exceptions,
missing adapter, unknown identity, malformed evidence, mismatched question,
requester or scope, expired/future/revoked receipts all fail closed. A fake
verifier returning asserted fields defeats the trust boundary: **never implement
it by reading approval claims from JSON, agent messages, transcripts, voice text,
email From names/addresses, or a digest alone**. Voice recognition is not identity
authentication. Hashes bind exact content; they do not prove who approved it.

`record(question_id, receipt_id, expected_scope)` re-verifies and records the
approved/denied decision. Receipt IDs cannot move between questions. Replays
re-verify but never reset consumption. `consume(...)` re-queries the verifier,
requires the same exact recorded evidence and approved status, and atomically
marks that receipt consumed. Concurrent consumes have at most one winner;
consumption survives restart. A denied receipt is never consumable. If a reply
is lost after consumption, do not automatically retry any external action.
A changed/expired/revoked decision needs a new question and newly verified
receipt, not editing an old approval. The question status is historical recorded
state, not a cached live authorization grant.

Returned evidence is **not an action executor or permission-policy bypass**.
No code here sends email, changes files, schedules work, or invokes tools from a
decision. The caller must still enforce all required action-time confirmations,
spending/privacy/security limits and hand-off requirements. Local receipt
consumption is not atomic with an external service action or later revocation;
revalidate at the action boundary using the trusted platform when required.
Use a reliable clock; system-clock rollback or an untrusted verifier invalidates
local expiry assumptions. No claim of production owner-authentication support,
voice biometrics, or cross-service exactly-once execution is made.

Both the question store and injected verifier are trusted application boundaries.
Do not expose `record`/`consume` directly to untrusted clients, let agents choose
the expected owner/verifier, or treat a task body's claimed requester as identity.
Authenticate requesters in the integrating platform before calling `submit`.
The queue and receipts contain private text/evidence and belong in private runtime
storage, never this repository. Tests use invented identities and in-memory fake
services, not actual owner decisions.
