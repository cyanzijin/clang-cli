import os
import sys
import time
import argparse
import tempfile
import subprocess


class Process(object):
    """ Manages an process in flight """
    def __init__(self, proc, outfile):
        self.proc = proc
        self.outfile = outfile
        self.output = None

    def poll(self):
        """ Return the exit code if the process has completed, None otherwise.
        """
        return self.proc.poll()

    @property
    def returncode(self):
        return self.proc.returncode

    def get_output(self):
        """ Return stdout+stderr output of the process.

        This call blocks until the process is complete, then returns the output.
        """
        if not self.output:
            self.proc.wait()
            self.outfile.seek(0)
            self.output = self.outfile.read().decode("utf-8")
            self.outfile.close()

        return self.output

    @classmethod
    def start(cls, invocation):
        """ Start a Process for the invocation and capture stdout+stderr. """
        outfile = tempfile.TemporaryFile(prefix='tidy')
        process = subprocess.Popen(
            invocation.command,
            stdout=outfile,
            stderr=subprocess.STDOUT)
        process.poll()
        return cls(process, outfile)


class Invocation(object):
    """ clang-tidy invocation. """
    def __init__(self, command):
        self.command = command

    def __str__(self):
        return ' '.join(self.command)

    @classmethod
    def get_command(cls, tidy_executable, file_path):
        """ Parse a JSON compilation database entry into new Invocation. """
        command = [tidy_executable, file_path]
        return cls(command)

    def start(self, verbose):
        """ Run invocation and collect output. """
        if verbose:
            print('# %s' % self, file=sys.stderr)

        return Process.start(self)


def worst_exit_code(worst, cur):
    """Return the most extreme exit code of two.

    Negative exit codes occur if the program exits due to a signal (Unix) or
    structured exception (Windows). If we've seen a negative one before, keep
    it, as it usually indicates a critical error.

    Otherwise return the biggest positive exit code.
    """
    if cur < 0:
        # Negative results take precedence, return the minimum
        return min(worst, cur)
    elif worst < 0:
        # We know cur is non-negative, negative worst must be minimum
        return worst
    else:
        # We know neither are negative, return the maximum
        return max(worst, cur)


def execute(invocations, verbose, jobs, max_load_average=0):
    """ Launch processes described by invocations. """
    exit_code = 0
    if jobs == 1:
        for invocation in invocations:
            proc = invocation.start(verbose)
            print(proc.get_output())
            exit_code = worst_exit_code(exit_code, proc.returncode)
        return exit_code

    pending = []
    while invocations or pending:
        # Collect completed tidy processes and print results.
        complete = [proc for proc in pending if proc.poll() is not None]
        for proc in complete:
            pending.remove(proc)
            print(proc.get_output())
            exit_code = worst_exit_code(exit_code, proc.returncode)

        # Schedule new processes if there's room.
        capacity = jobs - len(pending)

        # if max_load_average > 0:
        #     one_min_load_average, _, _ = os.getloadavg()
        #     load_capacity = max_load_average - one_min_load_average
        #     if load_capacity < 0:
        #         load_capacity = 0
        #     if load_capacity < capacity:
        #         capacity = int(load_capacity)
        #         if not capacity and not pending:
        #             # Ensure there is at least one job running.
        #             capacity = 1

        pending.extend(i.start(verbose) for i in invocations[:capacity])
        invocations = invocations[capacity:]

        # Yield CPU.
        time.sleep(0.0001)
    return exit_code


def main(source_path, tidy_executable, verbose, jobs,
         max_load_average, extra_args):
    """ Entry point. """

    if not tidy_executable:
        print('error: clang-tidy executable not found',
              file=sys.stderr)
        return 1
    
    scan_files = []

    for root, _, files in os.walk(source_path):
        for file in files:
            if file.endswith('.c') or file.endswith('.cc') or file.endswith('.cpp'):
                scan_files.append(os.path.join(root, file))

    invocations = [
        Invocation.get_command(tidy_executable, f) for f in scan_files
    ]

    return execute(invocations, verbose, jobs, max_load_average)


def _bootstrap(sys_argv):
    """ Parse arguments and dispatch to main(). """

    # Parse arguments.
    parser = argparse.ArgumentParser()
    
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Print tidy commands')
    parser.add_argument('-b', '--binary', type=str, required=True,
                        default="", help='clang-tidy-binary')
    parser.add_argument('-j', '--jobs', type=int, default=1)
    parser.add_argument('-l', '--load', type=float, default=0,
                        help=('Do not start new jobs if the 1min load average '
                              'is greater than the provided value'))
    parser.add_argument('-p', metavar='<build-path>', required=True,
                        help='source path', dest='dbpath')

    def partition_args(argv):
        """ Split around '--' into args. """
        try:
            double_dash = argv.index('--')
            return argv[:double_dash], argv[double_dash+1:]
        except ValueError:
            return argv, []
    argv, extra_args = partition_args(sys_argv[1:])
    args = parser.parse_args(argv)

    return main(args.dbpath, args.binary, args.verbose,
                args.jobs, args.load, extra_args)


if __name__ == '__main__':
    sys.exit(_bootstrap(sys.argv))