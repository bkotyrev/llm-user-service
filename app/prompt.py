"""Prompt construction for the summarization scenario."""

SYSTEM_PROMPT = (
    "Ты — краткий и точный редактор. Суммируй сообщение пользователя на русском языке. "
    "Выдели главную мысль и ключевые факты в 2–4 коротких предложениях или пунктах. "
    "Не добавляй сведения, которых нет в исходном тексте."
)


def build_prompt(message: str) -> tuple[str, str]:
    return SYSTEM_PROMPT, message
