#!/usr/bin/env python3
"""Per-task metrics and the batch summary table.

    task_report.py task <task_dir> <idx> <instance_id>   -> <task_dir>/result.json
    task_report.py summary <batch_dir>                     -> <batch_dir>/summary.md

<task_dir> layout (written by batch_run.sh):
    run_task.log  model_patch.diff  trace/*.jsonl  pro_eval/  result.json

The "hidden risk" checks are heuristics; they flag things for a human to
confirm, they do not decide anything.
"""

import ast
import glob
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from madsLoop import decode_field  # noqa: E402

TASKS_JSON = os.path.join(ROOT, "evaluation_scripts", "agent_task_input.json")
TESTS_JSON = os.path.join(ROOT, "evaluation_scripts", "task_test.json")


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def parse_list(value):
    if isinstance(value, list):
        return value
    for loader in (json.loads, ast.literal_eval):
        try:
            out = loader(value)
            if isinstance(out, list):
                return out
        except (ValueError, SyntaxError):
            pass
    return []


def read_trace(task_dir):
    files = sorted(glob.glob(os.path.join(task_dir, "trace", "**", "*.jsonl"), recursive=True))
    if not files:
        return None
    rows = []
    with open(files[-1], encoding="utf-8") as fh:  # latest run
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def parse_diff(text):
    """-> list of {path, new, deleted, added: [lines], removed: [lines]}"""
    files = []
    cur = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)", line)
            cur = {"path": m.group(2) if m else line, "new": False, "deleted": False,
                   "added": [], "removed": []}
            files.append(cur)
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur["new"] = True
        elif line.startswith("deleted file mode"):
            cur["deleted"] = True
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            cur["added"].append(line[1:])
        elif line.startswith("-"):
            cur["removed"].append(line[1:])
    return files


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def ts(r):
    return datetime.fromisoformat(r["timestamp"])


def trace_metrics(rows):
    m = {"iterations": None, "end_reason": None, "duration_s": None, "max_prompt_tokens": None,
         "api_retries": 0, "tool_errors": 0, "model_wait_s": None, "model_wait_ratio": None,
         "tool_calls": 0}
    if not rows:
        return m
    start, end = ts(rows[0]), ts(rows[-1])
    m["duration_s"] = (end - start).total_seconds()
    if rows[-1].get("event") == "run_end":
        m["iterations"] = rows[-1]["num_iterations"]
        m["end_reason"] = rows[-1]["reason"]
    api = [r for r in rows if r["event"] == "api_request"]
    if api:
        m["max_prompt_tokens"] = max(r["prompt_tokens"] for r in api)
    m["api_retries"] = sum(1 for r in rows if r["event"] == "api_retry")
    m["tool_errors"] = sum(1 for r in rows if r["event"] == "tool_result" and r["is_error"])
    m["tool_calls"] = sum(1 for r in rows if r["event"] == "tool_call")
    # Model wait: from the previous run_start / tool_result to each api_request
    # (includes retry back-off, which is also time spent waiting on the API).
    wait = 0.0
    prev = start
    for r in rows:
        if r["event"] == "api_request":
            wait += max(0.0, (ts(r) - prev).total_seconds())
            prev = ts(r)
        elif r["event"] in ("tool_result", "run_start"):
            prev = ts(r)
    m["model_wait_s"] = wait
    if m["duration_s"]:
        m["model_wait_ratio"] = wait / m["duration_s"]
    return m


def compaction_count(task_dir):
    path = os.path.join(task_dir, "run_task.log")
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for line in fh if "[madsLoop] compacted" in line)


