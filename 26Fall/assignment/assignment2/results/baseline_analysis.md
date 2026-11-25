# 基线失败任务分析（任务 1、2、6、7、8、9）

本文分析基线中 6 个失败的任务，基线数据见 [baseline_summary.md](baseline_summary.md)。这一步只做分析，没有改代码。

## 依据和局限

- **依据**：每个任务都对照了以下材料：
  - 官方评测输出：`pro_eval/<id>/madsLoop_stdout.log`；
  - agent 的 trace 和最终补丁；
  - 任务文本：problem_statement、requirements、interface；
  - 参考补丁和隐藏测试：在任务镜像里用 `git show <gold commit>` 取得。
- **两类结论要分开看**：
  - 失败信息和期望值都来自评测日志原文，是确定的。
  - 模型的"意图"是从工具调用推断出来的。trace 不记录模型的文字和推理，只有工具调用和 `done` 的总结，所以这部分是推断。
- **"第 N 轮"的含义**：指 trace 里的 iteration，也就是第几次请求模型。
- **分类**：
  - (a) 理解错了需求，或漏了 requirements / interface 里写明的要求；
  - (b) 方向对了，但细节有误，模型自己的验证没能发现；
  - (c) 测试期望的东西无法从任务描述中得知；
  - (d) 框架或评测环境的问题。

## 汇总表

| # | 没通过的 F2P 测试 | 直接原因 | 主分类 | 次分类 | 把握 | 框架改进能否救回 |
|---|---|---|---|---|---|---|
| 1 | `test_hostblock.py::test_subdomain_blocking`（1/11） | 只实现了 interface 里的 `widened_hostnames`，没改 `hostblock.py` 的拦截逻辑 | **(a)** | — | 高 | 很可能 |
| 2 | `test_Request.py::test_open_url`（1/4） | `use_netrc` 传给了 `Request()` 构造函数，测试要求它作为 `.open()` 的关键字参数 | **(c)** | (b) | 中，两类很难区分 | 有可能，要靠"照着相邻参数的写法"这条规则 |
| 6 | `TestReadAtMost`（1/1） | 内容长度正好等于上限时返回 nil，测试要求 `ErrLimitReached` | **(b)** | (c) | 中高 | 有可能 |
| 7 | `test_configutils.py::test_repr`（1/3） | repr 输出 `values=[...]`，测试要求 `vmap=odict_values([...])` | **(c)** | (b) | 高 | 不太可能 |
| 8 | `test_api.py::test_api_no_auth_but_required`（1/1） | 报错信息措辞和测试的正则不符 | **(c)** | (a)；另有 (d)，见下文 | 中高 | 不太可能 |
| 9 | 9 个用例都没运行（测试文件编译失败） | 只有命名导出，没有默认导出；隐藏测试用的是默认导入，TS2613 | **(b)/(c)** | — | 中 | 很可能，要靠"照着仓库惯例写"这条规则 |

**所有失败任务的共同点**：都以 `done` 结束，用掉的轮数和时间都远低于上限（最多 72 轮，最长 27 分 47 秒）。也就是说，模型并不是没时间做完，而是自己的验证全部通过之后就停了。

## 各任务分析

### 任务 1 · qutebrowser c580ebf（子域名拦截）

**1. 失败的测试和错误信息**

`tests/unit/components/test_hostblock.py::test_subdomain_blocking` 把 `example.com` 加进拦截列表，然后断言它的子域名也会被拦截：

```
tests/unit/components/test_hostblock.py:573: in test_subdomain_blocking
    assert host_blocker._is_blocked(QUrl("https://subdomain.example.com"))
E   AssertionError: assert False
```

另外 10 个 F2P（`test_urlutils.py` 里的 `TestWiden`）都通过了。

**2. 模型做了什么，为什么认为完成了**

- 补丁只改了 `qutebrowser/utils/urlutils.py`：新增了 `widened_hostnames`，逻辑照搬 `configutils.py` 里已有的私有函数 `_widened_hostnames`。
- 整个 trace 里，模型**一次也没有读过或 grep 过 `hostblock.py`**，也没有跑 `test_hostblock.py`。
- 它在原地打转：对 `widened` 的 grep 重复了 11 次（第 3–22 轮）。
- 验证只跑了 base 版的 `test_urlutils.py` 和 `test_configutils.py`。这两个文件在改动前本来就能通过。
- 第 44 轮调用 done，总结里写着 "All existing tests pass (382 tests ...)"。

