import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import google.generativeai as genai
from PIL import Image
from supabase import create_client, Client

# --- 1. 页面配置 (必须在第一行) ---
st.set_page_config(page_title="Smart Asset Pro", page_icon="💳", layout="wide")

# --- 2. 核心连接 (从 Secrets 读取) ---
# 请在 Streamlit Cloud 的 Secrets 中配置以下三个键
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    
    # 初始化客户端
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    genai.configure(api_key=GOOGLE_API_KEY)
except Exception as e:
    st.error(f"❌ 配置加载失败，请检查 Secrets: {e}")
    st.stop()

# --- 3. 数据库操作函数 (Supabase 云端版) ---

def load_data():
    """从 Supabase 获取全量数据"""
    try:
        # 获取所有数据，按日期倒序排列
        response = supabase.table("transactions").select("*").order("date", desc=True).execute()
        return pd.DataFrame(response.data)
    except Exception as e:
        st.error(f"读取失败: {e}")
        return pd.DataFrame()

def save_to_cloud(rows):
    """批量追加数据到 Supabase，绝不覆盖旧数据"""
    try:
        formatted_rows = []
        for r in rows:
            formatted_rows.append({
                "date": str(r['date']),
                "item": r['item'],
                "category": r['category'],
                "type": r['type'],
                "amount": float(r['amount']),
                "note": r.get('note', '')
            })
        supabase.table("transactions").insert(formatted_rows).execute()
        return True
    except Exception as e:
        st.error(f"写入失败: {e}")
        return False

# --- 4. AI 逻辑 ---
def ai_analyze_receipt(image):
    model = genai.GenerativeModel('gemini-2.0-flash') # 使用最新的 flash 模型
    prompt = """
    你是一个精明的财务助理。请分析收据并将每一项拆分。
    要求：输出严格的 JSON 数组，包含 date (YYYY-MM-DD), item, category, amount。
    类别选其一：饮食、交通、购物、居住、娱乐、医疗、工资、投资、其他。
    """
    try:
        with st.spinner('🤖 AI 正在识别中...'):
            response = model.generate_content([prompt, image])
            text = response.text.strip().replace("```json", "").replace("```", "")
            import json
            data = json.loads(text)
            return data if isinstance(data, list) else [data], None
    except Exception as e:
        return None, f"AI 识别出错: {str(e)}"

# --- 5. 主程序 UI ---
tab1, tab2, tab3, tab4 = st.tabs(["📝 智能记账", "📊 报表分析", "📅 详细记录", "⚙️ 设置"])

# === Tab 1: 记账 ===
with tab1:
    st.caption("✅ 实时同步至 Supabase 云数据库")
    
    uploaded_file = st.file_uploader("📷 上传收据 (自动识别)", type=['jpg', 'png', 'jpeg'])
    if uploaded_file and st.button("🚀 开始识别", type="primary"):
        image = Image.open(uploaded_file)
        ai_data, err = ai_analyze_receipt(image)
        if ai_data:
            st.session_state['pending'] = ai_data
            st.success("识别成功！")
        else:
            st.error(err)

    if 'pending' in st.session_state:
        st.subheader("🧐 请核对并保存")
        edited = st.data_editor(st.session_state['pending'], num_rows="dynamic", use_container_width=True)
        
        c1, c2 = st.columns(2)
        if c1.button("✅ 确认并上传", type="primary"):
            if save_to_cloud(edited):
                st.toast("已安全存入云端！", icon="🚀")
                del st.session_state['pending']
                st.rerun()
        if c2.button("🗑️ 放弃"):
            del st.session_state['pending']
            st.rerun()

    with st.expander("➕ 手动记账"):
        with st.form("manual"):
            d = st.date_input("日期")
            it = st.text_input("项目")
            cat = st.selectbox("类别", ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "工资", "投资", "其他"])
            t = st.radio("类型", ["Expense", "Income"], horizontal=True)
            amt = st.number_input("金额 (RM)", min_value=0.0)
            if st.form_submit_button("立即同步"):
                if save_to_cloud([{"date":d, "item":it, "category":cat, "type":t, "amount":amt}]):
                    st.success("已保存！")
                    st.rerun()

# === Tab 2: 报表分析 ===
with tab2:
    df = load_data()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
        # 简单 KPI
        total_exp = df[df['type']=='Expense']['amount'].sum()
        st.metric("本年总支出", f"RM {total_exp:,.2f}")
        
        # 饼图
        fig = px.pie(df[df['type']=='Expense'], values='amount', names='category', hole=0.5, title="支出构成")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("暂无数据，请先记账。")

# === Tab 3: 活动记录 ===
with tab3:
    st.subheader("📜 云端原始数据")
    df_raw = load_data()
    if not df_raw.empty:
        st.dataframe(df_raw, use_container_width=True, hide_index=True)

# === Tab 4: 设置 ===
with tab4:
    st.header("⚙️ 系统状态")
    st.write("🟢 数据库连接状态：Supabase 已连接")
    if st.button("🔥 强制同步刷新"):
        st.rerun()