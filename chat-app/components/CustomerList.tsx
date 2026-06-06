'use client'

import { useEffect, useState } from 'react'
import { supabase } from '@/lib/supabase'

export interface Customer {
  id: number
  name: string
  phone1: string | null
  kakao_friend_added: boolean
  kakao_user_key: string | null
  last_message?: string
  last_message_at?: string
}

interface Props {
  storeName: string | null
  selectedId: number | null
  onSelect: (customer: Customer) => void
}

export default function CustomerList({ storeName, selectedId, onSelect }: Props) {
  const [customers, setCustomers] = useState<Customer[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)

  async function loadCustomers() {
    setLoading(true)
    let q = supabase
      .from('kakao_mapping')
      .select(`
        customer_id,
        app_customers!inner(id, name, phone1, kakao_friend_added, kakao_user_key)
      `)
      .limit(200)

    if (storeName) {
      q = q.eq('store_name', storeName)
    }

    const { data, error } = await q
    if (error) {
      console.error('CustomerList 조회 실패:', error)
      setLoading(false)
      return
    }

    const list: Customer[] = (data || []).map((row: any) => ({
      id: row.app_customers.id,
      name: row.app_customers.name || '이름 없음',
      phone1: row.app_customers.phone1,
      kakao_friend_added: row.app_customers.kakao_friend_added ?? false,
      kakao_user_key: row.app_customers.kakao_user_key,
    }))

    // 최신 메시지 일시로 정렬 (없으면 이름순)
    list.sort((a, b) => {
      if (a.last_message_at && b.last_message_at) {
        return b.last_message_at.localeCompare(a.last_message_at)
      }
      return a.name.localeCompare(b.name)
    })

    setCustomers(list)
    setLoading(false)
  }

  useEffect(() => {
    loadCustomers()

    // kakao_mapping 변경 시 목록 새로고침
    const channel = supabase
      .channel('customer-list-changes')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'kakao_mapping' }, () => {
        loadCustomers()
      })
      .subscribe()

    return () => { supabase.removeChannel(channel) }
  }, [storeName])

  const filtered = customers.filter(c =>
    c.name.includes(search) || (c.phone1 || '').includes(search)
  )

  return (
    <aside className="w-72 min-w-[200px] bg-gray-900 border-r border-gray-700 flex flex-col h-full">
      <div className="p-3 border-b border-gray-700">
        <h2 className="text-sm font-bold text-yellow-400 mb-2">고객 목록</h2>
        <input
          type="text"
          placeholder="이름 또는 전화번호 검색"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:border-yellow-400"
        />
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading && (
          <p className="text-xs text-gray-500 p-4 text-center">불러오는 중...</p>
        )}
        {!loading && filtered.length === 0 && (
          <p className="text-xs text-gray-500 p-4 text-center">
            채널에 연결된 고객이 없습니다.
          </p>
        )}
        {filtered.map(c => (
          <button
            key={c.id}
            onClick={() => onSelect(c)}
            className={`w-full text-left px-4 py-3 border-b border-gray-800 hover:bg-gray-800 transition flex flex-col gap-1 ${
              selectedId === c.id ? 'bg-gray-800 border-l-2 border-l-yellow-400' : ''
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium truncate">{c.name}</span>
              {c.kakao_friend_added ? (
                <span className="text-xs bg-green-700 text-green-200 rounded px-1.5 py-0.5 ml-1 shrink-0">
                  연결됨
                </span>
              ) : (
                <span className="text-xs bg-yellow-800 text-yellow-200 rounded px-1.5 py-0.5 ml-1 shrink-0">
                  대기중
                </span>
              )}
            </div>
            <span className="text-xs text-gray-400">{c.phone1 || '번호 없음'}</span>
          </button>
        ))}
      </div>
    </aside>
  )
}
