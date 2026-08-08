"""Shared speech-adaptation and correction context for 大成佛經 lessons."""
from __future__ import annotations

from google.cloud.speech_v2.types import cloud_speech

MANTRA_TITLE = "《得見彌勒根本大明神咒》"
MANTRA_LINES = (
    "南謨囉怛那怛囉夜耶。",
    "南謨吠嚕左那莎彌儞。",
    "怛他誐多耶。",
    "阿囉喝帝三藐三沒馱耶。",
    "怛姪他。唵。",
    "昧咄侶怛哩。",
    "昧怛囉縛婆悉儞。",
    "昧咄侶怛葛吒耶。",
    "三摩囉三摩囉。",
    "莎剛鉢囉底倪也。",
    "娑囉娑囉。",
    "尾娑囉尾娑囉。",
    "冒馱耶。冒馱耶。",
    "冒馱耨誐帝。",
    "摩訶冒地。",
    "波哩縛哩",
    "底多摩那細 莎訶。",
)
MANTRA_TEXT = "\n".join((MANTRA_TITLE, *MANTRA_LINES))


def speech_adaptation() -> cloud_speech.SpeechAdaptation:
    """Bias Chirp toward the supplied, non-translated mantra spellings."""
    phrase_set = cloud_speech.PhraseSet(
        phrases=[
            cloud_speech.PhraseSet.Phrase(value=MANTRA_TITLE, boost=12),
            *(
                cloud_speech.PhraseSet.Phrase(value=line, boost=12)
                for line in MANTRA_LINES
            ),
        ],
        boost=10,
    )
    return cloud_speech.SpeechAdaptation(
        phrase_sets=[
            cloud_speech.SpeechAdaptation.AdaptationPhraseSet(
                inline_phrase_set=phrase_set,
            )
        ]
    )