**3. 分类：(a)，把握高**

题面写得很清楚：
- 标题是 "Host blocking does not apply to subdomains"；
- requirements 的前四条讲的都是拦截行为，比如 "Respect whitelist overrides first"、"if example.com is blocked, then ... sub.example.com ... must all be blocked"、"checking ... against both the runtime blocked set and the config-defined blocked set"。

参考补丁确实改了 `qutebrowser/components/hostblock.py`（18 行）。测试用的例子也正是需求里举的 example.com / sub.example.com。

模型把 interface 里列出的唯一一个函数当成了整个任务。

**4. 可能有帮助的改进**

- done 之前，要求模型把 requirements 逐条对应到 diff 里的改动。
- 提示词里说明：interface 里的新函数通常要被现有代码调用。
- 检测重复的工具调用，提醒模型换个方向。

### 任务 2 · ansible a26c325（`use_netrc` 参数）

**1. 失败的测试和错误信息**

隐藏测试 `test_open_url` mock 掉 `Request.open`，然后断言调用参数里带有 `use_netrc=True`：

```
E   AssertionError: expected call not found.
E   Expected: open('GET', 'https://ansible.com/', ..., ciphers=None, use_netrc=True)
E     Actual: open('GET', 'https://ansible.com/', ..., ciphers=None)
```

另外 3 个 F2P 和全部 P2P 都通过了。

**2. 模型做了什么，为什么认为完成了**

- 第 32 轮，模型把 `open_url` 改成了 `Request(use_netrc=use_netrc).open(...)`，也就是把值传给构造函数。
- 两种写法在运行时效果一样：`open()` 收到 None 时会退回到 `self.use_netrc`。但 mock 断言检查的是具体的调用形式。
- 参考实现写的是 `Request().open(..., use_netrc=use_netrc)`，和函数里其余 20 多个参数的传递方式一致。
- 验证方面：
  - 第 50–70 轮多次跑 `urls/` 目录的单元测试；
  - 第 51、52、61 轮修改了旧测试的期望，让它们接受新参数；
  - 第 70 轮测试全部通过，第 72 轮调用 done，总结写着 "All 120 URL-related tests pass"。
- 旧的 `test_open_url` 期望的调用里本来就没有 `use_netrc`。模型的写法正好没有改变 `.open()` 的调用形式，所以本地验证不可能发现这个差异。

**3. 分类：主 (c)、次 (b)，把握中等，两类很难区分**

- **(c) 的理由**：requirements 原文是 "`open_url` has to ... forward the value **when creating a `Request`**"。字面上更接近模型的构造函数写法。interface 写的是 "No new interfaces are introduced"。
- **(b) 的理由**：`open_url` 里原有的所有参数都是传给 `.open()` 的，一个也没传给构造函数。如果严格照着相邻参数的写法，就能写对。

**4. 可能有帮助的改进**

- 提示词规定：给转发链加新参数时，传递的位置和形式要和相邻参数完全一致。
- done 之前，grep 测试里针对被改函数的 `assert_called*_with` 断言，提醒模型检查。

收益有限，因为任务文本本身在往另一个方向引导。

**补充：模型对测试文件的修改**

模型改了 `test_fetch_url.py`（给两处断言加上 `use_netrc=True`）和 `test_Request.py`（`test_Request_fallback` 里加了一个 `call(None, True)`，调用次数 17 改成 18）。这些修改方向正确，和隐藏测试一致，没有掩盖失败。评测时这些文件会被隐藏测试覆盖，对分数没有影响。

### 任务 6 · teleport 89f0432（`utils.ReadAtMost`，Go）

**1. 失败的测试和错误信息**

隐藏测试的三个用例都用 `strings.NewReader("hello")` 作为输入：

```go
{4, "hell", ErrLimitReached},
{5, "hello", ErrLimitReached},   // 内容长度正好等于上限
{6, "hello", nil},
```

这是断言失败，不是编译错误：

```
utils_test.go:565: Error: Not equal:
    expected: *errors.errorString(&errors.errorString{s:"read limit reached"})
    actual  : <nil>(<nil>)
```

