import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config, tools  # noqa: E402
from src.tools import ToolBox, ToolError, parse_arguments  # noqa: E402


class ToolTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="madsloop_test_")
        self.box = ToolBox(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, rel, text):
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", newline="") as fh:
            fh.write(text)
        return p

    def read(self, rel):
        with open(os.path.join(self.dir, rel), newline="") as fh:
            return fh.read()


class TestParseArguments(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_arguments("bash", '{"command": "ls"}'), {"command": "ls"})

    def test_invalid_json(self):
        with self.assertRaises(ToolError) as cm:
            parse_arguments("bash", '{"command": "ls"')
        self.assertIn("Invalid JSON", str(cm.exception))

    def test_not_object(self):
        with self.assertRaises(ToolError):
            parse_arguments("bash", '["ls"]')

    def test_missing_required(self):
        with self.assertRaises(ToolError) as cm:
            parse_arguments("edit_file", '{"path": "a"}')
        self.assertIn("old_string", str(cm.exception))

    def test_integer_coercion(self):
        args = parse_arguments("read_file", '{"path": "a", "start_line": "5", "end_line": 9.0}')
        self.assertEqual((args["start_line"], args["end_line"]), (5, 9))

    def test_bad_type(self):
        with self.assertRaises(ToolError):
            parse_arguments("bash", '{"command": 3}')
        with self.assertRaises(ToolError):
            parse_arguments("read_file", '{"path": "a", "start_line": "x"}')

    def test_empty_args(self):
        self.assertEqual(parse_arguments("unknown_tool", ""), {})


class TestBash(ToolTestBase):
    def test_exit_code_and_cwd(self):
        text, err = self.box.execute("bash", {"command": "pwd; echo oops >&2; exit 3"})
        self.assertFalse(err)
        self.assertTrue(text.startswith("exit code: 3\n"))
        self.assertIn(os.path.realpath(self.dir), text)
        self.assertIn("oops", text)

    def test_stdin_is_closed(self):
        start = time.time()
        text, err = self.box.execute("bash", {"command": "cat; echo after", "timeout": 10})
        self.assertFalse(err)
        self.assertIn("after", text)
        self.assertLess(time.time() - start, 5)

    def test_timeout_kills_process_group(self):
        marker = os.path.join(self.dir, "child_pid")
        cmd = "sleep 100 & echo $! > %s; sleep 100" % marker
        start = time.time()
        text, err = self.box.execute("bash", {"command": cmd, "timeout": 1})
        self.assertTrue(err)
        self.assertIn("timed out after 1s", text)
        self.assertLess(time.time() - start, 10)
        with open(marker) as fh:
            pid = int(fh.read().strip())
        time.sleep(0.2)
        self.assertFalse(_alive(pid), "background child survived the timeout")

    def test_timeout_is_capped(self):
        # A huge timeout is clamped rather than rejected.
        text, err = self.box.execute("bash", {"command": "echo hi", "timeout": 99999})
        self.assertFalse(err)
        self.assertIn("hi", text)

    def test_env_has_no_secrets_or_harness_deps(self):
        old = dict(os.environ)
        try:
            os.environ["CS2680_API_KEY"] = "secret-1"
            os.environ["MADSLOOP_API_KEY"] = "secret-2"
            os.environ["PYTHONPATH"] = config.HARNESS_DEPS_DIR + os.pathsep + "/keep/me"
            text, _ = self.box.execute("bash", {"command": "env"})
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertNotIn("secret-1", text)
        self.assertNotIn("secret-2", text)
        self.assertIn("PYTHONPATH=/keep/me", text)
        self.assertNotIn(config.HARNESS_DEPS_DIR, text)
        self.assertIn("PAGER=cat", text)
        self.assertIn("GIT_PAGER=cat", text)

    def test_pythonpath_removed_when_only_deps(self):
        env = tools.bash_env({"PYTHONPATH": config.HARNESS_DEPS_DIR + "/"})
        self.assertNotIn("PYTHONPATH", env)

    def test_empty_command(self):
        _, err = self.box.execute("bash", {"command": "  "})
        self.assertTrue(err)


class TestReadFile(ToolTestBase):
    def test_numbers_and_range(self):
        self.write("a.py", "".join("line%d\n" % i for i in range(1, 11)))
        text, err = self.box.execute("read_file", {"path": "a.py", "start_line": 3, "end_line": 4})
        self.assertFalse(err)
        self.assertIn("lines 3-4 of 10", text)
        self.assertIn("     3\tline3", text)
        self.assertIn("     4\tline4", text)
        self.assertNotIn("line5", text)
        self.assertIn("Use start_line/end_line", text)

    def test_default_cap(self):
        n = config.READ_DEFAULT_MAX_LINES + 50
        self.write("big.txt", "".join("x%d\n" % i for i in range(1, n + 1)))
        text, err = self.box.execute("read_file", {"path": "big.txt"})
        self.assertFalse(err)
        self.assertIn("of %d" % n, text)
        self.assertIn("x%d" % config.READ_DEFAULT_MAX_LINES, text)
        self.assertNotIn("x%d\n" % (config.READ_DEFAULT_MAX_LINES + 1), text)

    def test_missing_and_directory(self):
        self.assertTrue(self.box.execute("read_file", {"path": "nope.py"})[1])
        os.makedirs(os.path.join(self.dir, "pkg"))
        self.assertTrue(self.box.execute("read_file", {"path": "pkg"})[1])

    def test_start_past_end(self):
        self.write("a.py", "one\n")
        self.assertTrue(self.box.execute("read_file", {"path": "a.py", "start_line": 5})[1])

    def test_outside_workdir_rejected(self):
        text, err = self.box.execute("read_file", {"path": "/etc/passwd"})
        self.assertTrue(err)
        self.assertIn("outside", text)
        text, err = self.box.execute("read_file", {"path": "../../etc/passwd"})
        self.assertTrue(err)

    def test_tmp_allowed(self):
        fd, p = tempfile.mkstemp(dir="/tmp", suffix=".py")
        try:
            os.write(fd, b"print(1)\n")
            os.close(fd)
            text, err = self.box.execute("read_file", {"path": p})
            self.assertFalse(err, text)
        finally:
            os.unlink(p)


