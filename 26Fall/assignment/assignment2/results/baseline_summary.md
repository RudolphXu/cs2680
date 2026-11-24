# madsLoop 基线结果（10 个任务）

本文件只整理已有的运行数据，不包含失败原因分析。

- 模型：`qwen/qwen3.6-35b-a3b`，通过 OpenRouter 调用（fp8，服务商优先顺序 parasail → deepinfra → primeintellect）
- 每个任务都用 `evaluation_scripts/run_task.sh` 运行，用官方 SWE-bench Pro 评测（`evaluation_scripts/evaluate.sh`）判定
- 原始数据：任务 1、2、4–9 在 `run_logs/baseline/<idx>_<instance_id>/`；任务 0 和 3 的产物在 `madsLoop_logs/`、`model_patch_*.diff`、`run_logs/task_{0,3}.manual.log` 和 `pro_eval/`，指标由 `scripts/task_report.py` 计算（`run_logs/validate/`）
- `run_logs/` 不在仓库中（已加入 .gitignore），本文件是它的汇总

## 总览

**通过 4 个，失败 6 个**（共 10 个）

| 结果 | 任务 |
|---|---|
| RESOLVED | 0（ansible）、3（flipt）、4（qutebrowser）、5（openlibrary） |
| 失败 | 1（qutebrowser）、2（ansible）、6（teleport）、7（qutebrowser）、8（ansible）、9（protonmail） |

整体观察：

- **10 个任务都以 `done` 结束**，失败的 6 个也一样：都是 agent 自己判断已经完成，不是因为超时、轮数用完或 API 出错。
- **没有任务触发上下文压缩**。最大 prompt 是任务 2 的 83,026 token，低于 10 万的压缩阈值；其余任务都在 6.3 万以下。
- **没有任务接近时间上限**。最长的是任务 0，38 分 37 秒，上限是 45 分钟；其余任务都在 28 分钟以内，任务 5 和 9 只用了 2～3 分钟。
- **没有任务接近轮数上限**。最多的是任务 2，72 轮，上限是 80 轮。
- **时间几乎都花在等模型上**：各任务等待模型的时间占 81%～100%，工具执行很快。
- **API 和工具都很稳定**：10 个任务一共只有 2 次 API 重试（任务 0 和 8），1 次工具报错（任务 4）。
- **失败的任务大多只差 1 个用例**：任务 1、2、6、7、8 都只有 1 个 `fail_to_pass` 用例没过。任务 9 的测试文件编译失败，没有任何用例运行。
- **通过的任务里也有隐患**：4 个通过的任务中，有 3 个（0、3、4）被隐患检查标记。另外，有 3 个任务（2、3、8）改了已有的测试文件。

## 汇总表

| # | 任务 | 评测 | 轮数 | 用时 | 最大 prompt | API 重试 | 工具报错 | 等待模型 | 代码版本 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | ansible-f327e65 | RESOLVED | 66 | 38m37s | 36,024 | 1 | 0 | 100% | `06c6d29` |
| 1 | qutebrowser-c580ebf | 失败：F2P 1/11 未通过 | 44 | 19m21s | 26,608 | 0 | 0 | 94% | `a7c1fed` |
| 2 | ansible-a26c325 | 失败：F2P 1/4 未通过 | 72 | 21m27s | 83,026 | 0 | 0 | 100% | `a7c1fed` |
| 3 | flipt-2eac0df | RESOLVED | 52 | 26m16s | 38,166 | 0 | 0 | 98% | `3d38601` |
| 4 | qutebrowser-de4a1c1 | RESOLVED | 68 | 18m37s | 62,510 | 0 | 1 | 81% | `a7c1fed` |
| 5 | openlibrary-4a5d2a7 | RESOLVED | 10 | 2m21s | 9,224 | 0 | 0 | 98% | `a7c1fed` |
| 6 | teleport-89f0432 | 失败：F2P 1/1 未通过 | 39 | 15m35s | 29,925 | 0 | 0 | 99% | `a7c1fed` |
| 7 | qutebrowser-21b426b | 失败：F2P 1/3 未通过 | 33 | 19m40s | 24,641 | 0 | 0 | 99% | `a7c1fed`（重跑） |
| 8 | ansible-83909bf | 失败：F2P 1/1 未通过 | 58 | 27m47s | 43,775 | 1 | 0 | 99% | `a7c1fed` |
| 9 | webclients-944adbf | 失败：测试未运行 | 17 | 3m21s | 13,694 | 0 | 0 | 96% | `a7c1fed`（重跑） |

所有任务都以 `done` 结束，都没有触发压缩。

**"等待模型"的算法**：每次 `api_request` 与上一个 `tool_result`（或 `run_start`）之间的时间加起来，除以运行总时长。日志时间戳精确到秒。

## 代码版本

### 每个任务用的是哪个 commit

任务 1、2、4–9 由批量脚本运行，commit 记录在各自目录的 `harness_commit` 文件里，都是 `a7c1fed`。