def eval_outcome(task_dir, iid, patch_text, test_info, end_reason):
    """-> (resolved: bool|None, reason: str, details: [str])"""
    if not patch_text.strip():
        why = "补丁为空"
        if end_reason and end_reason != "done":
            why += "（run_end=%s）" % end_reason
        return False, why, []
    res_path = os.path.join(task_dir, "pro_eval", "eval_results.json")
    if not os.path.exists(res_path):
        return None, "评测未产出结果（见 eval.log）", []
    with open(res_path) as fh:
        resolved = json.load(fh).get(iid)
    if resolved:
        return True, "RESOLVED", []

    out_path = os.path.join(task_dir, "pro_eval", iid, "madsLoop_output.json")
    statuses = {}
    if os.path.exists(out_path):
        try:
            with open(out_path) as fh:
                statuses = {t["name"]: t["status"] for t in json.load(fh).get("tests", [])}
        except ValueError:
            pass
    if not statuses:
        return False, "补丁无法应用或测试未运行", []
    f2p = parse_list(test_info.get("fail_to_pass", "[]"))
    p2p = parse_list(test_info.get("pass_to_pass", "[]"))
    f2p_bad = [t for t in f2p if statuses.get(t) != "PASSED"]
    p2p_bad = [t for t in p2p if statuses.get(t) != "PASSED"]
    parts = []
    if f2p_bad:
        parts.append("fail_to_pass 未通过 %d/%d" % (len(f2p_bad), len(f2p)))
    if p2p_bad:
        parts.append("pass_to_pass 退步 %d/%d" % (len(p2p_bad), len(p2p)))
    reason = "；".join(parts) or "未解决（原因不明，见 pro_eval/）"
    if end_reason and end_reason != "done":
        reason += "（run_end=%s）" % end_reason
    details = ["F2P %s: %s" % (statuses.get(t, "MISSING"), t) for t in f2p_bad[:5]]
    details += ["P2P %s: %s" % (statuses.get(t, "MISSING"), t) for t in p2p_bad[:5]]
    return False, reason, details


# --------------------------------------------------------------------------
# hidden-risk heuristics
# --------------------------------------------------------------------------

TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|testing)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|"
    r"\.(test|spec)\.[jt]sx?$|(^|/)conftest\.py$")
DEF_KEYWORDS = r"(?:def|func|class|type|interface|function|const|let|var|export|struct|enum)"
IDENT = r"[A-Za-z_][A-Za-z0-9_]*"


def interface_names(text):
    """Names the interface section says must exist."""
    names = []
    for line in text.splitlines():
        m = re.match(r"\s*(?:\d+\.\s*)?(?:[-*]\s*)?(Name|Function|Method|Class|Type)\s*:\s*(.+)", line)
        if not m:
            continue
        kind, value = m.group(1), m.group(2).strip()
        bt = re.search(r"`([^`]+)`", value)
        if kind == "Type" and not bt:
            continue  # "Type: Method" / "Type: Public function" is a kind, not a name
        candidate = bt.group(1) if bt else value
        im = re.match(r"\s*(?:" + IDENT + r"\.)*(" + IDENT + r")", candidate)
        if im and im.group(1) not in names:
            names.append(im.group(1))
    return names


def defines(lines, name):
    pat = re.compile(r"\b" + DEF_KEYWORDS + r"\b.*\b" + re.escape(name) + r"\b|^\s*" + re.escape(name) + r"\s*[:=]")
    return any(pat.search(l) for l in lines)


def removal_targets(requirements):
    """Identifiers / files the requirements say must be removed."""
    idents, files = [], []
    for sentence in re.split(r"(?<=[.!?])\s+|\n", requirements):
        if not re.search(r"\b(remov|delet|eliminat|drop)", sentence, re.I):
            continue
        for tok in re.findall(r"`([^`]+)`", sentence):
            tok = tok.strip().rstrip("()")
            if re.fullmatch(IDENT, tok) and tok not in idents:
                idents.append(tok)
        for f in re.findall(r"`?([\w./-]+\.(?:py|go|ts|tsx|js))`?\s+file", sentence):
            if f not in files:
                files.append(f)
    return idents, files


