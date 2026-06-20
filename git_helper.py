import os
import sys

def main():
    if len(sys.argv) < 2 or sys.argv[1] != 'get':
        return
    
    is_github = False
    for line in sys.stdin:
        if line.strip().startswith('host=github.com'):
            is_github = True
            
    if is_github:
        token = os.environ.get('GIT4SW_TOKEN', '').strip()
        if token:
            print("username=x-access-token")
            print(f"password={token}")

if __name__ == '__main__':
    main()
