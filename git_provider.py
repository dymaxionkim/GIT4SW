import os
import re
import json
import urllib.request
import urllib.error
from abc import ABC, abstractmethod


def parse_remote_url(remote_url):
    if not remote_url:
        return None, None
    url = remote_url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if url.endswith("/"):
        url = url[:-1]
    if ":" in url and not url.startswith("http"):
        parts = url.split(":")
        url = "/".join(parts)
    path_parts = [p for p in url.split("/") if p]
    if len(path_parts) >= 2:
        return path_parts[-2], path_parts[-1]
    return None, None


def load_config_for_token(config_paths=None):
    token = ""
    if config_paths is None:
        config_paths = ["config.json"]
    for cp in config_paths:
        if os.path.exists(cp):
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                token = cfg.get("git_token", "") or cfg.get("github_token", "")
                if token:
                    return token.strip()
            except Exception:
                pass
    return ""


class GitProvider(ABC):

    @abstractmethod
    def get_name(self):
        pass

    @abstractmethod
    def handles_url(self, remote_url):
        pass

    @abstractmethod
    def get_credential_host(self, remote_url=None):
        pass

    @abstractmethod
    def check_token_access(self, token, remote_url):
        pass

    @abstractmethod
    def get_username(self, token, remote_url=None):
        pass

    @abstractmethod
    def get_network_graph_url(self, remote_url):
        pass

    @abstractmethod
    def create_repository(self, token, name, organization, private):
        pass

    @abstractmethod
    def create_branch(self, token, remote_url, branch_name, sha):
        pass

    @abstractmethod
    def get_api_repo_url(self, remote_url):
        pass


