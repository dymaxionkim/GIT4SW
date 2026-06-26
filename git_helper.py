import os
import sys

def main():
    if len(sys.argv) < 2 or sys.argv[1] != 'get':
        return
    
    allowed_hosts = set()
    hosts_env = os.environ.get('GIT4SW_GIT_HOSTS', '').strip()
    if hosts_env:
        for h in hosts_env.split(','):
            h = h.strip()
            if h:
                allowed_hosts.add(h.lower())
    if not allowed_hosts:
        allowed_hosts.add('github.com')
    
    matched = False
    for line in sys.stdin:
        stripped = line.strip()
        if stripped.startswith('host='):
            host_val = stripped.split('=', 1)[1].strip().lower()
            if host_val in allowed_hosts:
                matched = True
                break
    
    if matched:
        token = os.environ.get('GIT4SW_TOKEN', '').strip()
        if token:
            print("username=x-access-token")
            print(f"password={token}")

if __name__ == '__main__':
    main()
