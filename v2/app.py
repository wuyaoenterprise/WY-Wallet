"""WY Wallet V2 entry point with durable multi-turn finance-chat focus.

The previous implementation is kept verbatim in ``app_core.py``. This tiny
loader upgrades only the finance-chat prompt at runtime so short follow-up
questions inherit the last explicit subject (for example, 打油) while allowing
new time ranges, years, metrics, items, or categories to override it.
"""

from pathlib import Path
import re


core_path = Path(__file__).with_name("app_core.py")
source = core_path.read_text(encoding="utf-8")

replacement = r'''
def _wy_history_user_turns(history: str, current_question: str) -> list[str]:
    """Extract earlier user turns from the compact chat transcript."""
    turns = []
    for raw_line in str(history or "").splitlines():
        line = raw_line.strip()
        if line.startswith("user:"):
            value = line.split(":", 1)[1].strip()
            if value:
                turns.append(value)
    if turns and turns[-1].strip() == str(current_question or "").strip():
        turns.pop()
    return turns


def _wy_topic_phrase(question: str) -> str:
    """Return the explicit subject left after removing time/query scaffolding.

    Empty means the turn is elliptical (e.g. '1到8月分别多少' or '那去年呢')
    and should normally inherit the previous conversation focus.
    """
    text = str(question or "").strip().casefold()
    if not text:
        return ""

    # Remove explicit time scopes. A new time scope should replace the previous
    # one without erasing the previous *subject*.
    text = re.sub(r"(?<!\d)20\d{2}\s*年?", " ", text)
    text = re.sub(r"去年|前年|今年|明年|本年|這一年|这一年", " ", text)
    text = re.sub(
        r"(?:1[0-2]|0?[1-9]|[一二三四五六七八九十]{1,3})\s*(?:月)?\s*"
        r"(?:到|至|[-~—–])\s*"
        r"(?:1[0-2]|0?[1-9]|[一二三四五六七八九十]{1,3})\s*月",
        " ",
        text,
    )
    text = re.sub(r"(?:1[0-2]|0?[1-9]|[一二三四五六七八九十]{1,3})\s*月", " ", text)
    text = re.sub(r"(?:3[01]|[12]?\d)\s*(?:日|号|號)", " ", text)
    text = re.sub(r"本月|这个月|這個月|上月|上个月|上個月|下个月|下個月", " ", text)

    # Remove wording that asks how to aggregate/compare but does not define a
    # finance subject. Keep explicit subjects such as 收入、总支出、交通、打油.
    text = re.sub(r"分[别別](?:都|各自)?(?:是)?多少(?:钱|錢)?", " ", text)
    generic_phrases = [
        "请帮我", "請幫我", "帮我", "幫我", "请问", "請問", "告诉我", "告訴我",
        "看一下", "查一下", "我说", "我說", "各个月", "各個月", "每个月", "每個月",
        "每月", "各月", "一共", "总共", "總共", "合计", "合計", "平均",
        "哪个月", "哪個月", "哪月", "最高", "最低", "为什么", "為什麼",
        "怎么会", "怎麼會", "怎么", "怎麼", "这么高", "這麼高", "这么低", "這麼低",
        "是多少", "多少钱", "多少錢", "多少", "分别", "分別", "那麼", "那么",
        "然后", "然後", "而已", "而已", "继续", "繼續", "再看看", "再看",
    ]
    for phrase in generic_phrases:
        text = text.replace(phrase, " ")

    text = re.sub(r"^[那再又]\s*", " ", text)
    text = re.sub(r"[呢吗嗎嘛呀啊吧喔哦]", " ", text)
    text = re.sub(r"[？?！!，,。；;：:\s]+", " ", text)
    text = text.replace("的", " ")
    return "".join(text.split())


def _wy_dialogue_state(question: str, history: str) -> dict:
    previous_users = _wy_history_user_turns(history, question)
    current_topic = _wy_topic_phrase(question)

    focus_topic = str(st.session_state.get("_wy_ai_focus_topic") or "").strip()
    focus_source = str(st.session_state.get("_wy_ai_focus_source") or "").strip()

    # Recover a focus from transcript when the session-state key is absent.
    if not focus_topic:
        for previous in reversed(previous_users):
            candidate = _wy_topic_phrase(previous)
            if candidate:
                focus_topic = candidate
                focus_source = previous
                break

    is_followup = not bool(current_topic)
    if current_topic:
        focus_topic = current_topic
        focus_source = question
        st.session_state["_wy_ai_focus_topic"] = focus_topic
        st.session_state["_wy_ai_focus_source"] = focus_source
    elif focus_topic:
        st.session_state["_wy_ai_focus_topic"] = focus_topic
        st.session_state["_wy_ai_focus_source"] = focus_source

    previous_user = previous_users[-1] if previous_users else ""
    if is_followup and focus_topic:
        resolved_question = (
            f"围绕上一轮明确主题“{focus_topic}”继续回答：{question}。"
            "继承主题/对象，但当前问题明确给出的年份、月份、日期、范围或统计方式优先，"
            "不要把主题擅自扩大成全部支出。"
        )
    else:
        resolved_question = question

    return {
        "is_followup": is_followup,
        "focus_topic": focus_topic,
        "focus_source": focus_source,
        "previous_user_question": previous_user,
        "current_explicit_topic": current_topic,
        "resolved_question": resolved_question,
    }


def _enrich_finance_prompt(contents):
    parsed = _extract_finance_question(contents) if isinstance(contents, str) else None
    if parsed is None:
        return contents

    question, selected_year, history = parsed
    dialogue = _wy_dialogue_state(question, history)
    try:
        context = _build_full_ledger_context(dialogue["resolved_question"], selected_year)
    except Exception as exc:
        return f"""你是私人财务分析助手。当前完整账本读取失败：{type(exc).__name__}。
不要假装拥有未读取的数据。请告诉用户读取失败，并建议稍后重试。
问题：{question}"""

    context_json = json.dumps(context, ensure_ascii=False, default=str, separators=(",", ":"))
    dialogue_json = json.dumps(dialogue, ensure_ascii=False, default=str, separators=(",", ":"))
    return f"""你是 WY Wallet 的私人财务账本分析助手。

你拥有用户所选年份的完整账本，以及应用维护的多轮对话焦点。账本事实的优先级最高；
对话焦点只用于解释“分别多少、那去年呢、哪个月最高、为什么这么高”等省略了主题的追问。

【多轮对话连续性】
- dialogue_state.is_followup=true 时，当前句没有明确提出新主题，必须继承 focus_topic。
- 继承的是项目/类别/指标主题，不是旧时间范围。当前句的新年份、月份、日期或区间覆盖旧范围。
- 只有当前句出现明确新主题时才切换主题。例如“交通呢”“收入呢”“总支出呢”。
- 严禁把“1到8月分别多少”这种追问自动解释成“1到8月总支出”，如果 focus_topic 是“打油费用”，它就是问 1 到 8 月的打油费用。
- “那2025呢”应换到 2025，但保留上一主题；“哪个月最高”应继续比较上一主题。
- 如果上一轮助手答错了，但用户说“我说打油的而已”之类的纠正，以用户最新明确主题为准。

数据结构：
- transactions：逐笔交易，字段 date / item / category / type / amount / note。
- monthly_item_expense：月份 × 项目 × 类别的本地精确支出与笔数。
- monthly_category_expense：月份 × 类别的本地精确支出与笔数。
- daily_item_expense：日期 × 项目 × 类别的本地精确支出与笔数。
- monthly_totals：每月总收入、总支出和交易笔数。
- ledger_complete=true 表示该年份完整交易均已包含。

回答规则：
1. 只根据真实账本数据回答，不编造。
2. 金额优先使用本地聚合字段，避免逐笔心算误差。
3. 允许合理语义匹配，例如“油费/打油/加油/petrol/fuel/汽油”；“Grab/打车/e-hailing”。
4. 指定月份只使用该 month；指定日期只使用该 date。
5. 金额回答尽量说明纳入的账本项目与笔数，RM 保留两位小数。
6. ledger_complete=true 时，不得声称没有月份明细或系统只提供年度合计。
7. 查遍完整账本仍无匹配，才说该范围内没有找到相关交易。
8. 使用中文，简洁直接。
9. 最近对话中的助手答案可能是错的；它只能帮助理解指代，绝不能覆盖账本事实或 dialogue_state。

所选年份：{selected_year}
用户原句：{question}
应用解析后的独立问题：{dialogue['resolved_question']}
对话状态：{dialogue_json}
近期对话（仅辅助理解）：
{history or '无'}

完整账本数据：
{context_json}
"""
'''

pattern = r"def _enrich_finance_prompt\(contents\):.*?\n\n_original_generative_model"
source, replaced = re.subn(
    pattern,
    replacement + "\n\n_original_generative_model",
    source,
    count=1,
    flags=re.S,
)
if replaced != 1:
    raise RuntimeError("Unable to install finance-chat continuity upgrade")

# Clear old conversation once so a previously wrong assistant answer cannot
# anchor the upgraded dialogue state.
source = source.replace("_wy_ai_full_ledger_v4_ready", "_wy_ai_full_ledger_v5_ready")

namespace = {
    "__name__": "__main__",
    "__file__": str(core_path),
    "__package__": None,
}
exec(compile(source, str(core_path), "exec"), namespace)
