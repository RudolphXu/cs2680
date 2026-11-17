#!/usr/bin/env python3
"""madsLoop — a minimal coding-agent harness.

    python madsLoop.py -p "<problem statement>" [--log] <workdir>
        [--requirements-file F] [--interface-file F]

stdout is left untouched (run_task.sh reserves it for the patch); all
diagnostics go to stderr. Compatible with Python 3.9+.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from src.config import load_model_config  # noqa: E402
from src.logger import TraceLogger, debug  # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def decode_field(text):
    """Undo the extra JSON string encoding some task fields carry.

    '"# Title:\\n..."' -> '# Title:\n...'. Text that is not a JSON string
    literal is returned unchanged.
    """
    if text is None:
        return ""
    for _ in range(2):
        s = text.strip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            try:
                value = json.loads(s)
            except ValueError:
                break
            if isinstance(value, str):
                text = value
                continue
        break
    return text


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="madsLoop.py", description="Minimal coding-agent harness.")
    ap.add_argument("-p", "--problem", required=True, help="problem statement (issue text)")
    ap.add_argument("--log", action="store_true", help="write a JSONL trace to ./madsLoop_logs/")
    ap.add_argument("--requirements", default="", help="requirements text")
    ap.add_argument("--requirements-file", default=None, help="file with the requirements text")
    ap.add_argument("--interface", default="", help="interface text")
    ap.add_argument("--interface-file", default=None, help="file with the interface text")
    ap.add_argument("workdir", help="path to the repository checkout")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    workdir = os.path.abspath(args.workdir)
    if not os.path.isdir(workdir):
        debug("error: workdir %s is not a directory" % workdir)
        return EXIT_USAGE

    try:
        requirements = read_text(args.requirements_file) if args.requirements_file else args.requirements
        interface = read_text(args.interface_file) if args.interface_file else args.interface
    except (IOError, OSError) as exc:
        debug("error: cannot read task file: %s" % exc)
        return EXIT_USAGE

    problem = decode_field(args.problem)
    requirements = decode_field(requirements)
    interface = decode_field(interface)

    cfg = load_model_config()
    if not cfg.api_key:
        debug("warning: no API key (set MADSLOOP_API_KEY or CS2680_API_KEY)")
    debug("model=%s base_url=%s workdir=%s" % (cfg.model, cfg.base_url, workdir))

    logger = TraceLogger(args.log)
    if logger.path:
        debug("trace log: %s" % logger.path)

    # Imported here so that `--help` and usage errors work without openai.
    from src.agentic_loop import Agent
    from src.llm import LLMClient
    from src.tools import ToolBox

    try:
        llm = LLMClient(cfg, on_retry=logger.api_retry)
    except Exception as exc:
        debug("error: cannot create API client: %s" % exc)
        logger.run_start(cfg.model, workdir)
        logger.run_end("error", 0)
        logger.close()
        return EXIT_ERROR

    agent = Agent(llm, ToolBox(workdir), logger, cfg.model, workdir)
    reason = agent.run(problem, requirements, interface)
    return EXIT_ERROR if reason == "error" else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
