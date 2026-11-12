"""第 11 课：证据、审查、一次修正与复核、审查故障不算通过。"""
import json

from course_support import ScriptedLLM, demo_resources, prepare_output, reply, run, tool_call

from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.reflection import ReflectionAgent
from app.core.memory.session import InMemorySessionStore


async def main(registry):
    def inspect_evidence(messages, kwargs):
        evidence = messages[-1]["content"]
        print("[1] 审查模型实际看到的输入\n", evidence)
        assert "SELECT COUNT(*) FROM regions" in evidence
        assert '[[5]]' in evidence
        return reply(json.dumps({"passed": False, "quality_score": 20,
                                 "issues": ["SQL 结果为 5，答案却说 6"], "summary": "数字不一致"},
                                ensure_ascii=False))
    llm = ScriptedLLM([
        reply("", tool_call("run_sql", sql="SELECT COUNT(*) FROM regions")),
        reply("共有 6 个区域。"),
        inspect_evidence,
        reply("修正：共有 5 个区域。"),
        # 修正后再审查一次；通过时必须给出逐项结论和实际存在的 E 编号
        reply(json.dumps({"passed": True, "quality_score": 95, "issues": [], "summary": "修正后数字一致",
                          "checks": [{"claim": "共有 5 个区域", "status": "supported",
                                      "evidence_ids": ["E1"], "reason": "SQL 结果为 5"}]},
                         ensure_ascii=False)),
    ])
    agent = AgentOrchestrator(llm, registry, InMemorySessionStore(), system_prompt="SYSTEM")
    result = await agent.run("有几个区域？")
    print("\n[2] 最终回答:", result.answer)
    print("revised:", result.revised, "复核状态:", result.reflection_status.value)
    print("实际 chat 次数:", len(llm.calls), "响应 llm_calls:", result.llm_calls,
          "其中执行与修正:", result.execution_llm_calls)
    # 执行 2 次 + 审查 1 次 + 修正 1 次 + 复核 1 次；llm_calls 包含两次审查
    assert len(llm.calls) == 5 and result.llm_calls == 5 and result.execution_llm_calls == 3
    assert result.revised and result.reflection.passed
    report = await ReflectionAgent(ScriptedLLM([RuntimeError("审查服务不可用")])).reflect(
        "有几个区域", "5 个", ["SQL 结果 [[5]]"])
    print("\n[3] 审查故障:", report.to_dict())
    # 审查服务故障时返回 unavailable，passed 和分数都是 None，不会伪装成通过
    assert report.passed is None and report.quality_score is None and report.error
    assert report.status.value == "unavailable"


if __name__ == "__main__":
    prepare_output()
    with demo_resources() as (_, __, registry):
        run(main(registry))
