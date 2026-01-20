import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, date
import google.generativeai as genai
from PIL import Image
from supabase import create_client, Client
import calendar
import json 
import io

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

@st.cache_data(ttl=600)
def load_data():
    try:
        res = supabase.table("transactions").select("*").order("date", desc=True).order("id", desc=True).execute()
        df = pd.DataFrame(res.data)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date']).dt.date
        return df
    except:
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def get_categories():
    try:
        res = supabase.table("categories").select("name").execute()
        if not res.data:
            return ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "其他"]
        return [c['name'] for c in res.data]
    except:
        return ["饮食", "交通", "购物", "居住", "娱乐", "医疗", "其他"]

# ⚡️ 新增功能：按使用频率对类别进行排序
def get_sorted_categories(df_all, categories):
    if df_all.empty:
        return categories
    # 统计频率
    counts = df_all['category'].value_counts().to_dict()
    # 按频率降序排列，没用过的类别排在最后
    return sorted(categories, key=lambda x: counts.get(x, 0), reverse=True)

def delete_row(row_id):
    try:
        supabase.table("transactions").delete().eq("id", row_id).execute()
        st.cache_data.clear() 
        st.toast(f"✅ 已删除 ID: {row_id}")
        st.rerun()
    except Exception as e:
        st.error(f"删除失败: {e}")

def update_row(row_id, updated_data):
    try:
        supabase.table("transactions").update(updated_data).eq("id", row_id).execute()
        st.cache_data.clear() 
        st.toast(f"✅ 修改成功")
        st.rerun()
    except Exception as e:
        st.error(f"修改失败: {e}")

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
        st.cache_data.clear() 
        return True
    except Exception as e:
        st.error(f"保存失败: {e}")
        return False

# --- 4. AI 翻译逻辑 ---
def ai_analyze_receipt(image):
    current_cats = get_categories()
    model_name = 'gemini-3-flash-preview' 
    try:
        model = genai.GenerativeModel(model_name)
        prompt = f"""
        你是一个精明的财务助理。分析收据并将每一项拆分。
        要求：
        1. 必须将 item(项目名称) 翻译成简练的中文。
        2. 输出纯粹的 JSON 数组格式，不要包含 Markdown 标记。
        3. 如果有折扣直接算入折扣项目，无需分出显示。
        4. 格式示例：[{{"date": "YYYY-MM-DD", "item": "中文名称", "category": "类别", "amount": 10.5, "type": "Expense"}}]
        5. 类别(category)必须从以下列表中选择: {", ".join(current_cats)}
        """
        with st.spinner(f'🤖 AI 正在识别...'):
            response = model.generate_content([prompt, image])
            if not response.text:
                return None, "AI 返回了空内容"
            raw_text = response.text.strip()
            if raw_text.startswith("```json"): raw_text = raw_text[7:]
            if raw_text.startswith("```"): raw_text = raw_text[3:]
            if raw_text.endswith("```"): raw_text = raw_text[:-3]
            try:
                data = json.loads(raw_text.strip())
                return data, None
            except json.JSONDecodeError:
                return None, f"解析失败: {raw_text}"
    except Exception as e:
        return None, f"请求出错: {str(e)}"
        
# ⚡️ Tab 4 的核心逻辑：AI 宏观归类
@st.cache_data(show_spinner=False)
def ai_categorize_macro(unique_items_json):
    """
    将用户的具体消费项目归类为宏观大类。
    """
    model_name = 'gemini-2.5-flash'
    try:
        model = genai.GenerativeModel(model_name)
        prompt = f"""
        你是一个高级数据分析师。我给你一列用户的消费项目和当前的小分类。
        请根据常识，将它们归类为以下【宏观大类】(Macro Category) 之一：
        [餐饮美食, 交通出行, 居家生活, 购物消费, 休闲娱乐, 医疗健康, 教育学习, 投资理财, 旅游度假, 其他]
        
        输入数据: {unique_items_json}
        
        要求：
        1. 必须返回纯 JSON 格式，不要 Markdown。
        2. 格式为 Key-Value 对对象： {{"项目名": "宏观大类", "项目名2": "宏观大类"}}
        3. 例如：{{"KFC": "餐饮美食", "机票": "旅游度假", "Grab": "交通出行"}}
        """
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        # 清理
        if raw_text.startswith("```json"): raw_text = raw_text[7:]
        if raw_text.startswith("```"): raw_text = raw_text[3:]
        if raw_text.endswith("```"): raw_text = raw_text[:-3]
        return json.loads(raw_text)
    except Exception as e:
        return {}
        
