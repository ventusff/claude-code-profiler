# claude-code-profiler

[English](README.md) | **中文**

> 为 Claude Code 会话切一段窗口，量出墙钟时间、API 时间、工具时间、Token 成本，以及按桶（docker pull / build、数据集下载、checkpoint 下载、benchmark、test……）拆出的细粒度耗时。

`/profile start` 起一个窗口，做事，`/profile stop` 出报告。纯 Python 标准库实现，单文件脚本；不需要 OpenTelemetry，不需要后台守护进程，不需要改 Claude Code 任何配置。

适合：
- 想知道一段调试 / 跑 benchmark 到底慢在哪儿（API？工具？docker pull？）
- 想知道这一段对话烧了多少 token、按当前价格表估的 USD 成本
- 想把"这趟跑下来花了 X 分钟、Y 美元、其中 docker pull 占了 N 秒"这种数字粘进 issue / PR / 周报

---

## 用法 —— 就三行

```
/profile start             # 开一个 profile 窗口
…像平常一样跟 Claude 对话:跑 benchmark、调试、跑工具,怎么用都行…
/profile stop              # 关窗口、出报告
```

整个工作流就这三行。**会话中间不用塞额外提示、不用特殊语法、不用"记得记录这个"。**`stop` 时 profiler 自动回头扫一次 transcript,把 `start` ~ `stop` 之间发生过的事情聚合成下面那张表。一个会话里 `start → stop → start → stop …` 想开几次开几次,每次都是独立的 window。

---

## 它能算出来什么

一次 `/profile stop` / `profile status` 之后，终端默认输出大致长这样：

```
╭─ profile: bench-run [b214a5be]  18m24s wall
├─ time
│  wall                  18m24s
│  api time              4m12s
│  tool time sum         13m51s
│  tool time wall        12m38s   (critical path; sum > wall = parallel)
│  user-thinking            42s
│  idle/wait                52s
├─ api time by model
│  claude-opus-4-7        3m48s
│  claude-sonnet-4-6        24s
├─ tool time by bucket
│  docker_pull            6m11s
│  benchmark_run          4m02s
│  bash                   1m38s
│  coding                 1m20s
│  test                     40s
├─ top tools  (exact = solo bundle, ~ = N-way parallel split)
│  Bash               9m51s   n=37  ok=35  fail=2
│  Read               1m12s   n=58  ok=58  fail=0  (~12/58)
│  Edit                 48s   n=11  ok=11  fail=0
│  Write                21s   n=4   ok=4   fail=0
│  Agent              1m02s   n=2   ok=2   fail=0
├─ tokens
│  input              1.2M
│  output             38.4k
│  cache write        420.1k
│  cache read         8.30M
│  tool result (est)  612.0k
├─ cost
│  estimated         $   3.8412  (not billing truth)
├─ turns / errors
│  assistant turns           94
│    debug (w/tool)          71
│    sidechain                4
│  user prompts              12
│  tool calls               118
│  subagent calls             2
│  api errors                 0
│  retries                    0
│  compactions                1
│    pre-tokens         158.2k
│    post-tokens         38.4k
│  lines +312 / -88
╰─ artifacts under: /home/you/.local/state/claude-code-profiler/windows/20260430T163913Z__bench__d717a1
```

同一份数据还能用 `--format markdown` 出可粘贴到 issue 的 Markdown 表格，或者 `--format json` 出完整结构化字段。

---

## 安装（推荐：作为 Claude Code 插件）

仓库本身就是一个合法的 Claude Code 插件 + 单插件 marketplace。三步装好，之后**任何**项目里的 Claude Code 会话都有 `/profile`。

### 步骤 1：把这个仓库注册为一个 marketplace

```bash
# 方式 A：从 GitHub 直接装（仓库公开后）
claude plugin marketplace add https://github.com/ventusff/claude-code-profiler

# 方式 B：先克隆到本地、再用本地路径注册
git clone https://github.com/ventusff/claude-code-profiler.git ~/src/claude-code-profiler
claude plugin marketplace add ~/src/claude-code-profiler
```

`claude plugin marketplace add` 接 GitHub URL、git URL，或本地路径；它会读 `.claude-plugin/marketplace.json` 把这个仓库注册成名为 **`claude-code-profiler`** 的 marketplace。

### 步骤 2：从这个 marketplace 装插件

```bash
claude plugin install claude-code-profiler@claude-code-profiler
```

格式是 `<plugin-name>@<marketplace-name>`，前者来自 `.claude-plugin/plugin.json` 的 `name`，后者来自 `.claude-plugin/marketplace.json` 的 `name`。这里两个名字凑巧都叫 `claude-code-profiler`。

