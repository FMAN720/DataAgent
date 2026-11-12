"""BM25 关键词检索（中文 bigram）与 RRF 融合；口径库和文档库共用。"""

from __future__ import annotations

import asyncio
import math
import re
from collections import defaultdict

from loguru import logger

from app.models.schemas import RetrievalResult

_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_CJK_RUN = re.compile(r"[一-鿿]+")


def _tokenize(text: str) -> list[str]:
    """
    中英文分词：英文/数字/下划线按词切分；中文按字二元组（bigram）切分。

    中文没有空格，按单词正则切分会把整句当成一个 token，BM25 几乎无法命中。
    bigram 不需要词典，对「退款率」「客单价」这类短术语召回足够。
    """
    lower = text.lower()
    tokens = _ASCII_WORD.findall(lower)
    for run in _CJK_RUN.findall(lower):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class BM25Index:
    """内存 BM25（Okapi）索引，用于关键词检索。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._doc_ids: list[str] = []
        self._documents: list[str] = []
        self._doc_freqs: list[dict[str, int]] = []
        self._doc_lens: list[int] = []
        self._avgdl: float = 0.0
        self._df: defaultdict[str, int] = defaultdict(int)
        self._N: int = 0
        self._idf: dict[str, float] = {}

    def clear(self) -> None:
        """清空索引。"""
        self._doc_ids.clear()
        self._documents.clear()
        self._doc_freqs.clear()
        self._doc_lens.clear()
        self._avgdl = 0.0
        self._df.clear()
        self._N = 0
        self._idf.clear()

    def add_document(self, doc_id: str, text: str) -> None:
        """添加文档并更新统计量。"""
        tokens = _tokenize(text)
        tf: defaultdict[str, int] = defaultdict(int)
        for t in tokens:
            tf[t] += 1
        for t in tf:
            self._df[t] += 1
        self._doc_ids.append(doc_id)
        self._documents.append(text)
        self._doc_freqs.append(dict(tf))
        self._doc_lens.append(len(tokens))
        self._N += 1
        dl_sum = sum(self._doc_lens)
        self._avgdl = dl_sum / self._N if self._N else 0.0
        # 重新计算 IDF（对当前语料）
        self._idf = {}
        for term, df in self._df.items():
            # 平滑 IDF
            self._idf[term] = math.log(1.0 + (self._N - df + 0.5) / (df + 0.5))

    def add_documents(self, documents: dict[str, str]) -> None:
        """一次构建统计量，避免逐块重算整个词频表的平方开销。"""
        self.clear()
        for doc_id, content in documents.items():
            tf: defaultdict[str, int] = defaultdict(int)
            for term in _tokenize(content):
                tf[term] += 1
            self._doc_ids.append(doc_id)
            self._documents.append(content)
            self._doc_freqs.append(dict(tf))
            self._doc_lens.append(sum(tf.values()))
            for term in tf:
                self._df[term] += 1
        self._N = len(self._doc_ids)
        self._avgdl = sum(self._doc_lens) / self._N if self._N else 0
        self._idf = {
            term: math.log(1 + (self._N - df + 0.5) / (df + 0.5)) for term, df in self._df.items()
        }

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """返回 (doc_id, bm25_score) 降序。"""
        if self._N == 0:
            return []
        q_terms = _tokenize(query)
        if not q_terms:
            return []
        scores: dict[str, float] = {}
        for i, doc_id in enumerate(self._doc_ids):
            tf = self._doc_freqs[i]
            dl = self._doc_lens[i]
            s = 0.0
            for term in q_terms:
                if term not in tf:
                    continue
                idf = self._idf.get(term, 0.0)
                f = tf[term]
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self._avgdl or 1.0))
                s += idf * (f * (self.k1 + 1)) / denom
            if s > 0:
                scores[doc_id] = s
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return ranked


class MultiRetriever:
    """BM25 关键词检索，附带 RRF 融合工具。

    向量检索已由 ``app/knowledge``（PostgreSQL + pgvector）实现；这里只保留进程内的关键词检索。
    """

    def __init__(self) -> None:
        self._bm25 = BM25Index()
        # doc_id -> 全文，用于 BM25 结果组装
        self._id_to_text: dict[str, str] = {}
        self._rrf_k: int = 60

    def register_keyword_documents(self, id_to_text: dict[str, str]) -> None:
        """
        注册用于 BM25 的文档。

        :param id_to_text: 文档 ID 到文本的映射
        """
        self._bm25.clear()
        self._id_to_text.clear()
        for did, text in id_to_text.items():
            self._bm25.add_document(did, text)
            self._id_to_text[did] = text
        logger.info("BM25 索引已更新，文档数: {}", len(id_to_text))

    async def keyword_search(self, query: str, top_k: int) -> list[RetrievalResult]:
        """BM25 关键词检索（需先 register_keyword_documents）。"""

        def _run() -> list[RetrievalResult]:
            ranked = self._bm25.search(query, top_k)
            out: list[RetrievalResult] = []
            for doc_id, sc in ranked:
                text = self._id_to_text.get(doc_id, "")
                out.append(
                    RetrievalResult(
                        id=doc_id,
                        content=text,
                        score=float(sc),
                        metadata={},
                        source="keyword",
                    )
                )
            return out

        try:
            return await asyncio.to_thread(_run)
        except Exception as e:
            logger.exception("BM25 检索失败: {}", e)
            raise RuntimeError(f"BM25 检索失败: {e}") from e

    def _rrf_fuse(
        self,
        lists: list[list[RetrievalResult]],
        top_k: int,
    ) -> list[RetrievalResult]:
        """倒数排名融合（RRF），按 id 合并多路结果。"""
        scores: dict[str, float] = {}
        id_best: dict[str, RetrievalResult] = {}
        k = self._rrf_k
        for lst in lists:
            for rank, item in enumerate(lst):
                rid = item.id
                scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank + 1)
                if rid not in id_best or item.score > id_best[rid].score:
                    id_best[rid] = item
        merged_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:top_k]
        merged: list[RetrievalResult] = []
        for rid in merged_ids:
            base = id_best[rid].model_copy(deep=True)
            base.score = scores[rid]
            base.metadata = {**base.metadata, "rrf": scores[rid]}
            base.source = "hybrid"
            merged.append(base)
        return merged
