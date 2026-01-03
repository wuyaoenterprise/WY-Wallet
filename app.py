import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import google.generativeai as genai
from PIL import Image
from supabase import create_client, Client

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

# --- 3. 数据库操作函数 ---

def load_data():
    """从云端获取最新账目"""
    try:
        response = supabase.table("transactions").select("*").order("date", desc=True).execute()
        return pd.DataFrame(response.data)
    except Exception as e:
        return pd.DataFrame()

def save_to_cloud(rows):
    """保存并自动补全字段"""
    try:
        formatted_rows = []
        for r in rows:
            formatted_rows.append({
                "date": str(r.get('date', datetime.now().date())),
                "item": str(r.get('item', '未知项目')),
                "category": str(r.get('category', '其他')),
                "type": str(r.get('type', 'Expense')), 
                "amount": float(r.get('amount', 0.0)),
                "note": str(r.get('note', ''))
            })
        supabase.table("transactions").insert(formatted_rows).execute()
        return True
    except Exception as e:
        st.error(f"写入失败: {e}")
        return False

def delete_row(row_id):
    """删除指定 ID 的账目"""
    try:
        supabase.table("transactions").delete().eq("id", row_id).execute()
        st.success(f"已删除记录 ID: {row_id}")
        st.rerun()
    except Exception as e:
        st.error(f"删除失败: {e}")

# --- 4. AI 翻译识别逻辑 ---
def ai_analyze_receipt(image):
    # 修正版本号为官方支持的 2.0 实验版
    model = genai.GenerativeModel('gemini-2.0-flash-exp') 
    prompt = """
    你是一个财务助理。分析收据并将每一项拆分。
    要求：
    1. 必须将项目名称(item)自动翻译成简练的中文。
    2. 输出严格的 JSON 数组。包含：date (YYYY-MM-DD), item, category, amount, type。
    3. 类型(type)统一填 "Expense"。
    """
    try:
        with st.spinner('🤖 AI 正在识别并翻译成中文...'):
            response = model.generate_content([prompt, image])
            text = response.text.strip().replace("```json", "").replace("```", "")
            import json
            data = json.loads(text)
            return data, None
    except Exception as e:
        return None, f"AI 识别出错 (检查版本号或额度): {str(e)}"

# --- 5. 主程序 UI ---
tab1, tab2, tab4 = st.tabs(["📝 智能记账 & 记录", "📊 报表分析", "⚙️ 设置"])

# === Tab 1: 记账 & 详细记录 (合并版) ===
with tab1:
    col_input, col_recent = st.columns([1, 1.2])

    with col_input:
        st.subheader("📥 新增账目")
        uploaded_file = st.file_uploader("📷 上传收据", type=['jpg', 'png', 'jpeg'])
        if uploaded_file and st.button("🚀 AI 识别", type="primary"):
            image = Image.open(uploaded_file)
            ai_data, err = ai_analyze_receipt(image)
            if ai_data: st.session_state['pending'] = ai_data
            else: st.error(err)

        if 'pending' in st.session_state:
            edited = st.data_editor(st.session_state['pending'], num_rows="dynamic", use_container_width=True)
            if st.button("✅ 确认并同步云端"):
                if save_to_cloud(edited):
                    st.toast("同步成功！")
                    del st.session_state['pending']
                    st.rerun()

        with st.expander("➕ 手动记账"):
            with st.form("manual"):
                d = st.date_input("日期")
                it = st.text_input("项目")
                cat = st.selectbox("类别", ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "工资", "投资", "其他"])
                t = st.radio("类型", ["Expense", "Income"], horizontal=True)
                amt = st.number_input("金额 (RM)", min_value=0.0)
                if st.form_submit_button("立即存入"):
                    if save_to_cloud([{"date":d, "item":it, "category":cat, "type":t, "amount":amt}]):
                        st.rerun()

    with col_recent:
        st.subheader("📜 详细记录 (可删除)")
        df_logs = load_data()
        if not df_logs.empty:
            # 使用 data_editor 实现快速查看，并在下方提供删除选择
            st.dataframe(df_logs[['date', 'item', 'category', 'amount', 'type', 'id']], use_container_width=True, hide_index=True)
            
            with st.popover("🗑️ 点击这里选择要删除的项目"):
                del_id = st.selectbox("选择 ID 进行删除", df_logs['id'])
                if st.button(f"确认删除 ID: {del_id}", type="primary"):
                    delete_row(del_id)
        else:
            st.info("暂无记录")

# === Tab 2: 报表分析 (柱状图 & 筛选版) ===
with tab2:
    df = load_data()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        df['year'] = df['date'].dt.year
        df['month'] = df['date'].dt.month
        df['day'] = df['date'].dt.day

        # 筛选器
        c1, c2 = st.columns(2)
        sel_year = c1.selectbox("年份", sorted(df['year'].unique(), reverse=True))
        sel_month = c2.selectbox("月份", range(1, 13), index=datetime.now().month-1)

        # 过滤数据
        filtered_df = df[(df['year'] == sel_year) & (df['month'] == sel_month)]
        
        if not filtered_df.empty:
            # KPI
            exp_sum = filtered_df[filtered_df['type']=='Expense']['amount'].sum()
            st.metric(f"{sel_year}年{sel_month}月 总支出", f"RM {exp_sum:,.2f}")

            # --- 新增：每日开销柱状图 ---
            daily_df = filtered_df[filtered_df['type']=='Expense'].groupby('day')['amount'].sum().reset_index()
            fig_bar = px.bar(daily_df, x='day', y='amount', title="每日支出分布", 
                             labels={'day':'日期', 'amount':'金额 (RM)'},
                             color_discrete_sequence=['#FF4B4B'])
            st.plotly_chart(fig_bar, use_container_width=True)

            # 饼图
            fig_pie = px.pie(filtered_df[filtered_df['type']=='Expense'], values='amount', names='category', hole=0.4, title="支出构成")
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.warning(f"{sel_year}年{sel_month}月 没有记录数据。")
    else:
        st.info("请先前往 Tab 1 记账")


# === Tab 4: 设置 ===
with tab4:
    st.header("⚙️ 系统状态")
    st.write("🟢 数据库连接状态：Supabase 已连接")
    if st.button("🔥 强制同步刷新"):

        st.rerun()



