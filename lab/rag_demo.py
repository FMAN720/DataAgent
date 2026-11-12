"""第 08 课：真实分块、中文分词、BM25 检索与 RRF；口径库不用向量服务。"""
from course_support import ROOT, prepare_output, run

from app.analytics.glossary import GlossaryStore, split_markdown_sections
from app.core.rag.retriever import MultiRetriever, _tokenize
from app.models.schemas import RetrievalResult


async def main():
    text = (ROOT / "app/demo/glossary.md").read_text(encoding="utf-8")
    entries = split_markdown_sections(text)
    print("切块数量:", len(entries))
    print("标题:", "、".join(e.title for e in entries))
    print("退款率 的 token:", _tokenize("退款率"))
    print("orders.pay_amount 的 token:", _tokenize("orders.pay_amount"))
    store = GlossaryStore(entries)
    for query in ["退款率", "销售额", "活跃用户", "火星矿石"]:
        hits = await store.search(query)
        print(f"\nquery={query!r} =>", [e.title for e in hits])
    print("\nRRF 实验（下面两份榜单是手写的，未调用向量模型）")
    def item(rid):
        return RetrievalResult(id=rid, content=rid, score=1)
    retriever = MultiRetriever()
    fused = retriever._rrf_fuse([[item("A"), item("B")], [item("B"), item("C")]], 3)
    for hit in fused:
        print(hit.id, round(hit.score, 6))
    assert fused[0].id == "B"


if __name__ == "__main__":
    prepare_output()
    run(main())
