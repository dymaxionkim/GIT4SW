import os
import sys
import subprocess
import re
import datetime
import git
import threading

active_processes = set()
active_processes_lock = threading.Lock()

def register_process(proc):
    with active_processes_lock:
        active_processes.add(proc)

def deregister_process(proc):
    with active_processes_lock:
        active_processes.discard(proc)

def terminate_all_processes():
    terminated = []
    with active_processes_lock:
        for proc in list(active_processes):
            try:
                proc.terminate()
                terminated.append(proc.pid)
            except Exception:
                pass
    return terminated


def check_token_access(token, remote_url):
    """
    Checks if the given token has access to the GitHub repository specified by the remote URL.
    Returns True if access is confirmed (HTTP 200), False otherwise.
    """
    if not token or not remote_url or "github.com" not in remote_url.lower():
        return False

    # Parse owner and repo name
    match = re.search(r'github\.com[/:]([^/]+)/([^/.]+)(?:\.git)?', remote_url, re.IGNORECASE)
    if not match:
        return False

    owner, repo = match.groups()
    api_url = f"https://api.github.com/repos/{owner}/{repo}"

    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            api_url,
            headers={
                "Authorization": f"token {token}",
                "User-Agent": "GIT4SW-App"
            }
        )
        with urllib.request.urlopen(req, timeout=2.0) as response:
            if response.status == 200:
                return True
    except Exception as e:
        print(f"DEBUG (global): Token check for {owner}/{repo} failed: {e}")

    return False


def optimize_credentials_for_path(repo_path):
    """
    Given a local repository path, checks if it is a git repository,
    creates a custom git credential helper that reads the token from config.json,
    and configures Git to use it instead of GCM to prevent any account picker popups.
    """
    if not repo_path:
        repo_path = os.path.abspath(".")
    
    git_dir = os.path.join(repo_path, ".git")
    if not os.path.exists(git_dir):
        return

    # Find the python executable path
    python_path = sys.executable.replace("\\", "/")
    
    # Locate config.json in the current working directory or check parent directory
    config_path = "config.json"
    if not os.path.exists(config_path):
        config_path = os.path.join(repo_path, "config.json")
        if not os.path.exists(config_path):
            config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "config.json"))
            
    if not os.path.exists(config_path):
        return

    config_abs_path = os.path.abspath(config_path).replace("\\", "/")

    # Read config.json to see if token is present
    token = ""
    try:
        import json
        with open(config_abs_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            token = config.get("github_token", "").strip()
    except Exception:
        pass

    git_exe = "git"
    if os.path.exists(config_abs_path):
        try:
            import json
            with open(config_abs_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                custom_git = config.get("git_path")
                if custom_git and os.path.exists(custom_git):
                    git_exe = custom_git
        except Exception:
            pass

    def run_simple_git(cmd):
        res = subprocess.run([git_exe] + cmd, cwd=repo_path, capture_output=True, text=True, errors="ignore")
        return res.returncode, res.stdout.strip(), res.stderr.strip()

    # Get remote URL
    code, remote_url, _ = run_simple_git(["remote", "get-url", "origin"])
    if code != 0 or not remote_url:
        return

    if "github.com" not in remote_url.lower():
        return

    # If token exists and has access, we set up the custom credential helper to completely bypass GCM
    if token and check_token_access(token, remote_url):
        # Write .git/git_helper.py
        helper_path = os.path.join(git_dir, "git_helper.py")
        helper_code = f"""import json
import os
import sys

def main():
    if len(sys.argv) < 2 or sys.argv[1] != 'get':
        return
    
    is_github = False
    for line in sys.stdin:
        if line.startswith('host=github.com'):
            is_github = True
            
    if is_github:
        config_path = {repr(config_abs_path)}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    token = config.get('github_token', '').strip()
                    if token:
                        print("username=x-access-token")
                        print(f"password={{token}}")
            except Exception:
                pass

if __name__ == '__main__':
    main()
"""
        try:
            with open(helper_path, "w", encoding="utf-8") as f:
                f.write(helper_code)
        except Exception as we:
            print(f"DEBUG (global): Failed to write helper script: {we}")
            return

        helper_escaped = helper_path.replace("\\", "/")
        helper_val = f'!"{python_path}" "{helper_escaped}"'

        # Check if already set
        code_helper, current_helper, _ = run_simple_git(["config", "--local", "credential.helper"])
        if helper_val not in current_helper or "manager" in current_helper or "wincred" in current_helper:
            try:
                # 1. Clear local helper
                run_simple_git(["config", "--local", "credential.helper", ""])
                # 2. Add our helper
                run_simple_git(["config", "--local", "--add", "credential.helper", helper_val])
                print(f"DEBUG (global): Configured custom file-based credential helper.")
            except Exception as ce:
                print(f"DEBUG (global): Failed to set credential helper config: {ce}")
                
        # Also clean the remote URL of any embedded plaintext tokens
        if "@" in remote_url:
            match = re.match(r'^(https?://)([^@]+)@(github\.com/.*)$', remote_url, re.IGNORECASE)
            if match:
                prefix, _, rest = match.groups()
                cleaned_url = f"{prefix}{rest}"
                run_simple_git(["remote", "set-url", "origin", cleaned_url])
                print(f"DEBUG (global): Cleaned remote URL to {cleaned_url}")
                
    else:
        # If token does not exist in config.json or lacks access, revert to GCM
        # 1. Clear any custom local helper if it was set
        run_simple_git(["config", "--local", "--unset-all", "credential.helper"])
        
        # 2. Configure username on GCM
        code_user, current_user_config, _ = run_simple_git(["config", "--local", "credential.https://github.com.username"])
        has_token_in_url = "@" in remote_url

        # Force correct username 'dymaxionkim' if currently set to 'dhkima' or unset
        if code_user != 0 or not current_user_config or current_user_config.lower() == "dhkima":
            fallback_user = "dymaxionkim"
            if has_token_in_url:
                match = re.match(r'^(https?://)([^@]+)@(github\.com/.*)$', remote_url, re.IGNORECASE)
                if match:
                    prefix, _, rest = match.groups()
                    cleaned_url = f"{prefix}{rest}"
                    run_simple_git(["remote", "set-url", "origin", cleaned_url])
                    print(f"DEBUG (global): Cleaned remote URL to {cleaned_url}")

            try:
                run_simple_git(["config", "--local", "credential.https://github.com.username", fallback_user])
                run_simple_git(["config", "--local", "credential.username", fallback_user])
                print(f"DEBUG (global): Corrected GCM credential username to {fallback_user}")
            except Exception as e:
                print(f"DEBUG (global): Failed to config GCM credential: {e}")



def run_git_subprocess(cmd_args, cwd, check=True):
    import json
    args = list(cmd_args)
    if args and os.path.basename(args[0]).lower() in ("git", "git.exe"):
        network_cmds = {"fetch", "pull", "push", "clone", "ls-remote", "lock", "unlock", "locks"}
        if any(cmd in args for cmd in network_cmds):
            try:
                optimize_credentials_for_path(cwd)
            except Exception as e:
                print(f"DEBUG: Failed to optimize credentials in run_git_subprocess: {e}")

        try:
            if os.path.exists("config.json"):
                with open("config.json", "r", encoding="utf-8") as f:
                    config = json.load(f)
                    git_path = config.get("git_path")
                    if git_path and os.path.exists(git_path):
                        args[0] = git_path
        except Exception:
            pass

    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore"
    )
    register_process(proc)
    try:
        stdout, stderr = proc.communicate()
    finally:
        deregister_process(proc)

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, args, output=stdout, stderr=stderr)
    return proc.returncode, stdout, stderr