任务 0 和 3 是单独运行的，commit 是根据运行开始时间和提交时间确定的。两个任务开始时，工作区里的 harness 文件都没有未提交的修改。

| 任务 | 运行开始（EST） | 当时最新的提交 | 下一个提交 | 使用版本 |
|---|---|---|---|---|
| 0 | 2026-11-22 19:09:05 | `06c6d29`（19:08:50） | `3d38601`（20:00:35） | `06c6d29` |
| 3 | 2026-11-22 20:00:48 | `3d38601`（20:00:35） | `a4f2b9e`（21:08:53） | `3d38601` |
| 1、2、4、5、6、8 | 2026-11-22 21:15:21 起 | `a7c1fed`（21:15:16） | — | `a7c1fed` |
| 7、9（重跑） | 2026-11-23 19:24:23 起 | `a7c1fed` | — | `a7c1fed` |

### 版本之间的差别

| 区间 | 改动的文件 | 内容 | 是否可能影响 agent 行为 |
|---|---|---|---|
| `06c6d29` → `3d38601` | `src/config.py`、`.gitignore`、`DESIGN.md` | OpenRouter 路由参数：删掉 `only`，`allow_fallbacks` 从 `false` 改成 `true`（`order`、`require_parameters: true`、`quantizations: ["fp8"]` 都不变） | **可能有轻微影响。** 提示词、工具和主循环都没有变。但三家服务商都不可用时，请求会被转到其他 fp8 服务商（仍然要求支持 tools），不同的服务部署可能输出略有差异。日志里没有记录每次请求由哪家服务商处理，所以无法确认基线运行中是否真的发生过转移 |
| `3d38601` → `a4f2b9e` | `docker_env.sh` | 设置 `PIP_ROOT_USER_ACTION=ignore`，不再打印 pip 的 root 警告 | 不影响。只改了安装依赖时的输出，安装的包和版本都不变 |
| `a4f2b9e` → `a7c1fed` | `scripts/batch_run.sh`、`scripts/task_report.py`（新增） | 批量运行和统计脚本 | 不影响。agent 运行时不会用到这两个脚本 |

所以，任务 0 和 `a7c1fed` 只差 OpenRouter 的路由参数；任务 3 与 `a7c1fed` 之间，agent 用到的代码完全相同。

## 各任务详情

隐患是用启发式规则检查出来的，需要人工确认。检查的内容有四项：
- 是否改了已有的测试文件；
- interface 要求的名字是否在新增代码中定义；
- requirements 要求删除的代码或文件是否删除了；
- 是否新增了 requirements 和 interface 都没有提到的文件。

### 0 · instance_ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5

- 代码版本：`06c6d29`（单独运行）
- 评测：**RESOLVED**
- 轮数 66，用时 38m37s，最大 prompt 36,024，API 重试 1，工具报错 0，等待模型 100%
- 改动的文件：`lib/ansible/galaxy/dependency_resolution/dataclasses.py`、`lib/ansible/utils/collection_loader/_collection_finder.py`
- 隐患：requirements 要求删除 `_is_fqcn`，但它仍然存在

### 1 · instance_qutebrowser__qutebrowser-c580ebf0801e5a3ecabc54f327498bb753c6d5f2-v2ef375ac784985212b1805e1d0431dc8f1b3c171

- 代码版本：`a7c1fed`
- 评测：**失败**，`fail_to_pass` 11 个中有 1 个没过
  - `tests/unit/components/test_hostblock.py::test_subdomain_blocking`
- 轮数 44，用时 19m21s，最大 prompt 26,608，API 重试 0，工具报错 0，等待模型 94%
- 改动的文件：`qutebrowser/utils/urlutils.py`
- 隐患：未发现

### 2 · instance_ansible__ansible-a26c325bd8f6e2822d9d7e62f77a424c1db4fbf6-v0f01c69f1e2528b935359cfe578530722bca2c59

- 代码版本：`a7c1fed`
- 评测：**失败**，`fail_to_pass` 4 个中有 1 个没过
  - `test/units/module_utils/urls/test_Request.py::test_open_url`
- 轮数 72，用时 21m27s，最大 prompt 83,026，API 重试 0，工具报错 0，等待模型 100%
- 改动的文件：`lib/ansible/module_utils/urls.py`、`lib/ansible/modules/get_url.py`、`lib/ansible/modules/uri.py`、`lib/ansible/plugins/lookup/url.py`、`test/units/module_utils/urls/test_Request.py`、`test/units/module_utils/urls/test_fetch_url.py`
- 隐患：改了已有的测试文件 `test/units/module_utils/urls/test_Request.py` 和 `test/units/module_utils/urls/test_fetch_url.py`

### 3 · instance_flipt-io__flipt-2eac0df47b5ecc8bb05002d80383ceb08ab3620a

