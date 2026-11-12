"""
第 02 课练习：用代码直接和大模型对话，理解 messages、角色、temperature 和 token。

读取项目 .env 里的 OPENAI_API_KEY / OPENAI_API_BASE / OPENAI_MODEL，
用的是和项目相同的 openai SDK。每个实验会调用 1～6 次模型，消耗少量额度。

在 project-python 目录下运行：
    python lab/chat_basics.py hello        # 实验 1：最简单的一次调用，看完整返回
    python lab/chat_basics.py system       # 实验 2：同一个问题，换不同的 system 消息
    python lab/chat_basics.py memory       # 实验 3：模型没有记忆，「记忆」是你每次重发历史
    python lab/chat_basics.py temperature  # 实验 4：temperature 0 和 1.3 各跑 3 次
    python lab/chat_basics.py tokens       # 实验 5：数 token（不调用模型，不花钱）
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI  # noqa: E402

from app.config import get_settings  # noqa: E402


def make_client() -> tuple[OpenAI, str]:
    s = get_settings()
    if not s.openai_api_key or s.openai_api_key == "sk-your-key":
        sys.exit("请先在 .env 里填写 OPENAI_API_KEY")
    return OpenAI(api_key=s.openai_api_key, base_url=s.openai_api_base or None), s.openai_model


def ask(client: OpenAI, model: str, messages: list[dict], **kwargs) -> str:
    """发一次请求，返回模型回复的文字。"""
    resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------- 实验 1
def exp_hello() -> None:
    client, model = make_client()
    messages = [
        {"role": "user", "content": "用一句话介绍你自己"},
    ]
    resp = client.chat.completions.create(model=model, messages=messages, temperature=0)

    print("发出去的 messages:")
    print(f"  {messages}\n")
    print("模型回复 (resp.choices[0].message.content):")
    print(f"  {resp.choices[0].message.content}\n")
    print(f"结束原因 finish_reason: {resp.choices[0].finish_reason}")
    print(f"实际使用的模型: {resp.model}")
    u = resp.usage
    print(f"token 用量: 输入 {u.prompt_tokens} + 输出 {u.completion_tokens} = 共 {u.total_tokens}")


# ---------------------------------------------------------------- 实验 2
def exp_system() -> None:
    client, model = make_client()
    question = "上个月的销售额是多少？"
    systems = {
        "没有 system": None,
        "数据分析师": "你是电商数据分析师。你无法访问任何数据库。如果没有数据，必须明确说明，绝不编造数字。",
        "海盗": "你是一个说话像海盗的助手，每句话都要带「嘿哟」。回答不超过两句。",
    }
    for label, system in systems.items():
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": question})
        print(f"===== {label} =====")
        print(ask(client, model, messages, temperature=0, max_tokens=200))
        print()


# ---------------------------------------------------------------- 实验 3
def exp_memory() -> None:
    client, model = make_client()

    print("===== 第一次请求 =====")
    first = [{"role": "user", "content": "我叫小王，我在学 Agent。请记住我的名字。"}]
    reply1 = ask(client, model, first, temperature=0, max_tokens=100)
    print(f"模型: {reply1}\n")

    print("===== 第二次请求：只发新问题，不带历史 =====")
    second = [{"role": "user", "content": "我叫什么名字？"}]
    print(f"发出去的 messages 有 {len(second)} 条")
    print(f"模型: {ask(client, model, second, temperature=0, max_tokens=100)}\n")

    print("===== 第三次请求：把历史一起发过去 =====")
    third = first + [
        {"role": "assistant", "content": reply1},
        {"role": "user", "content": "我叫什么名字？"},
    ]
    print(f"发出去的 messages 有 {len(third)} 条: " + " → ".join(m["role"] for m in third))
    print(f"模型: {ask(client, model, third, temperature=0, max_tokens=100)}")


# ---------------------------------------------------------------- 实验 4
def exp_temperature() -> None:
    client, model = make_client()
    messages = [{"role": "user", "content": "给一家卖咖啡豆的网店起一个名字，只输出名字本身。"}]
    for t in (0, 1.3):
        print(f"===== temperature = {t}，同一个问题问 3 次 =====")
        for i in range(3):
            print(f"  第 {i + 1} 次: {ask(client, model, messages, temperature=t, max_tokens=30).strip()}")
        print()


# ---------------------------------------------------------------- 实验 5
def exp_tokens() -> None:
    import tiktoken

    # 不同模型的分词器不一样，这里用 OpenAI 的 cl100k_base 做演示，数量级是接近的
    enc = tiktoken.get_encoding("cl100k_base")
    samples = [
        "hello world",
        "上个月的销售额是多少？",
        "SELECT SUM(pay_amount) FROM orders WHERE status IN ('paid','completed')",
    ]
    for text in samples:
        ids = enc.encode(text)
        pieces = [enc.decode([i]) for i in ids]
        print(f"{text}")
        print(f"  {len(text)} 个字符 → {len(ids)} 个 token")
        print(f"  切分: {pieces}\n")

    from app.analytics.prompts import build_system_prompt

    prompt = build_system_prompt("sqlite", "2025-12-31")
    print(f"项目的 system prompt: {len(prompt)} 个字符 → {len(enc.encode(prompt))} 个 token")
    print("Agent 每调用一次模型，这段 system prompt 都要重新发一遍。")


EXPERIMENTS = {
    "hello": exp_hello,
    "system": exp_system,
    "memory": exp_memory,
    "temperature": exp_temperature,
    "tokens": exp_tokens,
}

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2 or sys.argv[1] not in EXPERIMENTS:
        print(__doc__)
        sys.exit(0)
    EXPERIMENTS[sys.argv[1]]()
