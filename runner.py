from playlite_plugins.cracktools.jobs import PRIVATE as PRIVATE, write_json as write_json, main as run_main
from .vm_backend import process

def main():
    return run_main(process)

if __name__ == '__main__':
    import sys
    sys.exit(main())
