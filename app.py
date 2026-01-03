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

# --- 云端保存函数 ---
def save_to_cloud(rows):
    """批量追加数据，增加容错处理"""
    try:
        formatted_rows = []
        for r in rows:
            formatted_rows.append({
                "date": str(r.get('date', datetime.now().date())),
                "item": str(r.get('item', '未知项目')),
                "category": str(r.get('category', '其他')),
                "type": str(r.get('type', 'Expense')), # 如果没有 type，默认给 Expense
                "amount": float(r.get('amount', 0.0)),
                "note": str(r.get('note', ''))
            })
        # 写入 Supabase
        supabase.table("transactions").insert(formatted_rows).execute()
        return True
    except Exception as e:
        st.error(f"写入失败: {e}")
        return False
        
# --- 4. AI 逻辑 ---
def ai_analyze_receipt(image):
    model = genai.GenerativeModel('gemini-2.5-flash') 
    prompt = """
    你是一个精明的财务助理。请分析收据并将每一项拆分。
    要求：输出严格的 JSON 数组。
    必须包含字段：date (YYYY-MM-DD), item, category, amount, type。
    注意：收据识别的项目，type 统一填写 "Expense"。
    """
    try:
        with st.spinner('🤖 AI 正在识别并标记类型...'):
            response = model.generate_content([prompt, image])
            text = response.text.strip().replace("```json", "").replace("```", "")
            import json
            data = json.loads(text)
            # 确保每一行都有 type 字段，防止报错
            if isinstance(data, list):
                for item in data:
                    if 'type' not in item:
                        item['type'] = 'Expense'
            return data, None
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