在模型的实现下，只有 `{5, "hello"}` 这一个用例会返回 nil。参考实现是 `io.LimitedReader` 加上 `if limitedReader.N <= 0 { return data, ErrLimitReached }`，也就是读满上限就报错。

**2. 模型做了什么，为什么认为完成了**

- 第 16 轮和第 22 轮的两版实现，在内容长度等于上限时都返回 `ErrLimitReached`，**和参考实现一致**。
- 第 23 轮，模型没有先跑测试就重写了实现，改成长度等于上限时返回 nil。
- 第 24 轮，它自己写的测试脚本报错：`FAIL Test 3: expected ErrLimitReached, got: <nil>`。
- 第 25 轮，它**没有回退实现，而是重写了测试脚本**：把 Test 3 改成 "should succeed"，又加了一个 Test 8 来巩固这个错误的语义。
- 之后它跑了 `go test ./lib/utils/...`。唯一的失败 `TestRejectsSelfSignedCertificate` 在 base 上本来就失败，它用 `git stash` 确认过。
- 第 39 轮调用 done。仓库里没有任何测试覆盖 `ReadAtMost`。

**3. 分类：主 (b)、次 (c)，把握中高**

- **(c) 的成分**：requirements 写的是 "reaches the limit **before completing the content**" 和 "When the limit **allows reading all available content** ... without error"。长度正好等于上限时该怎么处理，这两句确实有歧义。
- **但主要是 (b)**：
  - interface 写的是 "If the limit is reached, returns the error `ErrLimitReached`"，倾向于读满就报错；
  - 模型自己最初的两版实现，以及第一版自测，选的都是对的；
  - 失败的直接原因，是它推翻了正确的判断，并反过来修改测试期望，去迁就新的实现。

**4. 可能有帮助的改进**

- 提示词规定：自测失败时，要先判断是实现错了还是期望错了；修改期望必须引用需求原文作为依据。
- done 之前，要求列出每个边界情况（等于上限、0、空输入）选择的语义；有歧义时按 interface 的措辞解释。

### 任务 7 · qutebrowser 21b426b（`Values` 改用 OrderedDict）

**1. 失败的测试和错误信息**

`test_repr` 拿 `repr(values)` 和一个写死的字符串逐字比较。期望的结尾和实际输出分别是：

```
期望：..., vmap=odict_values([ScopedValue(value='global value', pattern=None), ScopedValue(...)]))
实际：..., values=[ScopedValue(...), ScopedValue(...)])
```

参考实现是 `utils.get_repr(self, opt=self.opt, vmap=self._vmap.values(), constructor=True)`。另外 2 个 F2P 和全部 P2P 都通过了。

**2. 模型做了什么，为什么认为完成了**

- 第 12 轮写成 `values=self._vmap.values()`，和参考实现只差关键字名，但这一版同样过不了。
- 第 15 轮，**旧版** `test_repr` 失败了，因为旧测试期望的是 list。
- 第 16 轮，模型改成 `values=list(self._vmap.values())` 去迎合旧测试。
- 第 18 轮，又加了一个只读属性 `_values`，让旧的 `test_iter` 能通过。
- 第 33 轮调用 done，总结写着 "All 27 existing tests pass"，并明确说 `_values` 是 "for backward compatibility with existing tests"。

**3. 分类：主 (c)、次 (b)，把握高**

逐字对照任务文本：
- problem_statement："`__repr__` should generate its output from this mapping"；
- requirements："`__repr__` ... should be updated to use the new internal `_vmap` structure"；
- interface："No new interfaces are introduced"。

三处都没有给出 repr 字符串，没有提到关键字要叫 `vmap`，也没说值要保留 `odict_values(...)` 的原样。唯一的线索是原代码的惯例：属性名去掉下划线就是关键字名（`values=self._values`）。但要从这个惯例推出 `vmap=`，推断的跨度很大。

次要的 (b)：模型把"旧测试全部通过"当成了正确的标准，主动把实现改回了旧格式。不过这一点不影响结果，因为第 12 轮那一版同样会失败。

**4. 可能有帮助的改进**

- 提示词里说明：旧测试反映的是改动前的行为；当它和需求冲突时，以需求为准，应该更新测试，而不是把实现改回去。

