import { createClient } from 'npm:@supabase/supabase-js@2'

const MODEL = 'gemini-3.7-flash'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  })
}

Deno.serve(async (req) => {
  if (req.method !== 'POST') return jsonResponse({ error: 'POST required' }, 405)

  const authHeader = req.headers.get('Authorization') ?? ''
  const supabaseUrl = Deno.env.get('SUPABASE_URL')!
  const anonKey = Deno.env.get('SUPABASE_ANON_KEY')!
  const geminiKey = Deno.env.get('GEMINI_API_KEY')
  if (!geminiKey) return jsonResponse({ error: 'GEMINI_API_KEY is not configured' }, 500)

  const client = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: authHeader } },
  })
  const { data: { user }, error: authError } = await client.auth.getUser()
  if (authError || !user) return jsonResponse({ error: 'Unauthorized' }, 401)

  const body = await req.json().catch(() => null)
  const imageBase64 = body?.imageBase64
  const mimeType = body?.mimeType || 'image/jpeg'
  const categories = Array.isArray(body?.categories) && body.categories.length
    ? body.categories.map((x: unknown) => String(x))
    : ['饮食','交通','购物','居住','娱乐','医疗','教育','投资','旅游','其他']

  if (!imageBase64 || typeof imageBase64 !== 'string') {
    return jsonResponse({ error: 'imageBase64 is required' }, 400)
  }
  if (imageBase64.length > 18_000_000) {
    return jsonResponse({ error: 'Image is too large' }, 413)
  }

  const prompt = `你是私人记账 App 的收据识别助手。读取真实收据并逐项拆分。\n\n` +
    `只返回 JSON 数组，不要 Markdown。每项必须包含：date(YYYY-MM-DD), item, category, type, amount, note。\n` +
    `category 只能从这些类别中选择：${categories.join('、')}。无法判断用“其他”。\n` +
    `普通购买 type=Expense；只有明确退款才用 Income。amount 必须是正数。\n` +
    `不要把小计、总计、税额汇总、找零、余额、卡号、付款方式单独建立为交易。\n` +
    `若只有总额没有可靠明细，只建立一笔以商家为 item 的交易。不要虚构不存在的项目或金额。`

  const geminiResp = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${encodeURIComponent(geminiKey)}`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        contents: [{
          role: 'user',
          parts: [
            { text: prompt },
            { inlineData: { mimeType, data: imageBase64 } },
          ],
        }],
        generationConfig: {
          responseMimeType: 'application/json',
        },
      }),
    },
  )

  const geminiJson = await geminiResp.json()
  if (!geminiResp.ok) {
    return jsonResponse({ error: 'Gemini request failed', details: geminiJson }, 502)
  }

  const text = geminiJson?.candidates?.[0]?.content?.parts?.[0]?.text ?? '[]'
  let transactions: unknown = []
  try {
    transactions = JSON.parse(text)
  } catch {
    const start = text.indexOf('[')
    const end = text.lastIndexOf(']')
    if (start >= 0 && end > start) transactions = JSON.parse(text.slice(start, end + 1))
  }

  if (!Array.isArray(transactions)) transactions = []
  return jsonResponse({ transactions, model: MODEL })
})
