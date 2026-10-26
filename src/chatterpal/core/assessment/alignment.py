"""Word sequence edits describe transcripts, never acoustic phoneme accuracy."""

import re
from typing import Any, Dict, List


def words(text: str) -> List[str]:
    return re.findall(r"\w+(?:['’]\w+)*", text.casefold().replace("’", "'"))


def align_text(recognized_text: str, target_text: str) -> Dict[str, Any]:
    """Minimum word edit alignment, retaining order and every repeated token."""
    target, recognized = words(target_text), words(recognized_text)
    rows, columns = len(target), len(recognized)
    costs = [[0] * (columns + 1) for _ in range(rows + 1)]
    for i in range(rows, -1, -1):
        for j in range(columns, -1, -1):
            if i == rows:
                costs[i][j] = columns - j
            elif j == columns:
                costs[i][j] = rows - i
            elif target[i] == recognized[j]:
                costs[i][j] = costs[i + 1][j + 1]
            else:
                costs[i][j] = 1 + min(costs[i + 1][j], costs[i][j + 1], costs[i + 1][j + 1])
    alignment = []
    i = j = 0
    while i < rows or j < columns:
        target_index, recognized_index = i, j
        if i < rows and j < columns and target[i] == recognized[j]:
            operation, target_word, recognized_word = "equal", target[i], recognized[j]
            i, j = i + 1, j + 1
        elif i < rows and costs[i][j] == 1 + costs[i + 1][j]:
            operation, target_word, recognized_word = "delete", target[i], ""
            i += 1
        elif j < columns and costs[i][j] == 1 + costs[i][j + 1]:
            operation, target_word, recognized_word = "insert", "", recognized[j]
            j += 1
        else:
            operation, target_word, recognized_word = "substitute", target[i], recognized[j]
            i, j = i + 1, j + 1
        alignment.append({
            "operation": operation,
            "target_word": target_word,
            "recognized_word": recognized_word,
            "target_index": target_index if target_word else None,
            "recognized_index": recognized_index if recognized_word else None,
        })
    return {
        "alignment": alignment,
        "edit_distance": costs[0][0],
        "target_word_count": rows,
        "recognized_word_count": columns,
        "score": max(0.0, 100 * (1 - costs[0][0] / rows)) if rows and columns else None,
    }
