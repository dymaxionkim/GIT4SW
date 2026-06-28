# GIT4SW - SolidWorks Git 버전 관리 클라이언트

Git 워크플로우와 Git LFS 잠금을 통합하여 3D CAD 모델의 동시 작업 충돌을 방지하는 에이전틱 CAD 버전 관리 데스크톱 앱입니다.

---

## 1. 대시보드 모드

저장소 설정, 동기화 및 실시간 상태를 확인하는 중앙 제어판입니다.

- **Local Path** — 활성 로컬 Git 저장소의 전체 경로를 표시합니다.
- **Change Workspace** — 폴더 선택 대화상자를 열어 다른 Git 저장소로 작업공간을 전환합니다.
- **Remote Server** — 연결된 원격 URL을 표시하거나 새 저장소를 복제할 URL을 입력합니다.
- **Clone** — 원격 저장소를 로컬 경로 디렉토리로 복제합니다.
- **Active Branch** — 현재 브랜치를 표시하는 드롭다운. 다른 브랜치를 선택하면 체크아웃합니다.
- **Make my branch** — 사용자 이름으로 개인 개발자 브랜치를 생성 및 전환하여 `main`과 작업을 분리합니다.
- **README.md** — README.md를 메모장으로 엽니다. 저장 시 `git add`, `commit`, `push`가 자동 실행됩니다. 파일이 없으면 템플릿으로 생성합니다.
- **Get Latest Version (Sync)** — 원격 변경사항을 pull합니다. 충돌 시 `.backup/`에 자동 백업 후 해결합니다. 최신 상태면 건너뜁니다.
- **Merge main branch into current branch** — `main`을 현재 브랜치에 병합, 커밋, 푸시 후 원래 브랜치로 복귀합니다. 병합 전 자동 백업하며, 이미 병합된 경우 건너뜁니다.
- **Cleanup LFS Cache** — 사용하지 않는 `.git/lfs/objects/`를 스캔 및 삭제하는 위저드를 열어 디스크 공간을 확보합니다.
- **Auto Sync** — 체크박스. ON이면 시작 또는 작업공간 변경 시 "동기화" 후 "main 병합"을 자동 실행합니다.
- **Live Monitor** — SolidWorks 상태(Active/Inactive), 총 파일 수, 열린 파일, 잠긴 파일, 저장소 크기를 실시간 표시합니다.

---

## 2. 파일 관리자 모드

파일 수준의 체크아웃, 체크인 및 로컬 수정 관리 기능을 제공합니다.

- **File Table** — 작업공간 파일을 트리뷰로 표시 (파트=초록, 어셈블리=주황, 도면=빨강, 기타=보라). 상태, SolidWorks 열림 여부, 잠금 상태, 소유자 정보를 제공합니다. `Ctrl+A`로 전체 선택 가능. 이름/확장자/상태/SolidWorks/잠금별 정렬 지원. `.gitignore` 준수.
- **CAD File Preview Canvas** — 선택한 CAD 파일의 180x135 썸네일을 사이드바에 표시합니다 (OLE + COM 추출). 파일 1개 선택 시에만 활성화됩니다.
- **Click-to-Copy** — 미리보기 캔버스를 클릭하면 썸네일 비트맵이 클립보드(CF_DIB)에 복사되어 다른 앱에 붙여넣기 가능합니다.
- **Refresh** — 파일 목록, LFS 잠금, SolidWorks 문서 상태를 즉시 갱신합니다.
- **Find Top** — COM API로 `.sldasm` 의존성 그래프를 스캔합니다 (파일 미열람, 고속). 최상위 어셈블리를 빨간색 **TOP** 레이블로 표시합니다.
- **Open** — 작업공간 폴더를 파일 탐색기로 엽니다.
- **Lock** — 선택한 파일에 LFS 잠금을 획득합니다. SolidWorks에서 파일 열 시 자동 획득됩니다.
- **Unlock File** — 선택한 파일의 LFS 잠금을 해제합니다.
- **Force Unlock** — 다른 개발자의 LFS 잠금을 강제 해제합니다 (주의).
- **Discard** — 선택한 파일을 마지막 커밋 상태로 되돌립니다.
- **eDrawings** — 선택한 CAD 파일을 eDrawings으로 엽니다 (경량 뷰어).
- **SolidWorks** — 선택한 파일을 SolidWorks로 엽니다.
- **Diff** — 파트/도면 파일의 두 버전을 비교합니다. 커밋 내역 팝업에서 선택한 커밋을 `_THEIRS`로, 현재 버전을 `_OURS`로 `.backup/`에 추출하여 SolidWorks에서 엽니다.
- **EXPORT** — 도면/파트/어셈블리를 PDF/DXF/STEP/STEP_ASM으로 일괄 변환합니다. 비동기 백그라운드 실행. 감시 타이머(3분), UTF-8 안전 처리, 교착 상태 방지 기능 포함.
- **Version Description** — 커밋 메시지를 입력하는 텍스트 상자.
- **Upload Selected File Version** — 선택한 수정 파일을 커밋, 푸시, 업로드하고 잠금을 해제합니다. SolidWorks 열림 또는 타인 잠금 시 차단됩니다.
- **Upload Every Files Version** — 작업공간의 모든 수정 파일을 일괄 커밋, 푸시, 업로드 및 잠금 해제합니다.

---

## 3. 히스토리 로그 모드

이전 리비전을 탐색하고 변경 내역을 감사합니다.

- **Revision List** — 과거 커밋 목록 (해시, 작성자, 날짜, 메시지). 현재 체크아웃 행은 초록색 굵게 강조됩니다.
- **Graph** — 터미널을 열어 `git log --graph --oneline --all --decorate`로 ASCII 커밋 트리를 표시합니다.

---

## 4. 관리자 모드

프로젝트 관리자를 위한 통합 관리 및 저장소 초기화 모드입니다.

- **Merge all branches into main** — 모든 원격 브랜치를 가져와 `main`에 병합, 푸시한 후 원래 개발자 브랜치로 복원합니다.
- **Repository Name + Create New CAD Repository** — 저장소 이름을 입력하고 "Make"를 클릭하면 GitHub 비공개 저장소를 자동 생성, LFS 설정, 잠금 확장자 할당, 초기 커밋 푸시까지 한 번에 처리합니다.

---

## 5. 설정 모드

Git, SolidWorks 및 통합 관련 시스템 경로를 구성합니다.

- **Path Configurations** — Git, Git LFS, SolidWorks, eDrawings, Git Token, 서버 타입/URL, 기본 디렉토리, GitHub 조직명 경로 설정. 각 필드에 "찾기" 버튼이 있어 C 드라이브를 스캔합니다. `auto_sync`는 대시보드 체크박스로 관리됩니다.
- **Save Configuration** — 설정을 `config.json`에 저장하고 연결을 재초기화합니다.
- **Edit** — `config.json`을 메모장으로 직접 열어 수동 편집합니다.

---

## 6. 시스템 로그 및 프로세스 종료

작업 로그 표시 및 프로세스 제어 하단 패널입니다.

- **Status Indicator** — 대기 시 초록 "● 대기 중", 작업 중 빨간 "● 작업 중"을 표시합니다.
- **Sequential Button Queuing** — 작업 중 버튼 클릭 시 대기열에 추가되어 순차 실행됩니다.
- **Terminate** — 실행 중인 Git 작업을 강제 종료하고 상태를 복원합니다. 대기 중엔 비활성(분홍), 작업 중엔 활성(빨강).
- **Clear** — 로그 창을 비웁니다.
