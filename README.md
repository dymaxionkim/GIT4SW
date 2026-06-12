# GIT4SW: SolidWorks Git Version Control Client

SolidWorks로 설계 작업을 진행할 때 도면(`.slddrw`), 파트(`.sldprt`), 어셈블리(`.sldasm`) 등의 3D CAD 바이너리 파일은 일반 텍스트 코딩 작업과 다르게 Git에서 코드 차원 병합(Merge)이 불가능하여 다자간 협업 시 덮어쓰기나 변경점 소실 등의 심각한 문제가 수시로 발생합니다.

**GIT4SW**는 이러한 비정형 CAD 파일들의 다자간 동시 수정으로 발생할 수 있는 버전 엉킴과 충돌을 원천 예방하기 위해 고안된 **SolidWorks 전용 Git 버전 관리 데스크톱 클라이언트**입니다. 표준 Git 브랜치 워크플로우에 **Git LFS(Large File Storage) Lock 메커니즘**을 결합하여, 특정 사용자가 파일을 수정하는 동안 다른 사용자가 동일한 파일을 덮어쓰지 못하도록 사전에 완벽 차단해 줍니다.

![](GIT4SW.png)

---

## 1. 주요 기능 및 특징

* **실시간 SolidWorks API 연동 모니터링**: 백그라운드 스레드가 활성화된 SolidWorks 창의 문서 개체들을 주기적으로 추적하여, 사용자가 CAD 파일을 여는 즉시 자동으로 원격 LFS Lock을 획득하고, 창을 닫으면 자동으로 Lock을 반납(Release)합니다.
* **작업 안전성 및 업로드 예방 기능**:
  - 브랜치를 전환하거나 싱크를 마칠 때 SolidWorks에 미저장된 변경사항이 있는 경우, 파일 파손 및 락 충돌을 방지하기 위해 사용자에게 먼저 변경사항을 저장하고 닫을 것인지 다이얼로그 팝업을 통해 유도하여 데이터를 안전하게 조율합니다.
  - **업로드 시 안전 검사**: 버전을 업로드할 때 대상 파일 중 SolidWorks에서 열려 있는 파일이 있으면 경고 팝업과 함께 작업을 차단합니다. 또한 본인 외에 다른 계정이 잠근(Locked) 파일이 포함된 경우, Yes/No 경고창을 통해 해당 파일들만 제외하고 진행할지 여부를 선택할 수 있습니다.
* **직관적인 확장자별 색상 구분 및 정렬**:
  - 파일 테이블의 확장자별 색상 구분(파트: 초록 `#059669`, 어셈블리: 주황 `#d97706`, 도면: 빨강 `#dc2626`)으로 시인성을 극대화했습니다.
  - **정렬 옵션 강화**: 파일 목록 정렬 방법을 선택하는 콤보박스에 `by Status`, `by Solidworks`, `by Locked` 등이 추가되었으며, `by Status` 선택 시 **New File -> Modified -> Unmodified** 순서로, `by Solidworks` 선택 시 **Open 상태 파일이 최상단**에 위치합니다. 모든 정렬은 1차 정렬 기준 완료 후 전체 상대 파일 경로(`File Path`) 기준의 2차 알파벳 정렬이 일괄 적용됩니다.
  - **단축키 지원**: File Manager 파일 목록에서 `Ctrl+A`, `Ctrl+a`, `Ctrl+ㅁ` 입력 시 전체 파일이 일괄 선택(Selected)됩니다.
* **유연한 브랜치 관리 및 원격 배포**:
  - **"Make my branch" 기능**: 사용자의 GitHub 계정 명칭을 조회하여 즉시 개인 개발용 원격 브랜치를 자동 생성하고 업스트림을 동기화하여 main 브랜치를 해치지 않고 안전하게 작업하도록 돕습니다.
  - **"Merge all branches into main" 기능**: 관리자(Maintainer) 모드에서 여러 협업 개발 브랜치를 `main` 브랜치로 일괄 비동기 머지 및 충돌 해결 옵션(Ours/Theirs 선택 모달)을 제공합니다.
