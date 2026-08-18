# Learning Platform M1 scope

Stacked implementation after Reviewer/Admin UX V2. No production deployment, paid generation or YouTube write is authorized by this scope document.

## In scope

### Learner

- shared Google/LINE reviewer identity;
- course catalog from reviewer YouTube videos;
- one canonical playback resume point;
- explicit not-started / in-progress / completed learning state;
- saved courses;
- timestamp bookmarks;
- timestamped personal notes;
- personal learning dashboard;
- one-stop lesson workspace;
- approved subtitle reading;
- AI Study Pack display;
- 3-minute and 10-minute review;
- key-point review;
- Q&A;
- Flashcards and per-card review state;
- quiz and attempt history;
- 1/3/7/14/30 day lesson review queue;
- grounded cross-video text search;
- jump from learning content/search result to source video timestamp;
- direct transition from learning into subtitle共修.

### Owner/admin

- explicit formal-learning-source approval of an immutable subtitle version;
- source/latest-version comparison;
- explicit paid Study Pack generation;
- generation status/history;
- current/stale/missing artifact state;
- learner-view preview.

### AI artifact

- immutable source version ID + SHA;
- model and prompt version;
- idempotent generation for the same source/prompt;
- structured Study Pack;
- server-validated segment references;
- server-rebuilt time/text citations;
- stale detection when subtitle evidence changes;
- fail-closed generation errors.

## Explicitly deferred

These are deliberate later milestones rather than accidental omissions:

- learner-facing generative cross-video Q&A/RAG synthesis;
- vector embeddings / semantic retrieval infrastructure;
- push/email/LINE review reminders;
- organization/class cohorts and teacher dashboards;
- certificates or gamified course scoring;
- paid subscriptions/content access control;
- offline media downloads;
- automatic paid AI generation after every subtitle version;
- hourly/scheduled YouTube playlist synchronization;
- automatic YouTube subtitle publication.

The first two require a learner-facing model cost/rate policy and retrieval-quality acceptance criteria. Notification/scheduling features require a separate operational policy. None should be added to M1 merely because they are technically adjacent.

## Non-negotiable boundaries

- watching to the end does not automatically mark a lesson learned;
- learning completion does not mark subtitle review complete;
- subtitle review completion does not mark learning complete;
- learner reads do not invoke paid models;
- model output cannot become formal without an approved immutable subtitle source;
- learning actions do not call `captions.update`;
- no learning schema replaces or forks the existing logical reviewer identity.