class TestEditFile(ToolTestBase):
    def test_unique_replace(self):
        self.write("a.py", "def f():\n    return 1\n")
        text, err = self.box.execute("edit_file", {"path": "a.py", "old_string": "return 1", "new_string": "return 2"})
        self.assertFalse(err, text)
        self.assertEqual(self.read("a.py"), "def f():\n    return 2\n")
        self.assertIn("return 2", text)

    def test_not_found(self):
        self.write("a.py", "def f():\n    return 1\n")
        text, err = self.box.execute("edit_file", {"path": "a.py", "old_string": "return 3", "new_string": "x"})
        self.assertTrue(err)
        self.assertIn("not found", text)
        self.assertEqual(self.read("a.py"), "def f():\n    return 1\n")

    def test_not_found_indentation_hint(self):
        self.write("a.py", "def f():\n    return 1\n")
        text, err = self.box.execute("edit_file", {"path": "a.py", "old_string": "\treturn 1", "new_string": "x"})
        self.assertTrue(err)
        self.assertIn("line(s) 2", text)

    def test_line_number_prefix_hint(self):
        self.write("a.py", "def f():\n    return 1\n")
        text, err = self.box.execute("edit_file", {"path": "a.py", "old_string": "     2\t    return 1", "new_string": "x"})
        self.assertTrue(err)
        self.assertIn("line-number", text)

    def test_multiple_matches(self):
        self.write("a.py", "x = 1\ny = 2\nx = 1\n")
        text, err = self.box.execute("edit_file", {"path": "a.py", "old_string": "x = 1", "new_string": "x = 9"})
        self.assertTrue(err)
        self.assertIn("2 times", text)
        self.assertIn("lines 1, 3", text)
        self.assertEqual(self.read("a.py"), "x = 1\ny = 2\nx = 1\n")

    def test_crlf_preserved(self):
        self.write("w.txt", "a\r\nb\r\nc\r\n")
        text, err = self.box.execute("edit_file", {"path": "w.txt", "old_string": "a\nb", "new_string": "A\nB"})
        self.assertFalse(err, text)
        self.assertEqual(self.read("w.txt"), "A\r\nB\r\nc\r\n")

    def test_empty_old_and_identical(self):
        self.write("a.py", "x\n")
        self.assertTrue(self.box.execute("edit_file", {"path": "a.py", "old_string": "", "new_string": "y"})[1])
        self.assertTrue(self.box.execute("edit_file", {"path": "a.py", "old_string": "x", "new_string": "x"})[1])

    def test_missing_file(self):
        self.assertTrue(self.box.execute("edit_file", {"path": "n.py", "old_string": "a", "new_string": "b"})[1])


class TestWriteFile(ToolTestBase):
    def test_create_with_parents(self):
        text, err = self.box.execute("write_file", {"path": "new/dir/m.py", "content": "a\nb\n"})
        self.assertFalse(err, text)
        self.assertIn("Created", text)
        self.assertEqual(self.read("new/dir/m.py"), "a\nb\n")

    def test_overwrite_reported(self):
        self.write("a.py", "old\n")
        text, err = self.box.execute("write_file", {"path": "a.py", "content": "new\n"})
        self.assertFalse(err)
        self.assertIn("Overwrote", text)

    def test_outside_rejected(self):
        self.assertTrue(self.box.execute("write_file", {"path": "/etc/madsloop_x", "content": "x"})[1])


class TestDispatchAndGit(ToolTestBase):
    def test_unknown_tool(self):
        text, err = self.box.execute("search", {})
        self.assertTrue(err)
        self.assertIn("Unknown tool", text)

    def test_bad_kwargs_do_not_crash(self):
        text, err = self.box.execute("bash", {"command": "ls", "bogus": 1})
        self.assertTrue(err)

    def test_workdir_changes_sees_new_files(self):
        subprocess.run(["git", "init", "-q", self.dir], check=True)
        self.write("a.py", "x\n")
        subprocess.run(["git", "-C", self.dir, "add", "a.py"], check=True)
        subprocess.run(["git", "-C", self.dir, "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "init"], check=True)
        self.assertEqual(tools.workdir_changes(self.dir), "")
        self.write("new.py", "y\n")
        self.assertIn("new.py", tools.workdir_changes(self.dir))


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0); check /proc state when available.
    try:
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True


if __name__ == "__main__":
    unittest.main()
