import calendar, io, json
from datetime import date, datetime
import pandas as pd
import plotly.express as px
import streamlit as st
from PIL import Image
from supabase import create_client
import google.generativeai as genai

st.set_page_config(page_title='WY Wallet V2', page_icon='💳', layout='wide')
st.markdown('''<style>
.block-container{max-width:1380px;padding-top:1rem}.hero{padding:20px;border:1px solid #8883;border-radius:18px;background:linear-gradient(135deg,#4e73df22,#2ca87f16);margin-bottom:14px}.card{padding:11px 13px;border:1px solid #8883;border-radius:13px;margin:7px 0}.muted{opacity:.68}.cal{display:grid;grid-template-columns:repeat(7,1fr);gap:5px}.day{min-height:60px;padding:6px;border:1px solid #8883;border-radius:9px}.amt{font-weight:700;margin-top:7px}@media(max-width:760px){.block-container{padding-left:.7rem;padding-right:.7rem}.hero{padding:14px}.day{min-height:46px;padding:3px}.amt{font-size:.68rem}}
</style>''', unsafe_allow_html=True)
try:
    sb=create_client(st.secrets['SUPABASE_URL'],st.secrets['SUPABASE_KEY']);genai.configure(api_key=st.secrets['GOOGLE_API_KEY'])
except Exception as e: st.error(f'配置加载失败：{e}');st.stop()
DEFAULT=['饮食','交通','购物','居住','娱乐','医疗','教育','投资','旅游','其他'];PAGE=20

@st.cache_data(ttl=300,show_spinner=False)
def load():
    try:
        d=pd.DataFrame(sb.table('transactions').select('*').order('date',desc=True).order('id',desc=True).execute().data)
        if d.empty:return pd.DataFrame(columns=['id','date','item','category','type','amount','note'])
        for c,v in {'item':'未知','category':'其他','type':'Expense','amount':0,'note':''}.items():
            if c not in d:d[c]=v
        d.date=pd.to_datetime(d.date,errors='coerce');d.amount=pd.to_numeric(d.amount,errors='coerce').fillna(0);d.note=d.note.fillna('').astype(str)
        return d.dropna(subset=['date'])
    except Exception as e: st.session_state.db_error=str(e);return pd.DataFrame(columns=['id','date','item','category','type','amount','note'])
@st.cache_data(ttl=1800,show_spinner=False)
def cats():
    try:return [x['name'] for x in sb.table('categories').select('name').execute().data] or DEFAULT
    except:return DEFAULT
def clear():load.clear();cats.clear()
def norm(rows):
    rows=rows.to_dict('records') if isinstance(rows,pd.DataFrame) else list(rows);out=[]
    for r in rows:
        a=pd.to_numeric(r.get('amount'),errors='coerce');d=pd.to_datetime(r.get('date',date.today()),errors='coerce')
        if pd.isna(a) or pd.isna(d):raise ValueError('日期或金额格式错误')
        out.append({'date':d.date().isoformat(),'item':str(r.get('item') or '未知').strip(),'category':str(r.get('category') or '其他').strip(),'type':'Income' if str(r.get('type'))=='Income' else 'Expense','amount':float(a),'note':str(r.get('note') or '').strip()})
    return out
def save(rows):
    try:r=norm(rows);sb.table('transactions').insert(r).execute();clear();st.toast(f'已保存 {len(r)} 笔');return True
    except Exception as e:st.error(f'保存失败：{e}');return False
def update(i,r):
    try:sb.table('transactions').update(norm([r])[0]).eq('id',i).execute();clear();return True
    except Exception as e:st.error(f'修改失败：{e}');return False
def remove(r):
    try:sb.table('transactions').delete().eq('id',int(r.id)).execute();st.session_state.deleted=norm([r.to_dict()])[0];clear();return True
    except Exception as e:st.error(f'删除失败：{e}');return False
