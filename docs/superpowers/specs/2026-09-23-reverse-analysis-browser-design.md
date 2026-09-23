# 逆向分析浏览器设计

## 目标

基于 `camoufox-reverse` 构建一个面向 Web 协议、混淆代码、动态脚本和 JavaScript 虚拟机分析的研究浏览器。

浏览器负责提供低扰动、可关联、可回溯的执行证据；具体目标的协议还原、算法提取和纯 Go 复现由分析器或目标适配器完成。Google 登录与 BotGuard VM 是第一条真实验收链，但浏览器核心不写死 Google 逻辑。

## 已确认约束

- 启动时必须提供绝对或可规范化为绝对路径的 `project_dir`。
- 每次启动默认创建全新的隔离 `session_id`。
- 只有显式指定 `resume_session` 时才允许恢复旧 session。
- 原始网络、脚本、Cookie、Storage、动态值和执行事件不脱敏保存。
- 原始证据与派生分析结果分离，原始证据只追加、不被分析器覆盖。
- 所有分析产物写入工程目录；浏览器二进制、依赖和普通缓存保留在系统缓存目录。
- 第一版只提供 MCP 和 CLI，不建设独立 GUI。
- 默认不上传、不遥测、不自动提交 Git。
- 工程目录按敏感数据目录处理，默认仅当前用户可读写。
- 浏览器观测模式是研究工具，不作为 `web_google` 纯 Go 运行时依赖。

## 用户入口

### MCP

启动接口提供至少以下参数：

```text
launch_browser(
  project_dir: string,
  proxy?: string,
  browser_version?: string,
  enable_trace?: boolean,
  trace_profile?: "overview" | "targeted" | "deep"
)
```

缺少 `project_dir`、路径不可写、路径通过符号链接逃逸或浏览器版本不匹配时，启动失败且不创建半成品 session。

第一版 MCP 能力：

- 启动、关闭和查询浏览器状态；
- 开始、停止和查询 trace；
- 列出脚本、网络请求和 session 文件；
- 获取请求关联的脚本、调用帧和执行事件；
- 查询指定值的生成链；
- 导出 session 摘要和分析报告；
- 对比两个 session 的执行轨迹。

### CLI

CLI 与 MCP 使用同一套核心库，不复制业务逻辑：

```sh
reverse-browser launch \
  --project-dir /absolute/path/to/project \
  --proxy http://127.0.0.1:7890 \
  --trace-profile targeted

reverse-browser session list --project-dir /absolute/path/to/project
reverse-browser trace index --project-dir /absolute/path/to/project --session <session-id>
reverse-browser trace compare --left <session-id> --right <session-id>
reverse-browser report build --project-dir /absolute/path/to/project --session <session-id>
```

## 工程与 session 存储

工程目录使用以下布局：

```text
project/
├── project.json
├── runs/
│   └── <session-id>/
│       ├── manifest.json
│       ├── raw/
│       │   ├── network/
│       │   ├── cookies/
│       │   ├── storage/
│       │   ├── scripts/
│       │   └── screenshots/
│       ├── trace/
│       ├── derived/
│       └── report/
└── indexes/
```

`project.json` 保存工程级元数据；`manifest.json` 保存单次 session 的浏览器版本、代理结构、探针配置、起止时间、文件清单、事件丢失数和完成状态。

session 状态至少包括：

```text
starting -> running -> stopping -> complete
                         \-> incomplete
```

异常退出时保留原始文件并标记 `incomplete`，禁止下次启动自动覆盖。索引和报告可以从 raw 文件重建。

## 观测分层

### 概览模式

低开销记录：

- 页面、Worker 和动态脚本加载；
- 网络请求、响应、重定向和请求 ID；
- 关键环境 API 访问；
- 任务调度和跨上下文消息的摘要；
- trace 开始、停止、丢失和错误状态。

### 定向模式

针对指定 URL、脚本 hash、函数、RPC 或请求开启：

- 函数进入、退出、异常；
- 调用帧和源码位置；
- 参数与返回值原始快照；
- 指定对象的 property get/set；
- 动态 `eval`/`Function` 的来源关系；
- 请求字段与调用链关联。

### 深度模式

只在锁定的脚本或 VM 适配器范围内开启：

- bytecode offset 和执行帧；
- VM dispatcher、虚拟 PC、opcode 和 handler；
- 虚拟寄存器或栈的选定快照；
- proof 输出边界；
- 浏览器执行与外部复现实现的逐步差分。

