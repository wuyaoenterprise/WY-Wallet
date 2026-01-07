import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, date
import google.generativeai as genai
from PIL import Image
from supabase import create_client, Client
import calendar
import json 

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
        res = supabase.table("transactions").select("*").order("date", desc=True).execute()
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

# ⚡️ 核心修复：增强版保存函数，兼容 DataFrame 和 列表
def save_to_cloud(data_input):
    try:
        # 如果传入的是 DataFrame（表格数据），先转成字典列表
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

# --- 4. AI 翻译逻辑 ---
def ai_analyze_receipt(image):
    current_cats = get_categories()
    
    # 锁定 gemini-2.5-flash
    model_name = 'gemini-2.5-flash' 
    
    try:
        model = genai.GenerativeModel(model_name)
        
        prompt = f"""
        你是一个精明的财务助理。分析收据并将每一项拆分。
        要求：
        1. 必须将 item(项目名称) 翻译成简练的中文。
        2. 输出纯粹的 JSON 数组格式，不要包含 Markdown 标记。
        3. 格式示例：[{{"date": "YYYY-MM-DD", "item": "中文名称", "category": "类别", "amount": 10.5, "type": "Expense"}}]
        4. 类别(category)必须从以下列表中选择: {", ".join(current_cats)}
        """
        
        with st.spinner(f'🤖 AI ({model_name}) 正在识别...'):
            response = model.generate_content([prompt, image])
            
            if not response.text:
                return None, "AI 返回了空内容"
            
            raw_text = response.text.strip()
            # 清理 Markdown
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

# --- 5. 主程序 UI ---
tab1, tab2, tab3 = st.tabs(["📝 记账与历史", "📊 深度报表", "⚙️ 设置"])

# === Tab 1: 左右排布 + 表格化历史记录 ===
with tab1:
    col_left, col_right = st.columns([1, 1.8], gap="large")

    # --- 左侧：记账输入 ---
    with col_left:
        st.subheader("📥 新增账目")
        up_file = st.file_uploader("📷 上传收据", type=['jpg', 'jpeg', 'png'])
        if up_file and st.button("🚀 AI 识别", type="primary"):
            data, err = ai_analyze_receipt(Image.open(up_file))
            if data: st.session_state['pending_data'] = data
            else: st.error(err)

        if 'pending_data' in st.session_state:
            st.info("💡 请核对结果 (类别可点击下拉修改)")
            
            # ⚡️ 核心修复：先把数据转成 DataFrame 再喂给编辑器，防止崩溃
            df_pending = pd.DataFrame(st.session_state['pending_data'])
            
            # 确保日期和金额的类型正确
            if not df_pending.empty:
                if 'date' in df_pending.columns:
                    df_pending['date'] = pd.to_datetime(df_pending['date'])
                if 'amount' in df_pending.columns:
                    df_pending['amount'] = df_pending['amount'].astype(float)
            
            current_options = get_categories()
            
            edited = st.data_editor(
                df_pending, # 这里传入处理好的 DataFrame
                num_rows="dynamic", 
                use_container_width=True,
                column_config={
                    "category": st.column_config.SelectboxColumn(
                        "类别",
                        help="点击选择类别",
                        width="medium",
                        options=current_options,
                        required=True,
                    ),
                    "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
                    "amount": st.column_config.NumberColumn("金额 (RM)", format="%.2f"),
                    "type": st.column_config.SelectboxColumn("类型", options=["Expense", "Income"])
                }
            )
            
            if st.button("✅ 确认同步到云端"):
                if save_to_cloud(edited):
                    st.success("同步成功！")
                    del st.session_state['pending_data']
                    st.rerun()

        # 手动记账
        with st.expander("➕ 手动记账", expanded=True):
            with st.form("manual_form"):
                d_in = st.date_input("日期", date.today())
                it_in = st.text_input("项目名称")
                cat_in = st.selectbox("类别", get_categories())
                t_in = st.radio("类型", ["Expense", "Income"], horizontal=True)
                # 默认留空
                amt_in = st.number_input("金额 (RM)", min_value=0.0, step=0.01, value=None, placeholder="输入金额...")
                
                if st.form_submit_button("立即存入"):
                    if amt_in is not None:
                        if save_to_cloud([{"date":d_in, "item":it_in, "category":cat_in, "type":t_in, "amount":amt_in}]):
                            st.rerun()
                    else:
                        st.warning("⚠️ 请输入金额")

    # --- 右侧：历史记录 (日期清晰版) ---
    with col_right:
        st.subheader("📜 历史记录")
        df_all = load_data()
        
        if not df_all.empty:
            df_all['date'] = pd.to_datetime(df_all['date'])
            u_years = sorted(df_all['date'].dt.year.unique(), reverse=True)
            f_c1, f_c2 = st.columns(2)
            sel_y = f_c1.selectbox("年份", u_years, key="h_y")
            sel_m = f_c2.selectbox("月份", range(1, 13), index=datetime.now().month-1, key="h_m")
            
            mask = (df_all['date'].dt.year == sel_y) & (df_all['date'].dt.month == sel_m)
            df_filtered = df_all[mask]

            if not df_filtered.empty:
                st.markdown("---")
                
                # 表头：明确显示日期
                h1, h2, h3, h4, h5 = st.columns([1.2, 2, 1.2, 1, 0.6])
                h1.markdown("**📅 日期**")
                h2.markdown("**📝 项目**")
                h3.markdown("**🏷️ 类别**")
                h4.markdown("**💰 金额**")
                h5.markdown("**操作**")
                
                st.divider()

                # 循环渲染每一行
                for _, row in df_filtered.iterrows():
                    c1, c2, c3, c4, c5 = st.columns([1.2, 2, 1.2, 1, 0.6])
                    
                    # 1. 日期 (独立一列，不再混淆)
                    c1.write(row['date'].strftime('%Y-%m-%d'))
                    
                    # 2. 项目
                    c2.write(row['item'])
                    
                    # 3. 类别
                    c3.caption(row['category'])
                    
                    # 4. 金额
                    color = "red" if row['type'] == "Expense" else "green"
                    c4.markdown(f":{color}[{row['amount']:.2f}]")
                    
                    # 5. 删除按钮
                    if c5.button("🗑️", key=f"del_{row['id']}"):
                        delete_row(row['id'])
                    
                    st.markdown("<hr style='margin: 5px 0; opacity: 0.3;'>", unsafe_allow_html=True)
            else:
                st.info("本月无数据")
        else:
            st.info("暂无数据")

