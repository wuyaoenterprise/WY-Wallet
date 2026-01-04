import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, date
import google.generativeai as genai
from PIL import Image
from supabase import create_client, Client
import calendar
import json 
import re # 用于计算器逻辑

# --- 1. 页面配置 ---
st.set_page_config(page_title="Smart Asset Pro", page_icon="💳", layout="wide")

# --- 2. 核心连接 ---
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    genai.configure(api_key=GOOGLE_API_KEY)
except Exception as e:
    st.error(f"❌ 配置加载失败: {e}")
    st.stop()

# --- 3. 数据库操作 ---
def load_data():
    try:
        # ⚡️ 改进 1：日期降序 + ID 降序，确保同一天最新的在最上
        res = supabase.table("transactions").select("*").order("date", desc=True).order("id", desc=True).execute()
        df = pd.DataFrame(res.data)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date']).dt.date
        return df
    except:
        return pd.DataFrame()

def get_categories():
    try:
        res = supabase.table("categories").select("name").execute()
        if not res.data:
            return ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "其他"]
        return [c['name'] for c in res.data]
    except:
        return ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "其他"]

def delete_row(row_id):
    try:
        supabase.table("transactions").delete().eq("id", row_id).execute()
        st.toast(f"✅ 已删除 ID: {row_id}")
        st.rerun()
    except Exception as e:
        st.error(f"删除失败: {e}")

# ⚡️ 增加：更新函数，用于编辑历史记录
def update_row(row_id, updated_dict):
    try:
        # 移除不可修改的字段
        updated_dict.pop('id', None)
        if 'date' in updated_dict: updated_dict['date'] = str(updated_dict['date'])
        supabase.table("transactions").update(updated_dict).eq("id", row_id).execute()
    except Exception as e:
        st.error(f"更新失败: {e}")

def save_to_cloud(data_input):
    try:
        if isinstance(data_input, pd.DataFrame):
            rows = data_input.to_dict('records')
        else:
            rows = data_input

        formatted = []
        for r in rows:
            formatted.append({
                "date": str(r.get('date', date.today())),
                "item": str(r.get('item', '未知')),
                "category": str(r.get('category', '其他')),
                "type": str(r.get('type', 'Expense')),
                "amount": float(r.get('amount') or 0.0),
                "note": str(r.get('note', ''))
            })
        supabase.table("transactions").insert(formatted).execute()
        return True
    except Exception as e:
        st.error(f"保存失败: {e}")
        return False

# ⚡️ 增加：安全计算器逻辑
def safe_calculate(expression):
    try:
        # 仅允许数字和运算符
        clean_expr = re.sub(r'[^-+*/0-9.]', '', expression)
        return float(eval(clean_expr))
    except:
        return 0.0

# --- 4. AI 翻译逻辑 (保持不变) ---
def ai_analyze_receipt(image):
    current_cats = get_categories()
    model_name = 'gemini-2.5-flash' 
    try:
        model = genai.GenerativeModel(model_name)
        prompt = f"""分析收据并将每一项拆分。要求项目名称翻译成中文。输出纯JSON。类别从中选: {", ".join(current_cats)}
        格式示例：[{{"date": "YYYY-MM-DD", "item": "中文名称", "category": "类别", "amount": 10.5, "type": "Expense"}}]"""
        with st.spinner(f'🤖 AI ({model_name}) 正在识别...'):
            response = model.generate_content([prompt, image])
            raw_text = response.text.strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:]
            if raw_text.startswith("```"): raw_text = raw_text[3:]
            if raw_text.endswith("```"): raw_text = raw_text[:-3]
            data = json.loads(raw_text.strip())
            return data, None
    except Exception as e:
        return None, f"请求出错: {str(e)}"

# --- 5. 主程序 UI ---
tab1, tab2, tab3 = st.tabs(["📝 记账与历史", "📊 报表分析", "⚙️ 设置"])

