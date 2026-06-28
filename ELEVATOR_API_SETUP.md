# 한국승강기안전공단 승강기 정보 API 키 발급 가이드

엘리베이터 사이즈 점검 메뉴에서 사용하는 **공공데이터포털 승강기 정보 API** 인증키 발급 절차입니다.

## API 사양 (확인됨)

- **엔드포인트**: `https://apis.data.go.kr/B553664/ElevatorInformationService/getElevatorListM`
- **요청 파라미터**: `serviceKey`, `pageNo`, `numOfRows`, `sido` (예: `경남`), `sigungu` (예: `진주`), `buld_nm` (건물명, 부분 일치)
- **응답 필드**: `elevatorNo`, `elvtrAsignNo`(호기), `buldNm`, `address1`, `elvtrKindNm`, `elvtrModel`, `ratedCap`(정원), `liveLoad`, `lastInspctDe`, 등
- **한계**: 응답에 **내부 치수(폭/깊이/높이)는 포함되지 않음**. 정원(인승) 기반으로 KS 표준치수 자동 추정 후 시뮬레이션 탭에서 실측치로 수정 가능.

## 1. 공공데이터포털 회원가입 / 로그인

1. [공공데이터포털 (data.go.kr)](https://www.data.go.kr) 접속
2. 우측 상단 **회원가입** 또는 **로그인**

## 2. API 활용신청

1. 검색창에 **"한국승강기안전공단 승강기 정보"** 입력
2. **"한국승강기안전공단_승강기 정보"** 선택
3. 우측 **활용신청** 버튼 클릭
4. 활용 목적 예시:
   - 시스템 유형: `웹사이트 개발`
   - 활용 목적: `가구 운반 시 엘리베이터 사이즈 검증`
   - 일일 트래픽: `10000`
5. **상세기능** 중 `승강기목록 /getElevatorListM` 필수 체크
6. **라이선스 동의** 후 신청 완료

## 3. 인증키 확인

신청 후 **자동 승인**되며 약 1~2시간 뒤 실제 호출 가능.

1. [마이페이지 → 오픈API → 인증키 발급현황](https://www.data.go.kr/iim/api/selectAPIAcountView.do) 접속
2. 발급된 키 두 종류 확인:
   - **Encoding 키** (URL 인코딩됨, % 포함)
   - **Decoding 키** (원본 hex) ← **이 키를 사용**

## 4. 키 등록

### 로컬 개발 (Streamlit)

`.streamlit/secrets.toml` 에 다음을 추가:

```toml
[elevator_api]
service_key = "여기에_디코딩_키_붙여넣기"
```

### Render 배포 (운영)

Render 대시보드 → 서비스 → **Environment** 탭:

```
Key:   ELEVATOR_API_KEY
Value: 여기에_디코딩_키_붙여넣기
```

저장 후 **Manual Deploy → Deploy latest commit** 으로 재배포.

## 5. 동작 확인

1. 모모 앱 → **4. 엘리베이터 사이즈 점검** 메뉴 진입
2. **주소로 스펙 조회** 탭에서 본인이 알고 있는 아파트 주소 입력
3. 결과가 표시되면 정상 동작

## 6. 자주 발생하는 오류

| 오류 메시지 | 원인 / 해결 |
|------------|------------|
| `SERVICE_KEY_IS_NOT_REGISTERED_ERROR` | 활용신청 직후 1~2시간 대기 / 키 오타 확인 |
| `LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR` | 일일 트래픽 초과 / 다음 날 재시도 또는 한도 상향 신청 |
| `조회된 승강기가 없습니다` | 도로명주소가 정확한지, 시·도·시·군·구를 함께 입력했는지 확인 |
| `API 호출 실패` | 네트워크 문제 / Render 콜드스타트 / 잠시 후 재시도 |

## 7. 참고 링크

- [공공데이터포털 - 행정안전부_승강기 정보](https://www.data.go.kr/data/15077091/openapi.do)
- [국가승강기정보센터](https://www.elevator.go.kr/opn/MainPage.do)