深度模式不承诺记录整个 JS 引擎的所有指令。全量引擎 opcode 只作为诊断 fallback，避免日志体积和时序扰动失控。

## 代码与值的生成链

浏览器必须同时保存两类关联：

```text
宿主 JS：脚本 hash、源码位置、调用帧
目标 VM：program hash、虚拟 PC、opcode、handler、寄存器/栈位置
```

以网络请求字段或某个输出值为入口，分析器生成反向链：

```text
目标值
  <- 编码/序列化
  <- 算法函数
  <- 中间对象或 VM 寄存器
  <- 环境、时间、随机数和请求输入
```

每个链节点必须标记证据等级：

- `observed`：直接捕获了输入和输出；
- `call-linked`：仅由调用关系关联；
- `inferred`：由分析器推断；
- `gap`：存在未观测路径。

不得把时间相近的日志自动标记成数据依赖。

## 数据格式原则

- raw 事件使用追加式 JSONL 或等价不可变帧格式；
- 每个事件包含 `session_id`、进程、线程、序号、单调时间和 wall time；
- 请求、脚本、执行帧、对象快照和 VM 状态使用稳定 ID 互相引用；
- 原始值保存完整内容，派生索引只保存引用和摘要；
- 每个 trace 文件记录丢失事件、采样策略和启用的探针；
- 事件写入使用原子创建、短写处理和 session 独占文件，避免多进程互相覆盖。

## 安全与隐私边界

这是明确的本地原始证据模式，`manifest.json` 必须写入：

```json
{
  "contains_sensitive_data": true,
  "capture_mode": "raw",
  "telemetry": false
}
```

启动器负责：

- 创建或校验工程目录权限；
- 拒绝路径穿越和符号链接逃逸；
- 不把密码、Cookie 或 token 写入 stdout/stderr；
- 不自动复制到系统临时目录以外的位置；
- 不上传网络数据或 session 文件。

## 代码组织

建议把能力拆成以下边界：

- `project`：工程目录校验、权限和生命周期；
- `session`：session 创建、恢复、状态和 manifest；
- `evidence`：raw 文件、追加写入、索引和重建；
- `browser`：Camoufox 启动、代理、版本和进程管理；
- `observability`：网络、脚本、环境和执行事件；
- `adapters`：Google BotGuard 等目标特定 VM 解释器；
- `analysis`：调用图、值生成链、差分和报告；
- `mcp`/`cli`：薄入口层，只调用核心库。

目标适配器不得反向污染浏览器核心。例如 Google 的 `MI613e`、`B4hajb` 和 BotGuard 字段只应存在于 Google adapter 或独立分析模块。

## 第一阶段范围

第一阶段只实现能验证基础闭环的最小能力：

1. `project_dir` 必填启动契约；
2. session 隔离、manifest 和 raw 目录；
3. 网络、脚本、环境访问和调用帧关联；
4. 原始事件可靠落盘和索引重建；
5. CLI/MCP 查询 session、请求和脚本；
6. Google 登录场景中，从 `B4hajb` 请求反查到相关执行证据；
7. 浏览器真实 trace 与 Go VM 的首个分歧报告。

第一阶段不包含：

- 独立 GUI；
- 通用自动反混淆；
- 一键生成纯 Go 实现；
- 完整 JavaScript 引擎指令全量录制；
- 服务端内部判断观测；
- 将浏览器作为 `web_google` 运行时依赖。

## 验收标准

在本地代理环境下，使用一个全新工程目录启动浏览器，必须能够：

1. 因缺少 `project_dir` 拒绝启动；
2. 为每次启动创建唯一且隔离的 session；
3. 将原始网络、脚本和 trace 文件全部写入该 session；
4. 在异常退出后保留证据并标记 `incomplete`；
5. 从 raw 文件重建索引和报告；
6. 对 Google 登录样本关联 `B4hajb`、脚本、执行帧和环境输入；
7. 输出浏览器与 Go VM 的首个可核验分歧位置；
8. 全流程不依赖独立 GUI。

## 后续扩展

完成第一阶段后，再按实际证据增加：

- SpiderMonkey 深度执行 hook；
- Promise、Worker 和跨进程异步因果链；
- VM opcode/寄存器适配器 SDK；
- 值生成链查询语言；
- 两次执行的确定性差分与回放辅助；
- 可选的本地 Web viewer，但不改变 MCP/CLI 为主入口的定位。
