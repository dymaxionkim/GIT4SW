# GIT4SW: SolidWorks Git Version Control Client

[README_ko.md](README_ko.md)

## Necessity

* When performing design work with SolidWorks, 3D CAD binary files such as drawings (`.slddrw`), parts (`.sldprt`), and assemblies (`.sldasm`) cannot be merged at the code level in Git, unlike general text coding tasks. This frequently causes serious issues like overwriting or loss of changes during multi-user collaboration.
* **GIT4SW** is a **SolidWorks-exclusive GitHub integrated version control desktop client** designed to fundamentally prevent version entanglement and conflicts that can occur due to simultaneous modification of these unstructured CAD files by multiple users.
* By combining the standard Git branch workflow with the **Git LFS (Large File Storage) Lock mechanism**, it completely blocks other users from overwriting a file while a specific user is modifying it.

![](GIT4SW.png)

* Demo Movie : [https://youtu.be/SGs7_w_s2pI](https://youtu.be/SGs7_w_s2pI)

---

## 1. Key Features

* **Auto Lock & Monitor**: Background thread tracks open files via SolidWorks COM API. Acquires LFS Lock on file open, releases on close. Dashboard shows real-time repository size.
* **LFS Cache Cleanup Wizard**: GUI tool to safely purge unused `.git/lfs/objects/`, keeping only files from the current index and last 2 commits.
* **Work Safety**: Blocks upload if files are open in SolidWorks or locked by others. Branch switching warns about unsaved changes. Only the SolidWorks button manages LFS locks; eDrawings/EXPORT/BOM use ReadOnly mode.
* **Color-coded File List**: Parts green `#059669`, assemblies orange `#d97706`, drawings red `#dc2626`. Sort by name, extension, status, SW open state, or lock state.
* **CAD Thumbnail Preview**: Auto-extracts 4:3 thumbnail for selected file; click to copy to clipboard.
* **Branch Management**: "Make my branch" auto-creates a personal remote branch. "Merge all branches into main" bulk-merges with Ours/Theirs conflict resolution.
* **Sequential Task Queue**: Background operations queue automatically; Terminate button force-kills stuck Git processes.
* **Auto Sync**: Fetches and merges on startup/repo change; skips if already up-to-date.
* **Conflict Resolution**: Multi-file selection dialog with auto-backup to `.backup/`. Handles LFS pointer errors.
* **EXPORT (Bulk Conversion)**: Convert `.sldprt`/`.sldasm`/`.slddrw` to PDF, DXF, STEP (AP214 color). Multiple formats per run. Watchdog (180s) kills and restarts hung SolidWorks automatically. Per-file progress counting, not per-configuration.
* **BOM Extraction**: Recursively traverse assemblies to generate hierarchical BOM Tree and flat Partlist as Excel (`.xlsx`). Excludes suppressed and BOM-excluded components. Configuration selector for multi-config assemblies.
* **Visual Diff (Diff button)**: Browse Git commit history for a file, then open the current version (`_OURS`) and selected commit (`_THEIRS`) in SolidWorks side-by-side for manual comparison (Geometry Compare for parts, Draw Compare for drawings).
* **Version History & Graph**: Double-click any commit to restore workspace to that state. ASCII commit graph (via cmd) or interactive GitHub Network browser.
* **Multi-Server Support**: Supports both `github.com` and Gitea-based remote repositories. Configure via `git_server_type` in config. GitHub uses PyGithub API; Gitea uses its REST API v1.
* **Find Top (Top-Level Assembly Scanner)**: Scans `.sldasm` dependency graph to identify top-level assemblies (not referenced by any other assembly). Uses `GetDocumentDependencies2` COM API—reads metadata only, never opens files—making it orders of magnitude faster than `OpenDoc6`.
* **Performance Optimized**: Auto-lock suppressed during EXPORT/BOM (ReadOnly files need no lock). All stabilization delays reduced by 50%.
* **Config Editor**: "Edit" button opens the currently loaded configuration file directly in Notepad. Custom config path is set via `--config` CLI argument (e.g., `--config config_codeberg.json`).
* **Multilingual Help**: Language combo box in Help view to switch between English (`help_en.txt`) and Korean (`help_ko.txt`). All GUI terms include the original English in parentheses.

---

## 2. Requirements and Required Software

* **Operating System**: Windows 10 / 11 (x64)
* **CAD System**: Installation of Dassault Systèmes SolidWorks and eDrawings Viewer is mandatory (for real-time drawing tracking based on SolidWorks COM API and opening external eDrawings previews).
* **Required Utilities**:
  - **Git**: `git` version 2.x or higher (path can be specified)
  - **Git LFS**: Extension for handling large files and binary locks
  - **uv**: High-speed Python package and virtual environment manager

  > [!TIP]
  > You can easily install `git`, `git-lfs`, and `uv` using the **Scoop package manager**:
  > ```powershell
  > scoop install git git-lfs uv
  > ```

---

## 3. Execution Method (Automatic Dependency Installation and Startup)

Since this project is based on the high-speed Python package manager `uv`, no separate manual library installation procedure is required.

Simply double-click the **`GIT4SW.bat`** batch file prepared in the project folder to run it immediately.

Alternatively, launch from the terminal with a custom config file:
```bash
uv run main.py --config config_codeberg.json
```

> [!NOTE]
> `GIT4SW.bat` internally executes `.venv\Scripts\pythonw.exe main.py --config config.json`.
> You can pass a different config file using `--config config_name.json` from the command line.
> Before the first run, execute `uv sync` in the project directory to build the virtual environment (`.venv`) and install dependencies.

---

## 4. User Manual

### 4.1 Initial Setup

![](GIT4SW_05.png)

After running the program for the first time, you must first perform essential environment settings by clicking the **Config** button at the bottom of the left sidebar menu. After entering the appropriate paths and values in each input field, click the **[Save Configuration]** button at the bottom to save it to the configuration file, which is immediately reflected in the app. You can also click **[Edit]** to open the configuration file directly in Notepad for manual editing.

Details and examples for each configuration item are as follows:

* **Git Path**: The absolute path of the `git.exe` binary that the program will call internally to execute Git commands.
  - *Example*: `C:\Users\dhkima\scoop\apps\git\current\bin\git.exe`
* **Git-Lfs Path**: The absolute path of the `git-lfs.exe` executable called to acquire/query Git LFS binary locks.
  - *Example*: `C:\Users\dhkima\scoop\apps\git\current\mingw64\bin\git-lfs.exe`
* **Solidworks Path**: The absolute path of the SolidWorks executable (`SLDWORKS.exe`) installed on your local system. It is used as the execution path when clicking the "Open Solidworks" button in the File Manager or during Fallback exception recovery.
  - *Example*: `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe`
* **eDrawings Path**: The absolute path of the external eDrawings drawing preview executable (`eDrawings.exe`). It is used when clicking the eDrawings button in the File Manager.
  - *Example*: `C:\Program Files\SOLIDWORKS Corp\eDrawings\eDrawings.exe`
* **Git Token**: The Personal Access Token used for authenticating when creating personal development remote branches or automatically publishing new private repositories in Maintainer mode. Works for both GitHub and Gitea.
  - *Example*: `ghp_**********************************`
* **Git Server Type**: Select your remote server type (`github` or `gitea`). When set to `gitea`, the **Git Server URL** field below becomes enabled.
  - *Example*: `github`, `gitea`
* **Git Server URL**: The base URL of your Gitea server (only used when `git_server_type` is `gitea`). When `github` is selected, this field is disabled and auto-filled with `https://github.com`.
  - *Example*: `https://codeberg.org`
* **Default Local Path**: The default local parent directory path to be used when creating new repositories or performing remote clone operations.
  - *Example*: `C:\Users\dhkima\github`
* **Organization Name**: The name of the GitHub Organization target for automatically opening new private repositories in Administrator mode.
  - *Example*: `mech-higenmotor`
* **Auto Sync**: A Boolean setting variable that determines whether to sequentially execute "Get Latest Version (Sync)" and "Merge main branch" tasks upon program startup or upon completion of repository switching, cloning, or new creation. This is controlled by the Auto Sync checkbox on the Dashboard and is excluded from the manual editing list in the Config screen.
  - *Example*: `true` or `false`

### 4.2 Basic Workflow

1. **Register Workspace**:
   - Set the **Local Path** within the `Repository Configuration` card in the center of the `Dashboard` screen to your actual working folder path. If it is a valid Git repository, `(🟢 Active)` and current branch information will be updated on the right.

2. **Create Personal Development Branch**:
   - Press the **[Make my branch]** button on the Dashboard to create a dedicated branch with the same name as your Current GitHub account or local name, automatically inject the Ref into remote `origin`, and switch to the upstream. (If an identical branch already exists, the button will be disabled and the text will be hidden.)

![](GIT4SW_01.png)

3. **Open and Edit README.md**:
   - You can open and edit the project information of the workspace in Notepad at any time via the **[README.md]** button located to the right of the Active Branch area on the Dashboard. If the file does not exist in the local workspace, it will be automatically generated from the program template.

![](GIT4SW_02.png)

4. **SolidWorks Part Design and Automatic Locking**:
   - As soon as you open a part or assembly file in SolidWorks and begin editing, `git lfs lock` is executed by the background monitor. When other collaborators refresh the remote status, that drawing will appear as "Locked," allowing for safe collaboration without fear of losing modifications.

5. **Save and Upload Version (Check-in)**:
   - Once file modification is complete, go to the `File Manager` tab.
   - Use the **[Upload Selected File Version]** button for single or multiple selections, or the **[Upload Every Files Version]** button to stage, commit, and immediately publish all modified/new files in the workspace to the remote branch.

![](GIT4SW_03.png)

6. **Verify Drawings and Restore (History Log)**:
   - To check a specific history version or revert, enter `History log` mode. Double-clicking a desired commit row will immediately roll back the source and CAD drawings to that commit state.

7. **Bulk Convert Drawing and Model Formats (Export)**:
   - While filtering for the desired range of files in the file list, press the **[EXPORT]** button.
   - Check the target formats (PDF, DXF, STEP, etc.) and enter a `PREFIX` if there are prefix conditions.
   - Press the **[Start]** button; the SolidWorks engine will run in the background and batch convert and save the target files into the specified subdirectories (e.g., `2D/PDF/`, `2D/STEP/`) with high quality, maintaining black & white pen tables (for PDF) and AP214 color (for STEP).

8. **Extract Assembly BOM and Partlist**:
   - Select exactly one assembly (`.sldasm`) file and click the **[BOM]** button in the File Manager toolbar.
   - If the assembly has multiple configurations, select the target configuration in the dropdown popup window.
   - The extraction process runs in the background. Once completed, verify the generated `[assembly]__BOM.xlsx` (indented tree) and `[assembly]__PL.xlsx` (flat partlist) files under the `2D/BOM/` subfolder relative to the assembly's directory.

9. **Git LFS Cache Cleanup (Cleanup LFS Cache)**:
   - When local disk space becomes low, click the **[Cleanup LFS Cache]** button on the dashboard to launch the cleanup wizard dialog.
   - The wizard automatically scans the `.git/lfs/objects/` path and identifies unused large binary files that do not belong to the current index or the last 2 commits (`HEAD`, `HEAD~1`). Clicking the [Cleanup Cache] button will safely remove these files to reclaim local disk space.

10. **Find Top (Top-Level Assembly Scanner)**:
    - Click the **[Find Top]** button in the File Manager toolbar (right of Refresh) to scan the workspace's `.sldasm` dependency graph.
    - The scanner uses SolidWorks `GetDocumentDependencies2` API which reads dependency metadata without opening files, making it dramatically faster than the traditional `OpenDoc6` approach.
    - Identified top-level assemblies are highlighted in the file list with a red bold `#dc2626` "TOP" tag. A file is "top-level" if no other `.sldasm` in the workspace references it.

### 4.3 Maintainer Mode (Administrator Functions)

![](GIT4SW_04.png)

* **Create and Deploy Repository (Make New Repository)**: When planning a new CAD management project, enter the repository name and execute the **[Make]** button to automatically create a Private repository under your GitHub Organization, inject template files (`.gitattributes`, `.gitignore`), and complete everything from main/user branch deployment. Upon completion, the dashboard is automatically updated with the new repository information and transitions to the Dashboard view.
* **Bulk Merge (Merge all branches into main)**: Executed when a project leader wants to merge the progress of all development branches. If a conflict is detected during merging, a popup dialog asking whether to use Ours (keep main) or Theirs (import development branch) is displayed, and merging is performed safely and sequentially in a background thread.

### 4.4 Troubleshooting

#### 4.4.1 Git Authentication with GitHub Token

* Git remote operations (push, pull, locks, etc.) are authenticated seamlessly using the `git_token` configured in `config.json` (backward compatible: `github_token` is also read as a fallback).
* The `git_server_type` field (`github` or `gitea`) determines which API provider is used. For `gitea`, the `gitea_url` must point to your Gitea instance (e.g., `https://codeberg.org`).
* During execution, the program dynamically unsets local credential helpers and bypasses the Windows Credential Manager (GCM) using an inline temporary helper. This prevents any interactive browser/GCM login popup windows from interrupting your work.
* If authentication fails:
  - Verify that the `git_token` in the config file is a valid Personal Access Token (PAT) with appropriate scopes (especially `repo` or `write` access for GitHub, or API access for Gitea).
  - Check your internet connection or repository permissions. Do not manually adjust your local/global Git credential helpers.

#### 4.4.2 Restoring Overwritten Local Modifications

* If you accidentally chose to resolve a conflict by selecting "Theirs" (Remote Version) and lost your local changes, you can retrieve them from the `.backup/` folder in your workspace root.
* All original conflicted local files are copied here before the conflict prompt is shown. The files are named using the pattern `[filename]_[YYYYMMDD_HHMMSS].[ext]`.
* Simply copy the backup file back to its original location and rename it to restore your work.

#### 4.4.3 SOLIDWORKS CAM Add-in Popup Appearing During EXPORT

If unexpected popup windows from **SOLIDWORKS CAM** appear while running the EXPORT batch conversion, it means the SOLIDWORKS CAM add-in is currently enabled in your SolidWorks installation. This add-in can interfere with the automated background conversion process by displaying its own initialization or warning dialogs, which will block progress.

**Resolution**: Disable the SOLIDWORKS CAM add-in in SolidWorks before running EXPORT:
1. Open SolidWorks.
2. Go to **Tools → Add-ins...**
3. In the Add-ins dialog, find **SOLIDWORKS CAM** in the list.
4. Uncheck **both** the "Active Add-ins" checkbox (immediate load) and the "Start Up" checkbox (load on startup).
5. Click **OK** and close SolidWorks.
6. Run EXPORT again — the popups will no longer appear.

> [!NOTE]
> You only need to do this once. After disabling SOLIDWORKS CAM, your setting is remembered by SolidWorks and will remain disabled on subsequent EXPORT runs.

#### 4.4.4 Sub-components/Sub-assemblies inside an Opened Assembly Loading as 'Read-Only' in SolidWorks

When opening an assembly (`.sldasm`) file using the **[Solidworks]** button in GIT4SW, the sub-assemblies (`.sldasm`) and part (`.sldprt`) files contained within the assembly may automatically open in **'Read-Only'** mode depending on SolidWorks system configurations.
Under this state, any modifications made by the designer cannot be successfully saved to disk, which prevents GIT4SW from detecting modifications and performing a Check-in (upload).

**Resolution**: Change the reference documents configuration in SolidWorks System Options:
1. Open SolidWorks.
2. Go to **Tools → Options...** (or click the Options gear icon).
3. In the **System Options** tab, select **External References** from the left panel.
4. Uncheck the **"Open referenced documents with read-only access"** checkbox.
5. Click **OK** to save options and close SolidWorks.
6. Re-open the assembly file; the referenced sub-components will now load with write-access enabled.