# === Tab 1: 记账与历史 ===
with tab1:
    col_left, col_right = st.columns([1, 1.8], gap="large")

    # --- 左侧：记账输入 ---
    with col_left:
        st.subheader("📥 新增账目")
        
        # ⚡️ 初始化用于清空状态的 session_state
        if 'reset_trigger' not in st.session_state: st.session_state.reset_trigger = 0

        up_file = st.file_uploader("📷 上传收据", type=['jpg', 'jpeg', 'png'])
        if up_file and st.button("🚀 AI 识别", type="primary"):
            data, err = ai_analyze_receipt(Image.open(up_file))
            if data: st.session_state['pending_data'] = data
            else: st.error(err)

        # AI 识别编辑区
        if 'pending_data' in st.session_state:
            st.info("💡 请核对结果")
            df_pending = pd.DataFrame(st.session_state['pending_data'])
            if not df_pending.empty:
                if 'date' in df_pending.columns: df_pending['date'] = pd.to_datetime(df_pending['date'])
                if 'amount' in df_pending.columns: df_pending['amount'] = pd.to_numeric(df_pending['amount'], errors='coerce').fillna(0.0)
            
            edited = st.data_editor(df_pending, num_rows="dynamic", use_container_width=True,
                column_config={
                    "category": st.column_config.SelectboxColumn("类别", options=get_categories(), required=True),
                    "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
                    "amount": st.column_config.NumberColumn("金额 (RM)", format="%.2f"),
                    "type": st.column_config.SelectboxColumn("类型", options=["Expense", "Income"])
                })
            
            if st.button("✅ 确认同步到云端"):
                if save_to_cloud(edited):
                    st.success("同步成功！")
                    del st.session_state['pending_data']
                    st.rerun()

        # 手动记账
        with st.expander("➕ 手动记账", expanded=True):
            # ⚡️ 改进：使用 key 实现保存后自动清空
            # 注意：Streamlit 的 text_input/number_input 在使用 key 时，如果不手动处理会很难清空，这里配合 rerun 实现
            with st.form("manual_form", clear_on_submit=True):
                d_in = st.date_input("日期", date.today())
                
                # ⚡️ 改进 2：项目在上，类别在下
                it_in = st.text_input("项目名称", placeholder="输入项目...", key=f"it_{st.session_state.reset_trigger}")
                cat_in = st.selectbox("类别", get_categories())
                t_in = st.radio("类型", ["Expense", "Income"], horizontal=True)
                
                # ⚡️ 改进 3：增加简易计算器
                calc_expr = st.text_input("🔢 简单计算 (例: 10+15.5)", placeholder="在此输入算式，会自动填入下方金额")
                calc_val = safe_calculate(calc_expr) if calc_expr else None
                
                # 金额输入框：如果计算器有值，默认显示计算结果
                amt_in = st.number_input("金额 (RM)", min_value=0.0, step=0.01, 
                                        value=calc_val if calc_val else 0.0, 
                                        key=f"amt_{st.session_state.reset_trigger}")
                
                if st.form_submit_button("立即存入"):
                    if amt_in > 0:
                        if save_to_cloud([{"date":d_in, "item":it_in, "category":cat_in, "type":t_in, "amount":amt_in}]):
                            # ⚡️ 改进 5：保存成功后递增触发器，强行刷新输入框
                            st.session_state.reset_trigger += 1
                            st.rerun()
                    else:
                        st.warning("⚠️ 请输入金额")

    # --- 右侧：历史记录 ---
    with col_right:
        st.subheader("📜 历史记录")
        df_all = load_data()
        
        if not df_all.empty:
            u_years = sorted(df_all['date'].apply(lambda x: x.year).unique(), reverse=True)
            f_c1, f_c2 = st.columns(2)
            sel_y = f_c1.selectbox("年份", u_years, key="h_y")
            sel_m = f_c2.selectbox("月份", range(1, 13), index=datetime.now().month-1, key="h_m")
            
            mask = (df_all['date'].apply(lambda x: x.year) == sel_y) & (df_all['date'].apply(lambda x: x.month) == sel_m)
            df_filtered = df_all[mask].copy()

            if not df_filtered.empty:
                st.info("💡 双击表格内容可直接编辑，完成后点击下方保存按钮。")
                
                # ⚡️ 改进 4：历史记录改为直接编辑模式
                edited_history = st.data_editor(
                    df_filtered,
                    key="history_editor",
                    use_container_width=True,
                    disabled=["id"], # ID 不可改
                    column_config={
                        "id": None, # 隐藏 ID
                        "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
                        "amount": st.column_config.NumberColumn("金额", format="%.2f"),
                        "category": st.column_config.SelectboxColumn("类别", options=get_categories()),
                        "type": st.column_config.SelectboxColumn("类型", options=["Expense", "Income"])
                    }
                )

                # 检查是否有改动并保存
                if st.button("💾 保存历史修改"):
                    # 对比原始数据和编辑后的数据（这里简单处理：全量更新）
                    for index, row in edited_history.iterrows():
                        update_row(row['id'], row.to_dict())
                    st.success("更改已同步到数据库！")
                    st.rerun()
                
                st.divider()
                
                # 删除功能（保留你的原始设计）
                with st.expander("🗑️ 快速删除"):
                    del_id = st.selectbox("选择要删除的项目ID", df_filtered['id'])
                    if st.button("确认彻底删除"):
                        delete_row(del_id)
            else:
                st.info("本月无数据")
        else:
            st.info("暂无数据")

