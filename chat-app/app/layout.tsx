import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: '이몬스 고객 상담',
  description: '카카오채널 실시간 고객 상담 대시보드',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="bg-gray-950 text-gray-100 h-full">{children}</body>
    </html>
  )
}
