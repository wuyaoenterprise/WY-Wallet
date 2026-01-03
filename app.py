import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, date
import google.generativeai as genai
from PIL import Image
from supabase import create_client, Client
import calendar

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
    st.error(f"❌ 配置加载失败，请检查 Secrets: {e}")
    st.stop()

# --- 3. 数据库逻辑 ---
def load_full_data():
    try:
        res = supabase.table("transactions").select("*").order("date", desc=True).execute()
        df = pd.DataFrame(res.data)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
        return df
    except:
        return pd.DataFrame()

def save_to_cloud(rows):
    try:
        formatted = []
        for r in rows:
            formatted.append({
                "date": str(r.get('date', date.today())),
                "item": str(r.get('item', '未知')),
                "category": str(r.get('category', '其他')),
                "type": str(r.get('type', 'Expense')),
                "amount": float(r.get('amount', 0.0)),
                "note": str(r.get('note', ''))
            })
        supabase.table("transactions").insert(formatted).execute()
        return True
    except Exception as e:
        st.error(f"写入失败: {e}")
        return False

def delete_by_id(row_id):
    try:
        supabase.table("transactions").delete().eq("id", row_id).execute()
        st.toast(f"✅ 已删除 ID: {row_id}")
        st.rerun()
    except Exception as e:
        st.error(f"删除失败: {e}")

# --- 4. AI 逻辑 ---
def ai_analyze_receipt(image):
    # 修正：使用支持的 gemini-1.5-flash 或 gemini-2.0-flash-exp
    model = genai.GenerativeModel('gemini-1.5-flash') 
    prompt = """
    你是一个财务助理。请分析收据并拆分物品。
    要求：
    1. 必须将 item(项目名称) 翻译成简洁的中文。
    2. 输出 JSON 数组：[{"date": "YYYY-MM-DD", "item": "中文名称", "category": "类别", "amount": 10.5, "type": "Expense"}]
    """
    try:
        with st.spinner('🤖 AI 正在识别并翻译中...'):
            response = model.generate_content([prompt, image])
            text = response.text.strip().replace("```json", "").replace("```", "")
            import json
            return json.loads(text), None
    except Exception as e:
        return None, str(e)

# --- 5. UI 逻辑 ---
tab1, tab2, tab3 = st.tabs(["📝 记账与历史", "📊 深度报表", "⚙️ 设置"])

# === Tab 1: 左右布局 ===
with tab1:
    col_input, col_history = st.columns([1, 1.5], gap="large")

    # --- 左侧：记账输入 ---
    with col_input:
        st.subheader("📥 新增数据")
        up_file = st.file_uploader("📷 上传收据", type=['jpg', 'jpeg', 'png'])
        if up_file and st.button("🚀 AI 识别(中文)", type="primary"):
            data, err = ai_analyze_receipt(Image.open(up_file))
            if data: st.session_state['pending'] = data
            else: st.error(err)

        if 'pending' in st.session_state:
            st.info("💡 请核对 AI 识别结果（已自动翻译）")
            edited = st.data_editor(st.session_state['pending'], num_rows="dynamic", use_container_width=True)
            if st.button("✅ 确认同步到云端"):
                if save_to_cloud(edited):
                    st.toast("已安全存入 Supabase")
                    del st.session_state['pending']
                    st.rerun()

        with st.expander("➕ 手动记账"):
            with st.form("man_form"):
                d = st.date_input("日期")
                it = st.text_input("项目")
                cat = st.selectbox("分类", ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "其他"])
                amt = st.number_input("金额 (RM)", min_value=0.0)
                if st.form_submit_button("保存"):
                    if save_to_cloud([{"date":d, "item":it, "category":cat, "amount":amt}]):
                        st.rerun()

    # --- 右侧：详细历史 (含筛选和删除) ---
    with col_history:
        st.subheader("📜 历史记录")
        all_df = load_full_data()
        
        if not all_df.empty:
            # 筛选区
            h_c1, h_c2, h_c3 = st.columns([1, 1, 1.5])
            u_years = sorted(all_df['date'].dt.year.unique(), reverse=True)
            s_year = h_c1.selectbox("筛选年份", u_years, key="h_year")
            s_month = h_c2.selectbox("筛选月份", range(1, 13), index=datetime.now().month-1, key="h_month")
            
            # 删除区 (放在筛选旁边)
            with h_c3:
                with st.popover("🗑️ 快速删除"):
                    target_id = st.number_input("输入要删除的 ID", min_value=1, step=1)
                    if st.button(f"确认删除 ID: {target_id}", type="primary"):
                        delete_by_id(target_id)

            # 过滤并显示
            mask = (all_df['date'].dt.year == s_year) & (all_df['date'].dt.month == s_month)
            display_df = all_df[mask]
            
            st.dataframe(
                display_df[['id', 'date', 'item', 'category', 'amount', 'type']],
                use_container_width=True,
                hide_index=True,
                column_config={"id": st.column_config.NumberColumn("ID", width="small")}
            )
        else:
            st.info("暂无数据")

# === Tab 2: 报表分析 ===
with tab2:
    if not all_df.empty:
        st.subheader("📊 每日支出走势")
        
        # 筛选逻辑同步
        r_c1, r_c2 = st.columns(2)
        r_year = r_c1.selectbox("选择年份", u_years, key="r_year")
        r_month = r_c2.selectbox("选择月份", range(1, 13), index=datetime.now().month-1, key="r_month")
        
        # 过滤
        report_df = all_df[(all_df['date'].dt.year == r_year) & (all_df['date'].dt.month == r_month)]
        
        if not report_df.empty:
            # 准备 1-31 号的完整数据
            report_df['day'] = report_df['date'].dt.day
            exp_only = report_df[report_df['type'] == 'Expense']
            
            # 汇总每天、每个类别的金额
            daily_cat = exp_only.groupby(['day', 'category'])['amount'].sum().reset_index()

            # 画柱状图
            last_day = calendar.monthrange(r_year, r_month)[1]
            fig = px.bar(
                daily_cat, x='day', y='amount', color='category',
                title=f"{r_year}年{r_month}月 每日支出明细",
                labels={'day': '日期', 'amount': '金额 (RM)', 'category': '类别'},
                text_auto='.0f'
            )
            # 强制 X 轴显示 1 号到月底
            fig.update_xaxes(dtick=1, range=[0.5, last_day + 0.5])
            st.plotly_chart(fig, use_container_width=True)
            
            # 饼图
            st.divider()
            fig_pie = px.pie(exp_only, values='amount', names='category', hole=0.4, title="支出构成")
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.warning("该月没有数据")

# === Tab 3: 设置 ===
with tab3:
    st.write(f"🟢 云端连接状态: 正常 (Supabase)")
    st.write(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if st.button("🔄 强制重载页面"):
        st.rerun()