- 代码版本：`3d38601`（单独运行）
- 评测：**RESOLVED**
- 轮数 52，用时 26m16s，最大 prompt 38,166，API 重试 0，工具报错 0，等待模型 98%
- 改动的文件：`internal/config/analytics.go`、`internal/telemetry/telemetry.go`、`internal/telemetry/telemetry_test.go`
- 隐患：
  - 改了已有的测试文件 `internal/telemetry/telemetry_test.go`
  - interface 要求的是 `AnalyticsStorageConfig.String()`，补丁里定义的是 `Storage()`

### 4 · instance_qutebrowser__qutebrowser-de4a1c1a2839b5b49c3d4ce21d39de48d24e2091-v2ef375ac784985212b1805e1d0431dc8f1b3c171

- 代码版本：`a7c1fed`
- 评测：**RESOLVED**
- 轮数 68，用时 18m37s，最大 prompt 62,510，API 重试 0，工具报错 1，等待模型 81%
- 改动的文件：`qutebrowser/app.py`、`qutebrowser/config/configinit.py`、`qutebrowser/config/qtargs.py`、`scripts/dev/check_coverage.py`、`tests/unit/config/test_qtargs.py`
  - 其中 `test_qtargs.py` 是新建的文件，不是修改已有测试
- 隐患：requirements 要求删除 `_init_envvars`，但它仍然存在

### 5 · instance_internetarchive__openlibrary-4a5d2a7d24c9e4c11d3069220c0685b736d5ecde-v13642507b4fc1f8d234172bf8129942da2c2ca26

- 代码版本：`a7c1fed`
- 评测：**RESOLVED**
- 轮数 10，用时 2m21s，最大 prompt 9,224，API 重试 0，工具报错 0，等待模型 98%
- 改动的文件：`openlibrary/core/wikidata.py`
- 隐患：未发现

### 6 · instance_gravitational__teleport-89f0432ad5dc70f1f6a30ec3a8363d548371a718

- 代码版本：`a7c1fed`
- 评测：**失败**，`fail_to_pass` 唯一的 1 个用例没过
  - `TestReadAtMost`
- 轮数 39，用时 15m35s，最大 prompt 29,925，API 重试 0，工具报错 0，等待模型 99%
- 改动的文件：`lib/utils/utils.go`
- 隐患：未发现

### 7 · instance_qutebrowser__qutebrowser-21b426b6a20ec1cc5ecad770730641750699757b-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d

- 代码版本：`a7c1fed`。以 2026-11-23 的重跑为准
- 第一次运行被中断：2026-11-22 22:02:30 启动，22:22:04 电脑意外重启，Docker 中断（`error waiting for container: unexpected EOF`，run_task.sh 退出码 125）。当时已经跑到第 16 轮，没有拿到补丁。这次的记录保留在 `run_logs/baseline/7_….aborted-docker-crash-20261122-2222/`，不计入结果
- 评测：**失败**，`fail_to_pass` 3 个中有 1 个没过
  - `tests/unit/config/test_configutils.py::test_repr`
- 轮数 33，用时 19m40s，最大 prompt 24,641，API 重试 0，工具报错 0，等待模型 99%
- 改动的文件：`qutebrowser/config/configutils.py`
- 隐患：未发现

### 8 · instance_ansible__ansible-83909bfa22573777e3db5688773bda59721962ad-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5

- 代码版本：`a7c1fed`
- 评测：**失败**，`fail_to_pass` 唯一的 1 个用例没过
  - `test/units/galaxy/test_api.py::test_api_no_auth_but_required`
- 轮数 58，用时 27m47s，最大 prompt 43,775，API 重试 1，工具报错 0，等待模型 99%
- 改动的文件：`lib/ansible/cli/galaxy.py`、`lib/ansible/galaxy/api.py`、`test/units/galaxy/test_api.py`
- 隐患：
  - 改了已有的测试文件 `test/units/galaxy/test_api.py`
  - requirements 要求删除 `login.py` 文件，但没有删除

### 9 · instance_protonmail__webclients-944adbfe06644be0789f59b78395bdd8567d8547

- 代码版本：`a7c1fed`。以 2026-11-23 的重跑为准
- 第一次运行被中断：2026-11-22 22:21:17 启动，22:22:04 电脑意外重启，Docker 中断（同任务 7，run_task.sh 退出码 125）。当时已经跑到第 5 轮，没有拿到补丁。这次的记录保留在 `run_logs/baseline/9_….aborted-docker-crash-20261122-2222/`，不计入结果
- 评测：**失败**，测试未运行
  - 补丁应用成功，但测试文件 `packages/metrics/tests/observeApiError.test.ts` 编译失败，报错 `TS2613: Module '".../lib/observeApiError"' has no default export`
  - 因为没有运行任何用例，`fail_to_pass` 的用例全部计为未通过
  - `task_report.py` 把这种情况笼统地记为"补丁无法应用或测试未运行"；具体原因见评测输出 `pro_eval/<instance_id>/madsLoop_stdout.log`
- 轮数 17，用时 3m21s，最大 prompt 13,694，API 重试 0，工具报错 0，等待模型 96%
- 改动的文件：`packages/metrics/index.ts`、`packages/metrics/lib/observeApiError.ts`
- 隐患：未发现