def money(x):return f'RM {float(x):,.2f}'
def month(d,y,m):return d[(d.date.dt.year==y)&(d.date.dt.month==m)].copy()
def totals(d):
    i=d.loc[d.type=='Income','amount'].sum();e=d.loc[d.type=='Expense','amount'].sum();return float(i),float(e),float(i-e)
def clean_json(t):
    t=(t or '').strip().removeprefix('```json').removeprefix('```').removesuffix('```');return json.loads(t.strip())
def receipt(img):
    try:
        p=f'''读取收据并逐项返回纯JSON数组。格式：{{"date":"YYYY-MM-DD","item":"简洁中文名称","category":"类别","amount":10.5,"type":"Expense","note":""}}。category只能从{cats()}选择；无法判断日期用{date.today()}。'''
        x=clean_json(genai.GenerativeModel('gemini-3.5-flash').generate_content([p,img]).text);return x if isinstance(x,list) else [x],None
    except Exception as e:return None,str(e)
@st.cache_data(ttl=86400,show_spinner=False)
def macro(items):
    try:
        p=f'把项目归类为餐饮美食、交通出行、居家生活、购物消费、休闲娱乐、医疗健康、教育学习、投资理财、旅游度假或其他。只返回JSON对象。输入：{items}'
        x=clean_json(genai.GenerativeModel('gemini-2.5-flash').generate_content(p).text);return x if isinstance(x,dict) else {}
    except:return {}

df=load();categories=cats();counts=df.category.value_counts().to_dict() if not df.empty else {};categories=sorted(categories,key=lambda x:(-counts.get(x,0),x))
st.markdown('<div class="hero"><h1>💳 WY Wallet V2</h1><span class="muted">独立升级版 · 继续使用现有 Supabase 数据</span></div>',unsafe_allow_html=True)
if st.session_state.get('db_error'):st.error('数据库读取失败：'+st.session_state.db_error)
nav=st.radio('页面',['🏠 首页','🧾 记账与记录','📊 深度报表','🤖 AI 洞察','⚙️ 设置与备份'],horizontal=True,label_visibility='collapsed')

if nav=='🏠 首页':
    now=datetime.now();cur=month(df,now.year,now.month);py,pm=(now.year-1,12) if now.month==1 else (now.year,now.month-1);prev=month(df,py,pm);inc,exp,bal=totals(cur);_,pexp,_=totals(prev);delta='—' if pexp==0 else f'{(exp-pexp)/pexp:+.1%}'
    st.subheader(f'{now.year} 年 {now.month} 月概览');a,b,c,d=st.columns(4);a.metric('本月收入',money(inc));b.metric('本月支出',money(exp),delta,delta_color='inverse');c.metric('本月结余',money(bal));d.metric('平均每日支出',money(exp/max(now.day,1)))
    l,r=st.columns([1.5,1]);ed=cur[cur.type=='Expense']
    with l:
        st.markdown('#### 本月趋势')
        if ed.empty:st.info('本月暂无支出')
        else:
            t=ed.groupby(ed.date.dt.day).amount.sum().reset_index();t.columns=['day','amount'];fig=px.area(t,x='day',y='amount',markers=True);fig.update_layout(height=310,margin=dict(l=0,r=0,t=10,b=0));st.plotly_chart(fig,use_container_width=True,config={'displayModeBar':False})
    with r:
        st.markdown('#### 类别排行')
        for k,v in ed.groupby('category').amount.sum().sort_values(ascending=False).head(7).items():st.write(f'**{k}** · {money(v)}');st.progress(min(float(v/exp),1.0) if exp else 0)
    st.markdown('#### 最近记录')
    for _,x in df.head(8).iterrows():st.markdown(f'<div class="card"><b>{x.item}</b> · {x.category}<span style="float:right;font-weight:700">{"+" if x.type=="Income" else "−"}{money(x.amount)}</span><br><span class="muted">{x.date.date()} {("· "+x.note) if x.note else ""}</span></div>',unsafe_allow_html=True)

