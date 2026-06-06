'use client'

import { useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import CustomerList, { type Customer } from '@/components/CustomerList'
import ChatWindow from '@/components/ChatWindow'
import ReplyBox from '@/components/ReplyBox'

export default function ChatPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const storeName = searchParams.get('store') ?? null

  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null)
  const [userEmail, setUserEmail] = useState<string>('')
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) {
        router.push('/')
      } else {
        setUserEmail(data.session.user.email ?? '')
        setAuthChecked(true)
      }
    })
  }, [])

  async function handleLogout() {
    await supabase.auth.signOut()
    router.push('/')
  }

  if (!authChecked) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-400 text-sm">
        인증 확인 중...
      </div>
    )
  }

  return (
    <div className="h-screen flex flex-col bg-gray-950 text-gray-100">
      {/* 상단 헤더 */}
      <header className="flex items-center justify-between px-5 py-2 bg-gray-900 border-b border-gray-700 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-yellow-400 font-bold text-sm">이몬스 고객 상담</span>
          {storeName && (
            <span className="text-xs text-gray-400 bg-gray-800 rounded px-2 py-0.5">
              {storeName}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400">{userEmail}</span>
          <button
            onClick={handleLogout}
            className="text-xs text-gray-400 hover:text-red-400 transition"
          >
            로그아웃
          </button>
        </div>
      </header>

      {/* 본문: 좌측 고객 목록 + 우측 채팅창 */}
      <div className="flex flex-1 overflow-hidden">
        <CustomerList
          storeName={storeName}
          selectedId={selectedCustomer?.id ?? null}
          onSelect={setSelectedCustomer}
        />

        {/* 우측 채팅 영역 */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <ChatWindow customer={selectedCustomer} />
          <ReplyBox
            customer={selectedCustomer}
            storeName={storeName}
            sentBy={userEmail}
          />
        </div>
      </div>
    </div>
  )
}
