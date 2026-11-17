# madsLoop 设计说明

## A. 运行环境
- 代码兼容 Python 3.9（10 个镜像的 Python 版本为 3.9.2 到 3.12.2），统一用 python3 运行
- 镜像的 pip 源指向无效地址 127.0.0.1:9876，安装时指定 --index-url https://pypi.org/simple
- 依赖装到 /opt/madsloop_deps（--target），用 PYTHONPATH 引用，不改动项目自身的 Python 环境
- 固定 openai==2.48.0：openai 3.x 不支持 Python 3.9，不固定会导致不同任务用不同主版本
- 模型配置通过环境变量：MADSLOOP_BASE_URL、MADSLOOP_API_KEY、MADSLOOP_MODEL
- 服务商优先列表：Parasail → DeepInfra → Prime Intellect（待确认量化和工具支持）
- 服务商优先列表：Parasail → DeepInfra → Prime Intellect（均为 fp8、支持工具调用，已确认）；
  只允许这三家，要求支持请求中的所有参数（tools），量化限定 fp8；
  排除 Darkbloom（fp4）、CoreWeave 和 SiliconFlow（不支持工具

## B. 工具
- bash：在工作目录下运行；默认超时 120 秒，模型可指定更长，上限 600 秒；
  超时杀掉整个进程组；标准输入接空，设置 PAGER=cat、GIT_PAGER=cat；
  返回退出码；输出过长时截断，保留开头和结尾
- read_file：带行号；支持读取指定行范围；默认最多返回几百行并告知总行数；
  提醒模型行号不能复制进修改内容
- edit_file：原文必须唯一匹配；找不到或匹配多处时返回清楚的错误和提示（含行号）
- write_file：新增，用于创建新文件（如 /tmp 下的复现脚本）
- 不加专门的搜索工具，用 bash 里的 grep
- 讲义 1.3 提到 repro_check，但 1.1 工具表里没有，且没有创建文件的工具，见 C 部分

## C. 主循环
- 停止条件：调用 done；连续几次（如 3 次）没有工具调用时，先提醒继续，仍没有才结束；
  最大轮数（如 80），接近上限时提醒模型收尾
- API 失败：指数退避重试（1、2、4、8 秒……），记录 api_retry
- 非法 JSON 参数、未知工具、工具执行报错：作为工具结果返回模型，is_error=true，不崩溃
- 一次回复多个工具调用：按顺序执行，每个调用配一条结果
- done 验证（第一版）：git diff 为空时拒绝并提醒；最多拒绝 2 到 3 次
- 待第二版：repro_check（注册时应失败，done 时应通过），根据基线的失败情况再决定
- 补丁问题：git diff 不包含新建文件，生成补丁前用 git add -N 标记新文件，注意排除缓存等无关文件

## D. 上下文管理
- 单次工具输出上限约 1 万字符（保留开头和结尾）
- 总长度超过 10 万 token 时，把较早轮次的工具输出替换为"[早期输出已省略，如需要请重新运行]"，最近几轮保持完整
- 只在超过阈值时压缩，避免每轮修改历史导致服务商缓存失效
- 模型的推理内容第一版不发回历史，之后做对比实验

## E. 提示词
- 用户消息包含 problem_statement、requirements、interface 三部分（分标题），
  讲义只传了 problem_statement；requirements 里有隐藏测试依赖的具体函数名和行为
  （例：任务 0 要求新增 is_python_identifier）
- requirements 和 interface 字段被双重 JSON 编码，传入前先解码
- 需要修改 run_task.sh 来传递这两个字段
- 系统提示词：英文、简洁；包含角色和环境、规则（小改动、不改测试、临时文件放 /tmp、
  不安装新包）、工作流程（定位、复现、修改、验证、done）、区分自己引起的失败和原有失败
  （git stash 对比）、工具使用提示、多语言测试方式
- 第一版不求完美，根据日志里的实际错误迭代

## F. 日志
- 严格按讲义字段，只用规定的字段，不加额外内容（费用、推理 token 等另写调试日志）
- 每行都有 timestamp（ISO-8601）和 event
- tool_result 的 result 必须和发给模型的截断后内容完全一致
- run_end 的 reason：done / no_tool_calls / error / max_iterations
- 只在 --log 时写到 ./madsLoop_logs/，每次运行一个新文件（时间戳命名）
- 调试信息打印到 stderr
- 用 try/finally 确保异常退出时也写 run_end