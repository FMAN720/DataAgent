"""第 09 课：离线检查真正交给 ReAct 的 system 消息。"""
from course_support import ScriptedLLM, demo_resources, prepare_output, reply, run

from app.analytics.prompts import build_system_prompt
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.memory.session import InMemorySessionStore
from app.models.enums import AgentMode


async def main(registry):
    system = build_system_prompt("sqlite", "2025-12-31")
    print("[1] 正式 system prompt\n", system)
    def inspect(messages, kwargs):
        print("\n[2] ReAct 实际收到的 system 消息\n", messages[0]["content"])
        assert "2025-12-31" in messages[0]["content"]
        assert "分析计划" in messages[0]["content"]
        return reply("这是剧本答案，只演示 prompt 拼装，不评价效果。")
    llm = ScriptedLLM([reply('{"steps":["查询口径","按月统计后比较"]}'), inspect])
    agent = AgentOrchestrator(llm, registry, InMemorySessionStore(), system_prompt=system,
                              enable_reflection=False)
    result = await agent.run("比较 11 月和 12 月销售额", mode=AgentMode.PLAN_EXECUTE)
    print("\n[3] 实际 chat 次数（含规划，均为假模型）:", len(llm.calls))
    print("响应里的 llm_calls（仅 ReAct）:", result.llm_calls)


if __name__ == "__main__":
    prepare_output()
    with demo_resources() as (_, __, registry):
        run(main(registry))
