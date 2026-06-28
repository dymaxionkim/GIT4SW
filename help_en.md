# GIT4SW - SolidWorks Git Version Control Client

Agentic CAD version control desktop app integrating Git workflows & Git LFS locking for conflict-free 3D CAD collaboration.

---

## 1. Dashboard Mode

Central control panel for repository setup, sync, and real-time status.

- **Local Path** — Shows the full path to your active local Git repository.
- **Change Workspace** — Opens a folder dialog to switch to a different Git repository.
- **Remote Server** — Displays the linked remote URL; enter a URL to clone a new repository.
- **Clone** — Clones the remote repository into the local path directory.
- **Active Branch** — Dropdown showing the current branch. Select another branch to check it out.
- **Make my branch** — Creates & switches to a personal developer branch under your username, isolating work from `main`.
- **README.md** — Opens the repo's README.md in Notepad. On save, auto runs `git add`, `commit`, `push`. Creates from template if missing.
- **Get Latest Version (Sync)** — Pulls the latest remote changes. Detects merge conflicts and auto-backups conflicted files to `.backup/` before resolving. Skips if already up-to-date.
- **Merge main branch into current branch** — Merges `main` into your branch, commits, pushes, and switches back to your branch. Auto-backups local changes before merging. Skips if already merged.
- **Cleanup LFS Cache** — Opens a wizard to scan & purge unused `.git/lfs/objects/` (excluding HEAD and HEAD~1), freeing disk space.
- **Auto Sync** — Checkbox. When ON, auto-runs "Sync" then "Merge main" on startup or workspace change.
- **Live Monitor** — Real-time panel showing: SolidWorks status (Active/Inactive), total tracked files, open files, locked files, and repository size.

---

## 2. File Manager Mode

File-level checkout, check-in, and local modification management.

- **File Table** — Treeview listing all workspace files with columns: File Path (color-coded: green=part, orange=assembly, red=drawing, purple=other), Status (Unmodified/Modified/New File), SolidWorks (Open if loaded), Locked (me/other), By (lock owner). Press `Ctrl+A` to select all. Sortable by Name, Extension, Status, SolidWorks, or Locked. Respects `.gitignore`.
- **CAD File Preview Canvas** — Shows a 180x135 thumbnail of the selected CAD file (hybrid OLE + COM extraction). Active only in File Manager with exactly one file selected.
- **Click-to-Copy** — Click the preview canvas to copy the thumbnail bitmap to clipboard (CF_DIB) for pasting into other apps.
- **Refresh** — Reloads files, queries LFS locks, and updates SolidWorks open documents.
- **Find Top** — Scans `.sldasm` dependency graph via COM API (fast, no file opening). Marks top-level assemblies with a red **TOP** label. Runs in background subprocess.
- **Open** — Opens the workspace folder in File Explorer.
- **Lock** — Acquires LFS locks on selected files. Also auto-acquired when opening files in SolidWorks.
- **Unlock File** — Releases your LFS locks on selected files.
- **Force Unlock** — Breaks another developer's LFS lock (use with caution).
- **Discard** — Reverts selected files to their last committed state.
- **eDrawings** — Opens the selected CAD file in eDrawings for lightweight viewing.
- **SolidWorks** — Opens the selected file in active SolidWorks instance.
- **Diff** — Compare two versions of a part or drawing. Opens commit history popup; select a commit and click Diff to extract `_OURS` (current) and `_THEIRS` (selected commit) into `.backup/`, then opens both in SolidWorks with instructions for manual comparison.
- **EXPORT** — Bulk-converts drawings, parts, assemblies to PDF/DXF/STEP/STEP_ASM with format selection, prefix filter, and output directory. Runs asynchronously in a background process. Includes watchdog (3-min timeout), UTF-8 safety, and deadlock-free cleanup.
- **Version Description** — Text box for commit message input.
- **Upload Selected File Version** — Commits, pushes, and uploads selected modified files; releases locks on success. Blocks if a file is open in SolidWorks or locked by another user.
- **Upload Every Files Version** — Same as above, but applies to all modified files in the workspace.

---

## 3. History Log Mode

Browse previous revisions and audit changes.

- **Revision List** — Table of past commits (hash, author, date, message). The current checkout is highlighted in bold green.
- **Graph** — Opens a terminal showing the ASCII commit tree via `git log --graph --oneline --all --decorate`.

---

## 4. Maintainer Mode

For project administrators to oversee integration and initialize repositories.

- **Merge all branches into main** — Fetches, pulls, and merges all remote branches into `main`, pushes, then restores the original developer branch.
- **Repository Name + Create New CAD Repository** — Enter a name and click "Make" to auto-create a private GitHub repo, initialize locally, configure LFS, set lock extensions, and push the first commit. Redirects to Dashboard on success.

---

## 5. Config Mode

Configure system paths for Git, SolidWorks, and integrations.

- **Path Configurations** — Set paths for Git, Git LFS, SolidWorks, eDrawings, Git Token, Git Server Type, Git Server URL, default local directory, and default GitHub org. Path fields have "Find" buttons to scan the C: drive. "Git Server Type" toggles the server URL field. `auto_sync` is managed via the Dashboard checkbox.
- **Save Configuration** — Saves all values to `config.json` and re-initializes connections.
- **Edit** — Opens `config.json` directly in Notepad for manual editing.

---

## 6. System Log & Process Termination

Bottom panel showing operation logs and process controls.

- **Status Indicator** — Green "● Idle" when idle, red "● Working" during background tasks.
- **Sequential Button Queuing** — Clicking action buttons while working queues them; execution runs sequentially after the current task finishes.
- **Terminate** — Forcefully aborts running Git operations, kills child processes, clears queues, and restores buttons. Disabled (pink) when idle, active (red) when working.
- **Clear** — Empties the log window.
