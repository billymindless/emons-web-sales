---
name: elevator inspection menu
overview: "\"4. 엘리베이터 사이즈 점검\" 메뉴를 신설하여 공공데이터포털 승강기 정보 API로 주소 기반 스펙 조회 + 매트리스 대각선 진입 시뮬레이션을 제공한다."
todos:
  - id: menu-shift
    content: app.py tab_labels 두 곳과 idx 분기 로직을 +1 시프트하고 idx==3에 render_elevator_inspection 호출 추가
    status: completed
  - id: module-skel
    content: elevator_inspection.py 신규 생성 — render_elevator_inspection() + 2개 탭 스켈레톤 구성
    status: completed
  - id: api-client
    content: fetch_elevators_by_address() 구현 — 인증키 로딩, XML 파싱, st.cache_data 적용, 에러 처리
    status: completed
  - id: tab1-ui
    content: 탭1 주소 입력 + 결과 DataFrame 표시 + '시뮬레이션으로 보내기' 버튼 구현
    status: completed
  - id: sim-algorithm
    content: 매트리스 진입 판정 함수 구현 — 눌힌 자세/세운 자세/평면 회전 세 가지 케이스
    status: completed
  - id: tab2-ui
    content: 탭2 입력 UI + 결과 카드 + matplotlib 평면도 시각화
    status: completed
  - id: secrets-deps
    content: requirements.txt에 xmltodict 추가, secrets.toml [elevator_api] 섹션 안내 추가
    status: completed
  - id: setup-doc
    content: ELEVATOR_API_SETUP.md 로 공공데이터포털 인증키 발급 절차 문서화
    status: completed
  - id: verify
    content: 메뉴 진입 테스트 + 알고리즘 3~4건 일치 계산 검증
    status: completed
isProject: false
---

# 엘리베이터 사이즈 점검 메뉴 추가 플랜

## 1. 목표

- 새 메뉴 `4. 엘리베이터 사이즈 점검` 신설 (기존 4번 "새로운 매출 등록"을 5번으로 밀어내고, 이후 번호 일괄 +1)
- 두 개의 하위 탭:
  - 탭 1 `주소로 승강기 스펙 조회` — 공공데이터포털 API 호출 → 호기/적재용량/내부 치수 표시
  - 탭 2 `매트리스 진입 시뮬레이션` — 엘리베이터 내부 치수와 매트리스 사이즈 입력 → 평면 회전/세움 모두 고려한 진입 가능 여부 판정

## 2. 데이터 소스 (공공데이터포털)

- 사용 서비스: `한국승강기안전공단_승강기 검사정보` (data.go.kr 무료 자동승인)
- 핵심 endpoint 예: `http://openapi.elevator.go.kr/openapi/service/ElevatorInfoService/getElevatorList`
- 입력 파라미터: `addr1`(시도), `addr2`(시군구), `buldNm`(건물명) 또는 `roadAddr`(도로명)
- 응답 핵심 필드: `elvtrAsignNo`(승강기번호), `elvtrKindNm`(종류), `liveLoad`(적재하중 kg), `ratedCap`(정원), `bdyWdthSz`(내부 폭 mm), `bdyLnghSz`(내부 깊이 mm), `bdyHgtSz`(내부 높이 mm), `doorWdthSz`(출입구 폭), `doorHgtSz`(출입구 높이)
- 인증키는 사용자가 직접 발급하여 [.streamlit/secrets.toml](.streamlit/secrets.toml) `[elevator_api] service_key = "..."` 에 저장, Render 환경변수는 `ELEVATOR_API_KEY`

## 3. 메뉴 구조 변경

[app.py](app.py) line 26646~26671 `tab_labels` 리스트 두 곳에 `"4. 엘리베이터 사이즈 점검"` 삽입하고 이후 번호 모두 +1:

```python
# store_admin
"3. 리드고객 관리",
"4. 엘리베이터 사이즈 점검",   # NEW
"5. 새로운 매출 등록",
"6. 고객 및 잔금 관리",
"7. 입금 관리",
# ... 이후 +1
```

[app.py](app.py) line 26716~26750 `idx` 분기 로직 동일하게 +1 시프트하고 `idx == 3` 분기에 새 호출 추가:

```python
elif idx == 3:
    from elevator_inspection import render_elevator_inspection
    render_elevator_inspection()
elif idx == 4:
    render_new_sales()
# ... 이후 +1
```

## 4. 신규 파일 [elevator_inspection.py](elevator_inspection.py)

단일 진입점 `render_elevator_inspection()`, 내부에 `st.tabs(["주소로 스펙 조회", "매트리스 진입 시뮬레이션"])` 구성.

### 4.1 API 클라이언트

- 함수 `fetch_elevators_by_address(road_addr: str, building_name: str = "") -> list[dict]`
- `@st.cache_data(ttl=3600)` 적용 — 동일 주소 반복 조회 비용 절감
- 인증키 로드: `os.environ.get("ELEVATOR_API_KEY")` 우선, `st.secrets["elevator_api"]["service_key"]` 폴백
- XML 응답이 기본이므로 `xmltodict` 또는 `xml.etree.ElementTree`로 파싱 ([requirements.txt](requirements.txt)에 `xmltodict` 추가)
- 오류 처리: 키 미설정 / HTTP ≥400 / 결과 0건 각각 다른 안내 메시지

