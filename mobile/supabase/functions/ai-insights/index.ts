import { createClient } from 'npm:@supabase/supabase-js@2'

const MODEL = 'gemini-3.6-flash'

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

  const summary = await req.json().catch(() => ({}))
  const prompt = `你是简洁、严谨的个人财务助理。根据下面的汇总数据给出中文分析。\n` +
    `不要假装知道未提供的信息，不要给投资产品建议。重点写：1) 本月钱主要花在哪里；2) 是否有明显异常或集中支出；3) 下月可执行的 2-3 个改善建议。\n` +
    `总长度控制在 250-450 中文字。\n\n汇总数据：${JSON.stringify(summary)}`

  const geminiResp = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${encodeURIComponent(geminiKey)}`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        contents: [{ role: 'user', parts: [{ text: prompt }] }],
        generationConfig: { temperature: 0.3 },
      }),
    },
  )
  const geminiJson = await geminiResp.json()
  if (!geminiResp.ok) return jsonResponse({ error: 'Gemini request failed', details: geminiJson }, 502)
  const insight = geminiJson?.candidates?.[0]?.content?.parts?.[0]?.text ?? ''
  return jsonResponse({ insight, model: MODEL })
})