* **백그라운드 순차 큐잉 및 실시간 프로세스 강제 종료 (Terminate 버튼)**:
  - **순차 버튼 실행 큐**: 백그라운드 프로세스가 실행 중("Working")일 때 다른 액션 버튼을 누르면 작업이 대기 큐에 적재되고, 현재 작업이 완전히 완료된 후에 순차적으로 시작됩니다.
  - **Git 프로세스 강제 종료**: System Log 패널에서 실행 중인 Git 서브 프로세스 트리를 즉시 안전하게 강제 종료할 수 있는 Terminate 버튼을 제공하며, 종료 시 대기 중인 모든 큐도 자동 소거됩니다.
* **대시보드 내 README.md 바로가기 및 자동 싱크**: 대시보드의 Active Branch 영역 우측에 README.md 전용 편집 버튼을 제공합니다. 편집 완료 후 메모장이 종료되면, 변경된 README.md 파일이 자동으로 원격 Git 저장소에 커밋 및 푸시(git add, commit, push)됩니다. 로컬 저장소에 파일이 없는 경우, 프로그램 템플릿의 `template/README.md`를 자동으로 로컬 저장소에 생성하고 반영한 뒤 메모장을 띄워 줍니다.
* **자동 동기화 (Auto Sync) 기능**: 대시보드 동기화(Synchronization) 카테고리에 Auto Sync 체크박스를 추가하여, 프로그램 구동 시 또는 저장소 스위칭/클론/신규 생성 완료 직후 자동으로 원격 업데이트를 가져오는 "Get Latest Version (Sync)" 작업과 "Merge main branch into current branch" 작업을 순차적으로 연달아 처리할 수 있도록 지원합니다.
* **강력한 충돌 해결 팝업 (LFS 포인터 오류 대응)**:
  - Sync/Merge/Upload 중 충돌이 발생하면 시스템 폰트로 렌더링된 다중 선택 대화상자가 표시되어 마우스 및 Ctrl/Shift 조합으로 여러 파일을 선택한 뒤 일괄 덮어쓰기 해소(Local/Remote 또는 브랜치명 기준)를 수행할 수 있습니다.
  - Git LFS 포인터 불일치로 인해 git merge가 실패하는 상황에서도 예외를 감지하여 충돌 대화상자를 안정적으로 띄우고 처리합니다.
* **Maintainer "Make" 저장소 자동 등록 및 화면 전환**: Maintainer 모드에서 새로운 저장소 생성(Make) 완료 시, 자동으로 신규 로컬 경로 및 원격 주소 설정을 대시보드와 환경설정에 연동 등록하고 대시보드 뷰로 즉시 자동 전환해 줍니다.
* **과거 버전 탐색 및 복원**: 전체 커밋 이력을 그래프 정렬하여 보여주며, 특정 이력을 더블클릭하는 것만으로 안전하게 해당 버전 시점으로 워크스페이스를 되돌립니다 (Standard Detached HEAD 상태 유지).
* **비차단 비동기 UI 모델**: 커밋, 브랜치 푸시, 원격 LFS 상태 쿼리 등의 긴 작업을 수행할 때 화면이 굳지 않도록 모든 동작을 백그라운드 다중 스레드로 분할 처리하며 하단 `System Log` 상태 인디케이터(● Working / ● Idle)와 실시간 로그를 연계해 직관적인 상태를 표시합니다.
* **독자적인 실행기 경로 설정**: `config.json`을 통해 `git.exe`와 `git-lfs.exe` 실행 경로를 각각 완벽하게 커스터마이징하여, GitPython 엔진이 Scoop과 같이 특수한 환경의 독립된 실행기를 그대로 호출해 동작하도록 연동합니다.
* **GitHub (github.com) 원격 저장소 전용**: 본 프로그램은 `PyGithub` API 라이브러리를 통해 원격 브랜치 갱신, 관리자용 신규 저장소 생성 및 연동 배포 기능을 수행하므로, **github.com 원격 저장소 서비스 사용**을 전제로 하여 긴밀하게 설계되었습니다.

---

## 2. 요구 환경 및 필수 소프트웨어