# === Tab 2 & 3: 报表分析与设置 (保持你的原始代码逻辑) ===
with tab2:
    if not df_all.empty:
        b_c1, b_c2 = st.columns(2)
        b_year = b_c1.selectbox("年份", u_years, key="b_y")
        b_month = b_c2.selectbox("月份", range(1, 13), index=datetime.now().month-1, key="b_m")
        df_all['date_dt'] = pd.to_datetime(df_all['date'])
        df_all['day'] = df_all['date_dt'].dt.day
        month_mask = (df_all['date_dt'].dt.year == b_year) & (df_all['date_dt'].dt.month == b_month)
        df_month = df_all[month_mask]
        
        if not df_month.empty:
            income = df_month[df_month['type'] == 'Income']['amount'].sum()
            expense = df_month[df_month['type'] == 'Expense']['amount'].sum()
            st.divider()
            k1, k2, k3 = st.columns(3)
            k1.metric("💰 总收入", f"{income:,.2f}")
            k2.metric("💸 总支出", f"{expense:,.2f}")
            k3.metric("🏦 结余", f"{(income-expense):,.2f}")
            st.divider()
            
            df_expense = df_month[(df_month['type'] == 'Expense') & (df_month['amount'] > 0)]
            if not df_expense.empty:
                daily_data = df_expense.groupby(['day', 'category'])['amount'].sum().reset_index()
                fig = px.bar(daily_data, x='day', y='amount', color='category', text_auto='.0f', template="plotly_dark")
                st.plotly_chart(fig, use_container_width=True)
                fig_pie = px.pie(df_expense, values='amount', names='category', hole=0.5)
                st.plotly_chart(fig_pie, use_container_width=True)

with tab3:
    st.header("⚙️ 类别管理")
    current_cats = get_categories()
    c1, c2 = st.columns(2)
    with c1:
        new_cat = st.text_input("✨ 新类别")
        if st.button("添加"):
            if new_cat and new_cat not in current_cats:
                supabase.table("categories").insert({"name": new_cat}).execute()
                st.rerun()
    with c2:
        del_cat = st.selectbox("🗑️ 删除类别", current_cats)
        if st.button("确认删除"):
            supabase.table("categories").delete().eq("name", del_cat).execute()
            st.rerun()