# --- 5. 主程序 UI ---
# 预先获取数据以供全局使用
df_all = load_data()
all_categories = get_categories()
# ⚡️ 获取排序后的类别列表
sorted_cats = get_sorted_categories(df_all, all_categories)

tab1, tab2, tab3, tab4 = st.tabs(["📝 记账与历史", "📊 深度报表",  "🤖 AI 洞察" , "⚙️ 设置"])

# === Tab 1: 记账与历史 ===
with tab1:
    col_left, col_right = st.columns([1, 1.8], gap="large")

    with col_left:
        st.subheader("📥 新增账目")
        up_file = st.file_uploader("📷 上传收据", type=['jpg', 'jpeg', 'png'])
        if up_file and st.button("🚀 AI 识别", type="primary"):
            data, err = ai_analyze_receipt(Image.open(up_file))
            if data: st.session_state['pending_data'] = data
            else: st.error(err)

        if 'pending_data' in st.session_state:
            st.info("💡 请核对结果")
            df_pending = pd.DataFrame(st.session_state['pending_data'])
            if not df_pending.empty:
                if 'date' in df_pending.columns:
                    df_pending['date'] = pd.to_datetime(df_pending['date'])
                if 'amount' in df_pending.columns:
                    df_pending['amount'] = df_pending['amount'].astype(float)
            
            # AI 识别后的编辑框也使用排序后的类别
            edited = st.data_editor(
                df_pending, 
                num_rows="dynamic", 
                use_container_width=True,
                column_config={
                    "category": st.column_config.SelectboxColumn("类别", options=sorted_cats, required=True),
                    "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
                    "amount": st.column_config.NumberColumn("金额 (RM)", format="%.2f"),
                    "type": st.column_config.SelectboxColumn("类型", options=["Expense", "Income"])
                }
            )
          # ⚡️ [新增功能] 确认与放弃按钮并排显示
            col_b1, col_b2 = st.columns([1, 1])
            with col_b1:
                if st.button("✅ 确认同步", type="primary", use_container_width=True):
                    if save_to_cloud(edited):
                        st.success("同步成功！")
                        del st.session_state['pending_data']
                        st.rerun()
            with col_b2:
                if st.button("🗑️ 放弃本次识别", use_container_width=True):
                    del st.session_state['pending_data']
                    st.rerun()

        with st.expander("➕ 手动记账", expanded=True):
            with st.form("manual_form", clear_on_submit=True):
                d_in = st.date_input("日期", date.today())
                # ⚡️ 使用排序后的类别
                cat_in = st.selectbox("类别", sorted_cats)
                it_in = st.text_input("项目名称")
                t_in = st.radio("类型", ["Expense", "Income"], horizontal=True)
                amt_in = st.number_input("金额 (RM)", min_value=0.0, step=0.01, value=None, placeholder="输入金额...")
                
                if st.form_submit_button("立即存入"):
                    if amt_in is not None:
                        if save_to_cloud([{"date":d_in, "item":it_in, "category":cat_in, "type":t_in, "amount":amt_in}]):
                            st.rerun()
                    else:
                        st.warning("⚠️ 请输入金额")
                
    with col_right:
        st.subheader("📜 历史记录")
        
        if not df_all.empty:
            # 确保日期格式正确
            df_display = df_all.copy()
            df_display['date'] = pd.to_datetime(df_display['date'])
            u_years = sorted(df_display['date'].dt.year.unique(), reverse=True)
            f_c1, f_c2 = st.columns(2)
            sel_y = f_c1.selectbox("年份", u_years, key="h_y")
            sel_m = f_c2.selectbox("月份", range(1, 13), index=datetime.now().month-1, key="h_m")
            
            mask = (df_display['date'].dt.year == sel_y) & (df_display['date'].dt.month == sel_m)
            df_filtered = df_display[mask]

            if not df_filtered.empty:
                st.markdown("---")
                h1, h2, h3, h4, h5, h6 = st.columns([1.2, 1.8, 1.2, 1, 0.45, 0.45])
                h1.markdown("**📅 日期**"); h2.markdown("**🏷️ 类别**"); h3.markdown("**📝 项目**")
                h4.markdown("**💰 金额**"); h5.markdown("**改**"); h6.markdown("**删**")
                st.divider()

                for _, row in df_filtered.iterrows():
                    c1, c2, c3, c4, c5, c6 = st.columns([1.2, 1.8, 1.2, 1, 0.45, 0.45])
                    c1.write(row['date'].strftime('%Y-%m-%d'))
                    c2.caption(row['category'])
                    c3.write(row['item'])
                    color = "red" if row['type'] == "Expense" else "green"
                    c4.markdown(f":{color}[{row['amount']:.2f}]")
                    
                    with c5.popover("📝"):
                        st.write(f"修改 ID: {row['id']}")
                        with st.form(f"edit_form_{row['id']}"):
                            new_date = st.date_input("修改日期", row['date'])
                            # ⚡️ 修改时也使用排序后的列表，并自动匹配当前索引
                            new_cat = st.selectbox("修改类别", sorted_cats, index=sorted_cats.index(row['category']) if row['category'] in sorted_cats else 0)
                            new_item = st.text_input("修改项目", row['item'])
                            new_amt = st.number_input("修改金额", value=float(row['amount']), step=0.01)
                            new_type = st.radio("修改类型", ["Expense", "Income"], index=0 if row['type'] == "Expense" else 1)
                            if st.form_submit_button("保存修改"):
                                update_row(row['id'], {"date": str(new_date), "category": new_cat, "item": new_item, "amount": new_amt, "type": new_type})
                    
                    if c6.button("🗑️", key=f"del_{row['id']}"):
                        delete_row(row['id'])
                    
                    st.markdown("<hr style='margin: 5px 0; opacity: 0.3;'>", unsafe_allow_html=True)
            else:
                st.info("本月无数据")
        else:
            st.info("暂无数据")