这个任务主要是 (c)，改进的收益有限。

### 任务 8 · ansible 83909bf（移除 `ansible-galaxy login`）

**1. 失败的测试和错误信息**

```python
def test_api_no_auth_but_required():
    expected = "No access token or username set. A token can be set with --api-key or at "
    with pytest.raises(AnsibleError, match=expected): ...
```

```
E AssertionError: Regex pattern '... A token can be set with --api-key or at ' does not match
  'No access token or username set. A token can be set with --api-key, or set in ansible.cfg.'
```

三个版本的报错信息分别是：
- base：`"... set with --api-key, with 'ansible-galaxy login', or set in ansible.cfg."`
- 参考实现：`"... set with --api-key or at {0}.".format(to_native(C.GALAXY_TOKEN_PATH))`
- 模型：只删掉了 login 那半句。

两个测试文件的结果是 1 个失败、40 个通过。

**2. 模型做了什么，为什么认为完成了**

- 第 18 轮 `rm` 了 `login.py`（详见下文）。
- 随后改了 CLI：login 命令改成报错退出。
- 第 27 轮改了 `api.py` 的报错信息。
- 第 29 轮**把已有测试里的期望字符串改成了自己写的新信息**。
- 第 34–35 轮用 `git stash` 确认，有一个失败的测试在 base 上本来就失败。
- 第 58 轮调用 done，总结写着 "All existing tests pass"。

它的验证是自证式的：测试断言的正是它自己写的字符串，不可能发现措辞上的偏差。

**3. 分类：主 (c)、次 (a)，把握中高**

- **(a)**：requirements 写的是 "update the error message ... to indicate the new authentication options via **token file** or --token parameter"。模型的新信息完全没有提到 token 文件，这条只做了一半。
- **(c)**：三段任务文本里都没有出现 `or at`、`GALAXY_TOKEN_PATH` 或任何具体措辞。problem_statement 里说的 "location to obtain the token (https://galaxy.ansible.com/me/preferences)"，指的是 CLI 里 login 命令的提示，不是这条 API 报错。即使模型补上了 token 文件，也几乎不可能正好写出 `with --api-key or at ` 这个前缀。

**4. 可能有帮助的改进**

- done 之前逐条对照需求，这能补上 token 文件那一半。
- 标记"修改已有测试的断言来迁就自己的实现"。

这两条都救不回这个 (c) 型的措辞要求。

### 任务 9 · protonmail 944adbf（`observeApiError`，TypeScript）

**1. 失败的测试和错误信息**

隐藏测试 `packages/metrics/tests/observeApiError.test.ts` 的第一行是：

```ts
import observeApiError from '../lib/observeApiError';
```

它有 9 个用例，分别传入 undefined、null、`'string'`、`''`、`{status:500}`、501、400、401、200，断言回调被调用了一次，参数分别是 `'failure'`、`'5xx'` 或 `'4xx'`。

评测日志里**只有一种编译错误**。它出现了两次，因为两个选定的测试路径各跑了一遍：

```
tests/observeApiError.test.ts:1:8 - error TS2613: Module '"/app/packages/metrics/lib/observeApiError"' has no default export.
Test Suites: 1 failed ... Tests: 0 total
```

**2. 模型做了什么，为什么认为完成了**

- 第 3 轮读了 `index.ts`：文件里全部是默认导入，比如 `import Counter from './lib/Counter'`。
- 同一轮只 `ls` 了一下 `tests/` 目录，**没有读里面的任何测试文件**。
- 第 5 轮写成 `export function observeApiError`，也就是命名导出。
- 第 8–9 轮用 ts-node 跑测试，因为路径写错报了 TS2307。
- 第 11 轮在 /tmp 写了一个 `.mjs` 文件，**把函数逻辑重新抄了一份**来测，没有 import 真正的模块，结果 "Passed: 16"。
- 第 12 轮跑 `tsc --noEmit`，通过。
- 第 17 轮调用 done，总结写着 "all edge cases ... have been verified"。

**3. 分类：(b)/(c)，把握中等**

