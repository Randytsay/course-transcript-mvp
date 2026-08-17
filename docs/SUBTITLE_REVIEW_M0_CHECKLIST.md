# Subtitle Review M0 verification checklist

This checklist is deliberately limited to the persistence contract. OAuth,
YouTube API calls, reviewer UI, owner approval, and YouTube publishing are later
milestones.

## Automated tests

Run the focused module with the same standard-library test runner used by CI:

```bash
python -m unittest discover -s tests -p 'test_review_store.py' -v
```

The repository CI runs the complete suite with:

```bash
python -m unittest discover -s tests -v
```

The review-store tests verify:

- human-oriented changed-character counting;
- first login auto-creates an active reviewer;
- repeated provider identity resolves to the same logical reviewer;
- Google and LINE identities can be explicitly linked to one logical reviewer;
- an external identity cannot be silently linked to two reviewers;
- initial subtitle import refuses destructive re-import;
- a submitted suggestion immediately appears in contribution statistics;
- submitting a suggestion does not change the segment working subtitle;
- revising one pending suggestion does not increment suggestion count;
- reviewed-through progress is monotonic while playback position may move;
- per-user contribution detail includes video and completion totals.

## Regression boundary

The branch should modify only:

- `app/review/*`
- `tests/test_review_store.py`
- subtitle-review/database documentation

It must not change existing Chirp/Gemini/M3 provider code, job state transitions,
Drive publishing, existing subtitle-editor behavior, Docker secrets, or production
route authentication.

## CI expectation

The repository's normal CI should run after a draft PR is opened. M0 is ready for
merge only after the updated branch completes CI successfully.