def parse_lfs_pointer_errors(err_str):
    files = []
    lines = err_str.splitlines()
    found_marker = False
    for line in lines:
        if "should have been a pointer" in line or "should have been pointers" in line:
            found_marker = True
            if ":" in line:
                after_colon = line.split(":", 1)[1].strip()
                if after_colon:
                    files.append(after_colon)
            continue
        if found_marker:
            stripped = line.strip()
            if not stripped:
                continue
            if line.startswith('\t') or line.startswith('   ') or line.startswith('  '):
                files.append(stripped)
            else:
                break
    return files


class MergeConflictError(Exception):
    """Raised when git merge results in one or more conflicts."""
    def __init__(self, message, conflicted_files):
        super().__init__(message)
        self.conflicted_files = conflicted_files  # list of relative file paths


class GitService:
    def __init__(self, repo_path):
        self.repo_path = os.path.abspath(repo_path)
        self.git_path = None
        self.git_lfs_path = None
        self._load_config_paths()
        self._apply_git_paths()
        self.repo = None
        self._load_repo()
        
    def _load_config_paths(self):
        config_path = "config.json"
        if os.path.exists(config_path):
            try:
                import json
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.git_path = config.get("git_path")
                    self.git_lfs_path = config.get("git-lfs_path")
            except Exception as e:
                print(f"Error loading config paths in GitService: {e}")

    def _apply_git_paths(self):
        if self.git_lfs_path and os.path.exists(self.git_lfs_path):
            lfs_dir = os.path.dirname(self.git_lfs_path)
            if lfs_dir and lfs_dir not in os.environ["PATH"]:
                os.environ["PATH"] = lfs_dir + os.pathsep + os.environ["PATH"]

        if self.git_path and os.path.exists(self.git_path):
            try:
                git.refresh(path=self.git_path)
            except Exception as e:
                print(f"Error refreshing git path in GitPython: {e}")
                
            git_dir = os.path.dirname(self.git_path)
            if git_dir and git_dir not in os.environ["PATH"]:
                os.environ["PATH"] = git_dir + os.pathsep + os.environ["PATH"]

    def _load_repo(self):
        try:
            self.repo = git.Repo(self.repo_path)
        except Exception:
            self.repo = None

    def _run_lfs_cmd(self, args, check=True):
        """Runs a Git CLI command for LFS operations (locks, pull, push) which GitPython doesn't natively wrap cleanly."""
        cmd_args = list(args)
        if cmd_args and cmd_args[0] == "git" and self.git_path and os.path.exists(self.git_path):
            cmd_args[0] = self.git_path

        try:
            _, stdout, stderr = run_git_subprocess(cmd_args, self.repo_path, check=check)
            return stdout.rstrip()
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.strip() if e.stderr else str(e)
            raise RuntimeError(f"Git CLI command {' '.join(cmd_args)} failed: {err_msg}")
        except FileNotFoundError:
            raise RuntimeError("Git CLI is not installed or not found on system PATH.")

    def is_git_repo(self):
        """Checks if the directory is a valid git repository."""
        self._load_repo()
        return self.repo is not None

    def clone_repository(self, remote_url):
        """Clones a remote repository into self.repo_path."""
        parent_dir = os.path.dirname(self.repo_path)
        if not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
            
        cmd_args = ["git", "clone", remote_url, self.repo_path]
        if self.git_path and os.path.exists(self.git_path):
            cmd_args[0] = self.git_path

        try:
            _, stdout, stderr = run_git_subprocess(cmd_args, parent_dir, check=True)
            self._load_repo()
            return stdout.strip()
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.strip() if e.stderr else str(e)
            raise RuntimeError(f"Git clone failed: {err_msg}")
        except FileNotFoundError:
            raise RuntimeError("Git CLI is not installed or not found on system PATH.")

    def initialize_repository(self, remote_url=None):
        """Initializes a new git repository, configures LFS for SolidWorks, and adds remote."""
        if not self.is_git_repo():
            git.Repo.init(self.repo_path)
            self._load_repo()
            
        # Configure LFS via subprocess
        try:
            self._run_lfs_cmd(["git", "lfs", "install"])
        except Exception:
            raise RuntimeError("Git LFS is not installed on system. Please install Git LFS first.")

        # Create/Update .gitattributes
        gitattributes_path = os.path.join(self.repo_path, ".gitattributes")
        sw_rules = [
            "*.sldprt filter=lfs diff=lfs merge=lfs -text lockable\n",
            "*.sldasm filter=lfs diff=lfs merge=lfs -text lockable\n",
            "*.slddrw filter=lfs diff=lfs merge=lfs -text lockable\n"
        ]
        
        existing_content = ""
        if os.path.exists(gitattributes_path):
            with open(gitattributes_path, "r", encoding="utf-8") as f:
                existing_content = f.read()

        rules_to_add = []
        for rule in sw_rules:
            ext = rule.split()[0]
            if ext not in existing_content:
                rules_to_add.append(rule)

        if rules_to_add:
            with open(gitattributes_path, "a", encoding="utf-8") as f:
                f.writelines(rules_to_add)

        # Create/Update .gitignore
        gitignore_path = os.path.join(self.repo_path, ".gitignore")
        ignore_rules = [
            "# SolidWorks temporary files\n",
            "~$*\n",
            "*.tmp\n",
            "*.sldlfp\n",
            "*.sldprt.tmp\n",
            "*.sldasm.tmp\n"
        ]
        
        existing_ignore = ""
        if os.path.exists(gitignore_path):
            with open(gitignore_path, "r", encoding="utf-8") as f:
                existing_ignore = f.read()
                
        ignore_to_add = []
        for r in ignore_rules:
            if r.strip() not in existing_ignore:
                ignore_to_add.append(r)
                
        if ignore_to_add:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                f.writelines(ignore_to_add)

        # Commit initial configurations
        try:
            self.repo.index.add([".gitattributes", ".gitignore"])
            author = git.Actor('SolidWorks Designer', 'designer@example.com')
            self.repo.index.commit("Initialize GIT4SW configuration", author=author, committer=author)
        except Exception:
            pass

        if remote_url:
            self.set_remote(remote_url)

    def set_remote(self, remote_url):
        """Sets or updates the remote 'origin' URL."""
        if not self.is_git_repo():
            return
        try:
            if "origin" in self.repo.remotes:
                self.repo.delete_remote("origin")
            self.repo.create_remote("origin", remote_url)
        except Exception:
            pass

    def get_remote_url(self):
        """Gets the remote 'origin' URL, if configured."""
        if not self.is_git_repo():
            return ""
        try:
            return self.repo.remote("origin").url
        except Exception:
            return ""

    def get_correct_filepath_casing(self, file_path):
        """
        Returns the exact case-sensitive relative path as it exists in the Git repository 
        or on disk, to prevent casing mismatches on Windows.
        """
        # Normalize slashes first
        rel_path = os.path.relpath(os.path.join(self.repo_path, file_path), self.repo_path).replace("\\", "/")
        rel_path_lower = rel_path.lower()
        
        # 1. Try to find it in the git index first (most accurate for Git/LFS)
        if self.repo:
            try:
                for entry in self.repo.index.entries:
                    entry_path = entry[0]
                    if entry_path.lower() == rel_path_lower:
                        return entry_path
            except Exception:
                pass
                
        # 2. Fallback: Search the filesystem to match casing of directories and filename
        parts = rel_path.split('/')
        current_dir = self.repo_path
        corrected_parts = []
        for part in parts:
            if not part:
                continue
            part_lower = part.lower()
            matched = part
            try:
                if os.path.isdir(current_dir):
                    for name in os.listdir(current_dir):
                        if name.lower() == part_lower:
                            matched = name
                            break
            except Exception:
                pass
            corrected_parts.append(matched)
            current_dir = os.path.join(current_dir, matched)
            
        return "/".join(corrected_parts)

    def get_status(self, locks=None):
        """
        Scans workspace directory files and merges Git status with LFS lock status.
        """
        if not self.is_git_repo():
            return []

        # 1. Fetch LFS locks if not provided
        if locks is None:
            locks = self.get_lfs_locks()
        
        # 1.5 Fetch ignored files to exclude them from the file list
        ignored_files = set()
        try:
            ignored_out = self._run_lfs_cmd(["git", "-c", "core.quotepath=false", "ls-files", "--others", "--ignored", "--exclude-standard"])
            for line in ignored_out.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith('"') and line.endswith('"'):
                    line = line[1:-1]
                    try:
                        import codecs
                        b, _ = codecs.escape_decode(bytes(line, "utf-8"))
                        line = b.decode("utf-8")
                    except Exception:
                        pass
                ignored_files.add(line.replace("\\", "/").lower())
        except Exception:
            pass
        
        # 2. Get status via git status --porcelain
        changed_files = {}
        try:
            status_out = self._run_lfs_cmd(["git", "-c", "core.quotepath=false", "status", "--porcelain", "-u"])
            for line in status_out.splitlines():
                if len(line) >= 3:
                    status_code = line[:2]
                    filepath = line[3:].strip().replace("\\", "/")
                    if "\t" in filepath:
                        filepath = filepath.split("\t")[0].replace("\\", "/")
                    if filepath.startswith('"') and filepath.endswith('"'):
                        filepath = filepath[1:-1]
                        try:
                            import codecs
                            b, _ = codecs.escape_decode(bytes(filepath, "utf-8"))
                            filepath = b.decode("utf-8").replace("\\", "/")
                        except Exception:
                            pass
                    
                    is_unmerged = status_code in {'DD', 'AA', 'UU', 'AU', 'UD', 'UA', 'DU'}
                    if is_unmerged:
                        is_new = False
                        is_mod = True
                    else:
                        is_new = "?" in status_code or status_code.startswith("A")
                        is_mod = any(c in status_code for c in "MDRTC") and not status_code.startswith("A")
                    
                    if is_new:
                        changed_files[filepath] = "untracked"
                    elif is_mod:
                        changed_files[filepath] = "modified"
        except Exception:
            pass
            
        sw_files = []
        try:
            cmd_args = ["git", "-c", "core.quotepath=false", "ls-files", "-co", "--exclude-standard"]
            ls_out = self._run_lfs_cmd(cmd_args)
            for line in ls_out.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith('"') and line.endswith('"'):
                    line = line[1:-1]
                    try:
                        import codecs
                        b, _ = codecs.escape_decode(bytes(line, "utf-8"))
                        line = b.decode("utf-8")
                    except Exception:
                        pass
                
                rel_path = line.replace("\\", "/")
                ext = os.path.splitext(rel_path)[1].lower()
                if ext in [".sldprt", ".sldasm", ".slddrw"]:
                    if rel_path.lower() in ignored_files:
                        continue
                    
                    full_path = os.path.join(self.repo_path, rel_path)
                    if os.path.exists(full_path):
                        status_desc = 'unmodified'
                        for c_path, c_status in changed_files.items():
                            if c_path.lower() == rel_path.lower():
                                status_desc = c_status
                                break
                        
                        # Case-insensitive lookup in locks
                        lock_info = None
                        for l_path, l_val in locks.items():
                            if l_path.lower() == rel_path.lower():
                                lock_info = l_val
                                break
                        locked = lock_info is not None
                        locked_by = lock_info['owner'] if locked else None
                        is_our_lock = lock_info['is_ours'] if locked else False
                        
                        sw_files.append({
                            'file': rel_path,
                            'type': ext,
                            'status': status_desc,
                            'locked': locked,
                            'locked_by': locked_by,
                            'is_our_lock': is_our_lock
                        })
        except Exception as e:
            print(f"Error querying files with git ls-files: {e}")
                     
        return sw_files

    def get_lfs_locks(self):
        """Retrieves active LFS locks using git lfs locks command line."""
        locks = {}
        if not self.is_git_repo():
            return locks
        if not self.get_remote_url():
            return locks
        try:
            locks_out = self._run_lfs_cmd(["git", "lfs", "locks"])
            if not locks_out:
                return locks
                
            # Get current git user name to check ownership
            current_user = ""
            try:
                current_user = self.repo.config_reader().get_value("user", "name", default="")
            except Exception:
                pass
                
            for line in locks_out.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = re.split(r'\t|\s{2,}', line)
                if len(parts) >= 2:
                    file_path = parts[0].strip().replace("\\", "/")
                    owner_info = parts[1].strip()
                    owner_name = re.sub(r'\s*\(ID:.*$', '', owner_info)
                    
                    is_ours = False
                    if current_user and current_user.lower() in owner_name.lower():
                        is_ours = True
                        
                    locks[file_path] = {
                        'owner': owner_name,
                        'is_ours': is_ours
                    }
        except Exception as e:
            print(f"Error fetching locks: {e}")
        return locks

    def lock_file(self, file_path):
        """Locks a file using git lfs lock."""
        rel_path = self.get_correct_filepath_casing(file_path)
        return self._run_lfs_cmd(["git", "lfs", "lock", rel_path])

    def unlock_file(self, file_path, force=False):
        """Unlocks a file using git lfs unlock."""
        rel_path = self.get_correct_filepath_casing(file_path)
        args = ["git", "lfs", "unlock", rel_path]
        if force:
            args.append("--force")
        return self._run_lfs_cmd(args)

    def sync_pull(self):
        """Sync pull remote repository (performs fetch + rebase via subprocess for robustness)."""
        if not self.get_remote_url():
            return "No remote server configured. Sync skipped."
        branch = self.get_current_branch()
        if not branch:
            raise RuntimeError("Cannot sync/pull because you are not currently on a branch (detached HEAD).")
        res = self._run_lfs_cmd(["git", "pull", "origin", branch, "--rebase"])
        self._load_repo()
        return res

    def save_version(self, file_paths, commit_message):
        """Stages specified files, creates a commit using GitPython, and pushes via subprocess."""
        if not self.is_git_repo():
            raise RuntimeError("Not a git repository.")
            
        if not file_paths:
            raise ValueError("No files selected to save.")
            
        rel_paths = []
        for fp in file_paths:
            rel_path = self.get_correct_filepath_casing(fp)
            rel_paths.append(rel_path)
            
        try:
            self._run_lfs_cmd(["git", "add"] + rel_paths)
        except Exception as e:
            raise RuntimeError(f"Failed to add files to index: {e}")
            
        try:
            name = "SolidWorks Designer"
            email = "designer@example.com"
            try:
                reader = self.repo.config_reader()
                name = reader.get_value("user", "name", default="SolidWorks Designer")
                email = reader.get_value("user", "email", default="designer@example.com")
            except Exception:
                pass
            author = git.Actor(name, email)
            import git.exc
            try:
                self.repo.index.commit(commit_message, author=author, committer=author)
            except git.exc.HookExecutionError as e:
                if "post-commit" in str(e):
                    # post-commit hook failure is ignored since the commit is already created
                    pass
                else:
                    raise
        except Exception as e:
            raise RuntimeError(f"Commit failed: {e}")
            
        if self.get_remote_url():
            try:
                branch = self._run_lfs_cmd(["git", "branch", "--show-current"])
                if not branch:
                    branch = "main"
                self._run_lfs_cmd(["git", "push", "origin", branch])
            except Exception as e:
                raise RuntimeError(f"Successfully saved locally, but server upload failed: {e}")

    def get_history(self):
        """Retrieves ALL commits across every branch (local + remote) sorted by time."""
        history = []
        if not self.is_git_repo():
            return history

        try:
            seen = set()
            revs = []
            for ref in self.repo.references:
                revs.append(ref.path)
            try:
                if self.repo.head.is_valid():
                    revs.append("HEAD")
            except Exception:
                pass
                
            if not revs:
                return history

            # Walk commits topological and date ordered
            for commit in self.repo.iter_commits(rev=revs, topo_order=True):
                hexsha = commit.hexsha
                if hexsha in seen:
                    continue
                seen.add(hexsha)
                commit_date = datetime.datetime.fromtimestamp(commit.committed_date).strftime('%Y-%m-%d %H:%M')
                history.append({
                    'hash': hexsha[:7],
                    'author': commit.author.name if commit.author else "Unknown",
                    'date': commit_date,
                    'message': commit.message.strip() if commit.message else ""
                })
        except Exception as e:
            print(f"Error walking commit logs globally: {e}")
            try:
                history = []
                seen = set()
                if self.repo.head.is_valid():
                    for commit in self.repo.iter_commits(topo_order=True):
                        hexsha = commit.hexsha
                        if hexsha in seen:
                            continue
                        seen.add(hexsha)
                        commit_date = datetime.datetime.fromtimestamp(commit.committed_date).strftime('%Y-%m-%d %H:%M')
                        history.append({
                            'hash': hexsha[:7],
                            'author': commit.author.name if commit.author else "Unknown",
                            'date': commit_date,
                            'message': commit.message.strip() if commit.message else ""
                        })
            except Exception:
                pass
        return history

    def restore_version(self, commit_hash):
        """Restores repository state to a specific commit hash using git checkout -f."""
        if not self.is_git_repo():
            return
        try:
            self._run_lfs_cmd(["git", "checkout", "-f", commit_hash])
            self._load_repo()
        except Exception as e:
            raise RuntimeError(f"Failed to restore version {commit_hash}: {e}")
        
    def restore_latest(self):
        """Restores workspace to the latest commit on the current branch."""
        if not self.is_git_repo():
            return
        try:
            branch = self._run_lfs_cmd(["git", "branch", "--show-current"])
            if not branch:
                branch = "main"
            self._run_lfs_cmd(["git", "checkout", branch])
            self._load_repo()
        except Exception as e:
            raise RuntimeError(f"Failed to restore latest: {e}")

    def get_remote_branches(self):
        """Gets list of branches on the remote repository."""
        if not self.is_git_repo() or not self.get_remote_url():
            return self.get_local_branches()
            
        try:
            out = self._run_lfs_cmd(["git", "ls-remote", "--heads", "origin"])
            branches = []
            for line in out.splitlines():
                match = re.search(r"refs/heads/(.+)$", line)
                if match:
                    branches.append(match.group(1))
            return branches if branches else ["main"]
        except Exception:
            return self.get_local_branches()

    def get_local_branches(self):
        """Gets list of local branches in the repository."""
        if not self.is_git_repo():
            return ["main"]
        try:
            return [b.name for b in self.repo.branches]
        except Exception:
            try:
                out = self._run_lfs_cmd(["git", "branch"])
                branches = []
                for line in out.splitlines():
                    name = line.replace("*", "").strip()
                    if name:
                        branches.append(name)
                return branches
            except Exception:
                return ["main"]

    def get_branch_tip_commit(self, branch_name):
        """Returns the commit hash (hex string) of the tip of the specified branch."""
        if not self.is_git_repo():
            return ""
        try:
            return self.repo.commit(branch_name).hexsha
        except Exception:
            pass
        try:
            out = self._run_lfs_cmd(["git", "rev-parse", branch_name])
            return out.strip()
        except Exception:
            return ""

    def get_current_branch(self):
        """Gets the name of the currently checked out branch."""
        if not self.is_git_repo():
            return ""
        try:
            if self.repo.head.is_detached:
                return ""
            return self.repo.active_branch.name
        except Exception:
            try:
                branch = self._run_lfs_cmd(["git", "branch", "--show-current"])
                return branch if branch else ""
            except Exception:
                return ""

    def get_branches_containing_commit(self, commit_hash):
        """Returns a list of local branch names containing the specified commit hash."""
        if not self.is_git_repo():
            return []
        try:
            out = self._run_lfs_cmd(["git", "branch", "--contains", commit_hash])
            branches = []
            for line in out.splitlines():
                name = line.replace("*", "").strip()
                if name and not name.startswith("("):
                    branches.append(name)
            return branches
        except Exception:
            return []

    def clean_fake_modified_files(self):
        """
        Scans modified files in status, hashes them, and checks if they match the Git index.
        If they match, runs 'git checkout --' to discard the false-positive modification status.
        """
        if not self.is_git_repo():
            return
            
        import hashlib
        
        try:
            status_out = self._run_lfs_cmd(["git", "status", "--porcelain"])
            fake_clean_list = []
            
            for line in status_out.splitlines():
                if len(line) >= 3 and line[0] == ' ' and line[1] == 'M':
                    filepath = line[3:].strip()
                    if filepath.startswith('"') and filepath.endswith('"'):
                         filepath = filepath[1:-1]
                         
                    abs_path = os.path.join(self.repo_path, filepath)
                    if not os.path.exists(abs_path):
                        continue
                        
                    try:
                        with open(abs_path, 'rb') as f:
                            data = f.read()
                        header = f"blob {len(data)}\0".encode('utf-8')
                        sha1 = hashlib.sha1()
                        sha1.update(header)
                        sha1.update(data)
                        local_hash = sha1.hexdigest()
                    except Exception as he:
                        print(f"DEBUG: Failed to hash {filepath}: {he}")
                        continue
                        
                    try:
                        entry = self.repo.index.entries[(filepath, 0)]
                        index_hash = entry.hexsha
                    except Exception as ie:
                        print(f"DEBUG: Failed to get index entry for {filepath}: {ie}")
                        continue
                        
                    if local_hash == index_hash:
                        print(f"DEBUG: Fake modification detected for {filepath}. Index: {index_hash}, Local: {local_hash}")
                        fake_clean_list.append(filepath)
            
            if fake_clean_list:
                print(f"DEBUG: Automatically clearing fake modifications for: {fake_clean_list}")
                for fp in fake_clean_list:
                     try:
                         self._run_lfs_cmd(["git", "checkout", "--", fp])
                     except Exception as ce:
                         print(f"DEBUG: Failed to auto-clear {fp}: {ce}")
                         
        except Exception as e:
            print(f"DEBUG: Error in clean_fake_modified_files: {e}")

    def switch_branch(self, branch_name, force=False):
        """Switches (checkouts) to the specified branch using git command line to ensure LFS filters run."""
        if not self.is_git_repo():
            return
        try:
            if not force:
                self.clean_fake_modified_files()
                
            if self.get_remote_url():
                try:
                    self._run_lfs_cmd(["git", "fetch", "origin"])
                except Exception as fe:
                    print(f"Warning: git fetch origin failed: {fe}")
            cmd = ["git", "checkout"]
            if force:
                cmd.append("-f")
            cmd.append(branch_name)
            self._run_lfs_cmd(cmd)
            self._load_repo()
        except Exception as e:
            raise RuntimeError(f"Failed to switch branch: {e}")

    def checkout_and_reset_branch(self, branch_name, commit_hash):
        """Switches to the specified branch and resets it hard to the target commit hash."""
        if not self.is_git_repo():
            return
        try:
            self._run_lfs_cmd(["git", "checkout", "-f", branch_name])
            self._run_lfs_cmd(["git", "reset", "--hard", commit_hash])
            self._load_repo()
        except Exception as e:
            raise RuntimeError(f"Failed to checkout and reset branch {branch_name} to {commit_hash}: {e}")

    def discard_changes(self, file_paths):
        """Discards local modifications to the specified files."""
        if not self.is_git_repo():
            raise RuntimeError("Not a git repository.")
        if not file_paths:
            return
            
        for fp in file_paths:
            rel_path = self.get_correct_filepath_casing(fp)
            abs_path = os.path.join(self.repo_path, rel_path)
            
            is_tracked = False
            try:
                is_tracked = (rel_path, 0) in self.repo.index.entries
            except Exception:
                pass
                
            if is_tracked:
                self._run_lfs_cmd(["git", "checkout", "--", rel_path])
            else:
                if os.path.exists(abs_path):
                    try:
                        os.remove(abs_path)
                    except Exception as e:
                        raise RuntimeError(f"Failed to delete untracked file {rel_path}: {e}")
        self._load_repo()

    def get_current_commit_hash(self):
        """Gets the short 7-character hash of the current checked-out commit."""
        if not self.is_git_repo():
            return ""
        try:
            return self.repo.head.commit.hexsha[:7]
        except Exception:
            return ""

    def merge_branch(self, source_branch):
        """Merges the specified source branch into the current branch using git CLI."""
        if not self.is_git_repo():
            raise RuntimeError("Not a git repository.")
        current = self.get_current_branch()
        if current == source_branch:
            raise ValueError(f"Already on branch '{source_branch}'. Nothing to merge.")
        try:
            result = self._run_lfs_cmd(["git", "merge", source_branch, "--no-edit"])
            self._load_repo()
            return result
        except Exception as e:
            err_str = str(e)
            conflicted = self.get_merge_conflicts()
            if conflicted or "conflict" in err_str.lower() or "pointer" in err_str.lower():
                lfs_files = parse_lfs_pointer_errors(err_str)
                resolved_conflicts = conflicted if conflicted else lfs_files
                if not resolved_conflicts:
                    resolved_conflicts = [err_str]
                raise MergeConflictError(
                    f"Merge conflict occurred while merging branch '{source_branch}'.",
                    resolved_conflicts
                )
            raise RuntimeError(f"Merge failed: {err_str}")

    def get_merge_conflicts(self):
        """Returns a list of file paths that currently have merge conflicts."""
        try:
            return list(self.repo.index.unmerged_blobs().keys())
        except Exception:
            try:
                result = self._run_lfs_cmd(
                    ["git", "diff", "--name-only", "--diff-filter=U"],
                    check=False
                )
                files = [f.strip() for f in result.strip().splitlines() if f.strip()]
                if files:
                    return files
            except Exception:
                pass
            return []

    def resolve_merge_conflicts(self, strategy, files):
        """Resolves conflicts by choosing one side for all conflicted files."""
        if not files:
            return ""
        for f in files:
            self._run_lfs_cmd(["git", "checkout", f"--{strategy}", "--", f])
            self._run_lfs_cmd(["git", "add", f])
        result = self._run_lfs_cmd(["git", "commit", "--no-edit"])
        self._load_repo()
        return result

    def resolve_conflicts_and_commit(self, source_branch, resolutions):
        """Resolves existing merge conflicts using the given resolutions dictionary and commits to finalize the merge."""
        if not self.is_git_repo():
            raise RuntimeError("Not a git repository.")
        try:
            conflicts = self.get_merge_conflicts()
            if conflicts:
                for f in conflicts:
                    res = resolutions.get(f, "ours")
                    if res == "ours":
                        self._run_lfs_cmd(["git", "checkout", "--ours", "--", f])
                    else:
                        self._run_lfs_cmd(["git", "checkout", "--theirs", "--", f])
                    self._run_lfs_cmd(["git", "add", f])
                
                self._run_lfs_cmd(["git", "commit", "-m", f"Merge branch '{source_branch}' (resolved conflicts)"])
            self._load_repo()
        except Exception as e:
            self.abort_merge()
            raise RuntimeError(f"Conflict resolution/commit failed: {e}")

    def abort_merge(self):
        """Aborts an in-progress merge and restores the pre-merge state."""
        try:
            self._run_lfs_cmd(["git", "merge", "--abort"])
        except Exception:
            pass
        self._load_repo()

    def check_merge_conflicts(self, target_ref):
        """Returns a list of conflicted files if target_ref were to be merged into HEAD.
        Returns empty list if there are no conflicts.
        """
        if not self.is_git_repo():
            return []
        
        cmd_args = ["git", "merge-tree", "--write-tree", "--name-only", "HEAD", target_ref]
        if self.git_path and os.path.exists(self.git_path):
            cmd_args[0] = self.git_path
            
        try:
            returncode, stdout, stderr = run_git_subprocess(cmd_args, self.repo_path, check=False)
            # If exit code is 0, no conflicts
            if returncode == 0:
                return []
                
            # If exit code is non-zero, let's parse stdout
            lines = stdout.strip().split("\n")
            if len(lines) <= 1:
                return []
                
            conflicted_files = []
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    break
                if line.startswith("Auto-merging") or line.startswith("CONFLICT"):
                    break
                conflicted_files.append(line)
            return conflicted_files
        except Exception:
            return []

    def merge_branch_with_resolutions(self, source_branch, resolutions):
        """Merges specified source branch and resolves conflicts using the given resolutions dictionary."""
        if not self.is_git_repo():
            raise RuntimeError("Not a git repository.")
        try:
            # 1. Start merge (use check=False to let it go into conflict state)
            self._run_lfs_cmd(["git", "merge", source_branch, "--no-edit"], check=False)
            
            # 2. Check if we actually have conflicts in the repo
            conflicts = self.get_merge_conflicts()
            if conflicts:
                for f in conflicts:
                    res = resolutions.get(f, "ours") # default to ours
                    if res == "ours":
                        self._run_lfs_cmd(["git", "checkout", "--ours", "--", f])
                    else:
                        self._run_lfs_cmd(["git", "checkout", "--theirs", "--", f])
                    self._run_lfs_cmd(["git", "add", f])
                
                # 3. Finalize merge commit
                self._run_lfs_cmd(["git", "commit", "-m", f"Merge branch '{source_branch}' (resolved conflicts)"])
            
            self._load_repo()
        except Exception as e:
            # Abort merge if anything failed
            self.abort_merge()
            raise RuntimeError(f"Merge with resolutions failed: {e}")

    def sync_pull_clean(self):
        """Fetches and merges origin/<branch> assuming no conflicts."""
        if not self.get_remote_url():
            return "No remote server configured. Sync skipped."
        branch = self.get_current_branch()
        if not branch:
            raise RuntimeError("Cannot sync/pull because you are not currently on a branch (detached HEAD).")
            
        self._run_lfs_cmd(["git", "fetch", "origin"])
        try:
            res = self._run_lfs_cmd(["git", "merge", f"origin/{branch}", "--no-edit"])
            self._load_repo()
            return res
        except Exception as e:
            err_str = str(e)
            conflicted = self.get_merge_conflicts()
            if conflicted or "conflict" in err_str.lower() or "pointer" in err_str.lower():
                lfs_files = parse_lfs_pointer_errors(err_str)
                resolved_conflicts = conflicted if conflicted else lfs_files
                if not resolved_conflicts:
                    resolved_conflicts = [err_str]
                raise MergeConflictError(
                    f"Merge conflict occurred while pulling origin/{branch}.",
                    resolved_conflicts
                )
            raise RuntimeError(f"Sync pull failed: {err_str}")

    def sync_pull_with_resolutions(self, resolutions):
        """Fetches and merges origin/<branch> resolving conflicts with the given resolutions."""
        if not self.get_remote_url():
            return "No remote server configured. Sync skipped."
        branch = self.get_current_branch()
        if not branch:
            raise RuntimeError("Cannot sync/pull because you are not currently on a branch (detached HEAD).")
            
        self._run_lfs_cmd(["git", "fetch", "origin"])
        self.merge_branch_with_resolutions(f"origin/{branch}", resolutions)
        return "Sync complete."

    def optimize_credential_helper(self, username):
        """
        Cleans the remote URL by removing any plaintext tokens, and configures
        local git config credential.username and credential.https://<domain>.username
        to prevent Git Credential Manager from showing account picker popups.
        """
        if not self.is_git_repo():
            return

        # 1. Clean remote URL of any embedded tokens (username/password)
        try:
            remote_url = self.get_remote_url()
            if remote_url:
                match = re.match(r'^(https?://)([^@]+)@(.*)$', remote_url, re.IGNORECASE)
                if match:
                    prefix, creds, rest = match.groups()
                    cleaned_url = f"{prefix}{rest}"
                    self.set_remote(cleaned_url)
                    print(f"DEBUG: Cleaned remote URL from {remote_url} to {cleaned_url}")
        except Exception as e:
            print(f"DEBUG: Failed to clean remote URL: {e}")

        # 2. Configure local git credentials for the domain
        if username:
            remote_url = self.get_remote_url()
            if remote_url:
                domain = None
                if remote_url.startswith("https://") or remote_url.startswith("http://"):
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(remote_url)
                        domain = parsed.netloc
                        if "@" in domain:
                            domain = domain.split("@")[-1]
                        if ":" in domain:
                            domain = domain.split(":")[0]
                    except Exception:
                        pass
                elif "@" in remote_url and ":" in remote_url:
                    try:
                        domain = remote_url.split("@")[-1].split(":")[0]
                    except Exception:
                        pass
                
                if not domain and "github.com" in remote_url.lower():
                    domain = "github.com"
                    
                if domain:
                    try:
                        self._run_lfs_cmd(["git", "config", f"credential.https://{domain}.username", username])
                        self._run_lfs_cmd(["git", "config", "credential.username", username])
                        print(f"DEBUG: Configured local credential.https://{domain}.username to {username}")
                    except Exception as e:
                        print(f"DEBUG: Failed to configure local git credentials: {e}")