- **任务文本里从头到尾没有出现 "default"**。interface 写的是 "Declares and exports the `observeApiError` function and the `MetricsApiStatusTypes` type"，读起来反而更像命名导出。只看文本，确实无从得知要用默认导出，这是 (c) 的成分。
- **但仓库的惯例很明确**，这是 (b) 的成分：
  - base 时 `packages/metrics/lib/` 下的 6 个文件全部是 `export default`；
  - `tests/*.test.ts` 全部用默认导入；
  - 这些模型在第 3 轮读过的 `index.ts` 里就能看到。
- 它的验证又绕开了真正的模块，所以没能发现问题。
- 不是 (d)。评测里其他 workspace 报的 "No tests found" 只是 `yarn workspaces foreach` 的正常输出；metrics 这个 workspace 确实跑了，是在 ts-jest 编译阶段失败的。

**4. 可能有帮助的改进**

- 新建文件前，先读同目录的已有文件和它们的测试，照着它们的导出和导入方式写；拿不准时，默认导出和命名导出都提供。
- 验证必须通过项目自己的测试工具，测试要 import 真正的模块；明确禁止把逻辑抄一份来自测。

## 三个专门问题

### 任务 8：`login.py` 是没删，还是删了但补丁没记录？

**删了，但补丁没有记录下来。原因是 run_task.sh 收集补丁的方式有 bug，属于 (d)。这和失败的那个测试无关。**

- 第 18 轮，模型执行了 `rm /app/lib/ansible/galaxy/login.py`，退出码 0。这是普通的 `rm`，不是 `git rm`。直到 done 之前，`git status` 一直显示 `deleted: lib/ansible/galaxy/login.py`（未暂存）。
- run_task.sh 收集补丁时执行的是 `git add -N .` 加 `git diff`。带上路径参数 `.` 之后，`git add` 会把工作区里已删除的文件从 index 中去掉，也就是把删除暂存了。随后的 `git diff` 比较的是工作区和 index，就看不到这个删除了。
- 我在任务 8 的镜像里复现了这个过程（git 2.25.1）：
  - `rm` 之后，状态是 ` D`；
  - 执行 `git add -N .` 之后，状态变成 `D `；
  - `git diff` 里已经没有 `login.py` 了，`git diff --cached` 里有。
- **影响范围**：任何任务里 agent 删除的文件，都会从补丁里丢失。这次没有影响分数，因为没有测试检查 `login.py` 是否存在；但换一个任务就可能导致误判。参考补丁是删除了 `login.py` 的。

### 任务 9：具体是什么编译错误？是模型的改动引起的吗？

- **错误**：只有一种，`TS2613: Module '"/app/packages/metrics/lib/observeApiError"' has no default export`，出现在测试文件第 1 行的默认导入。日志里没有其他 TypeScript 错误。
- **是模型的改动引起的**：
  - 参考实现是 `export default function observeApiError(...)`，并在 `index.ts` 里加了 `export { default as observeApiError } from './lib/observeApiError'` 和 `export * from './lib/observeApiError'`。
  - 模型只写了命名导出。除此之外，它的分类逻辑和参考实现等价。按代码推断，把测试里的 import 改成命名导入后，9 个用例都能通过（这一点只是推断，没有实际运行）。

### 任务 4：改了 `tests/unit/config/test_qtargs.py`，为什么隐患检查没报？

**检查是按设计工作的，这次不报是对的。**

- base 时 `tests/unit/config/test_qtargs.py` 并不存在，是模型新建的：补丁里这个文件标着 `new file mode`，有 497 行新增。
- 隐患检查只报"修改**已有**的测试文件"，所以这一条不会触发。
- "新增了 requirements 没提到的文件"这一条也不会触发，因为 requirements 里明确写了："The `check_coverage.py` script must be updated to map `tests/unit/config/test_qtargs.py` to `qutebrowser/config/qtargs.py`"。
- 评测时，`before_repo_set_cmd` 会用参考版本的 `test_qtargs.py` 覆盖模型写的这个文件，对结果没有影响。

## 共同的失败规律