# === Tab 2: 深度报表 (0延迟版) ===
with tab2:
    if not df_all.empty:
        @st.fragment
        def render_tab2_charts(df_input):
            # --- 顶部：选择器 ---
            st.subheader("📊 每日支出")
            b_c1, b_c2, b_c3 = st.columns([1, 1, 1])
            u_y = sorted(pd.to_datetime(df_input['date']).dt.year.unique(), reverse=True)
            b_year = b_c1.selectbox("年份", u_y, key="b_y_frag")
            b_month = b_c2.selectbox("月份", range(1, 13), index=datetime.now().month-1, key="b_m_frag")
            use_log = b_c3.toggle("对数模式", value=False)

            # 显示当月收支概览
            mask_summary = (pd.to_datetime(df_input['date']).dt.year == b_year) & \
                           (pd.to_datetime(df_input['date']).dt.month == b_month)
            df_summary = df_input[mask_summary]

            total_income = df_summary[df_summary['type'] == 'Income']['amount'].sum()
            total_expense = df_summary[df_summary['type'] == 'Expense']['amount'].sum()
            balance = total_income - total_expense

            st.markdown("###") 
            m1, m2, m3 = st.columns(3)
            m1.metric("总收入", f"{total_income:,.2f}")
            m2.metric("总支出", f"{total_expense:,.2f}")
            m3.metric("结余", f"{balance:,.2f}")
            st.markdown("---") 
            
            df_p = df_input.copy()
            df_p['date'] = pd.to_datetime(df_p['date'])
            df_p['day'] = df_p['date'].dt.day
            plot_mask = (df_p['date'].dt.year == b_year) & (df_p['date'].dt.month == b_month) & (df_p['type'] == 'Expense')
            df_plot = df_p[plot_mask]
            
            if not df_plot.empty:
                # === 📅 新增功能：可视化日历视图 ===
                st.subheader(f"{b_month}月 开销日历")
                
                # 准备日历数据
                daily_sums = df_plot.groupby('day')['amount'].sum().to_dict()
                cal_matrix = calendar.monthcalendar(b_year, b_month)
                
                # 简单的 CSS 样式
                st.markdown("""
                <style>
                .cal-day {
                    background-color: #262730;
                    border-radius: 5px;
                    padding: 4px;
                    text-align: center;
                    margin: 2px;
                    height: 50px;
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                }
                .cal-num { font-size: 12px; color: #888; }
                .cal-amt { font-size: 14px; font-weight: bold; color: #ff4b4b; }
                </style>
                """, unsafe_allow_html=True)

                # 绘制表头 (周一到周日)
                cols_header = st.columns(7)
                days_name = ["一", "二", "三", "四", "五", "六", "日"]
                for i, d_name in enumerate(days_name):
                    cols_header[i].markdown(f"<div style='text-align:center; color:gray; font-size:12px'>{d_name}</div>", unsafe_allow_html=True)

                # 绘制日期格子
                for week in cal_matrix:
                    cols = st.columns(7)
                    for i, day_num in enumerate(week):
                        with cols[i]:
                            if day_num == 0:
                                st.write("") # 空白
                            else:
                                amt = daily_sums.get(day_num, 0)
                                if amt > 0:
                                    # 有支出的日子：显示金额
                                    st.markdown(f"""
                                    <div class="cal-day" style="border: 1px solid #ff4b4b;">
                                        <div class="cal-num">{day_num}</div>
                                        <div class="cal-amt">{int(amt)}</div>
                                    </div>
                                    """, unsafe_allow_html=True)
                                else:
                                    # 没支出的日子：变暗
                                    st.markdown(f"""
                                    <div class="cal-day" style="opacity: 0.3;">
                                        <div class="cal-num">{day_num}</div>
                                    </div>
                                    """, unsafe_allow_html=True)
                
                st.divider()

                # === 下方图表区 ===
                daily_data = df_plot.groupby(['day', 'category'])['amount'].sum().reset_index()
                last_day = calendar.monthrange(b_year, b_month)[1]
                fig = px.bar(
                    daily_data, x='day', y='amount', color='category', 
                    title=f"每日分布趋势",
                    labels={'day':'日期', 'amount':'金额 (RM)', 'category':'类别'},
                    text_auto='.0f', template="plotly_dark", log_y=use_log
                )
                fig.update_xaxes(tickmode='linear', tick0=1, dtick=1, range=[0.5, last_day + 0.5], fixedrange=True)
                fig.update_yaxes(fixedrange=True)
                st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
                
                st.divider()
                st.subheader("支出分类排行")
                
                # 左右布局：左边圈图，右边排行条形图
                pie_data = df_plot.groupby('category')['amount'].sum().reset_index()
                
                col_chart, col_list = st.columns([1.6, 1], gap="medium")
                
                with col_chart:
                    fig_pie = px.pie(pie_data, values='amount', names='category', hole=0.5, color_discrete_sequence=px.colors.qualitative.Bold)
                    fig_pie.update_traces(textposition='outside', textinfo='label+percent', rotation=90, marker=dict(line=dict(color='#000000', width=1)))
                    fig_pie.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=350, showlegend=False) 
                    st.plotly_chart(fig_pie, use_container_width=True)

                with col_list:
                    bar_data = pie_data.sort_values('amount', ascending=False)
                    
                    fig_bar = px.bar(
                        bar_data, 
                        x='amount', 
                        y='category', 
                        orientation='h', 
                        text_auto='.2f',
                        color='category', 
                        color_discrete_sequence=px.colors.qualitative.Bold
                    )
                    
                    # ⚡️ [修复] 彻底固定坐标轴，防止手机误触滚动
                    fig_bar.update_layout(
                        xaxis_visible=False, 
                        yaxis_title=None,    
                        showlegend=False,
                        margin=dict(l=0, r=0, t=20, b=0),
                        height=350,
                        template="plotly_dark",
                        dragmode=False, # 禁止拖拽
                    )
                    # 强制固定 X 和 Y 轴，禁止缩放
                    fig_bar.update_xaxes(fixedrange=True)
                    fig_bar.update_yaxes(fixedrange=True)

                    st.plotly_chart(fig_bar, use_container_width=True, config={'displayModeBar': False, 'staticPlot': True}) 

            else:
                st.warning("该月无有效支出")

        # 调用函数
        render_tab2_charts(df_all)
    else:
        st.info("暂无数据")

