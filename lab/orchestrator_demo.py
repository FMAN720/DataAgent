"""第 10 课：规划、消息拼装、会话记忆与规划失败降级。"""
from course_support import ScriptedLLM, demo_resources, prepare_output, reply, run, tool_call

from app.analytics.prompts import build_system_prompt
from app.core.agent.orchestrator import AgentOrchestrator
from app.core.agent.planner import PlannerAgent
from app.core.memory.session import InMemorySessionStore
from app.models.enums import AgentMode


async def main(registry):
    sessions = InMemorySessionStore(max_turns=2)
    def follow_up(messages, kwargs):
        print("第二轮角色:", [m["role"] for m in messages])
        assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
        assert messages[1]["content"] == "有哪些区域？"
        assert "分析计划" not in messages[0]["content"]
        return reply("这是一次追问演示；假模型不会真正理解问题。")
    llm = ScriptedLLM([
        reply('{"steps":["查看区域表","读取区域名单"]}'),
        reply("", tool_call("run_sql", sql="SELECT region_name FROM regions ORDER BY region_code")),
        reply("共有 5 个区域，详见查询结果。"),
        follow_up,
    ])
    agent = AgentOrchestrator(llm, registry, sessions,
                              system_prompt=build_system_prompt("sqlite", "2025-12-31"),
                              enable_reflection=False)
    first = await agent.run("有哪些区域？", "course-session", mode=AgentMode.PLAN_EXECUTE)
    print("计划:", first.plan)
    print("工具:", [s.tool for s in first.steps])
    print("会话历史:", await sessions.get_history(first.session_id))
    await agent.run("那华东呢？", first.session_id)
    print("第二次问题追加后，历史条数:", len(await sessions.get_history(first.session_id)))
    print("\n规划器不可用:")
    plan = await PlannerAgent(ScriptedLLM([RuntimeError("模拟模型离线")])).plan("复杂问题")
    print("降级后的计划:", plan.steps)
    assert plan.steps == []


if __name__ == "__main__":
    prepare_output()
    with demo_resources() as (_, __, registry):
        run(main(registry))
