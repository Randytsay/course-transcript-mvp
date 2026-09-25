from __future__ import annotations

SCRIPTURE_KEY = "dacheng_scripture"
MANTRA_KEY = "dacheng_mantra"

SCRIPTURE_TITLE = "《佛說彌勒大成佛經》"
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
MANTRA_BODY = "\n".join(MANTRA_LINES)

SUPPORTED_KEYS = (SCRIPTURE_KEY, MANTRA_KEY)
DEFAULT_TITLES = {
    SCRIPTURE_KEY: SCRIPTURE_TITLE,
    MANTRA_KEY: MANTRA_TITLE,
}
