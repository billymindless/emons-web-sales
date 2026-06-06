import { NextRequest, NextResponse } from 'next/server'
import { createClient } from '@supabase/supabase-js'
import crypto from 'crypto'

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL!
const SUPABASE_SERVICE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!

const SOLAPI_API_KEY = process.env.SOLAPI_API_KEY ?? ''
const SOLAPI_API_SECRET = process.env.SOLAPI_API_SECRET ?? ''
const SOLAPI_SENDER = process.env.SOLAPI_SENDER ?? ''
const SOLAPI_PF_ID = process.env.SOLAPI_PF_ID ?? ''

function buildSolapiAuth(): string {
  const salt = crypto.randomBytes(16).toString('hex')
  const date = new Date().toISOString()
  const msg = date + salt
  const signature = crypto.createHmac('sha256', SOLAPI_API_SECRET).update(msg).digest('hex')
  return `HMAC-SHA256 apiKey=${SOLAPI_API_KEY}, date=${date}, salt=${salt}, signature=${signature}`
}

function normalizePhone(phone: string): string {
  return phone.replace(/\D/g, '')
}

export async function POST(req: NextRequest) {
  let body: { phone: string; message: string; customerId: number; storeName: string; sentBy: string }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: '잘못된 요청 형식' }, { status: 400 })
  }

  const { phone, message, customerId, storeName, sentBy } = body
  if (!phone || !message) {
    return NextResponse.json({ error: 'phone, message 필수' }, { status: 400 })
  }

  const digits = normalizePhone(phone)

  // Solapi 친구톡 발송
  let solapiStatus: 'sent' | 'failed' | 'skipped' = 'skipped'
  let solapiMsgId: string | null = null
  let solapiError: string | null = null

  if (SOLAPI_API_KEY && SOLAPI_API_SECRET && SOLAPI_PF_ID) {
    try {
      const payload = {
        messages: [{
          to: digits,
          from: SOLAPI_SENDER,
          text: message,
          type: 'CTA',
          kakaoOptions: { pfId: SOLAPI_PF_ID, disableSms: false },
        }],
      }
      const resp = await fetch('https://api.solapi.com/messages/v4/send-many', {
        method: 'POST',
        headers: {
          Authorization: buildSolapiAuth(),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      })
      const raw = await resp.json()
      if (resp.ok) {
        solapiStatus = 'sent'
        solapiMsgId = raw?.messageList?.[0]?.messageId ?? raw?.groupInfo?.groupId ?? null
      } else {
        solapiStatus = 'failed'
        solapiError = JSON.stringify(raw).slice(0, 300)
      }
    } catch (e: any) {
      solapiStatus = 'failed'
      solapiError = String(e)
    }
  }

  // app_customer_messages에 outbound 메시지 저장
  const sb = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY)
  const { error: dbErr } = await sb.from('app_customer_messages').insert({
    customer_id: customerId,
    store_name: storeName,
    message_body: message,
    direction: 'outbound',
    channel: 'friendtalk',
    message_type: 'cs_reply',
    status: solapiStatus,
    solapi_msg_id: solapiMsgId,
    error_detail: solapiError,
    sent_by: sentBy,
  })

  if (dbErr) {
    console.error('reply DB insert 실패:', dbErr)
    return NextResponse.json({ error: 'DB 저장 실패' }, { status: 500 })
  }

  if (solapiStatus === 'failed') {
    return NextResponse.json({ error: `Solapi 발송 실패: ${solapiError}` }, { status: 500 })
  }

  return NextResponse.json({ ok: true, status: solapiStatus, msgId: solapiMsgId })
}