* **운영체제**: Windows 10 / 11 (x64)
* **CAD 시스템**: Dassault Systèmes SolidWorks 및 eDrawings 뷰어 설치 필수 (SolidWorks COM API 기반 실시간 도면 추적 및 eDrawings 외부 미리보기 열기 실행 목적)
* **필수 유틸리티**:
  - **Git**: `git` 버전 2.x 이상 (경로 지정 가능)
  - **Git LFS**: 대형 파일 및 바이너리 락 처리를 위한 확장 기능
  - **uv**: 고속 Python 패키지 및 가상환경 관리자

  > [!TIP]
  > **Scoop 패키지 관리자**를 사용하여 `git`, `git-lfs`, `uv`를 손쉽게 설치할 수 있습니다:
  > ```powershell
  > scoop install git git-lfs uv
  > ```
* **Python 라이브러리 의존성** (`pyproject.toml`에 내장):
  - `gitpython >= 3.1.43` (Git 제어 백엔드)
  - `pygithub >= 2.9.1` (GitHub API 통신)
  - `pywin32 >= 306` (SolidWorks COM 연결 모니터링)

---

## 3. 실행 방법 (자동 의존성 설치 및 구동)

본 프로젝트는 고속 Python 패키지 관리자인 `uv`를 기반으로 하므로 별도의 수동 라이브러리 설치 절차가 필요 없습니다.

프로젝트 폴더 내에 준비된 **`GIT4SW.bat`** 배치 파일을 더블클릭하여 바로 실행하면 됩니다.

> [!NOTE]
> `GIT4SW.bat`는 내부적으로 `uv run main.py`를 실행시킵니다.
> 최초 실행 시 `uv`가 `pyproject.toml`에 기재된 스펙을 감지하여 가상환경(`.venv`)을 자동으로 빌드하고 필요한 의존성 라이브러리(`gitpython`, `pygithub`, `pywin32` 등)를 알아서 다운로드 및 설치한 뒤 프로그램을 안전하게 구동해 줍니다.

---

## 4. 사용 설명서

### 4.1 초기 설정

프로그램을 최초로 실행한 후, 좌측 사이드바 메뉴 맨 하단의 **Config** 버튼을 눌러 설정 화면(Configuration Manager)에서 필수 환경 설정을 먼저 진행해야 합니다. 각 입력 필드에 알맞은 경로와 값을 입력한 후 하단의 **[Save Configuration]** 버튼을 클릭하면 `config.json`에 저장되고 앱에 즉시 반영됩니다.

각 설정 항목의 상세 내용 및 예시는 다음과 같습니다:

* **Git Path**: 프로그램이 내부적으로 Git 명령을 호출해 실행할 `git.exe` 바이너리의 절대 경로입니다.
  - *예*: `C:\Users\dhkima\scoop\apps\git\current\bin\git.exe`
* **Git-Lfs Path**: Git LFS 바이너리 락 획득/조회를 위해 호출할 `git-lfs.exe` 실행 파일의 절대 경로입니다.
  - *예*: `C:\Users\dhkima\scoop\apps\git\current\mingw64\bin\git-lfs.exe`
* **Solidworks Path**: 로컬 시스템에 설치된 SolidWorks 실행 파일(`SLDWORKS.exe`)의 절대 경로입니다. 파일 매니저에서 Solidworks 열기 버튼 클릭 시 및 Fallback 예외 복구 시 실행 경로로 사용됩니다.
  - *예*: `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe`
* **Edrawings Path**: 외부 eDrawings 도면 미리보기 실행 파일(`eDrawings.exe`)의 절대 경로입니다. 파일 매니저에서 eDrawings 버튼 클릭 시 사용됩니다.
  - *예*: `C:\Program Files\SOLIDWORKS Corp\eDrawings\eDrawings.exe`
* **Github Token**: 사용자의 개인 개발용 원격 브랜치를 생성하거나, Maintainer 모드에서 원격 비공개(Private) 저장소를 자동 퍼블리싱할 때 인증용으로 사용할 GitHub 개인 액세스 토큰(Personal Access Token)입니다.
  - *예*: `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`
* **Default Local Path**: 신규 저장소 생성 및 원격 클론 작업 시 기본으로 사용할 로컬 부모 디렉터리 경로입니다.
  - *예*: `C:\Users\dhkima\github`