默认安装到 user scope（`~/.claude/plugins/`），所以**任何工作目录下打开 Claude Code 都能用 `/profile`**。要改 scope 用 `-s project|local`。

### 步骤 3：验证

```bash
claude plugin list
```

能看到 `claude-code-profiler` 即装好。重新打开（或新开）一个 Claude Code 会话，输入 `/profile`，应当看到 `claude-code-profiler: no active window.`。

之后的日常用法：

```
/profile start bench-run --tag run=1 --note "robocasa eval"
…正常做事…
/profile stop
```

### 升级 / 卸载

```bash
claude plugin update claude-code-profiler
claude plugin marketplace update claude-code-profiler   # 拉取仓库最新提交
claude plugin uninstall claude-code-profiler
```

### 仅本会话临时加载（开发用）

如果你正在改这个仓库本身、不想真装：

```bash
cd /any/project
claude --plugin-dir /path/to/claude-code-profiler
```

只在这个会话生效，不污染全局 plugin 列表。

---

## 备用方案：直接当 Python 脚本跑

不想用插件机制（比如 CI 环境、没装 Claude Code、或你只是想验证一下指标算得对不对）也行。`scripts/cc_profiler.py` 是单文件、纯 stdlib、Python 3.10+：

```bash
git clone https://github.com/ventusff/claude-code-profiler.git
cd claude-code-profiler

python3 scripts/cc_profiler.py start bench-run --tag run=1 --note "robocasa eval"
# … 在同一台机器、同一个 Claude Code 会话里继续做事 …
python3 scripts/cc_profiler.py stop --format table
```

注意：脚本通过环境探测当前会话的 transcript 路径——所以**必须在同一台机器、同一个 Claude Code 会话所在的 cwd**（或显式 `--session …`）运行 `start` / `stop`。这种用法没有 `/profile` 慢命令的便利，但所有指标完全一致。

---

## 子命令

| 命令 | 行为 |
|---|---|
| `start [name] [--tag k=v]... [--note "..."]` | 开一个窗口。落盘 session_id、transcript 路径、cwd、git sha & dirty、`CLAUDE_*/ANTHROPIC_*/OTEL_*` 环境快照、当前模型。已存在活跃窗口时拒绝。 |
| `status` | 看活跃窗口已经累计了多少时间、几条 turn。 |
| `mark <label>` | 在当前窗口里追加一个带时间戳的标签（"docker pull starts here"、"benchmark begins"），出报告时一起输出。 |
| `stop [--format=table\|markdown\|json] [--export DIR]` | 扫描 transcript [start_ts, now]，算指标，渲染报告，落盘 artifacts，清掉活跃指针。 |
| `reset` | 不出报告，直接丢掉活跃窗口指针。 |

每台机器同时只允许一个活跃窗口（v0 的有意约束；并发窗口在 v1）。一个 session 里可以开-停-再开-停多次，每次都是独立 window。

---

## 它怎么工作

Claude Code 把每条会话写到 `~/.claude/projects/<slug>/<session_id>.jsonl` —— `cc_profiler.py` 在 `start` 时记下 session 和起始时间戳，`stop` 时回头扫这段时间区间里的 JSONL 行，按事件类型聚合：

- **API 时间** = 上一条 `user` / tool_result → 下一条 `assistant` 的间隔，按 turn 的 `message.model` 分桶；
- **工具时间** = 父 `assistant` 消息 → 该批次最后一个 `tool_result` 的墙钟差（"bundle wall"），按工具名 + 命令分类（Bash 走正则，分到 `docker_pull / docker_build / dataset_dl / checkpoint_dl / benchmark_run / test / infra / download / bash`）；
- **Token 与成本** = 每条 assistant turn 的 `usage` × 该 turn 实际 `message.model` 对应的价格。多模型混跑时按 turn 算，不是按整段平均；
- **Compaction、retry、API error、subagent、idle/wait** 都是从 transcript 标记里识别。

完整指标列表见 `notes/plan.md` 里的 "Metric definitions" 一节。

### 并行工具调用的精度限制

如果一个 assistant 消息里同时发了 N 个 `tool_use`（典型场景：并发跑 Read），Anthropic 的协议要求所有 tool_result 一起返回，所以它们在 transcript 里会**共享同一个 `user` 行的时间戳**。这意味着：

- **N=1**（独跑工具）：单工具耗时 = 这个 bundle 的 wall 时间，**精确**。
- **N>1**（并行 bundle）：单工具耗时只能近似为 `bundle_wall / N`，profile 里这些会被标 `~`，并在 `tool_approx_count_by_tool` 里累计。