### 4.2 주소 조회 UI (탭 1)

- 도로명 주소 입력 (필수) + 건물명 (선택)
- 검색 버튼 클릭 → API 호출 → 결과를 `pandas.DataFrame`으로 표시
- 컬럼: 호기, 종류, 정원, 적재하중, 내부폭/깊이/높이, 출입구 폭/높이, 검사 유효기간
- 각 행 옆 `시뮬레이션으로 보내기` 버튼 → `st.session_state["elev_inner_w/d/h"]`에 자동 입력 후 탭 2로 이동

### 4.3 매트리스 진입 시뮬레이션 (탭 2)

#### 입력
- 엘리베이터 내부 치수: 폭 W / 깊이 D / 높이 H (mm) — 탭 1에서 자동 채움 가능
- 출입구 치수: 폭 dW / 높이 dH (mm)
- 매트리스 치수: 가로 mW / 세로 mL / 두께 mT (mm) — 보통 매트리스는 두께가 가장 작음

#### 판정 알고리즘 (단순·검증 가능)

매트리스를 직육면체로 보고 두 가지 자세를 모두 검사:

1. 눕혀서 진입 (매트리스 면을 천장과 평행):
   - 출입구 통과: `mT ≤ dH AND min(mW, mL) ≤ dW`
   - 엘리베이터 내부 안착: `mT ≤ H AND mW ≤ W AND mL ≤ D` (또는 W↔D 스왑) — 회전 가능 여부는 평면 대각선 비교
2. 세워서 진입 (매트리스 면을 출입구와 평행):
   - 출입구 통과: `mW ≤ dW AND mL ≤ dH` (또는 회전)
   - 내부 안착: 매트리스를 세웠을 때 바닥 footprint = `mW × mT` 가 `W × D` 안에 들어가는지

#### 평면 회전 (벽에 닿지 않게 돌릴 수 있는지)

- 직사각형 매트리스(`mW × mL`)가 직사각형 엘리베이터 바닥(`W × D`)에서 회전 가능한 최대 길이 공식 사용:
  - `L_max = sqrt(W^2 + D^2 - 2*sqrt(2)*sqrt(W*D*(W^2+D^2-...)) ...)` 는 복잡하므로,
  - 실용적 근사: 대각선 비교 `sqrt(W² + D²) ≥ mL` AND `mW ≤ min(W, D)` 인지 검사 + 안전계수 안내
- 정밀이 필요하면 회전각 0~90° 1° 단위 스캔으로 직사각형 충돌 검증 (`shapely`나 자체 SAT 알고리즘)

#### 출력

- 상태 카드: `진입 가능 (눕혀서)` / `진입 가능 (세워서)` / `회전 진입 가능` / `진입 불가`
- 사유 설명 (어느 치수가 초과되었는지)
- 시각화: `matplotlib`로 평면도 다이어그램 — 엘리베이터 바닥과 매트리스 회전 위치를 함께 표시

## 5. 데이터 흐름

```mermaid
flowchart LR
    user[사용자] --> tab1[탭1: 주소 입력]
    tab1 --> api[공공데이터 API]
    api --> df[스펙 DataFrame]
    df --> send[시뮬레이션으로 보내기]
    send --> tab2[탭2: 시뮬레이션 입력]
    user --> tab2
    tab2 --> calc[판정 알고리즘]
    calc --> result[결과 카드 + 평면도]
```

## 6. 외부 의존성 / 환경 설정

- [requirements.txt](requirements.txt) 추가: `xmltodict`, (이미 있는 `matplotlib`, `pandas` 활용)
- [.streamlit/secrets.toml](.streamlit/secrets.toml) 신규 섹션:
  ```toml
  [elevator_api]
  service_key = "디코딩된_인증키"
  ```
- Render 환경변수: `ELEVATOR_API_KEY`

## 7. 발급 가이드 별도 문서

- [ELEVATOR_API_SETUP.md](ELEVATOR_API_SETUP.md) 신규 작성:
  - data.go.kr 회원가입 → 활용신청 → 인증키 발급 절차
  - secrets/환경변수 설정 방법
  - 자주 발생하는 SERVICE_KEY_NOT_REGISTERED_ERROR 대응

## 8. 검증

- 알고리즘 단위 테스트: 명백한 진입 가능/불가 케이스 3~4건 수동 확인
  - W=1600, D=1500, H=2300, 매트리스 Q(1500×2000×250) → 회전 진입 가능 판정
  - W=1100, D=900, 매트리스 K(1800×2000×250) → 진입 불가 판정
- API 키 미설정 시 친절한 가이드 표시 확인
- 메뉴 번호 재배치 후 모든 분기(role별)에서 정상 진입 확인