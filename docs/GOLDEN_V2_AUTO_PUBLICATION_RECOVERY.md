# Synthetic auto-publication recovery and its limits

Real Drive publication remains disabled. The state machine is exercised only with
injected synthetic gateways; it is not a production deployment authorization.

* A committed SQLite fence blocks changes to the job and stage rows, including
  REPLACE conflicts on rowid and the ledger's existing primary-key indexes,
  even with SQLite's default `recursive_triggers=OFF`. An old, incomplete, or
  unbound fence cannot be resumed automatically. These additive triggers are
  installed when the publisher is invoked; existing prototype databases are
  not protected against REPLACE by the new triggers until that installation
  completes. The protection assumes the current job/stage unique indexes: any
  future additional unique index requires a matching conflict guard and tests.
* Completed receipt replay is read-only with respect to remote files, intents,
  and delivery/fence rows: all IDs, final names, ownership, parent, inventory,
  and contents must match; drift is rejected, not repaired or promoted.
* Before any remote content create, the fence durably records the manifest hash,
  exact owner grant hash, approved source Drive ID and version, source preflight
  hash, observed source parent, and all four complete intents including file IDs.
  A crash while constructing this binding leaves the fence in place.
* A bound retry validates the grant, source, parent, manifest, all intents, target
  and pending name collisions, owner metadata, and content readback before any
  new remote content write. It does not allocate replacement IDs. A missing
  bound object is safe to create only if the gateway can authoritatively prove
  that reserved ID was never created. The persistent test fake records this in
  a transactional history that survives object deletion; a Drive 404 is not such
  proof. Missing proof, missing intent, or drift stops with the fence intact.
* Four staged contents are checked before promotion. Final-name visibility may
  nevertheless be partial after a crash or concurrent edit. The receipt and
  fence release commit together in SQLite, not atomically with Drive.
* Local `flock` and SQLite triggers coordinate cooperating local processes and
  ledger writes only. They cannot lock untrusted third-party Drive editors.
  Name checks, metadata reads, readbacks, creates, promotions, and the receipt
  commit have external time-of-check/time-of-use gaps. A source or destination
  may change immediately after a check, including after final readback. There is
  no claim of atomic multi-file publication, exclusive remote ownership, or a
  remote-and-SQLite transaction. Verified provider-side preconditions and an
  authoritative missing-ID reconciliation protocol are required before any
  production gateway is enabled.

## Google Drive pre-generated IDs: evidence and decision

The [Drive upload guide](https://developers.google.com/workspace/drive/api/guides/manage-uploads#use_a_pre-generated_id_to_upload_files)
states: “You can safely retry uploads with pre-generated IDs if there's an
indeterminate server error or timeout. If the file action is successful,
subsequent retries return a `409 Conflict` HTTP status code response and
duplicate files aren't created.” The [file creation guide](https://developers.google.com/workspace/drive/api/guides/create-file#generate-ids)
also states that IDs may be passed in the `id` field of create/copy and that
successful creates/copies return 409 on retries. The guides exclude most
Google Workspace MIME types and conversion uploads. These guarantees are not
a blanket proof that an object with a 404 was never created, or that its ID
cannot be reused after deletion or loss of permissions.

The [Drive error guide](https://developers.google.com/workspace/drive/api/guides/handle-errors#404_errors)
explicitly says: “This error occurs when the user doesn't have read access to
a file, or the file doesn't exist.” A delete and an inaccessible existing
object are therefore distinct histories behind the same GET 404. Neither
Google guide above states whether the 409/uniqueness guarantee survives
permanent deletion or permission changes, nor provides authoritative proof
that an inaccessible/absent generated ID was never created. Do not turn a
404 into permission to replay a create. The synthetic tombstone database is
stronger than the documented Drive observations and is not a Drive feature.

The actual `DriveDeliveryGateway.create_pending` first makes a metadata-only
`POST /drive/v3/files` containing the generated ID, then separately sends a
media `PATCH /upload/drive/v3/files/{id}`. It is not a multipart create:
successful metadata creation followed by a crash can leave an empty file.
The documented retry behavior for the create does not establish completion of
the later media PATCH, and a 409 alone does not verify ownership, parent,
name, bytes, source revision, or collisions. The gateway currently maps GET
404 to `None`, converts non-2xx (including 409) to `V2Conflict`, and exposes
no authoritative never-created proof. It does not recover a 409 as success.
The auto publisher explicitly rejects this real gateway; the separate
`publish_granted_outputs` path has its own behavior and is not certified by
these tests or by this decision.

**Decision:** no Google-specific automatic replay or real auto-publication is
enabled. The inability to distinguish never-submitted from deleted/inaccessible
on restart conflicts with the required fail-closed 404 policy. The persistent
fake can safely create the former only because its durable tombstones prove
that history; production Drive cannot be assumed to supply that proof. Tests
simulate a committed response lost to a crash and independently simulate a
409 on a metadata POST; they do not establish a Drive-backed end-to-end 409
recovery protocol. An empty or corrupt object is not repaired automatically.

**Owner/design options before enabling:**

1. Keep the fence and require an authorized operator to reconcile the exact
   IDs, source, contents, permissions, and collisions, with an explicit
   owner-approved decision for any ambiguous/deleted object; do not silently
   allocate replacements or promise atomic multi-file publication.
2. Obtain an authoritative Google guarantee or provider-supported durable
   history that distinguishes never-created from deletion/access loss, and
   specify its scope (permanent delete, credentials, shared drives, and time).
   A local “request sent” flag alone cannot close the crash gap.
3. Redesign around a supported exact-ID single-request upload where applicable,
   explicit verification on 409, provider-enforced concurrency controls or a
   documented exclusive-writer trust model, then test crash windows, source
   drift, permission changes, and promotion/receipt races before enabling.
   This is an option to investigate, not an established safety guarantee.
