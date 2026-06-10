import sys
import os
from ui_tk import GIT4SWApp

def main():
    # Try loading workspace from config.json first
    workspace = None
    config_path = "config.json"
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
        
    # Command line argument overrides config.json
    if len(sys.argv) > 1:
        if os.path.isdir(sys.argv[1]):
            workspace = sys.argv[1]
            
    app = GIT4SWApp(workspace)
    app.mainloop()

if __name__ == "__main__":
    main()