* **Organization Name**: 관리자 모드에서 신규 비공개 저장소를 자동 개설할 대상 GitHub 조직(Organization)의 이름입니다.
  - *예*: `mech-higenmotor`
* **Auto Sync**: 프로그램 구동 시 또는 저장소 스위칭/클론/신규 생성 작업 완료 시 자동으로 원격 동기화(Get Latest Version) 및 메인 브랜치 병합(Merge main branch)을 순차 실행할지 여부를 결정하는 Boolean 설정 변수입니다. 대시보드의 Auto Sync 체크박스로 제어되며 Config 화면의 수동 편집 목록에서는 제외됩니다.
  - *예*: `true` 또는 `false`


### 4.2 기본 작업 워크플로우

1. **워크스페이스 등록**:
   - `Dashboard` 화면 중앙의 `Repository Configuration` 카드 내에서 **Local Path**를 사용자의 실제 작업 폴더 경로로 설정합니다. 유효한 Git 저장소일 경우 우측에 `(🟢 Active)` 표시와 현재의 브랜치 정보가 갱신됩니다.
2. **개발용 개인 브랜치 생성**:
   - 대시보드에서 **[Make my branch]** 버튼을 누르면 현재 GitHub 계정 명칭 혹은 로컬 명칭과 동일한 이름의 전용 브랜치를 생성하고 자동으로 원격 origin에 Ref를 주입하며 업스트림으로 전환됩니다. (이미 동일한 브랜치가 존재하는 경우 버튼은 비활성화 처리되어 텍스트가 노출되지 않도록 가려집니다.)
3. **README.md 열기 및 수정**:
   - 대시보드의 Active Branch 영역 우측에 배치된 **[README.md]** 버튼을 통해 언제든지 워크스페이스의 프로젝트 정보를 메모장으로 열고 편집할 수 있습니다. 로컬 워크스페이스에 파일이 없는 경우, 프로그램 템플릿에서 자동으로 생성하여 적용해 줍니다.
4. **SolidWorks 부품 설계 및 자동 잠금**:
   - SolidWorks에서 파트나 어셈블리 파일을 오픈하여 수정을 시작하는 즉시 백그라운드 모니터에 의해 `git lfs lock` 명령이 실행됩니다. 타 협업 개발자가 원격 상태를 새로고침하면 해당 도면이 "잠김" 상태로 표시되므로 수정 소실 걱정 없이 안전하게 협업이 유지됩니다.
5. **저장 및 버전 업로드 (Check-in)**:
   - 파일 수정이 완료되면 `File Manager` 탭으로 이동합니다.
   - 단일 또는 다중 선택 상태에서 **[Upload Selected File Version]** 또는 워크스페이스 내 수정/신규 파일을 모두 스테이징하여 커밋하고 즉시 원격 브랜치로 게시해 주는 **[Upload Every Files Version]** 버튼을 통해 업로드합니다.
6. **도면 확인 및 복원 (History Log)**:
   - 특정 이력 버전을 확인하거나 되돌려야 할 때는 `History log` 모드에 진입합니다. 원하는 커밋 줄을 더블클릭하면 해당 커밋 상태로 소스 및 CAD 도면들이 즉시 롤백 복원됩니다.

### 4.3 관리자 기능 (Maintainer Mode)
* **저장소 생성 및 배포 (Make New Repository)**: 신규 CAD 관리용 프로젝트를 기획 시, 저장소 이름을 입력하고 **[Make]** 버튼을 실행하면 GitHub 조직 하위에 Private 저장소를 생성하고 템플릿 파일(`.gitattributes`, `.gitignore`)을 주입한 뒤 main/user 브랜치 배포까지의 모든 과정을 자동으로 마칩니다. 완료 후 생성된 저장소 정보로 대시보드가 즉시 자동 갱신되며 대시보드로 이동합니다.
* **일괄 병합 (Merge all branches into main)**: 프로젝트 리더가 개발 브랜치들의 모든 진척 상황을 병합하려 할 때 실행합니다. 병합 도중 충돌(Conflict)이 감치되면 Ours(main 유지) 또는 Theirs(개발 브랜치 이식)를 묻는 팝업 다이어로그를 띄워 백그라운드 스레드에서 안전하고 순차적으로 병합 처리를 진행해 줍니다.