想拿到**真实**的并行单工具起止时间，唯一办法是装 PreToolUse / PostToolUse hook（见下文）。

---

## 可选：用 hook 拿到精确单工具时间

把下面这段写进 `.claude/settings.json`（项目级或用户级）就能让 profiler 同时吃 hook 事件，覆盖 transcript 的近似值。已用插件方式安装的话，路径用 `${CLAUDE_PLUGIN_ROOT}`：

```json
{
  "hooks": {
    "PreToolUse":       [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook PreToolUse"}]}],
    "PostToolUse":      [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook PostToolUse"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook UserPromptSubmit"}]}],
    "Stop":             [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook Stop"}]}],
    "SessionEnd":       [{"hooks": [{"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/cc_profiler.py _hook SessionEnd"}]}]
  }
}
```

如果是 git clone 直接当脚本跑，把 `${CLAUDE_PLUGIN_ROOT}` 换成 `${CLAUDE_PROJECT_DIR}` 或仓库绝对路径即可。

每个 hook 拿到 Claude 推过来的 JSON payload，写一行进当前活跃 window 的 `events.from_hooks.jsonl`。**没活跃 window 时所有 hook 静默退出**，不会污染你正常的会话。

---

## 输出 artifacts

每次 `stop` 后会在 `$XDG_STATE_HOME/claude-code-profiler/windows/<window_id>/`（默认 `~/.local/state/claude-code-profiler/windows/<window_id>/`）下落：

```
profile.json              # 完整结构化报告，含所有指标和 per-turn 拆分
profile.md                # 可粘贴到 issue / PR 的 Markdown 报告
events.jsonl              # 标准化事件流（turn_start / tool_start / mark / compaction / error / …）
transcript.snippet.jsonl  # 这段窗口里 transcript 的原始行（用于回放 / 复算）
state.json                # start 时冻结的元数据（git sha、env 快照、起始 ts）
```

`stop --export DIR` 会把整组 artifacts 同时拷一份到 `DIR/<window_id>/`，方便归档进 benchmark run 目录。

---

## 价格表

内置一份 2026-04 的 USD/百万 token 价格表，覆盖 Opus 4 / 4.7、Sonnet 4 / 4.5 / 4.6、Haiku 3.5 / 4.5。匹配规则是 `model.startswith(prefix)`，没匹中的模型 cost 报 `null` 并在终端打 warning。

```
claude-opus-4-7  : (input 15.00, output 75.00, cache_write_5m 18.75, cache_read 1.50)
claude-sonnet-4-6: ( 3.00, 15.00, 3.75, 0.30)
claude-haiku-4-5 : ( 1.00,  5.00, 1.25, 0.10)
…
```

`stop --prices "opus:15,75,18.75,1.5;sonnet:3,15,3.75,0.3"` 可在线覆盖。1h 缓存写按 5m 价的 2× 计。

> ⚠ 这是**估算**，不是 Anthropic 计费源。用作"哪一段烧得多 / 不同模型对比"够用，不要用作账单依据。

---

## 已知限制

- **OTel 不支持**（v0 主动放弃）。如果你已经在跑带 OTel 的 Claude Code，profiler 不会读 OTel；之后 v1 会加。
- **transcript 之外的耗时算不进去**：用户离开终端去喝咖啡的 5 分钟会被算成 `idle/wait`（默认阈值 300s 之上的 user 侧 gap）。
- **并行工具的单工具耗时**：transcript-only 模式下是近似（见上文）。要精确请装 hook。
- **同一时刻只能开一个窗口**。

---

## 仓库结构

```
claude-code-profiler/
├── .claude-plugin/
│   ├── plugin.json             # Claude Code 插件清单
│   └── marketplace.json        # 单插件 marketplace 清单
├── skills/
│   └── profile/
│       └── SKILL.md            # /profile 派发器
├── scripts/
│   └── cc_profiler.py          # 单文件实现，纯 stdlib
├── notes/                      # 设计 / 决策记录（gitignored）
│   └── plan.md
├── README.md                   # English
└── README_cn.md                # 中文
```

---

## Roadmap

- **v0**（已可用）：transcript-only，单窗口，五个子命令，table / markdown / json 三种输出，作为合法 Claude Code 插件分发。
- **v1**：hook 增强（精确单工具耗时）、可选 OTel 摄入、并发窗口、`update-config` 一键装 hook。
- **v2**：批量 run launcher（移植自 SWE-Skills-Bench-dev）+ 跨 run 聚合（CSV / JSON）。
- **v3**：Web/HTML 报告（如果有人需要）。
