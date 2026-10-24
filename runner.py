from playlite_plugins.cracktools.jobs import PRIVATE as PRIVATE, write_json as write_json, main as run_main
def process(request, cancel=lambda:False, status=lambda text:None):
    if request.get('Backend')=='vm':
        from .vm_backend import process as run
    else:
        from .container_backend import process as run
    return run(request,cancel,status)

def main():
    return run_main(process)

if __name__ == '__main__':
    import sys
    sys.exit(main())
