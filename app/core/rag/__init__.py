"""检索子系统：BM25（中文 bigram）关键词检索，供口径库和文档库使用。

向量与混合检索在 app/knowledge（PostgreSQL + pgvector）中实现。
"""

from app.core.rag.retriever import MultiRetriever

__all__ = ["MultiRetriever"]
