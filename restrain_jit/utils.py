import os
import sys


class MissingDict(dict):

    def __init__(self, fact):
        dict.__init__(self)
        self.fact = fact

    def __missing__(self, key):
        val = self[key] = self.fact()
        return val


class CodeOut(dict):

    def __missing__(self, key):
        v = self[key] = []
        return v

    def to_code_lines(self):
        for _, v in sorted(self.items(), key=lambda x: x[0]):
            yield from v


def exec_cc(cmd, args):
    """
    Execute with current context.
    Yes, you're right -- I'm naming it after call/cc.

    Return a generator.
        The first yielded one is the status of
        the execution of subprocess command.

        The following ones are the the buffer batches of stderr,
        each of which is a Python 'bytes' object
    """
    file = cmd
    err_in, err_out = os.pipe()
    if os.fork():
        _, status = os.wait()
        os.close(err_out)
        yield status
        while True:
            load = os.read(err_in, 1024)
            if not load:
                break
            yield load
    else:
        # for child process
        os.close(err_in)
        os.dup2(err_out, sys.stderr.fileno())
        os.execvpe(file, [cmd, *args], dict(os.environ))
        # in case that os.execvp fails
        sys.exit(127)