1. **验证的方式决定了它发现不了错误**。6 个任务里，模型的验证要么跑的是旧测试（1、7），要么是改过期望以后的测试或自测（2、6、8），要么根本没有调用真正的模块（9 把逻辑抄了一份，1 也有类似的做法）。没有一个任务，模型按照 requirements 写出新测试，再拿去测真正的模块。所以"所有测试都通过了"几乎不能说明什么，而这恰恰是每个 done 总结里给出的完成理由。
2. **没有逐条对照 requirements 和 interface**。任务 1 漏掉了拦截逻辑，任务 8 漏掉了 token 文件。通过的任务里也有同样的问题：任务 0 没删 `_is_fqcn`，任务 3 没按 interface 起名 `String()`，任务 4 没删 `_init_envvars`。
3. **把旧测试当成标准，反过来改实现或改期望**。任务 7 为了让旧测试通过，把实现改回了旧格式；任务 6 把自测的期望改成了错的；任务 8 把测试里的字符串改成了自己的。
4. **不照着仓库已有的写法来**。任务 9 用了命名导出，而周围文件全是默认导出；任务 2 新参数的传法和相邻参数不一样；任务 7 关键字名没有沿用原来的命名规律。隐藏测试往往依赖这些惯例。
5. **测试要求逐字一致，而文本没有给出来**。任务 7、8 和部分的任务 2 要求精确的字符串或调用形式，任务文本里没有。这部分即使框架改进也很难救回，可以作为报告第 1 节"无解任务"的候选。不过判断是否无解，要以你自己的判断为准。
6. **框架有一个 bug**：删除的文件会从补丁里丢失（任务 8），这和模型无关。
7. **预算不是瓶颈**：所有失败任务都在很早的时候主动 done 了，提高轮数或时间上限不会有帮助。

## 改进建议（按优先级排列）

1. **修好补丁收集，不再丢失删除的文件**（run_task.sh）
   - 现在的 `git add -N .` 改成只对未跟踪的文件做 intent-to-add，例如在 `$WD` 里执行 `git ls-files -z -o --exclude-standard | xargs -0 -r git add -N --`；
   - 或者改用 `git add -A` 加 `git diff --cached`。
   - 这是确定存在的 bug，改动小，不影响 agent 的行为。修完要验证三种情况：新建文件、删除文件、修改文件都能进入补丁。
2. **done 之前逐条核对需求**（主循环和提示词）
   - 第一次调用 done 时，即使 diff 不为空，也先拒绝一次；
   - 把 requirements 的每一条和 interface 里的名字列出来，要求模型逐条写出对应的改动在哪里，并确认该删的已经删了；
   - 针对规律 2；直接针对任务 1、8，以及通过任务 0、3、4 里的隐患。
3. **验证必须测到真正的代码**（提示词，可以配合 done 检查）
   - 按 requirements 写新测试，用项目自己的测试工具运行，并且 import 真正的模块；
   - 禁止把逻辑抄一份来自测；
   - 旧测试和需求冲突时以需求为准；
   - 不准为了让测试通过而修改已有测试的断言，除非引用需求原文作为依据；
   - Go、JS 项目可以在包里临时放一个测试文件，done 之前删掉（这和之前提过的第 2 条改进一致）。
   - 针对规律 1、3；对应任务 6、9，部分对应任务 2、7、8。
4. **照着仓库已有的写法来**（提示词）
   - 新建文件前，先读同目录的文件和它们的测试，沿用它们的导出方式；拿不准时两种导出都提供；
   - 给已有的调用链加参数时，传法和相邻参数保持一致；
   - 命名沿用原有的规律。
   - 针对规律 4；任务 9 很可能因此通过，任务 2 有可能。
5. **处理好边界情况，防止改了却没测**（提示词或主循环，优先级较低）
   - done 之前，写下每个边界情况（等于上限、0、空输入）选择的语义；
   - 如果一次编辑覆盖了还没测过的实现，提醒模型先跑测试。
   - 针对任务 6。
6. **检测重复的工具调用**（主循环，优先级低）
   - 同一条命令重复多次时，提醒模型换个方向。
   - 针对任务 1 的原地打转。

**预期效果（粗略估计，未经验证）**：
- 第 1 条不改变通过数，但能消除误判的风险。
- 第 2～4 条最有希望救回的是任务 1 和 9，其次是任务 6 和 2。
- 任务 7、8 的主因是 (c)，预计救不回来。
- 第 2、3 条也会减少通过任务里的隐患。

改动之后要用同样的 10 个任务对比结果。同时要注意：拒绝 done 以及更严格的验证，会增加轮数和时间。目前的预算余量还很大，但要留意任务 0 本来就用了 38 分钟。
