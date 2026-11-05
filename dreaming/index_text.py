"""Pure, budgeted index text helpers; importing this module performs no I/O."""
import re
import os

_TAGS = re.compile(r"^(?:\s*\[[A-Z][A-Z -]*\]\s*)+")
_QUESTION = re.compile(r"(?i)^(?:why|how|what|which|who|whom|whose|where|when|is|are|was|were|do|does|did|can|"
                       r"could|should|would|will|has|have|had|may|might|must)\b")


def index_row_text(desc, budget):
    """Existing answer-first policy, followed by boundary-safe shortening."""
    if os.environ.get("BRAIN_INDEX_ANSWER_FIRST", "1") == "0":
        return shorten_index_text(desc, budget)
    text = _TAGS.sub("", desc or "").strip()
    q = text.find("?")
    if _QUESTION.match(text) and 0 < q <= 200 and len(text) - q - 1 >= 20:
        text = text[q + 1:].strip()
    return shorten_index_text(text, budget)


def shorten_index_text(text, budget):
    """Keep a complete clause when useful, otherwise complete whitespace tokens.

    Budget counts characters, as the existing index writers do. A token longer
    than the entire budget is omitted rather than split. No ellipsis is added.
    """
    budget = max(0, int(budget))
    text = " ".join((text or "").split())
    if len(text) <= budget:
        return text
    # A previously shortened prefix already fits exactly. Keep it stable on
    # the next nightly pass instead of selecting an earlier clause again.
    if budget and text[budget].isspace():
        return text[:budget].rstrip()
    # Include punctuation only when followed by whitespace: don't split URLs,
    # decimal numbers, file names, hyphenated words or identifiers.
    clauses = [m.end() for m in re.finditer(r"[;,.!?](?=\s|$)", text)
               if m.end() <= budget]
    if clauses and clauses[-1] >= budget / 2:
        return text[:clauses[-1]].rstrip()
    boundaries = [m.start() for m in re.finditer(r"\s+", text)
                  if m.start() <= budget]
    return text[:boundaries[-1]].rstrip() if boundaries else ""


def frontmatter_description(path):
    """Read only a bounded frontmatter description; no body-derived summary."""
    try:
        with open(path, encoding="utf-8") as handle:
            head = handle.read(16384)
    except (OSError, UnicodeError):
        return ""
    fm = re.match(r"\ufeff?---[^\S\n]*\n(.*?)\n---(?:\s|$)", head, re.S)
    if not fm:
        return ""
    match = re.search(r"^description:[ \t]*(.*)$", fm.group(1), re.M)
    if not match:
        return ""
    value = match.group(1).strip()
    if value in ("|", ">", "|-", ">-", "|+", ">+"):
        continuation = fm.group(1)[match.end():].splitlines()
        parts = []
        for line in continuation:
            if line and not line[0].isspace():
                break
            parts.append(line.strip())
        value = " ".join(parts)
    elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        quote = value[0]
        value = value[1:-1]
        value = (value.replace('\\"', '"').replace('\\\\', '\\')
                 if quote == '"' else value.replace("''", "'"))
    return " ".join(value.split())