# === Tab 2: 报表分析 (UI 优化版) ===
with tab2:
    st.subheader("📊 财务仪表盘")

    # 1. 获取数据
    df_raw = run_query("SELECT * FROM transactions", fetch=True)
    
    if df_raw:
        # 数据清洗
        df = pd.DataFrame(df_raw, columns=['ID', '日期', '项目', '类别', '类型', '金额', '备注', '创建时间'])
        df['日期'] = pd.to_datetime(df['日期'])
        df['年份'] = df['日期'].dt.year
        df['月份'] = df['日期'].dt.month

        # --- 筛选控制区 (纯下拉菜单，告别日历) ---
        with st.container(border=True):
            col_filter1, col_filter2 = st.columns([1, 2])
            with col_filter1:
                filter_mode = st.radio("查看模式", ["按月份查看", "按年份查看"], horizontal=True)
            
            with col_filter2:
                # 获取数据库里现有的年份，如果没数据就默认今年
                unique_years = sorted(df['年份'].unique(), reverse=True)
                if not unique_years: unique_years = [datetime.now().year]
                
                if filter_mode == "按月份查看":
                    c_year, c_month = st.columns(2)
                    with c_year:
                        sel_year = st.selectbox("选择年份", unique_years, key="year_select")
                    with c_month:
                        # 默认选中当前月份 (注意 index 从 0 开始，所以要减 1)
                        current_month_idx = datetime.now().month - 1
                        sel_month = st.selectbox("选择月份", range(1, 13), index=current_month_idx, key="month_select", format_func=lambda x: f"{x}月")
                    
                    # 过滤逻辑
                    mask = (df['年份'] == sel_year) & (df['月份'] == sel_month)
                    title_text = f"{sel_year}年 {sel_month}月"
                    
                else:
                    # 年份模式：只需要一个年份下拉框
                    sel_year = st.selectbox("选择年份", unique_years, key="year_only_select")
                    mask = (df['年份'] == sel_year)
                    title_text = f"{sel_year}年 全年"

        # 应用筛选
        filtered_df = df[mask]

        if not filtered_df.empty:
            # --- 2. 核心 KPI ---
            inc = filtered_df[filtered_df['类型']=='Income']['金额'].sum()
            exp = filtered_df[filtered_df['类型']=='Expense']['金额'].sum()
            balance = inc - exp

            k1, k2, k3 = st.columns(3)
            k1.metric("💰 总收入", f"RM {inc:,.2f}")
            k2.metric("💸 总支出", f"RM {exp:,.2f}", delta=-exp, delta_color="inverse")
            k3.metric("🏦 结余", f"RM {balance:,.2f}", delta=balance)

            st.markdown("---")

            # --- 3. 图表区 ---
            c1, c2 = st.columns([1, 1])

            # 左边：甜甜圈图 (只看类别，不显示杂乱的项目名)
            with c1:
                st.subheader(f"{title_text} 支出构成")
                exp_df = filtered_df[filtered_df['类型']=='Expense']
                
                if not exp_df.empty:
                    # 按类别汇总
                    cat_group = exp_df.groupby('类别')['金额'].sum().reset_index()
                    
                    fig_pie = px.pie(cat_group, values='金额', names='类别', 
                                     hole=0.5, # 甜甜圈孔径
                                     color_discrete_sequence=px.colors.qualitative.Set3) # 使用更柔和的配色
                    
                    fig_pie.update_traces(textposition='outside', textinfo='percent+label')
                    fig_pie.update_layout(showlegend=False) # 隐藏图例，直接看图上的字，更简洁
                    st.plotly_chart(fig_pie, use_container_width=True)
                else:
                    st.info("本周期无支出")

            # 右边：每日/每月支出走势 (完美日历轴)
            with c2:
                # 1. 过滤：只看支出
                daily_exp = filtered_df[filtered_df['类型'] == 'Expense'].copy()
                
                if not daily_exp.empty:
                    # === 核心修改逻辑 ===
                    if filter_mode == "按月份查看":
                        st.subheader(f"📅 {sel_month}月 每日花销")
                        # 提取“几号” (1-31)
                        daily_exp['day_num'] = daily_exp['日期'].dt.day
                        # 算出这个月一共有多少天 (比如2月是28天，1月是31天)
                        days_in_month = pd.Period(f"{sel_year}-{sel_month}-01").days_in_month
                        
                        # 分组统计
                        group_data = daily_exp.groupby(['day_num', '类别'])['金额'].sum().reset_index()
                        
                        # 画图
                        fig_bar = px.bar(group_data, x='day_num', y='金额', color='类别',
                                         text_auto='.0f', # 显示整数金额，不带小数更干净
                                         color_discrete_sequence=px.colors.qualitative.Set3)
                        
                        # 强制 X 轴显示每一天 (1 到 月底)
                        fig_bar.update_xaxes(
                            range=[0.5, days_in_month + 0.5], # 强制范围，两边留点空隙
                            tickmode='linear', # 线性刻度
                            dtick=1, # 每一天都显示一个刻度
                            title_text="日期 (日)"
                        )

                    else: # 按年份查看
                        st.subheader(f"📅 {sel_year}年 每月趋势")
                        # 提取“几月” (1-12)
                        daily_exp['month_num'] = daily_exp['日期'].dt.month
                        
                        group_data = daily_exp.groupby(['month_num', '类别'])['金额'].sum().reset_index()
                        
                        fig_bar = px.bar(group_data, x='month_num', y='金额', color='类别',
                                         text_auto='.0f',
                                         color_discrete_sequence=px.colors.qualitative.Set3)
                        
                        # 强制 X 轴显示 1-12 月
                        fig_bar.update_xaxes(
                            range=[0.5, 12.5], 
                            tickmode='linear', 
                            dtick=1,
                            title_text="月份"
                        )
                    
                    # 通用配置
                    fig_bar.update_layout(
                        yaxis_title="金额 (RM)", 
                        showlegend=True,
                        hovermode="x unified",
                        bargap=0.2 # 柱子之间留点缝隙，更好看
                    )
                    st.plotly_chart(fig_bar, use_container_width=True)
                else:
                    st.info("本周期内没有支出记录")


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


