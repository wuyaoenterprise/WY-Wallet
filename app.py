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

# ⚡️ 新增缓存装饰器：解决数据库请求延迟
@st.cache_data(ttl=600)
def load_data():
    try:
        # 按照日期和ID降序，确保同一天最新的记录在最上方
        res = supabase.table("transactions").select("*").order("date", desc=True).order("id", desc=True).execute()
        df = pd.DataFrame(res.data)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date']).dt.date
        return df
    except:
        return pd.DataFrame()

# ⚡️ 新增缓存装饰器
@st.cache_data(ttl=3600)
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
        st.cache_data.clear() # ⚡️ 数据变动清空缓存
        st.toast(f"✅ 已删除 ID: {row_id}")
        st.rerun()
    except Exception as e:
        st.error(f"删除失败: {e}")

# ⚡️ 历史记录更新函数
def update_row(row_id, updated_data):
    try:
        supabase.table("transactions").update(updated_data).eq("id", row_id).execute()
        st.cache_data.clear() # ⚡️ 数据变动清空缓存
        st.toast(f"✅ 修改成功")
        st.rerun()
    except Exception as e:
        st.error(f"修改失败: {e}")

# ⚡️ 核心修复：增强版保存函数
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
        st.cache_data.clear() # ⚡️ 数据变动清空缓存
        return True
    except Exception as e:
        st.error(f"保存失败: {e}")
        return False

# --- 4. AI 翻译逻辑 ---
def ai_analyze_receipt(image):
    current_cats = get_categories()
    model_name = 'gemini-2.0-flash' 
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

# === Tab 1: 记账与历史 (维持原样) ===
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
            st.info("💡 请核对结果 (类别可点击下拉修改)")
            df_pending = pd.DataFrame(st.session_state['pending_data'])
            if not df_pending.empty:
                if 'date' in df_pending.columns:
                    df_pending['date'] = pd.to_datetime(df_pending['date'])
                if 'amount' in df_pending.columns:
                    df_pending['amount'] = df_pending['amount'].astype(float)
            
            current_options = get_categories()
            edited = st.data_editor(
                df_pending, 
                num_rows="dynamic", 
                use_container_width=True,
                column_config={
                    "category": st.column_config.SelectboxColumn("类别", options=current_options, required=True),
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

        with st.expander("➕ 手动记账", expanded=True):
            with st.form("manual_form", clear_on_submit=True):
                d_in = st.date_input("日期", date.today())
                cat_in = st.selectbox("类别", get_categories())
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
                h1, h2, h3, h4, h5, h6 = st.columns([1.2, 1.8, 1.2, 1, 0.45, 0.45])
                h1.markdown("**📅 日期**")
                h2.markdown("**🏷️ 类别**")
                h3.markdown("**📝 项目**")
                h4.markdown("**💰 金额**")
                h5.markdown("**改**")
                h6.markdown("**删**")
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
                            new_cat = st.selectbox("修改类别", get_categories(), index=get_categories().index(row['category']) if row['category'] in get_categories() else 0)
                            new_item = st.text_input("修改项目", row['item'])
                            new_amt = st.number_input("修改金额", value=float(row['amount']), step=0.01)
                            new_type = st.radio("修改类型", ["Expense", "Income"], index=0 if row['type'] == "Expense" else 1)
                            if st.form_submit_button("保存修改"):
                                update_row(row['id'], {
                                    "date": str(new_date),
                                    "category": new_cat,
                                    "item": new_item,
                                    "amount": new_amt,
                                    "type": new_type
                                })
                    
                    if c6.button("🗑️", key=f"del_{row['id']}"):
                        delete_row(row['id'])
                    
                    st.markdown("<hr style='margin: 5px 0; opacity: 0.3;'>", unsafe_allow_html=True)
            else:
                st.info("本月无数据")
        else:
            st.info("暂无数据")

# === Tab 2: 深度报表 (0延迟优化版) ===
with tab2:
    if not df_all.empty:
        # ⚡️ 新增局部渲染外壳，仅包裹 Tab 2 内容
        @st.fragment
        def render_tab2_charts():
            st.subheader("📊 每日支出")
            
            b_c1, b_c2, b_c3 = st.columns([1, 1, 1])
            # 使用唯一 key 避免冲突
            b_year = b_c1.selectbox("年份", u_years, key="b_y_frag")
            b_month = b_c2.selectbox("月份", range(1, 13), index=datetime.now().month-1, key="b_m_frag")
            use_log = b_c3.toggle("对数模式 (查看微小支出)", value=False, help="开启后可以看清几块钱的小额支出")

            # 以下 100% 维持你原本的绘图逻辑和参数
            df_all['day'] = df_all['date'].dt.day
            plot_mask = (df_all['date'].dt.year == b_year) & (df_all['date'].dt.month == b_month) & (df_all['type'] == 'Expense')
            df_plot = df_all[plot_mask]
            df_plot = df_plot[df_plot['amount'] > 0]
            
            if not df_plot.empty:
                daily_data = df_plot.groupby(['day', 'category'])['amount'].sum().reset_index()
                last_day = calendar.monthrange(b_year, b_month)[1]

                fig = px.bar(
                    daily_data, 
                    x='day', 
                    y='amount', 
                    color='category', 
                    title=f"{b_year}年{b_month}月 每日分布",
                    labels={'day':'日期', 'amount':'金额 (RM)', 'category':'类别'},
                    text_auto='.0f', 
                    template="plotly_dark",
                    log_y=use_log
                )
                fig.update_xaxes(tickmode='linear', tick0=1, dtick=1, range=[0.5, last_day + 0.5], fixedrange=True)
                fig.update_yaxes(fixedrange=True)
                st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
                
                st.divider()
                st.subheader("支出构成")
                pie_data = df_plot.groupby('category')['amount'].sum().reset_index()
                # 维持你原本复杂的饼图参数
                fig_pie = px.pie(pie_data, values='amount', names='category', hole=0.5, color_discrete_sequence=px.colors.qualitative.Bold)
                fig_pie.update_traces(textposition='outside', textinfo='label+percent', rotation=90, marker=dict(line=dict(color='#000000', width=1)))
                fig_pie.update_layout(margin=dict(t=80, b=80, l=120, r=120), autosize=True, uniformtext_minsize=11, uniformtext_mode='show', height=600)
                st.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.warning("该月无有效支出")

        # 执行局部渲染
        render_tab2_charts()

# === Tab 3: 设置 (维持原样) ===
with tab3:
    st.header("⚙️ 类别管理")
    current_cats = get_categories()
    c1, c2 = st.columns(2)
    with c1:
        new_cat = st.text_input("✨ 新类别")
        if st.button("添加"):
            if new_cat and new_cat not in current_cats:
                supabase.table("categories").insert({"name": new_cat}).execute()
                st.cache_data.clear()
                st.rerun()
    with c2:
        del_cat = st.selectbox("🗑️ 删除类别", current_cats)
        if st.button("确认删除"):
            supabase.table("categories").delete().eq("name", del_cat).execute()
            st.cache_data.clear()
            st.rerun()
