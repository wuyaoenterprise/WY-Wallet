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

# --- 3. 数据库操作 ---
def load_data():
    try:
        res = supabase.table("transactions").select("*").order("date", desc=True).execute()
        df = pd.DataFrame(res.data)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date']).dt.date # 统一只保留日期
        return df
    except:
        return pd.DataFrame()

def get_categories():
    try:
        res = supabase.table("categories").select("name").execute()
        # 如果表是空的，返回默认值
        if not res.data:
            return ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "其他"]
        return [c['name'] for c in res.data]
    except:
        return ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "其他"] # 兜底

def delete_row(row_id):
    try:
        supabase.table("transactions").delete().eq("id", row_id).execute()
        st.toast(f"✅ 已成功删除记录 ID: {row_id}")
        st.rerun()
    except Exception as e:
        st.error(f"删除失败: {e}")

def save_to_cloud(rows):
    try:
        formatted = []
        for r in rows:
            formatted.append({
                "date": str(r.get('date', date.today())),
                "item": str(r.get('item', '未知')),
                "category": str(r.get('category', '其他')),
                "type": str(r.get('type', 'Expense')),
                "amount": float(r.get('amount') or 0.0), # 防止空值报错
                "note": str(r.get('note', ''))
            })
        supabase.table("transactions").insert(formatted).execute()
        return True
    except Exception as e:
        st.error(f"保存失败: {e}")
        return False

# --- 4. AI 翻译逻辑 ---
def ai_analyze_receipt(image):
    # 先获取最新的类别列表
    current_cats = get_categories()
    
    model = genai.GenerativeModel('gemini-2.5-flash') 
    prompt = f"""
    你是一个精明的财务助理。分析收据并将每一项拆分。
    要求：
    1. 必须将 item(项目名称) 翻译成简练的中文。
    2. 输出 JSON 数组：[{{"date": "YYYY-MM-DD", "item": "中文名称", "category": "类别", "amount": 10.5, "type": "Expense"}}]
    3. 类别(category)必须从以下列表中选择: {", ".join(current_cats)}
    """
    try:
        with st.spinner('🤖 AI 正在识别并翻译成中文...'):
            response = model.generate_content([prompt, image])
            text = response.text.strip().replace("```json", "").replace("```", "")
            import json
            return json.loads(text), None
    except Exception as e:
        return None, str(e)

# --- 5. 主程序 UI ---
tab1, tab2, tab3 = st.tabs(["📝 记账与历史", "📊 深度报表", "⚙️ 设置"])

# === Tab 1: 左右排布 + 行内删除 ===
with tab1:
    col_left, col_right = st.columns([1, 1.8], gap="large")

    # --- 左侧：记账输入 ---
    with col_left:
        st.subheader("📥 新增账目")
        up_file = st.file_uploader("📷 上传收据", type=['jpg', 'jpeg', 'png'])
        if up_file and st.button("🚀 开始 AI 识别", type="primary"):
            data, err = ai_analyze_receipt(Image.open(up_file))
            if data: st.session_state['pending_data'] = data
            else: st.error(err)

        if 'pending_data' in st.session_state:
            st.info("💡 核对识别结果（已翻译为中文）")
            edited = st.data_editor(st.session_state['pending_data'], num_rows="dynamic", use_container_width=True)
            if st.button("✅ 确认同步到云端"):
                if save_to_cloud(edited):
                    st.success("同步成功！")
                    del st.session_state['pending_data']
                    st.rerun()

        # 手动记账区域
        with st.expander("➕ 手动记账", expanded=True):
            with st.form("manual_form"):
                d_in = st.date_input("日期", date.today())
                it_in = st.text_input("项目名称")
                cat_in = st.selectbox("类别", get_categories())
                t_in = st.radio("类型", ["Expense", "Income"], horizontal=True)
                
                # 优化：value=None 让输入框默认留空，不用删0
                amt_in = st.number_input("金额 (RM)", min_value=0.0, step=0.01, value=None, placeholder="输入金额...")
                
                if st.form_submit_button("立即存入"):
                    final_amt = amt_in if amt_in is not None else 0.0
                    if save_to_cloud([{"date":d_in, "item":it_in, "category":cat_in, "type":t_in, "amount":final_amt}]):
                        st.rerun()

    # --- 右侧：详细历史 (手机优化版) ---
    with col_right:
        st.subheader("📜 历史记录")
        df_all = load_data()
        
        if not df_all.empty:
            # 筛选逻辑
            df_all['date'] = pd.to_datetime(df_all['date'])
            u_years = sorted(df_all['date'].dt.year.unique(), reverse=True)
            f_c1, f_c2 = st.columns(2)
            sel_y = f_c1.selectbox("筛选年份", u_years, key="hist_y")
            sel_m = f_c2.selectbox("筛选月份", range(1, 13), index=datetime.now().month-1, key="hist_m")
            
            mask = (df_all['date'].dt.year == sel_y) & (df_all['date'].dt.month == sel_m)
            df_filtered = df_all[mask]

            if not df_filtered.empty:
                st.markdown("---")
                # 列表头 (手机端只需简单提示)
                st.caption("项目详情 | 金额 | 操作")

                # 动态生成每一行，针对手机端优化
                for _, row in df_filtered.iterrows():
                    # 手机端只分 3 列：项目(含日期类别)、金额、删除
                    r_cols = st.columns([2, 1, 0.5])
                    
                    # 第一列：项目名称 + 小字描述（日期和类别）
                    r_cols[0].markdown(f"**{row['item']}**\n:grey[{row['date'].strftime('%m-%d')} | {row['category']}]")
                    
                    # 第二列：金额（带颜色）
                    color = "red" if row['type'] == "Expense" else "green"
                    r_cols[1].write(f":{color}[RM{row['amount']:.2f}]")
                    
                    # 第三列：垃圾桶按钮
                    if r_cols[2].button("🗑️", key=f"del_{row['id']}"):
                        delete_row(row['id'])
                    
                    st.divider() 
            else:
                st.info(f"{sel_y}年{sel_m}月 暂无数据")
        else:
            st.info("目前没有数据，请先记账。")