# === Tab 2: 深度报表 (增强可视化版) ===
with tab2:
    if not df_all.empty:
        st.subheader("📊 每日支出分析")
        
        # 1. 筛选器
        b_c1, b_c2, b_c3 = st.columns([1, 1, 1])
        b_year = b_c1.selectbox("年份", u_years, key="b_y")
        b_month = b_c2.selectbox("月份", range(1, 13), index=datetime.now().month-1, key="b_m")
        
        # ⚡️ 解决痛点 1：增加对数坐标开关，解决巨大支出压扁小支出的问题
        use_log = b_c3.toggle("对数模式 (查看小额支出)", help="当某天支出巨大导致其他天看不清时，请开启此项")

        df_all['day'] = df_all['date'].dt.day
        plot_mask = (df_all['date'].dt.year == b_year) & (df_all['date'].dt.month == b_month) & (df_all['type'] == 'Expense')
        df_plot = df_all[plot_mask]
        
        df_plot = df_plot[df_plot['amount'] > 0]
        
        if not df_plot.empty:
            daily_data = df_plot.groupby(['day', 'category'])['amount'].sum().reset_index()
            last_day = calendar.monthrange(b_year, b_month)[1]

            # 柱状图
            fig = px.bar(
                daily_data, x='day', y='amount', color='category', 
                title=f"{b_year}年{b_month}月 每日分布",
                labels={'day':'日期', 'amount':'金额 (RM)', 'category':'类别'},
                text_auto='.0f', 
                template="plotly_dark",
                log_y=use_log # ⚡️ 动态切换对数坐标
            )
            
            fig.update_layout(
                xaxis=dict(tickmode='linear', tick0=1, dtick=1, range=[0.5, last_day + 0.5]),
                # ⚡️ 移除了 fixedrange，允许用户手动鼠标缩放 (Zoom)
                dragmode='zoom', 
                hovermode="x unified"
            )
            
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': True})
            
            st.divider()
            
            # 甜甜圈图
            st.subheader("🍩 支出构成")
            fig_pie = px.pie(
                df_plot, 
                values='amount', 
                names='category', 
                hole=0.5,
                color_discrete_sequence=px.colors.qualitative.Pastel
            )
            
            # ⚡️ 解决痛点 2：优化标签显示
            fig_pie.update_traces(
                textposition='outside', 
                textinfo='percent+label',
                insidetextorientation='horizontal'
            )
            
            fig_pie.update_layout(
                showlegend=True,
                # 强制设置最小字号，防止由于切片太小导致标签消失
                uniformtext_minsize=10, 
                uniformtext_mode='hide',
                # 自动调整边缘防止标签被图表框切断
                margin=dict(t=50, b=50, l=50, r=50) 
            )
            
            st.plotly_chart(fig_pie, use_container_width=True)
            
        else:
            st.warning("该月无有效支出")

# === Tab 3: 设置 ===
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

