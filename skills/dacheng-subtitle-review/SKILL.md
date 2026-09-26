# 佛說彌勒大成佛經字幕智慧校對 Skill v1

## Scope

This skill is intentionally limited to CBETA T14 No.0456
《佛說彌勒大成佛經》 and the configured
《得見彌勒根本大明神咒》 canonical text. It is not a generic course
transcript skill.

## Required inputs

1. One existing raw/QA SRT. No Chirp request is made by this skill.
2. One human transcript TXT for the same lesson.
3. Explicit content_mode=dacheng_buddhist. Filenames never enable this mode.

## Evidence priority

1. The scripture sentence currently being explained.
2. The scripture passage actually recited in this lesson.
3. Active CBETA T14 No.0456 canonical text.
4. Historical human-reviewed Golden Corpus.
5. Error Memory / Golden Rules.
6. General Buddhist terminology.
7. Ordinary Chinese.

Human text wins only on portions supported by ordered alignment evidence.
Human omissions preserve the source subtitle. Source omissions remain visible
in the bidirectional report unless reliable source timing supports restoration.
Raw ASR evidence is immutable.

## Review workflow

blind audit of pre-existing learning state
-> human/SRT ordered alignment
-> CBETA scripture sequence/context alignment
-> lecture-context-safe human correction
-> Golden Corpus / Error Memory / Golden Rules audit
-> bidirectional human_missing/source_missing report
-> canonical mantra leader-response folding
-> semantic cue repair
-> strict QA
-> SRT + QA JSON + completeness TXT + ZIP

Formal scripture is never replaced from phonetic similarity alone. Canonical
publication requires ordered exact anchors and maps only the passage actually
spoken onto source timing. Lecturer paraphrases are not forced back to
scripture wording.

For the mantra, raw leader and congregation speech remains untouched. The
display layer folds each verified leader-response pair to one canonical line
and preserves every verified whole recitation cycle.

## Learning boundary

Review mode never writes the current lesson to Golden Corpus, Error Memory or
Golden Rules. It only emits high/medium/low candidates. Learn Lesson is a
separate post-review operation and must not run until the lesson has explicit
human approval. This prevents a lesson from teaching the system its own
blind-audit answer.

## Command

PYTHONPATH=. python scripts/review_dacheng_lesson.py
  --srt INPUT.srt
  --human-txt HUMAN.txt
  --data-dir /opt/course-transcript-source/data
  --output-dir OUTPUT
  --lesson-id 20
  --lesson-date 20260823
  --content-mode dacheng_buddhist
  --stem lesson20_20260823_review

This command is deterministic and has no Chirp, Gemini, MiniMax, OpenRouter or
other paid-provider call path.