# === ⚡️ Tab 3: AI 宏观分析 (带对话功能版) ===
with tab3:
    st.header("🤖 AI 宏观消费洞察 & 对话")
    st.info("AI 将自动把你的支出归类为「宏观大类」，并允许你针对这些数据进行自由提问。")
    
    # --- 1. 初始化 Session State (用于保留分析结果和对话记录) ---
    if 'ai_chat_history' not in st.session_state:
        st.session_state['ai_chat_history'] = []
    
    # 如果还没有分析结果，显示分析界面
    if df_all.empty:
        st.warning("暂无数据可分析")
    else:
        # 数据准备
        df_analysis = df_all[df_all['type'] == 'Expense'].copy()
        
        # 筛选年份
        col_t1, col_t2 = st.columns(2)
        u_years = sorted(df_analysis['date'].apply(lambda x: x.year).unique(), reverse=True)
        target_year = col_t1.selectbox("选择年份", u_years, key="ai_year")
        
        # 提取当年的数据
        df_target = df_analysis[df_analysis['date'].apply(lambda x: x.year) == target_year]
        
        # --- 2. 分析按钮逻辑 ---
        # 只有当没有分析结果，或者用户切换了年份时，才显示“开始分析”按钮
        # 这里为了简单，提供一个“重新分析”的按钮来覆盖旧结果
        if st.button("🧠 开始 (或重新) AI 智能归类分析", type="primary"):
            if df_target.empty:
                st.warning(f"{target_year} 年无支出数据")
            else:
                unique_items = df_target['item'].unique().tolist()
                with st.spinner("AI 正在思考并归类你的所有账单..."):
                    # 调用 AI 归类 (复用你原有的函数)
                    mapping_dict = ai_categorize_macro(json.dumps(unique_items, ensure_ascii=False))
                    
                    if mapping_dict:
                        # 映射回 DataFrame
                        df_target['Macro Category'] = df_target['item'].map(mapping_dict).fillna("其他")
                        
                        # 🔥 关键：保存到 Session State
                        st.session_state['ai_macro_result'] = df_target
                        st.session_state['current_analysis_year'] = target_year
                        # 清空旧的对话记录，因为数据变了
                        st.session_state['ai_chat_history'] = []
                        st.success("分析完成！")
                        st.rerun() # 刷新页面以显示结果
                    else:
                        st.error("AI 分析失败，请重试")

        # --- 3. 展示分析结果 (如果存在) ---
        if 'ai_macro_result' in st.session_state:
            # 确保显示的是当前选择年份的数据（如果用户切了年份但没点分析，这里显示旧的也行，或者强制隐藏，这里选择显示已分析的数据）
            df_res = st.session_state['ai_macro_result']
            analyzed_year = st.session_state.get('current_analysis_year', target_year)
            
            st.divider()
            st.subheader(f"📈 {analyzed_year} 宏观消费分布")
            
            # 统计大类
            macro_stats = df_res.groupby('Macro Category')['amount'].sum().reset_index().sort_values('amount', ascending=False)
            
            c_chart, c_data = st.columns([1.5, 1])
            with c_chart:
                fig = px.pie(macro_stats, values='amount', names='Macro Category', 
                             title="AI 智能大类占比", hole=0.4, 
                             color_discrete_sequence=px.colors.qualitative.Pastel)
                fig.update_traces(textposition='outside', textinfo='label+percent')
                st.plotly_chart(fig, use_container_width=True)
                
            with c_data:
                st.write("📋 **详细归类清单**")
                st.dataframe(
                    macro_stats.style.format({"amount": "{:.2f}"}), 
                    use_container_width=True,
                    column_config={"Macro Category": "宏观大类", "amount": "总金额 (RM)"}
                )
            
            with st.expander("🔍 查看原始归类明细"):
                st.dataframe(df_res[['date', 'item', 'amount', 'Macro Category']].sort_values('date', ascending=False))

            # --- 4. 💬 AI 数据对话窗口 (新增功能) ---
            st.markdown("---")
            st.subheader(f"💬 与 {analyzed_year} 年的账单对话")
            
            # 显示历史消息
            for msg in st.session_state['ai_chat_history']:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            # 处理用户输入
            if prompt := st.chat_input("问问 AI，比如：'我在吃的方面花了多少钱？' 或 '哪个月开销最大？'"):
                # 1. 显示用户消息
                with st.chat_message("user"):
                    st.markdown(prompt)
                st.session_state['ai_chat_history'].append({"role": "user", "content": prompt})

                # 2. 调用 AI 回答
                # 准备上下文数据 (为了节省 Token，只发必要列，并转为 CSV 文本)
                context_data = df_res[['date', 'item', 'amount', 'Macro Category']].to_csv(index=False)
                
                chat_model_name = 'gemini-2.5-flash' # 保持和你的一致
                
                try:
                    chat_model = genai.GenerativeModel(chat_model_name)
                    # 构建 Prompt
                    full_prompt = f"""
                    你是一个专业的私人财务顾问。用户正在询问关于他 {analyzed_year} 年的账单数据。
                    
                    以下是用户的详细支出数据 (CSV格式):
                    {context_data}
                    
                    用户的当前问题: "{prompt}"
                    
                    要求:
                    1. 根据上面的数据进行计算或分析来回答问题。
                    2. 回答要自然、幽默一点。
                    3. 如果涉及金额，保留两位小数，单位 RM。
                    4. 如果用户问具体的统计（比如“最贵的那个”），请明确指出是哪一笔。
                    5. 使用中文回答。
                    """
                    
                    with st.chat_message("assistant"):
                        with st.spinner("AI 正在查阅账本..."):
                            response = chat_model.generate_content(full_prompt)
                            ai_reply = response.text
                            st.markdown(ai_reply)
                            
                    # 保存 AI 回复
                    st.session_state['ai_chat_history'].append({"role": "assistant", "content": ai_reply})
                    
                except Exception as e:
                    st.error(f"对话出错: {e}")

