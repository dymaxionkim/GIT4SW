import os
import sys

def main():
    if len(sys.argv) < 2 or sys.argv[1] != 'get':
        return
    
    allowed_hosts = set()
    hosts_env = os.environ.get('GIT4SW_GIT_HOSTS', '').strip()
    if hosts_env:
        for h in hosts_env.split(','):
            h = h.strip().lower()
            if h:
                allowed_hosts.add(h)
                if ':' in h:
                    allowed_hosts.add(h.split(':', 1)[0])
    if not allowed_hosts:
        allowed_hosts.add('github.com')
    
    matched = False
    for line in sys.stdin:
        stripped = line.strip()
        if stripped.startswith('host='):
            host_val = stripped.split('=', 1)[1].strip().lower()
            host_no_port = host_val.split(':', 1)[0]
            if host_val in allowed_hosts or host_no_port in allowed_hosts:
                matched = True
                break
    
    if matched:
        token = os.environ.get('GIT4SW_TOKEN', '').strip()
        if token:
            print("username=x-access-token")
            print(f"password={token}")

if __name__ == '__main__':
    main()
