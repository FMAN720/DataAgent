"""
第 03 课练习：一步一步看清 Function Calling 的每一个环节。

用一个假的「查天气」工具做演示（数据是写死的，不联网），读取项目 .env 里的模型配置。

在 project-python 目录下运行：
    python lab/function_calling.py once       # 实验 1：带上 tools 提问，看模型返回的 tool_calls
    python lab/function_calling.py roundtrip  # 实验 2：执行工具、回传结果，走完完整一轮
    python lab/function_calling.py parallel   # 实验 3：一次问两个城市，看并行 tool_calls
    python lab/function_calling.py notool     # 实验 4：问题不需要工具时，模型直接回答
    python lab/function_calling.py schema     # 实验 5：看项目里 run_sql 工具的 schema（不调用模型）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI  # noqa: E402

from app.config import get_settings  # noqa: E402

# ------------------------------------------------------------------ 工具定义
# 这一段是「菜单」：告诉模型有哪些工具、每个工具要什么参数。格式是 JSON Schema。
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询某个城市今天的天气",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，例如「北京」"},
            },
            "required": ["city"],
        },
    },
}

FAKE_WEATHER = {
    "北京": {"weather": "晴", "temp_c": 18},
    "上海": {"weather": "小雨", "temp_c": 21},
    "广州": {"weather": "多云", "temp_c": 27},
}


def get_weather(city: str) -> dict:
    """真正「做菜」的函数：由我们的程序执行，不是模型执行。"""
    if city not in FAKE_WEATHER:
        return {"error": f"没有 {city} 的数据，只支持: {', '.join(FAKE_WEATHER)}"}
    return {"city": city, **FAKE_WEATHER[city]}


# ------------------------------------------------------------------ 公共函数
def make_client() -> tuple[OpenAI, str]:
    s = get_settings()
    if not s.openai_api_key or s.openai_api_key == "sk-your-key":
        sys.exit("请先在 .env 里填写 OPENAI_API_KEY")
    return OpenAI(api_key=s.openai_api_key, base_url=s.openai_api_base or None), s.openai_model


def show_message(msg) -> None:
    """把模型返回的 message 打印成易读的样子。"""
    choice_content = msg.content
    print(f"  content:    {choice_content!r}")
    if not msg.tool_calls:
        print("  tool_calls: None")
        return
    print(f"  tool_calls: 共 {len(msg.tool_calls)} 个")
    for tc in msg.tool_calls:
        print(f"    - id:        {tc.id}")
        print(f"      name:      {tc.function.name}")
        print(f"      arguments: {tc.function.arguments!r}   <- 注意这是字符串")


def show_messages(messages: list[dict]) -> None:
    for i, m in enumerate(messages):
        extra = ""
        if m.get("tool_calls"):
            extra = "  tool_calls=" + ", ".join(
                f"{tc['function']['name']}({tc['function']['arguments']}) id={tc['id']}" for tc in m["tool_calls"]
            )
        if m.get("tool_call_id"):
            extra = f"  tool_call_id={m['tool_call_id']}"
        print(f"  [{i}] {m['role']:<9} content={m.get('content')!r}{extra}")


def assistant_message(msg) -> dict:
    """把模型返回的 message 转成可以追加回 messages 的字典（项目里对应 LLMResponse.to_assistant_message）。"""
    out: dict = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        out["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    return out


# ------------------------------------------------------------------ 实验 1
def exp_once() -> None:
    client, model = make_client()
    messages = [{"role": "user", "content": "北京今天天气怎么样？"}]
    resp = client.chat.completions.create(model=model, messages=messages, tools=[WEATHER_TOOL], temperature=0)
    choice = resp.choices[0]

    print("发出去：1 条 user 消息 + 1 个工具（get_weather）\n")
    print(f"finish_reason: {choice.finish_reason}")
    print("模型返回的 message:")
    show_message(choice.message)
    print("\n到这里为止，get_weather 函数一次都没有被执行过。模型只是「点了菜」。")


# ------------------------------------------------------------------ 实验 2
def exp_roundtrip() -> None:
    client, model = make_client()
    messages: list[dict] = [{"role": "user", "content": "北京今天天气怎么样？"}]

    print("===== 第 1 次请求模型 =====")
    resp = client.chat.completions.create(model=model, messages=messages, tools=[WEATHER_TOOL], temperature=0)
    msg = resp.choices[0].message
    print(f"finish_reason: {resp.choices[0].finish_reason}")
    show_message(msg)

    # 1) 先把模型的「点菜单」原样追加进 messages
    messages.append(assistant_message(msg))

    # 2) 我们的程序逐个执行工具，把结果作为 role=tool 消息追加，并带上对应的 tool_call_id
    print("\n===== 程序执行工具 =====")
    for tc in msg.tool_calls or []:
        args = json.loads(tc.function.arguments)          # 字符串 → 字典
        result = get_weather(**args)                      # 真正执行
        print(f"  get_weather({args}) → {result}")
        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": json.dumps(result, ensure_ascii=False),
        })

    print("\n===== 第 2 次请求模型，发出去的 messages =====")
    show_messages(messages)
    resp2 = client.chat.completions.create(model=model, messages=messages, tools=[WEATHER_TOOL], temperature=0)
    print(f"\nfinish_reason: {resp2.choices[0].finish_reason}")
    print(f"最终回答: {resp2.choices[0].message.content}")


# ------------------------------------------------------------------ 实验 3
def exp_parallel() -> None:
    client, model = make_client()
    messages = [{"role": "user", "content": "北京和上海今天哪个更暖和？"}]
    resp = client.chat.completions.create(model=model, messages=messages, tools=[WEATHER_TOOL], temperature=0)
    print(f"finish_reason: {resp.choices[0].finish_reason}")
    show_message(resp.choices[0].message)
    print("\n一次返回多个 tool_calls 时，它们互不依赖，程序可以同时执行。")
    print("每个结果都要用各自的 tool_call_id 对上号，模型才知道哪个结果属于哪次调用。")


# ------------------------------------------------------------------ 实验 4
def exp_notool() -> None:
    client, model = make_client()
    for q in ("你好，你能做什么？", "把「天气」翻译成英文"):
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": q}], tools=[WEATHER_TOOL], temperature=0, max_tokens=150
        )
        print(f"===== 问：{q} =====")
        print(f"finish_reason: {resp.choices[0].finish_reason}")
        show_message(resp.choices[0].message)
        print()
    print("给了工具不代表一定会用。用不用、用哪个，由模型根据问题和工具描述自己决定。")


# ------------------------------------------------------------------ 实验 5
def exp_schema() -> None:
    from loguru import logger

    from app.analytics import AnalyticsDatabase, GlossaryStore, build_analytics_tools
    from app.demo import ensure_demo_db

    logger.remove()
    s = get_settings()
    if s.analytics_db_url.startswith("sqlite:///"):
        ensure_demo_db(Path(s.analytics_db_url[len("sqlite:///"):]))
    db = AnalyticsDatabase(s.analytics_db_url)
    tools = build_analytics_tools(db, GlossaryStore.from_file(s.glossary_path))
    print("项目里 5 个工具的名字:", [t.name for t in tools])
    run_sql = next(t for t in tools if t.name == "run_sql")
    print("\nrun_sql 工具发给模型的 schema（由 BaseTool.to_openai_tool() 生成）:\n")
    print(json.dumps(run_sql.to_openai_tool(), ensure_ascii=False, indent=2))


EXPERIMENTS = {
    "once": exp_once,
    "roundtrip": exp_roundtrip,
    "parallel": exp_parallel,
    "notool": exp_notool,
    "schema": exp_schema,
}

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2 or sys.argv[1] not in EXPERIMENTS:
        print(__doc__)
        sys.exit(0)
    EXPERIMENTS[sys.argv[1]]()
