"""Context management: output truncation and history compaction."""

from . import config


def truncate(text, max_chars=None):
    """Keep the head and tail of long text, marking what was dropped."""
    if max_chars is None:
        max_chars = config.TOOL_OUTPUT_MAX_CHARS
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    omitted = len(text) - head - tail
    return "%s\n\n[... %d characters omitted ...]\n\n%s" % (text[:head], omitted, text[-tail:])


def maybe_compact(messages, last_prompt_tokens, threshold=None, keep_recent_turns=None):
    """Replace old tool outputs with a placeholder once the prompt is too large.

    Only acts above the threshold, so the history (and the provider's prompt
    cache) stays unchanged on ordinary turns. A "turn" is one assistant
    message; tool results belonging to the last `keep_recent_turns`
    assistant messages are kept intact. Returns the number of messages
    compacted.
    """
    if threshold is None:
        threshold = config.COMPACT_TOKEN_THRESHOLD
    if keep_recent_turns is None:
        keep_recent_turns = config.COMPACT_KEEP_RECENT_TURNS
    if not last_prompt_tokens or last_prompt_tokens <= threshold:
        return 0

    assistant_idx = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if len(assistant_idx) <= keep_recent_turns:
        return 0
    cutoff = assistant_idx[-keep_recent_turns]

    count = 0
    for i in range(cutoff):
        msg = messages[i]
        if msg.get("role") == "tool" and msg.get("content") != config.COMPACTED_PLACEHOLDER:
            msg["content"] = config.COMPACTED_PLACEHOLDER
            count += 1
    return count
