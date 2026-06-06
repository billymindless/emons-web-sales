'use client'

import { useState } from 'react'
import type { Customer } from './CustomerList'

interface Props {
  customer: Customer | null
  storeName: string | null
  sentBy: string
}

export default function ReplyBox({ customer, storeName, sentBy }: Props) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState('')

  async function handleSend() {
    if (!text.trim() || !customer) return
    if (!customer.phone1) {
      setError('전화번호가 없어 발송할 수 없습니다.')
      return
    }
    if (!customer.kakao_friend_added) {
      setError('채널 친구가 아닌 고객에게는 친구톡을 발송할 수 없습니다. 채널 초대 먼저 발송하세요.')
      return
    }

    setSending(true)
    setError('')
    try {
      const res = await fetch('/api/reply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: customer.phone1,
          message: text.trim(),
          customerId: customer.id,
          storeName: storeName ?? '',
          sentBy,
        }),
      })
      const json = await res.json()
      if (!res.ok || json.error) {
        setError(json.error || '발송 실패')
      } else {
        setText('')
      }
    } catch (e) {
      setError('네트워크 오류가 발생했습니다.')
    } finally {
      setSending(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      handleSend()
    }
  }

  if (!customer) return null

  return (
    <div className="border-t border-gray-700 p-3 flex flex-col gap-2 bg-gray-900">
      {error && <p className="text-xs text-red-400">{error}</p>}
      <div className="flex gap-2">
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="답장 메시지를 입력하세요. (Ctrl+Enter 발송)"
          rows={3}
          className="flex-1 bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm resize-none focus:outline-none focus:border-yellow-400"
        />
        <button
          onClick={handleSend}
          disabled={sending || !text.trim()}
          className="bg-yellow-400 text-gray-900 font-bold rounded-lg px-4 py-2 text-sm hover:bg-yellow-300 disabled:opacity-40 transition self-end"
        >
          {sending ? '발송 중...' : '발송'}
        </button>
      </div>
      <p className="text-xs text-gray-500">
        Ctrl+Enter로 빠르게 발송 · 카카오 친구톡으로 전송됩니다
      </p>
    </div>
  )
}
