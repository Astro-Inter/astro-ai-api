import re


_FENCED_CODE = re.compile(r"```[^\n`]*\n?(.*?)```", re.DOTALL)
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_TABLE_SEPARATOR = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_HORIZONTAL_RULE = re.compile(r"^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$")


def markdown_para_texto_simples(value: str) -> str:
    """Remove sintaxe Markdown sem alterar o conteúdo textual da resposta."""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _FENCED_CODE.sub(lambda match: match.group(1).strip("\n"), text)
    text = _IMAGE.sub(
        lambda match: f"{match.group(1) or 'Imagem'}: {match.group(2)}",
        text,
    )
    text = _LINK.sub(lambda match: f"{match.group(1)} ({match.group(2)})", text)
    text = re.sub(r"<((?:https?://|mailto:)[^>]+)>", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)

    lines: list[str] = []
    for line in text.split("\n"):
        if _TABLE_SEPARATOR.fullmatch(line) or _HORIZONTAL_RULE.fullmatch(line):
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"^\s{0,3}>\s?", "", line)
        line = re.sub(r"^(\s*)[-+*]\s+", r"\1• ", line)
        line = re.sub(r"^(\s*)(\d+)[.)]\s+", r"\1\2) ", line)
        line = re.sub(r"^(\s*)•\s+\[x\]\s+", r"\1☑ ", line, flags=re.IGNORECASE)
        line = re.sub(r"^(\s*)•\s+\[ \]\s+", r"\1☐ ", line)
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            line = line.replace("|", " | ").strip(" |")
            line = re.sub(r"\s+\|\s+", " | ", line)
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"(?<!\w)([*_])([^*_\n]+?)\1(?!\w)", r"\2", text)
    text = re.sub(r"\\([\\`*{}\[\]()#+\-.!_>])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
