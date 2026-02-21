# 프랜차이즈 가구 매장 · 세일즈 및 경영 대시보드

다중 매장(3개 이상) 환경용 **Database-per-Tenant** 아키텍처 기반 Streamlit 웹 앱입니다.

## 실행 방법

```bash
pip install -r requirements.txt
streamlit run app.py
```

브라우저에서 `http://localhost:8501` 로 접속합니다.

## 최초 로그인

- **사용자명**: `superadmin`
- **비밀번호**: `1234`

최초 실행 시 `databases/master_system.db`가 없으면 자동 생성되며, 위 계정이 등록됩니다.

## 데이터베이스 구조

- **Master DB** (`databases/master_system.db`): 매장(Stores), 사용자(Users) — 로그인·권한·매장 목록
- **Tenant DB** (`databases/store_1.db`, `store_2.db`, …): 매장별 독립 DB — 직원, 고객, 주문, 결제, To-Do

로그인한 사용자의 매장에 해당하는 Tenant DB만 `st.session_state['current_db']`로 지정되어, 모든 탭에서 해당 파일만 사용합니다.

## 주소 검색 API (선택)

한국 주소 자동 검색을 쓰려면 아래 중 하나를 설정하세요.

- **공공데이터 도로명주소 API**: 환경변수 `ADDRESS_API_KEY`에 발급받은 키 설정
- **카카오 주소 API**: 환경변수 `KAKAO_REST_KEY`에 REST API 키 설정 (또는 앱 내 기본 키 사용)

키가 없어도 주소는 수동 입력으로 등록 가능합니다.

### 카카오 로컬 API 사용 시

**"disabled OPEN_MAP_AND_LOCAL service"** 오류가 나면, 카카오 앱에서 지도/로컬 서비스를 켜야 합니다.

1. [Kakao Developers](https://developers.kakao.com) 로그인
2. **내 애플리케이션** → 사용 중인 앱(예: 에몬스판매관리) 선택
3. **앱 설정** → **제품 설정** (또는 **앱 키** 영역)
4. **카카오맵** / **로컬** 항목에서 **사용 설정**을 **ON**으로 변경

설정이 반영되기까지 잠시 걸릴 수 있습니다.

## 역할별 메뉴

| 역할 | 메뉴 |
|------|------|
| superadmin | 매장 생성, 매장별 계정 발급 |
| store_admin | 직원 마스터(등록/수정/비활성화), 매출 등록, 고객·잔금 관리, 대시보드·To-Do |
| user | 매출 등록, 고객·잔금 관리, 대시보드·To-Do |

## 1/n 실적 분배

한 주문에 여러 직원을 선택하면, 해당 주문 금액이 인원수(n)로 균등 분배되어 개인별 판매 실적에 합산됩니다. 대시보드 탭에서 개인별 분배 매출 차트로 확인할 수 있습니다.
