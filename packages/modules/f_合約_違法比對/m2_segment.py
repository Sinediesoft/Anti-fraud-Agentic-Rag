"""條款切分。

規則式拆條號/項號，不碰模型。保留原文 offset，方便報告回指「第幾條」。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

# 「第 X 條」「第 X 條之 X」「X、」等常見合約條款起始樣式
_CLAUSE_HEAD = re.compile(
    r"第\s*[一二三四五六七八九十百千0-9]+\s*條(?:之[一二三四五六七八九十0-9]+)?"
)


class Clause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clause_no: str
    text: str
    start: int
    end: int


def segment(masked_text: str) -> list[Clause]:
    """把去識別化過的合約全文切成一條條 Clause。

    TODO：v1 先實作最常見的「第 X 條」樣式；沒有明顯條號的合約
    （條列用 1./2./(一)(二) 等）留到之後補規則。
    """
    heads = list(_CLAUSE_HEAD.finditer(masked_text))
    clauses: list[Clause] = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(masked_text)
        clauses.append(
            Clause(
                clause_no=head.group(0),
                text=masked_text[head.start() : end].strip(),
                start=head.start(),
                end=end,
            )
        )
    return clauses