elif nav=='🧾 记账与记录':
    add,hist=st.tabs(['➕ 新增账目','📚 历史记录'])
    with add:
        rc,mc=st.columns(2)
        with rc:
            st.markdown('#### AI 收据识别');u=st.file_uploader('上传收据',type=['jpg','jpeg','png'])
            if u:
                st.image(u,use_container_width=True)
                if st.button('开始识别',type='primary',use_container_width=True):
                    with st.spinner('识别中...'):x,e=receipt(Image.open(u))
                    if e:st.error(e)
                    else:st.session_state.pending=x;st.rerun()
            if 'pending' in st.session_state:
                p=pd.DataFrame(st.session_state.pending)
                for c,v in {'date':date.today(),'item':'','category':'其他','type':'Expense','amount':0,'note':''}.items():
                    if c not in p:p[c]=v
                p.date=pd.to_datetime(p.date,errors='coerce').fillna(pd.Timestamp(date.today()))
                e=st.data_editor(p[['date','item','category','type','amount','note']],num_rows='dynamic',use_container_width=True,column_config={'date':st.column_config.DateColumn('日期'),'category':st.column_config.SelectboxColumn('类别',options=categories),'type':st.column_config.SelectboxColumn('类型',options=['Expense','Income']),'amount':st.column_config.NumberColumn('金额',min_value=0.,format='%.2f')})
                x,y=st.columns(2)
                if x.button('确认保存',type='primary',use_container_width=True) and save(e):del st.session_state.pending;st.rerun()
                if y.button('放弃',use_container_width=True):del st.session_state.pending;st.rerun()
        with mc:
            st.markdown('#### 手动记账')
            with st.form('manual',clear_on_submit=True):
                dd=st.date_input('日期',date.today());item=st.text_input('项目名称');x,y=st.columns(2);cat=x.selectbox('类别',categories);typ=y.selectbox('类型',['Expense','Income'],format_func=lambda z:'支出' if z=='Expense' else '收入');amt=st.number_input('金额 (RM)',min_value=0.,value=None,step=.01);note=st.text_area('备注')
                if st.form_submit_button('立即存入',type='primary',use_container_width=True):
                    if not item.strip() or not amt:st.warning('请填写项目和金额')
                    elif save([{'date':dd,'item':item,'category':cat,'type':typ,'amount':amt,'note':note}]):st.rerun()
    with hist:
        if st.session_state.get('deleted'):
            if st.button('↩️ 撤销上次删除',type='primary') and save([st.session_state.deleted]):del st.session_state.deleted;st.rerun()
        q,y,m,t=st.columns([1.5,1,1,1]);key=q.text_input('搜索');years=sorted(df.date.dt.year.unique(),reverse=True) if not df.empty else [];sy=y.selectbox('年份',['全部']+years);sm=m.selectbox('月份',['全部']+list(range(1,13)));stp=t.selectbox('类型',['全部','Expense','Income']);f=df.copy()
        if key:f=f[f.item.astype(str).str.contains(key,case=False,na=False)|f.category.astype(str).str.contains(key,case=False,na=False)|f.note.astype(str).str.contains(key,case=False,na=False)]
        if sy!='全部':f=f[f.date.dt.year==int(sy)]
        if sm!='全部':f=f[f.date.dt.month==int(sm)]
        if stp!='全部':f=f[f.type==stp]
        pages=max(1,(len(f)+PAGE-1)//PAGE);pg=st.number_input('页码',1,pages,1);st.caption(f'找到 {len(f)} 笔')
        for _,x in f.iloc[(pg-1)*PAGE:pg*PAGE].iterrows():
            with st.container(border=True):
                info,act=st.columns([5,1.4]);info.markdown(f'**{x.item}** · {x.category}  \n{x.date.date()} · **{"+" if x.type=="Income" else "−"}{money(x.amount)}**');info.caption(x.note) if x.note else None;e,d=act.columns(2)
                with e.popover('✏️',use_container_width=True):
                    with st.form(f'e{x.id}'):
                        ed=st.date_input('日期',x.date.date());ei=st.text_input('项目',x.item);ec=st.selectbox('类别',categories,index=categories.index(x.category) if x.category in categories else 0);et=st.selectbox('类型',['Expense','Income'],index=0 if x.type=='Expense' else 1);ea=st.number_input('金额',min_value=0.,value=float(x.amount));en=st.text_area('备注',x.note)
                        if st.form_submit_button('保存',type='primary') and update(x.id,{'date':ed,'item':ei,'category':ec,'type':et,'amount':ea,'note':en}):st.rerun()
                with d.popover('🗑️',use_container_width=True):
                    st.warning(f'确定删除“{x.item}”？')
                    if st.button('确认删除',key=f'd{x.id}',type='primary') and remove(x):st.rerun()

elif nav=='📊 深度报表':
    if df.empty:st.info('暂无数据')
    else:
        years=sorted(df.date.dt.year.unique(),reverse=True);a,b=st.columns(2);yy=a.selectbox('年份',years);mm=b.selectbox('月份',range(1,13),index=datetime.now().month-1);s=month(df,int(yy),int(mm));inc,exp,bal=totals(s);a,b,c,d=st.columns(4);a.metric('收入',money(inc));b.metric('支出',money(exp));c.metric('结余',money(bal));d.metric('笔数',len(s));ed=s[s.type=='Expense']
        if ed.empty:st.warning('该月无支出')
        else:
            sums=ed.groupby(ed.date.dt.day).amount.sum().to_dict();cells=''.join(f'<div class="day"><span class="muted">{n or ""}</span><div class="amt">{money(sums.get(n,0)).replace("RM ","") if n and sums.get(n,0) else ""}</div></div>' for w in calendar.monthcalendar(int(yy),int(mm)) for n in w);heads=''.join(f'<b style="text-align:center">{x}</b>' for x in '一二三四五六日');st.markdown(f'<div class="cal">{heads}{cells}</div>',unsafe_allow_html=True)
            daily=ed.groupby([ed.date.dt.day,'category']).amount.sum().reset_index();daily.columns=['day','category','amount'];fig=px.bar(daily,x='day',y='amount',color='category');fig.update_xaxes(dtick=1);st.plotly_chart(fig,use_container_width=True,config={'displayModeBar':False});cat=ed.groupby('category').amount.sum().reset_index();l,r=st.columns(2);l.plotly_chart(px.pie(cat,values='amount',names='category',hole=.5),use_container_width=True);r.dataframe(cat.sort_values('amount',ascending=False),hide_index=True,use_container_width=True)
        annual=df[df.date.dt.year==int(yy)].copy();annual['month']=annual.date.dt.month;st.plotly_chart(px.line(annual.groupby(['month','type']).amount.sum().reset_index(),x='month',y='amount',color='type',markers=True),use_container_width=True,config={'displayModeBar':False})

elif nav=='🤖 AI 洞察':
    if df.empty:st.info('暂无数据')
    else:
        years=sorted(df.loc[df.type=='Expense','date'].dt.year.unique(),reverse=True);yy=st.selectbox('分析年份',years);yd=df[(df.date.dt.year==int(yy))&(df.type=='Expense')]
        if st.button('AI 智能归类',type='primary'):
            with st.spinner('归类中...'):mp=macro(json.dumps(yd.item.unique().tolist(),ensure_ascii=False))
            if mp:r=yd.copy();r['Macro Category']=r.item.map(mp).fillna('其他');st.session_state.macro=r;st.session_state.macro_year=int(yy);st.rerun()
            else:st.error('归类失败')
        if st.session_state.get('macro_year')==int(yy):
            s=st.session_state.macro.groupby('Macro Category').amount.sum().reset_index();l,r=st.columns(2);l.plotly_chart(px.pie(s,values='amount',names='Macro Category',hole=.5),use_container_width=True);r.dataframe(s,hide_index=True,use_container_width=True)
        st.divider();st.markdown('#### 与账单对话');st.caption('只发送年度汇总、类别统计和最高金额记录，不发送完整账本。')
        if 'chat' not in st.session_state:st.session_state.chat=[]
        for z in st.session_state.chat:
            with st.chat_message(z['role']):st.markdown(z['content'])
        if p:=st.chat_input('例如：哪个月花最多？'):
            st.session_state.chat.append({'role':'user','content':p});summary={'total':round(float(yd.amount.sum()),2),'monthly':yd.groupby(yd.date.dt.month).amount.sum().round(2).to_dict(),'categories':yd.groupby('category').amount.sum().sort_values(ascending=False).round(2).to_dict(),'top':yd.nlargest(15,'amount')[['date','item','category','amount']].astype({'date':str}).to_dict('records')};history='\n'.join(f"{z['role']}:{z['content']}" for z in st.session_state.chat[-6:])
            try:
                with st.chat_message('assistant'):
                    with st.spinner('分析中...'):reply=genai.GenerativeModel('gemini-2.5-flash').generate_content(f'只根据资料回答，中文简洁，金额RM两位小数。资料:{json.dumps(summary,ensure_ascii=False,default=str)} 对话:{history} 问题:{p}').text;st.markdown(reply)
                st.session_state.chat.append({'role':'assistant','content':reply})
            except Exception as e:st.error(e)

else:
    ct,bt=st.tabs(['🏷️ 类别管理','📦 备份与导入'])
    with ct:
        l,r=st.columns(2)
        with l:
            n=st.text_input('新类别')
            if st.button('添加类别',type='primary'):
                if n.strip() and n.strip() not in categories:sb.table('categories').insert({'name':n.strip()}).execute();clear();st.rerun()
        with r:
            dc=st.selectbox('删除类别',categories);used=int((df.category==dc).sum()) if not df.empty else 0;st.caption(f'{used} 笔旧记录使用此类别；删除不会删除旧账目。');confirm=st.text_input('输入类别名称确认')
            if st.button('确认删除类别'):
                if confirm!=dc:st.warning('确认文字不一致')
                else:sb.table('categories').delete().eq('name',dc).execute();clear();st.rerun()
    with bt:
        if not df.empty:
            out=df.copy();out.date=out.date.dt.date;buf=io.BytesIO()
            with pd.ExcelWriter(buf,engine='xlsxwriter') as w:out.to_excel(w,index=False,sheet_name='Transactions')
            st.download_button('下载 Excel',buf.getvalue(),f'WY_Wallet_V2_{date.today()}.xlsx',type='primary');st.download_button('下载 CSV',out.to_csv(index=False).encode('utf-8-sig'),f'WY_Wallet_V2_{date.today()}.csv')
        st.warning('导入只会新增，不会覆盖或删除；请先检查重复记录。');u=st.file_uploader('上传 CSV 或 Excel',type=['csv','xlsx'],key='import')
        if u:
            try:
                x=pd.read_csv(u) if u.name.endswith('.csv') else pd.read_excel(u);need={'date','item','category','type','amount'};missing=need-set(x.columns)
                if missing:st.error('缺少栏位：'+','.join(missing))
                else:
                    if 'note' not in x:x['note']=''
                    x=x[['date','item','category','type','amount','note']];st.dataframe(x.head(50),hide_index=True,use_container_width=True);ok=st.checkbox(f'确认新增 {len(x)} 笔')
                    if st.button('开始导入',type='primary',disabled=not ok) and save(x):st.rerun()
            except Exception as e:st.error(f'读取失败：{e}')