import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from datetime import datetime
import google.generativeai as genai
from PIL import Image
# --- 补齐缺失的库 ---
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. 必须放在第一行的配置 ---
st.set_page_config(page_title="Smart Asset Pro", page_icon="💳", layout="wide")

# --- 核心配置 ---
# ⚠️ 修改：不再直接写死 Key，而是从云端保险箱读取
try:
    my_api_key = st.secrets["GOOGLE_API_KEY"]
except:
    my_api_key = "" 
    st.error("未检测到密钥，请在 Streamlit Cloud 配置 Secrets")

# 强制启动配置
if my_api_key:
    genai.configure(api_key=my_api_key)

# 容错配置
try:
    genai.configure(api_key=my_api_key)
except Exception as e:
    st.error(f"API Key 配置失败，请检查代码: {e}")

# --- 3. 数据库逻辑 (自动修复模式) ---
def init_db():
    conn = sqlite3.connect('expenses_pro.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  date TEXT, item TEXT, category TEXT, type TEXT, amount REAL, note TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS categories
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, type TEXT)''')
    
    # 初始化默认类别
    c.execute("SELECT count(*) FROM categories")
    if c.fetchone()[0] == 0:
        default_cats = [
            ("饮食", "Expense"), ("交通", "Expense"), ("购物", "Expense"), ("居住", "Expense"), 
            ("娱乐", "Expense"), ("医疗", "Expense"), ("工资", "Income"), ("投资", "Income"), ("其他", "Income")
        ]
        c.executemany("INSERT INTO categories (name, type) VALUES (?, ?)", default_cats)
        conn.commit()
    conn.commit()
    conn.close()

def run_query(query, params=(), fetch=False):
    try:
        conn = sqlite3.connect('expenses_pro.db')
        c = conn.cursor()
        c.execute(query, params)
        if fetch:
            data = c.fetchall()
            conn.close()
            return data
        conn.commit()
        conn.close()
    except Exception as e:
        st.error(f"数据库错误: {e}")
        return []

# --- 补齐缺失的备份函数 ---
def backup_to_cloud(spreadsheet_name):
    """将本地 SQLite 数据全量覆盖到 Google Sheets"""
    try:
        # 1. 连接 Google Sheets
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # 尝试读取机器人配置
        if "gcp_service_account" not in st.secrets:
            return False, "未找到机器人配置，请检查 Secrets 是否填写了 [gcp_service_account]"
            
        creds_dict = st.secrets["gcp_service_account"] 
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        # 2. 打开表格
        try:
            sh = client.open(spreadsheet_name)
        except gspread.SpreadsheetNotFound:
            return False, f"找不到名为 '{spreadsheet_name}' 的表格，请先去 Google Drive 创建并分享给机器人。"
            
        # 3. 读取本地所有数据
        data = run_query("SELECT * FROM transactions", fetch=True)
        if not data:
            return True, "本地没有数据，无需备份。"
            
        df = pd.DataFrame(data, columns=['ID', '日期', '项目', '类别', '类型', '金额', '备注', '创建时间'])
        
        # 4. 写入云端 (使用 Transactions 工作表)
        try:
            ws = sh.worksheet("Transactions")
        except:
            ws = sh.add_worksheet(title="Transactions", rows=1000, cols=10)
            
        # 清空旧数据并写入新数据
        ws.clear()
        # 写入表头和内容
        # Google Sheets 需要将 datetime 对象转为字符串，否则可能报错
        df = df.astype(str) 
        ws.update([df.columns.values.tolist()] + df.values.tolist())
        
        return True, f"成功备份 {len(df)} 条记录到云端！"
        
    except Exception as e:
        return False, f"备份失败: {str(e)}"

# --- 4. AI 智能识别逻辑 ---
def ai_analyze_receipt(image):
    model = genai.GenerativeModel('gemini-2.5-flash') 
    
    prompt = """
    你是一个精明的财务助理。请分析这张收据图片，并将每一项具体的购买物品拆分出来。
    要求：
    1. 识别每一行商品，如果无法精确识别单价，请根据总价合理估算分配。
    2. 为每一个商品自动匹配最合适的类别（例如：KFC是饮食，Panadol是医疗，洗发水是购物）。
    3. 输出必须严格为 JSON 数组格式（Array），不要包含 Markdown 标记。
    JSON 格式示例：
    [
        {"date": "2026-01-02", "item": "鸡肉", "category": "饮食", "amount": 15.50},
        {"date": "2026-01-02", "item": "洗洁精", "category": "居住", "amount": 8.90}
    ]
    """
    try:
        with st.spinner('🤖 AI 正在识别并拆单...'):
            response = model.generate_content([prompt, image])
            text = response.text.strip().replace("```json", "").replace("```", "")
            import json
            data = json.loads(text)
            if isinstance(data, dict): data = [data]
            return data, None
    except Exception as e:
        return None, f"AI 识别出错: {str(e)}"

# --- 主程序 ---
init_db()

# 导航栏
tab1, tab2, tab3, tab4 = st.tabs(["📝 智能记账", "📊 报表分析", "📅 每日详情", "⚙️ 设置"])

# === Tab 1: 记账 (安全版) ===
with tab1:
    st.caption("📷 拍照后 AI 会自动列出所有商品清单")
    
    with st.expander("📷 上传收据", expanded=True):
        uploaded_file = st.file_uploader("上传图片", type=['jpg', 'png', 'jpeg'], key="uploader_safe")
        
        # 只有点击按钮才触发 AI，避免死循环
        if uploaded_file and st.button("🚀 开始 AI 拆单识别", type="primary"):
            image = Image.open(uploaded_file)
            ai_data_list, error = ai_analyze_receipt(image)
            
            if ai_data_list:
                # 清洗数据
                clean_data = []
                for item in ai_data_list:
                    try:
                        d_str = item.get('date', str(datetime.now().date()))
                        d_obj = pd.to_datetime(d_str).date()
                    except:
                        d_obj = datetime.now().date()
                    
                    clean_data.append({
                        "date": d_obj, 
                        "item": item.get('item', '未知商品'),
                        "category": item.get('category', '其他'),
                        "amount": float(item.get('amount', 0.0)),
                        "type": "Expense",
                        "note": item.get('note', '')
                    })
                
                # 存入 Session State
                st.session_state['pending_items'] = clean_data
                st.success("识别成功！请在下方核对。")
                # 注意：这里不再自动 rerun，避免死循环
            elif error:
                st.error(error)

    # 结果展示区
    if 'pending_items' in st.session_state and st.session_state['pending_items']:
        st.divider()
        st.subheader("🧐 核对清单")
        
        cats_raw = run_query("SELECT name FROM categories", fetch=True)
        cat_options = [c[0] for c in cats_raw] if cats_raw else ["其他"]

        edited_df = st.data_editor(
            st.session_state['pending_items'],
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD", required=True),
                "item": st.column_config.TextColumn("项目", required=True),
                "category": st.column_config.SelectboxColumn("类别", options=cat_options, required=True),
                "amount": st.column_config.NumberColumn("金额", format="%.2f", required=True),
                "type": st.column_config.SelectboxColumn("类型", options=["Expense", "Income"], required=True)
            },
            key="editor_safe"
        )
        
        col1, col2 = st.columns([1, 1])
        if col1.button("✅ 确认保存", type="primary"):
            count = 0
            for row in edited_df:
                run_query("INSERT INTO transactions (date, item, category, type, amount, note) VALUES (?, ?, ?, ?, ?, ?)",
                          (row['date'], row['item'], row['category'], row['type'], row['amount'], row.get('note', '')))
                count += 1
            st.success(f"已保存 {count} 条记录！")
            del st.session_state['pending_items']
            # 手动移除上传文件缓存，防止误触
            
        if col2.button("🗑️ 放弃"):
            del st.session_state['pending_items']

    # 手动记账
    else:
        st.divider()
        st.caption("手动记账模式")
        with st.form("manual_form"):
            c1, c2 = st.columns(2)
            d = c1.date_input("日期", datetime.now())
            t = c2.radio("类型", ["支出", "收入"], horizontal=True)
            
            cats_raw = run_query("SELECT name FROM categories", fetch=True)
            cat_list = [c[0] for c in cats_raw] if cats_raw else ["其他"]
            cat = st.selectbox("类别", cat_list)
            
            amt = st.number_input("金额", 0.0)
            it = st.text_input("项目")
            note = st.text_area("备注")
            
            if st.form_submit_button("保存"):
                tx_type = "Expense" if t == "支出" else "Income"
                run_query("INSERT INTO transactions (date, item, category, type, amount, note) VALUES (?, ?, ?, ?, ?, ?)",
                          (d, it, cat, tx_type, amt, note))
                st.success("保存成功")

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
    st.subheader("📝 详细活动记录")
    
    # 1. 查询数据 (按日期倒序，最新的在最上面)
    # 我们只取需要的列，不取 created_at 这种系统时间，以免混淆
    data_log = run_query("SELECT date, item, category, type, amount, note FROM transactions ORDER BY date DESC, id DESC", fetch=True)
    
    if data_log:
        # 转为 DataFrame
        df_log = pd.DataFrame(data_log, columns=['日期', '项目', '类别', '类型', '金额', '备注'])
        
        # 2. 关键修复：确保日期格式被正确识别
        # 先转为标准时间格式，方便后续只提取“日期”部分
        df_log['日期'] = pd.to_datetime(df_log['日期'])

        # 3. 显示表格
        st.dataframe(
            df_log,
            use_container_width=True, # 铺满屏幕宽度
            hide_index=True,          # 隐藏左边的 0,1,2 序号
            column_config={
                # 强制格式化为 YYYY-MM-DD，彻底去除 00:00:00
                "日期": st.column_config.DateColumn("日期", format="YYYY-MM-DD", width="medium"),
                "项目": st.column_config.TextColumn("项目名称", width="medium"),
                "类别": st.column_config.TextColumn("类别", width="small"),
                "金额": st.column_config.NumberColumn("金额 (RM)", format="%.2f"),
                "类型": st.column_config.TextColumn("类型", width="small"),
                "备注": st.column_config.TextColumn("备注", width="large"),
            }
        )
    else:
        st.info("📭 暂无交易记录")

# === Tab 4: 设置 ===
with tab4:
    st.header("⚙️ 系统设置")
    
    # 1. 备份区
    with st.container(border=True):
        st.subheader("☁️ 云端备份")
        st.info("将本地数据同步到 Google Sheets (表格名: MyExpensesDB)")
        # ⚠️ 请确保你在 Google Drive 里创建了叫 'MyExpensesDB' 的表，并分享给了机器人邮箱
        if st.button("开始备份到云端", type="primary"):
            with st.spinner("正在连接 Google Cloud..."):
                success, msg = backup_to_cloud("MyExpensesDB")
                if success:
                    st.success(msg)
                else:
                    st.error(msg)
    
    # 2. 危险操作区
    st.markdown("---")
    with st.expander("危险操作 (清空数据)"):
        if st.button("⚠️ 清空所有本地记录"):
            run_query("DELETE FROM transactions")
            st.warning("数据已清空")
            st.rerun()
