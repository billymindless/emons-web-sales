'use client'

import { useEffect, useRef, useState } from 'react'
import { supabase } from '@/lib/supabase'
import type { Customer } from './CustomerList'

interface Message {
  id: number
  direction: 'inbound' | 'outbound'
  message_body: string
  sent_by: string | null
  channel: string | null
  created_at: string
}

interface Props {
  customer: Customer | null
}

export default function ChatWindow({ customer }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  // 대화 이력 초기 로드
  async function loadHistory(customerId: number) {
    setLoading(true)
    const { data, error } = await supabase
      .from('app_customer_messages')
      .select('id, direction, message_body, sent_by, channel, created_at')
      .eq('customer_id', customerId)
      .order('created_at', { ascending: true })
      .limit(100)

    if (!error && data) {
      setMessages(data as Message[])
    }
    setLoading(false)
  }

  useEffect(() => {
    if (!customer) {
      setMessages([])
      return
    }

    loadHistory(customer.id)

    // Supabase Realtime 구독 — INSERT 즉시 반영
    const channel = supabase
      .channel(`chat-${customer.id}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'app_customer_messages',
          filter: `customer_id=eq.${customer.id}`,
        },
        (payload) => {
          setMessages(prev => [...prev, payload.new as Message])
        }
      )
      .subscribe()

    return () => {
      supabase.removeChannel(channel)
    }
  }, [customer?.id])

  // 새 메시지 도착 시 하단으로 자동 스크롤
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (!customer) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-500 text-sm">
        왼쪽에서 고객을 선택하세요.
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* 헤더 */}
      <div className="px-5 py-3 border-b border-gray-700 flex items-center gap-3">
        <div>
          <p className="font-semibold text-sm">{customer.name}</p>
          <p className="text-xs text-gray-400">{customer.phone1 || '번호 없음'}</p>
        </div>
        {customer.kakao_friend_added ? (
          <span className="ml-auto text-xs bg-green-700 text-green-200 rounded px-2 py-0.5">
            채널 친구 ✅
          </span>
        ) : (
          <span className="ml-auto text-xs bg-yellow-800 text-yellow-200 rounded px-2 py-0.5">
            인증 대기 중 ⚠️
          </span>
        )}
      </div>

      {/* 메시지 목록 */}
      <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-3">
        {loading && (
          <p className="text-xs text-gray-500 text-center">대화 기록 불러오는 중...</p>
        )}
        {!loading && messages.length === 0 && (
          <p className="text-xs text-gray-500 text-center mt-10">
            아직 대화 내용이 없습니다.
          </p>
        )}

        {messages.map(msg => {
          const isInbound = msg.direction === 'inbound'
          const time = msg.created_at?.slice(0, 16).replace('T', ' ') ?? ''
          return (
            <div
              key={msg.id}
              className={`flex flex-col max-w-[75%] gap-1 ${isInbound ? 'self-start' : 'self-end items-end'}`}
            >
              {isInbound && (
                <span className="text-xs text-yellow-400 font-medium px-1">고객</span>
              )}
              <div
                className={`rounded-2xl px-4 py-2 text-sm whitespace-pre-wrap break-words ${
                  isInbound
                    ? 'bg-gray-700 text-gray-100 rounded-tl-sm'
                    : 'bg-yellow-400 text-gray-900 rounded-tr-sm'
                }`}
              >
                {msg.message_body}
              </div>
              <span className="text-xs text-gray-500 px-1">{time}</span>
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