def hidden_risks(diff_files, requirements, interface):
    risks = []
    added = [l for f in diff_files for l in f["added"]]
    removed = [l for f in diff_files for l in f["removed"]]

    tests = [f["path"] for f in diff_files if not f["new"] and TEST_PATH.search(f["path"])]
    if tests:
        risks.append("改了已有的测试文件：%s" % ", ".join(tests))

    for name in interface_names(interface):
        if not defines(added, name):
            risks.append("interface 要求的 `%s` 未在新增代码中定义" % name)

    idents, files = removal_targets(requirements)
    for name in idents:
        if defines(added, name) or not defines(removed, name):
            risks.append("requirements 要求删除的 `%s` 似乎仍然存在" % name)
    deleted = [f["path"] for f in diff_files if f["deleted"]]
    for fname in files:
        if not any(p.endswith(fname) for p in deleted):
            risks.append("requirements 要求删除的文件 %s 未删除" % fname)

    mentioned = requirements + "\n" + interface
    for f in diff_files:
        if f["new"] and os.path.basename(f["path"]) not in mentioned and f["path"] not in mentioned:
            risks.append("新增了 requirements/interface 未提到的文件：%s" % f["path"])
    return risks


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_task(task_dir, idx, iid):
    task = json.load(open(TASKS_JSON))[iid]
    test_info = json.load(open(TESTS_JSON)).get(iid, {})
    requirements = decode_field(task.get("requirements", ""))
    interface = decode_field(task.get("interface", ""))

    patch_path = os.path.join(task_dir, "model_patch.diff")
    patch = open(patch_path, encoding="utf-8", errors="replace").read() if os.path.exists(patch_path) else ""
    diff_files = parse_diff(patch)

    metrics = trace_metrics(read_trace(task_dir))
    resolved, reason, details = eval_outcome(task_dir, iid, patch, test_info, metrics["end_reason"])
    commit_path = os.path.join(task_dir, "harness_commit")
    result = {
        "idx": int(idx),
        "instance_id": iid,
        "harness_commit": open(commit_path).read().strip() if os.path.exists(commit_path) else None,
        "resolved": resolved,
        "eval": reason,
        "eval_details": details,
        "compactions": compaction_count(task_dir),
        "patch_files": [f["path"] for f in diff_files],
        # An empty patch has already failed; risk checks would only add noise.
        "hidden_risks": hidden_risks(diff_files, requirements, interface) if diff_files else [],
    }
    result.update(metrics)
    tmp = os.path.join(task_dir, "result.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(task_dir, "result.json"))  # atomic: result.json = task finished
    return result


def short_iid(iid):
    m = re.match(r"instance_([^_]+(?:-[^_]+)*)__([^-]+(?:-[^-]+)*?)-([0-9a-f]{7})", iid)
    return "%s-%s" % (m.group(2), m.group(3)) if m else iid[:40]


def fmt_dur(s):
    if s is None:
        return "-"
    return "%dm%02ds" % (int(s) // 60, int(s) % 60)


def cmd_summary(batch_dir):
    results = []
    for path in glob.glob(os.path.join(batch_dir, "*", "result.json")):
        if ".aborted-" in path:
            continue
        with open(path, encoding="utf-8") as fh:
            results.append(json.load(fh))
    results.sort(key=lambda r: r["idx"])
    n_ok = sum(1 for r in results if r["resolved"])
    lines = [
        "# 基线批量运行汇总", "",
        "更新时间：%s　已完成 %d 个任务，RESOLVED %d 个" % (
            datetime.now().strftime("%Y-%m-%d %H:%M"), len(results), n_ok), "",
        "| # | 任务 | 评测 | 轮数 | 用时 | 结束原因 | 最大 prompt | 压缩 | API 重试 | 工具报错 | 等待模型 | 隐患 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        ratio = "-" if r.get("model_wait_ratio") is None else "%.0f%%" % (100 * r["model_wait_ratio"])
        lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | %d | %d | %s | %d |" % (
            r["idx"], short_iid(r["instance_id"]), r["eval"],
            r["iterations"] if r["iterations"] is not None else "-",
            fmt_dur(r.get("duration_s")), r.get("end_reason") or "-",
            "{:,}".format(r["max_prompt_tokens"]) if r.get("max_prompt_tokens") else "-",
            "是（%d 次）" % r["compactions"] if r["compactions"] else "否",
            r["api_retries"], r["tool_errors"], ratio, len(r["hidden_risks"])))
    lines += ["", "## 详情（隐患为启发式检查，需人工确认）", ""]
    for r in results:
        lines.append("### %d · %s" % (r["idx"], r["instance_id"]))
        lines.append("")
        lines.append("- 评测：%s" % r["eval"])
        for d in r["eval_details"]:
            lines.append("  - %s" % d)
        lines.append("- 改动文件：%s" % (", ".join(r["patch_files"]) or "（无）"))
        if r["hidden_risks"]:
            lines.append("- 隐患：")
            lines += ["  - %s" % x for x in r["hidden_risks"]]
        else:
            lines.append("- 隐患：未发现")
        lines.append("- harness commit：%s" % (r.get("harness_commit") or "-"))
        lines.append("")
    tmp = os.path.join(batch_dir, "summary.md.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, os.path.join(batch_dir, "summary.md"))


def main(argv):
    if len(argv) == 4 and argv[0] == "task":
        r = cmd_task(argv[1], argv[2], argv[3])
        print(json.dumps({k: r[k] for k in ("eval", "iterations", "end_reason", "hidden_risks")},
                         ensure_ascii=False))
        return 0
    if len(argv) == 2 and argv[0] == "summary":
        cmd_summary(argv[1])
        return 0
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