class GitHubProvider(GitProvider):

    def get_name(self):
        return "github"

    def handles_url(self, remote_url):
        return "github.com" in remote_url.lower() if remote_url else False

    def get_credential_host(self, remote_url=None):
        return "github.com"

    def check_token_access(self, token, remote_url):
        if not token or not remote_url or not self.handles_url(remote_url):
            return False
        owner, repo = parse_remote_url(remote_url)
        if not owner or not repo:
            return False
        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        try:
            req = urllib.request.Request(
                api_url,
                headers={"Authorization": f"token {token}", "User-Agent": "GIT4SW-App"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as response:
                return response.status == 200
        except Exception:
            return False

    def get_username(self, token, remote_url=None):
        if not token:
            return None
        url = "https://api.github.com/user"
        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"token {token}", "User-Agent": "GIT4SW-App"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    return data.get("login")
        except Exception:
            pass
        return None

    def get_network_graph_url(self, remote_url):
        owner, repo = parse_remote_url(remote_url)
        if not owner or not repo:
            return None
        return f"https://github.com/{owner}/{repo}/network"

    def create_repository(self, token, name, organization, private):
        from github import Github
        g = Github(token)
        user = g.get_user()
        if organization:
            org = g.get_organization(organization)
            gh_repo = org.create_repo(name, private=private)
        else:
            gh_repo = user.create_repo(name, private=private)
        return gh_repo.clone_url, gh_repo.html_url

    def create_branch(self, token, remote_url, branch_name, sha):
        from github import Github, GithubException
        owner, repo = parse_remote_url(remote_url)
        if not owner or not repo:
            raise RuntimeError(f"Cannot parse owner/repo from {remote_url}")
        g = Github(token)
        gh_repo = g.get_repo(f"{owner}/{repo}")
        try:
            gh_repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=sha)
        except GithubException as ge:
            if ge.status == 422:
                pass
            else:
                raise
        return True

    def get_api_repo_url(self, remote_url):
        owner, repo = parse_remote_url(remote_url)
        if not owner or not repo:
            return None
        return f"https://api.github.com/repos/{owner}/{repo}"


class GiteaProvider(GitProvider):

    def __init__(self, gitea_url):
        self.gitea_url = gitea_url.rstrip("/") if gitea_url else ""

    def get_name(self):
        return "gitea"

    def handles_url(self, remote_url):
        if not remote_url or not self.gitea_url:
            return False
        return remote_url.lower().startswith(self.gitea_url.lower())

    def get_credential_host(self, remote_url=None):
        if remote_url and self.handles_url(remote_url):
            from urllib.parse import urlparse
            parsed = urlparse(remote_url)
            host = parsed.netloc or parsed.path.split("/")[0]
            if "@" in host:
                host = host.split("@")[-1]
            if ":" in host:
                host = host.split(":")[0]
            return host
        if self.gitea_url:
            from urllib.parse import urlparse
            parsed = urlparse(self.gitea_url)
            return parsed.netloc
        return ""

    def _api_base(self):
        return f"{self.gitea_url}/api/v1"

    def check_token_access(self, token, remote_url):
        if not token or not remote_url or not self.handles_url(remote_url):
            return False
        owner, repo = parse_remote_url(remote_url)
        if not owner or not repo:
            return False
        api_url = f"{self._api_base()}/repos/{owner}/{repo}"
        try:
            req = urllib.request.Request(
                api_url,
                headers={"Authorization": f"token {token}", "User-Agent": "GIT4SW-App"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as response:
                return response.status == 200
        except Exception:
            return False

    def get_username(self, token, remote_url=None):
        if not token:
            return None
        url = f"{self._api_base()}/user"
        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"token {token}", "User-Agent": "GIT4SW-App"},
            )
            with urllib.request.urlopen(req, timeout=2.0) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    return data.get("username") or data.get("login")
        except Exception:
            pass
        return None

    def get_network_graph_url(self, remote_url):
        owner, repo = parse_remote_url(remote_url)
        if not owner or not repo:
            return None
        return f"{self.gitea_url}/{owner}/{repo}/network"

    def create_repository(self, token, name, organization, private):
        import urllib.request
        if organization:
            api_url = f"{self._api_base()}/orgs/{organization}/repos"
        else:
            api_url = f"{self._api_base()}/user/repos"
        data_dict = {
            "name": name,
            "private": private,
            "auto_init": True,
        }
        data_bytes = json.dumps(data_dict).encode("utf-8")
        req = urllib.request.Request(
            api_url,
            data=data_bytes,
            headers={
                "Authorization": f"token {token}",
                "Content-Type": "application/json",
                "User-Agent": "GIT4SW-App",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                clone_url = result.get("clone_url", "")
                html_url = result.get("html_url", "")
                return clone_url, html_url
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Gitea create repo failed ({e.code}): {body}")
        except Exception as e:
            raise RuntimeError(f"Gitea create repo failed: {e}")

    def create_branch(self, token, remote_url, branch_name, sha):
        import urllib.request
        owner, repo_n = parse_remote_url(remote_url)
        if not owner or not repo_n:
            raise RuntimeError(f"Cannot parse owner/repo from {remote_url}")
        api_url = f"{self._api_base()}/repos/{owner}/{repo_n}/git/refs"
        data_bytes = json.dumps({"ref": f"refs/heads/{branch_name}", "sha": sha}).encode("utf-8")
        req = urllib.request.Request(
            api_url,
            data=data_bytes,
            headers={
                "Authorization": f"token {token}",
                "Content-Type": "application/json",
                "User-Agent": "GIT4SW-App",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                return resp.status == 201
        except urllib.error.HTTPError as e:
            if e.code == 409:
                return False
            body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Gitea create branch failed ({e.code}): {body}")
        except Exception as e:
            raise RuntimeError(f"Gitea create branch failed: {e}")

    def get_api_repo_url(self, remote_url):
        owner, repo = parse_remote_url(remote_url)
        if not owner or not repo:
            return None
        return f"{self._api_base()}/repos/{owner}/{repo}"


def create_provider(config):
    server_type = config.get("git_server_type", "github")
    if server_type == "gitea":
        gitea_url = config.get("gitea_url", "")
        return GiteaProvider(gitea_url)
    return GitHubProvider()


def detect_provider(remote_url, config=None):
    if not remote_url:
        return GitHubProvider()
    gh = GitHubProvider()
    if gh.handles_url(remote_url):
        return gh
    if config:
        gitea_url = config.get("gitea_url", "")
        if gitea_url:
            git = GiteaProvider(gitea_url)
            if git.handles_url(remote_url):
                return git
    server_type = config.get("git_server_type", "github") if config else "github"
    if server_type == "gitea":
        gitea_url = config.get("gitea_url", "") if config else ""
        return GiteaProvider(gitea_url)
    return GitHubProvider()
