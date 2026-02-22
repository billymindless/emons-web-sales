# assets 폴더 — 앱 아이콘 안내

## 웹 탭·홈화면 아이콘 (Favicon / Apple Touch Icon)

- 이 폴더에 다음 파일을 넣어 주세요.
  - 파일명: apple-touch-icon.png
  - 권장 크기: 180x180 픽셀 또는 192x192 픽셀 (정사각형 PNG)
  - 내용: 에몬스가구 'e' 로고 또는 가구를 상징하는 아이콘

- 넣는 위치:
  - 프로젝트 루트 기준: assets/apple-touch-icon.png
  - 즉, app.py가 있는 디렉터리와 같은 레벨에 assets 폴더를 두고,
    그 안에 apple-touch-icon.png 파일을 두면 됩니다.

  예시 (프로젝트 구조):
    emons-web-sales/
      app.py
      assets/
        apple-touch-icon.png   ← 여기에 이미지 파일 배치
        README.txt             (이 안내 파일)

- 동작:
  - 웹 브라우저 탭 아이콘: set_page_config의 page_icon으로 사용됩니다.
  - iOS 등에서 "홈 화면에 추가" 시 아이콘: <link rel="apple-touch-icon"> 로 주입됩니다.
  - 파일이 없으면 기본으로 🪑 이모지가 탭 아이콘에 사용됩니다.
