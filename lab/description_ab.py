"""
第 06 课实验：工具的名字和 description 写得好不好，会不会影响真模型？

同一个问题、同一个工具（真实的口径检索），只改「模型看到的说明」，对比三个版本：
  A 好名字 + 好描述：项目原文 search_glossary + 完整 description
  B 好名字 + 差描述：search_glossary + 「搜索。」
  C 差名字 + 差描述：tool_x + 「搜索。」

为了只看工具说明本身的作用，把 system prompt 里提到「查口径」的两句话去掉了。
统计：模型有没有用这个工具、最终答案对不对（正确答案 1,047,258.23 元）。

需要 API Key，每次运行大约调用模型 50～80 次，消耗少量额度。结果每次可能略有不同。

在 project-python 目录下运行：
    python lab/description_ab.py        # 每个版本问 3 次
    python lab/description_ab.py 5      # 每个版本问 5 次
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loguru import logger  # noqa: E402
from tools_cli import build_registry  # noqa: E402

from app.analytics.prompts import build_system_prompt  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.agent.react_agent import ReActAgent  # noqa: E402
from app.infrastructure.llm.model_router import ModelConfig, ModelRouter  # noqa: E402

QUESTION = "上个月的销售额是多少？"
CORRECT = ("1,047,258.23", "1047258.23")

# system prompt 里提到「查口径」的两句，去掉后只剩工具说明在起作用
PROMPT_HINTS = [
    "2. 问题涉及业务指标或术语（销售额、GMV、退款率、活跃用户、客单价、区域、渠道等）时，写 SQL 前先用 search_glossary 查口径，严格按口径写 SQL。\n",
    "- 结尾用一句话说明口径，例如「口径：净销售额，已排除测试账号，按下单时间统计」。\n",
]

VARIANTS = {
    "A 好名字 + 好描述": None,
    "B 好名字 + 差描述": ("search_glossary", "搜索。", "关键词"),
    "C 差名字 + 差描述": ("tool_x", "搜索。", "关键词"),
}


def build_system() -> str:
    prompt = build_system_prompt("sqlite", "2025-12-31")
    for hint in PROMPT_HINTS:
        assert hint in prompt, "prompts.py 改过了，请同步更新 PROMPT_HINTS"
        prompt = prompt.replace(hint, "")
    return prompt


def build_tools(variant):
    registry = build_registry()
    if variant is None:
        return registry, "search_glossary"
    name, desc, param_desc = variant
    tool = registry._tools.pop("search_glossary")   # 取出真实的口径工具，只改它的「说明」
    tool.name = name
    tool.description = desc
    tool.parameters[0].description = param_desc
    registry._tools[name] = tool
    return registry, name


async def ask_once(llm, variant) -> tuple[bool, bool]:
    registry, glossary_name = build_tools(variant)
    agent = ReActAgent(llm, registry, max_steps=10)
    result = await agent.run([{"role": "system", "content": build_system()}, {"role": "user", "content": QUESTION}])
    tools_in_order = [s.tool for s in result.steps]
    used = glossary_name in tools_in_order
    correct = any(c in result.final_answer for c in CORRECT)
    print(f"    {' → '.join(tools_in_order)}")
    print(f"    用了口径工具: {'是' if used else '否'}    答案正确: {'是' if correct else '否'}")
    return used, correct


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    s = get_settings()
    if not s.openai_api_key or s.openai_api_key == "sk-your-key":
        sys.exit("请先在 .env 里填写 OPENAI_API_KEY")
    llm = ModelRouter([ModelConfig(model_id=s.openai_model, api_key=s.openai_api_key, base_url=s.openai_api_base or None)])

    summary = {}
    for label, variant in VARIANTS.items():
        print(f"===== {label}，问 {n} 次 =====")
        used_n = correct_n = 0
        for i in range(n):
            print(f"  第 {i + 1} 次")
            used, correct = await ask_once(llm, variant)
            used_n += used
            correct_n += correct
        summary[label] = (used_n, correct_n)
        print()

    print("===== 汇总 =====")
    for label, (used_n, correct_n) in summary.items():
        print(f"  {label}：用了口径工具 {used_n}/{n} 次，答案正确 {correct_n}/{n} 次")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logger.remove()
    asyncio.run(main())
