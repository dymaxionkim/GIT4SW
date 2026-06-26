import sys
import os
import argparse
from ui_tk import GIT4SWApp
import git_service

def main():
    parser = argparse.ArgumentParser(description="GIT4SW - SolidWorks Git Integration")
    parser.add_argument("--config", default="config.json", help="Path to config.json file (default: config.json)")
    parser.add_argument("workspace", nargs="?", help="Workspace directory (overrides config)")
    args = parser.parse_args()

    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.abspath(config_path)

    # Set config file path in git_service module for global functions
    git_service.set_config_file_path(config_path)

    # Try loading workspace from config file first
    workspace = None
    if os.path.exists(config_path):
        try:
            import json
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                config_ws = config.get("workspace_path", "")
                if config_ws and os.path.isdir(config_ws):
                    workspace = config_ws
        except Exception:
            pass

    if not workspace:
        workspace = os.getcwd()
        
    # Command line workspace argument overrides config
    if args.workspace:
        if os.path.isdir(args.workspace):
            workspace = args.workspace
            
    app = GIT4SWApp(workspace, config_path)
    app.mainloop()

if __name__ == "__main__":
    main()