# === Tab 4: 添加类别/数据导出 ===
with tab4:
    st.header("⚙️ 类别管理")
    c1, c2 = st.columns(2)
    with c1:
        new_cat = st.text_input("✨ 新类别")
        if st.button("添加"):
            if new_cat and new_cat not in all_categories:
                supabase.table("categories").insert({"name": new_cat}).execute()
                st.cache_data.clear()
                st.rerun()
    with c2:
        # 删除时建议按字母排序，方便找，或者也按频率排序
        del_cat = st.selectbox("🗑️ 删除类别", sorted_cats)
        if st.button("确认删除"):
            supabase.table("categories").delete().eq("name", del_cat).execute()
            st.cache_data.clear()
            st.rerun()

# ⚡️ 新增：Excel 导出功能
    st.markdown("---")
    st.header("📂 数据备份")
    st.write("将数据库中的所有账目导出为 Excel 文件。")
    
    if not df_all.empty:
        # 使用 io.BytesIO 在内存中生成 Excel 文件
        output = io.BytesIO()
        # 注意：这里使用 to_excel，pandas 默认通常使用 openpyxl
        # 如果报错缺少 openpyxl，需要在 requirements.txt 中添加 openpyxl
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_all.to_excel(writer, index=False, sheet_name='Transactions')
        
        excel_data = output.getvalue()
        
        st.download_button(
            label="📥 下载 Excel 备份",
            data=excel_data,
            file_name=f"SmartAssetPro_Backup_{date.today()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )
    else:
        st.info("暂无数据可导出")