# === Tab 2: 深度报表 ===
with tab2:
    if not df_all.empty:
        st.subheader("📊 每日支出分析")
        
        b_c1, b_c2 = st.columns(2)
        b_year = b_c1.selectbox("选择年份", u_years, key="bar_y")
        b_month = b_c2.selectbox("选择月份", range(1, 13), index=datetime.now().month-1, key="bar_m")
        
        # 准备绘图数据
        df_all['day'] = df_all['date'].dt.day
        plot_mask = (df_all['date'].dt.year == b_year) & (df_all['date'].dt.month == b_month) & (df_all['type'] == 'Expense')
        df_plot = df_all[plot_mask]
        
        if not df_plot.empty:
            daily_data = df_plot.groupby(['day', 'category'])['amount'].sum().reset_index()
            last_day = calendar.monthrange(b_year, b_month)[1]

            # 1. 柱状图
            fig = px.bar(
                daily_data, 
                x='day', y='amount', color='category', 
                title=f"{b_year}年{b_month}月 每日支出分布",
                labels={'day': '日期', 'amount': '金额 (RM)', 'category': '类别'},
                text_auto='.0f', template="plotly_dark"
            )
            # 强制 1-31 号
            fig.update_xaxes(tickmode='linear', tick0=1, dtick=1, range=[0.5, last_day + 0.5])
            fig.update_layout(bargap=0.3)
            
            # 定死图表，防止手机误触
            st.plotly_chart(
                fig, 
                use_container_width=True,
                config={'staticPlot': False, 'scrollZoom': False, 'displayModeBar': False}
            )
            
            st.divider()
            
            # 2. 支出占比饼图 (这里加入了显示百分比的逻辑)
            fig_pie = px.pie(df_plot, values='amount', names='category', hole=0.5, title="本月支出构成")
            # 关键修改：显示标签和百分比，且位置在圆环外侧
            fig_pie.update_traces(textposition='outside', textinfo='percent+label')
            
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.warning("该月份没有支出记录。")

# === Tab 3: 设置 ===
with tab3:
    st.header("⚙️ 系统管理")
    
    st.subheader("🏷️ 类别管理")
    current_cats = get_categories()
    
    c1, c2 = st.columns(2)
    with c1:
        new_cat = st.text_input("✨ 添加新类别")
        if st.button("添加类别"):
            if new_cat and new_cat not in current_cats:
                supabase.table("categories").insert({"name": new_cat}).execute()
                st.success(f"已添加: {new_cat}")
                st.rerun()
    
    with c2:
        cat_to_del = st.selectbox("🗑️ 删除现有类别", current_cats)
        if st.button("确认删除", type="secondary"):
            supabase.table("categories").delete().eq("name", cat_to_del).execute()
            st.warning(f"已删除: {cat_to_del}")
            st.rerun()
            
    st.divider()
    st.write(f"🟢 Supabase 连接正常")

