# claude-code-profiler

[English](README.md) | **中文**

> 在 Claude Code 会话里 profile 一段时间，量出墙钟时间、API 时间、工具时间、Token 成本，以及按桶（docker pull / build、数据集下载、checkpoint 下载、benchmark、test……）拆出的细粒度耗时。

`/profile start` 起一个 profile，做事，`/profile stop` 出报告。纯 Python 标准库实现，单文件脚本；不需要 OpenTelemetry，不需要后台守护进程，不需要改 Claude Code 任何配置。

适合：
- 想知道一段调试 / 跑 benchmark 到底慢在哪儿（API？工具？docker pull？）
- 想知道这一段对话烧了多少 token、按当前价格表估的 USD 成本
- 想把"这趟跑下来花了 X 分钟、Y 美元、其中 docker pull 占了 N 秒"这种数字粘进 issue / PR / 周报

---

## 用法 —— 就三行

```
/profile start             # 开始一个 profile
…像平常一样跟 Claude 对话：跑 benchmark、调试、跑工具，怎么用都行…
/profile stop              # 结束 profile，出报告
```

整个工作流就这三行。**会话中间不用塞额外提示、不用特殊语法、不用"记得记录这个"。** `stop` 时 profiler 自动回头扫一次 transcript，把 `start` ~ `stop` 之间发生过的事情聚合成下面那张表。一个 Claude Code 会话里 `start → stop → start → stop …` 想开几次开几次，每次都是独立的 profile。

**忘了 `/profile start` 怎么办？** 用 `retro` —— 它直接扫已有的 transcript，按你指定的时间窗出同样的报告：

```
/profile retro                        # 整个会话从头到现在
/profile retro --since 30m            # 最近 30 分钟
/profile retro --since last-prompt    # 自从你最近一次发言
```

`retro` 写出的产物 bundle 跟 `stop` 一样，但**不动**当前的 active-profile 指针，所以正在跑 `/profile start` 时也能随手跑 `retro`，互不干扰。

整篇 README 里说到 **profile**（名词）就是指这样一段 start→stop 之间的测量。这是 profiler 工具领域的标准用法 —— cProfile、Go pprof、Linux `perf` 都用 "profile" 指这次测量产出的数据，跟我们这里的慢命令（`/profile`）、工具名（`claude-code-profiler`）、产物文件（`profile.json`、`profile.md`）正好对得上。

---

## 它能算出来什么

一次 `/profile stop` / `profile status` 之后，终端默认输出大致长这样：

```
 ╭─ profile: run [60943b15]  51m34s wall
 ├─ time
 │  wall                  51m34s
 │  api time              15m21s
 │  tool time sum         13m24s
 │  tool time wall        13m24s  (critical path; sum > wall = parallel)
 │  user-thinking          30.1s
 │  idle/wait             22m12s
 ├─ api time by model
 │  claude-opus-4-7                  10m22s
 │  claude-sonnet-4-6                 4m59s
 │  (main)                            28.6s
 │  (subagent)                       14m53s
 ├─ tool time by bucket
 │  bash                              5m47s
 │  benchmark_run                     4m26s
 │  coding                            2m12s
 │  test                              58.0s
 │  mcp                                0.4s
 ├─ top tools  (exact = solo bundle, ~ = N-way parallel split)
 │  Bash                 11m12s  n=85  ok=79  fail=6 (~17/85)
 │  Write                 1m35s  n=10  ok=9  fail=1
 │  Edit                  20.3s  n=9  ok=9  fail=0
 │  Read                  16.6s  n=38  ok=38  fail=0 (~11/38)
 │  mcp__plugin_nautilus_nautilus__lookup_benchmark     0.2s  n=1  ok=1  fail=0 (~1/1)
 ├─ tokens
 │  input                    147
 │  output                 26.4k
 │  cache write           201.1k  (5m=188.3k, 1h=12.9k)
 │  cache read             8.37M  (hit_ratio=0.98)
 │  thinking blocks            3  (0 tok est)
 │  tool result (est)      69.5k
 ├─ tokens / cost by source
 │  subagent   $ 13.4388  in=137  out=22.9k  cw=188.3k  cr=8.20M  calls=130
 │  main       $  1.0098  in=10  out=3.6k  cw=12.9k  cr=173.2k  calls=5
 ├─ tokens / cost by model
 │  claude-opus-4-7              $ 13.4222  in=96  out=20.8k  calls=86
 │  claude-sonnet-4-6            $  1.0264  in=51  out=5.6k  calls=49
 ├─ subagents (2 files)
 │  nautilus:policy-generator    $ 12.4124  in=86  out=17.2k  agents=1  calls=81
 │  nautilus:env-generator       $  1.0264  in=51  out=5.6k  agents=1  calls=49
 ├─ cost
 │  estimated         $  14.4486  (not billing truth)
 ├─ turns / errors
 │  assistant turns          135
 │    debug (w/tool)         131
 │    sidechain              130
 │  user prompts               6
 │  tool calls               146
 │  subagent calls             2  (2 agent files)
 │  api errors                 0
 │  retries                    0
 │  compactions                0
 │  stop reasons     tool_use=60, end_turn=4
 │  lines +885 / -39
 ╰─ artifacts under: /home/ventus/.local/state/claude-code-profiler/windows/20260503T112904Z__run__5162f
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

能看到 `claude-code-profiler` 即装好。重新打开（或新开）一个 Claude Code 会话，输入 `/profile`，应当看到 `claude-code-profiler: no active profile.`。

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
| `start [name] [--tag k=v]... [--note "..."]` | 开启一个新 profile。落盘 session_id、transcript 路径、cwd、git sha & dirty、`CLAUDE_*/ANTHROPIC_*/OTEL_*` 环境快照、当前模型。本会话已有活跃 profile 时拒绝。 |
| `status` | 看活跃 profile 已经累计了多长时间、几条 turn。 |
| `mark <label>` | 在当前 profile 里追加一个带时间戳的标签（"docker pull starts here"、"benchmark begins"），出报告时一起输出。 |
| `stop [--format=table\|markdown\|json] [--export DIR]` | 扫描 transcript [start_ts, now]，算指标，渲染报告，落盘 artifacts，清掉活跃指针。 |
| `retro [--since WHEN] [--until WHEN] [--name NAME] [--format=...] [--export DIR]` | 不需要事先 `start`，直接对 transcript 上 `[--since, --until]` 任意窗口出报告。产物跟 `stop` 一样，`state.json` 会标 `mode: "retroactive"`。**不动**活跃 profile 指针。 |
| `reset` | 不出报告，直接丢掉活跃 profile 指针。 |

每个 Claude Code 会话同时只允许一个活跃 profile。活跃 profile 指针是按 session 隔离的 —— 同一台机器上并发的多个 Claude Code 会话各有自己的指针，互不干扰（在 `tests/test_concurrent_sessions.py` 里有回归测试）。一个会话内 `start → stop → start → stop …` 没问题，同一个会话里同时开两个 profile 不行。（`retro` 是例外 —— 它从来不占活跃指针，所以 `start` 还在跑的时候随时能跑 `retro`。）

### `retro --since` / `--until` 接受的时间格式

`WHEN` 可以是：

- `now` —— 当前墙钟时间（`--until` 默认值）。
- `first` —— 父 transcript 第一条带时间戳的 row（`--since` 默认值）。
- `last-prompt` —— 最近一次真实的 user prompt（跳过 tool-result rows 和 sidechain 的 user 行）。适合"看看我最近这条消息触发了多少活儿"。
- ISO-8601 时间戳（如 `2026-05-04T11:26:45Z`）或裸的 epoch 秒数。
- 相对 duration（"多久之前"）：`30m`、`1h30m`、`2d12h`、`45s`。可以拼组合，空格随意。

`--since >= --until` 时 retro 退出码 2。

---

## 它怎么工作

### Claude Code 怎么存一个会话

Claude Code 把每条会话以 JSONL 写在 `~/.claude/projects/<slug>/` 下：

```
<slug>/
├── <session_id>.jsonl              # 父对话（isSidechain: false）
└── <session_id>/
    └── subagents/
        ├── agent-<aid>.jsonl       # 每次 Task 工具派发的子 agent 一份
        ├── agent-<aid>.meta.json   #   带 `agentType`、`description`
        └── …
```

子 agent **从不被合并到父文件里** —— 它们各自有自己的 JSONL，行带 `isSidechain: true`。只扫父文件（写起来最自然、大多数自制分析脚本就是这么干的）会静默地漏掉子 agent 的全部活动。我们实测的一次真实会话：父文件单独算是 \$5.93；父 + 3 个子 agent 文件合起来是 \$14.27，2.4× 的低估。详见 `notes/subagent-coverage.md`。

`/profile stop` 会同时走父文件**和它所有的同级子 agent 文件**，按时间戳合并排序，再聚合成下面这些指标。

### 聚合了哪些东西

- **Token 与成本** —— 每条 assistant 行的 `usage` block，按该 turn 实际 `message.model` 计价。多模型混跑（Opus + Sonnet，或主线用 Opus、子 agent 用 Haiku）按 turn 计费、不取平均。按 source / 按 model / 按 `agentType` 拆出的小账目加起来正好等于总数。
- **API 时间** —— 上一条 user / tool_result / compaction 行 → 下一条 assistant 行的间隔。按 source 拆成 `main` / `subagent`，再按 `message.model` 切一份。
- **工具时间** —— 父 assistant 消息 → 该批次最后一个 `tool_result` 的墙钟差（"bundle wall"）。按工具名分桶；Bash 命令走正则分到 `docker_pull` / `docker_build` / `dataset_dl` / `checkpoint_dl` / `benchmark_run` / `test` / `infra` / `download` / `bash`。同样有 `(main, subagent)` 拆分。
- **缓存** —— `cache_creation_input_tokens` 报总量，**同时**从 `usage.cache_creation` 拆出 5m vs 1h TTL 部分；再加一个 `cache_hit_ratio = cache_read / (input + cache_creation + cache_read)`，让你一眼看出 prompt caching 划不划算。
- **其他计数器** —— `thinking` block（extended-thinking 输出）、`usage.server_tool_use` 里的 Anthropic 侧 `web_search` / `web_fetch`、`stop_reason` 分布、retries（重复的 `requestId`）、API 错误（`stop_reason ∈ {error, refusal}`）、compaction（`type: summary` / `subtype: compact*`）、以及从 Edit / Write / MultiEdit 输入推算出的代码增删行数。

### 让总数靠谱的两条规则

**按 `(source, agent_id, message.id)` 去重。** Anthropic 的 SDK [文档明确说](https://code.claude.com/docs/en/agent-sdk/cost-tracking) 一个逻辑上的 assistant turn 可能被流式拆成多条 JSONL 行（每个 content block 一行 —— 文本、思考、并行 tool_use），共享同一个 `message.id` 和同一个 `usage` block。按行直接相加会把 token 算膨胀 2-6×。profiler 按 `(源文件, agent_id, message.id)` 三元组合并，所以这套去重也兜得住任何跨文件的 id 撞车。

**抑制父文件里 Agent / Skill / Task tool_use 的 bundle wall。** 父文件里一次 Task 工具的 bundle wall 等于*整个* 子 agent 的运行时间（从外面看就是这样）。子 agent 文件本身的 per-turn `api_time` + per-bundle 工具时间已经覆盖了那段间隔 —— 两边都加一遍就是双计。当子 agent 文件存在时，父侧的 Agent bundle 还会被记 COUNT（保证 `subagent calls` 是对的），但时间贡献清零。这条规则递归生效：嵌套子 agent 同样落到那个扁平的 `subagents/` 目录里，处理方式一致。

token 那一侧没有时间双计的对应风险：每行 transcript 是一次独立的 API 调用，自带独立 `usage`，跨文件相加就是精确加法。

### 报告字段怎么读

有几个字段不看说明不太直观：

- `tool time sum` vs `tool time wall` —— `sum` 是按桶累加 bundle wall；`wall` 是按不同父消息算的关键路径之和。transcript-only 模式下两者重合（没有 per-tool 起止可以区分）；接上 hook 之后才会分开。
- `api time by model` 下面的 `(main)` / `(subagent)` 行，是把上面 model 维度的总数按 source 文件拆。model 行和 source 行切的是同一个总数，不是相加关系。
- `tokens / cost by agent type` —— `agents=N` 是属于这个 type 的不同 `agent-<aid>.jsonl` 文件数；`calls=N` 是这些文件里的 API 调用次数。`agents=2 calls=100` 表示两次独立派发，每次大约 turn 了 50 来轮。
- `subagent calls (N agent files)` —— 括号里是合并进来的 `agent-*.jsonl` 文件数。如果是 0 但你预期有子 agent，那就是发现机制没看到 —— 去 `~/.claude/projects/<slug>/<session_id>/subagents/` 里看看。

### 并行工具调用的精度限制

如果一个 assistant 消息里同时发了 N 个 `tool_use`（典型场景：并发跑 Read），Anthropic 的协议要求所有 tool_result 一起返回，所以它们在 transcript 里**共享同一个 `user` 行的时间戳**：

- **N=1**（独跑工具）：单工具耗时 = 这个 bundle 的 wall 时间，**精确**。
- **N>1**（并行 bundle）：单工具耗时只能近似为 `bundle_wall / N`，profile 里这些会被标 `~`，并在 `tool_approx_count_by_tool` 里累计。

想拿到**真实**的并行单工具起止时间，唯一办法是装 PreToolUse / PostToolUse hook —— 见下文。

---

## 可选：用 hook 拿到精确的单工具时间

把下面这段写进 `.claude/settings.json`（项目级或用户级）就能让 profiler 同时吃 hook 事件，覆盖并行 bundle 的近似值：

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

`${CLAUDE_PLUGIN_ROOT}` 在用插件方式装好之后会自动解析。如果你是直接 git clone 当脚本跑，把它换成 `${CLAUDE_PROJECT_DIR}` 或仓库绝对路径即可。

每个 hook 拿到 Claude 推过来的 JSON payload，写一行进当前活跃 profile 的 `events.from_hooks.jsonl`。**没活跃 profile 时所有 hook 静默退出**，不会污染你正常的会话，所以可以放心一直挂着。

---

## 输出 artifacts

每次 `stop` 后会在 `$XDG_STATE_HOME/claude-code-profiler/windows/<profile_id>/`（默认 `~/.local/state/claude-code-profiler/windows/<profile_id>/`）下落：

```
profile.json              # 完整结构化报告，含所有指标和拆分维度，按 key 排序
profile.md                # 可粘贴到 issue / PR 的 Markdown 报告
events.jsonl              # 标准化事件流（turn_end / tool_end / mark / compaction / …）
transcript.snippet.jsonl  # 这段 profile 区间里 transcript 的原始行（用于回放 / 复算）
state.json                # start 时冻结的元数据（git sha、env 快照、起始 ts）
events.from_hooks.jsonl   # hook 事件（只有挂了 hook 才会有）
```

`profile.json` 是真值源 —— 表格和 markdown 里露出的字段都来自它，外加几个一屏放不下的（`tool_time_by_bucket_by_source_s`、`api_call_count_by_source`、`tokens_cost_by_agent_type`，等等）。

`stop --export DIR` 会把整组 artifacts 同时拷一份到 `DIR/<profile_id>/`，方便归档进 benchmark run 目录。

> 这个外层目录之所以叫 `windows/`，是为了兼容 0.1.x 写下来的状态文件；`state.json` 和 `profile.json` 里的 `window_id` / `window_dir` 字段名也是同样原因留着。文档行文里我们用 "profile"，但不打算破坏这层 on-disk 契约。

---

## 价格表

内置一份 2026-04 的 USD/百万 token 价格表，覆盖 Opus 4 / 4.7、Sonnet 4 / 4.5 / 4.6、Haiku 3.5 / 4.5。匹配规则是 `model.startswith(prefix)`，没匹中的模型 cost 报 `null`，并把模型 id 放进 `cost_uncovered_models`，让你能看到漏了哪个。

```
claude-opus-4-7  : (input 15.00, output 75.00, cache_write_5m 18.75, cache_read 1.50)
claude-sonnet-4-6: ( 3.00, 15.00,  3.75, 0.30)
claude-haiku-4-5 : ( 1.00,  5.00,  1.25, 0.10)
…
```

`stop --prices "opus:15,75,18.75,1.5;sonnet:3,15,3.75,0.3"` 可在线覆盖。1h 缓存写（`cache_creation.ephemeral_1h_input_tokens`）按 5m 价的 2× 计，存在时自动应用。

> ⚠ 这是**估算**，不是 Anthropic 计费源。用作"哪一段烧得多 / 不同模型对比"够用，不要拿去开发票。账单真值要看 [Claude Console 的 Usage 页](https://platform.claude.com/usage)。

---

## 已知限制

- **不读 OTel**（这是当前版本主动放弃的）。如果你已经在跑带 OpenTelemetry 的 Claude Code，profiler 直接忽略它。OTel 摄入是后续版本的候选项，主要用途是把 `claude_code.token.usage` 上的 `query_source`（main / subagent / auxiliary）属性拿来跟"按文件归源"做交叉校验。
- **transcript 之外的耗时算不进去。** 用户离开终端去喝咖啡的 5 分钟会被算成 `idle/wait`（默认阈值 300s 之上的 user 侧 gap）。profiler 没法区分你是真在喝咖啡、还是在仔细看一份长 plan。
- **并行 bundle 的单工具耗时**在 transcript-only 模式下是近似（`bundle_wall / N`）。要精确请装 hook —— 总数不受影响。
- **父和子 agent 活动有近距离重叠时，时间会略有膨胀。** 实测里 `api_time + tool_time + idle` 与 `wall` 的差值在 5-10% 量级。token 和成本不受影响 —— 那是 per-API-call 的累加，不是 gap 测量。
- **1h 缓存写 token 不是按 model 拆的。** 2× 加价是按各 model 占总 `cache_creation_input_tokens` 的比例分摊回去。只有一个 model 写 1h 缓存（常见情形）时是精确的，否则是近似。
- **每个 Claude Code 会话同时只能一个活跃 profile。** 同一台机器上并发的多个 Claude Code 会话各有自己的活跃 profile 指针（在 `tests/test_concurrent_sessions.py` 里有回归测试）；一个会话里 `start → stop → start → stop …` 没问题，同一个会话里同时开两个就不行。

---

## 测试

```bash
uv run pytest                                          # 全跑
uv run pytest tests/test_turn_counting.py -v           # 子 agent / 去重 / 计数器回归
uv run pytest tests/test_concurrent_sessions.py -v     # 跨会话的活跃指针隔离
```

`tests/_synth.py` 用来构造合成 JSONL transcript（父 + 子 agent 文件；text、tool_use、流式分片这几种 row 类型），让单个聚合行为可以脱离活跃 Claude Code 会话被锁定下来。`tests/conftest.py` 里的 `built_window` fixture（名字沿用了老的 on-disk 术语）开启一个 profile，把 `start_ts` 打补丁打成 0，这样测试里指定的过去时间戳就落得进 profile 区间。

---

## 仓库结构

```
claude-code-profiler/
├── .claude-plugin/
│   ├── plugin.json             # Claude Code 插件清单
│   └── marketplace.json        # 单插件 marketplace 清单
├── skills/
│   └── profile/
│       └── SKILL.md            # /profile 派发器（解析脚本路径，再 exec）
├── scripts/
│   └── cc_profiler.py          # 单文件实现，纯 stdlib
├── tests/
│   ├── _synth.py               # 单元测试用的 transcript 构造器
│   ├── conftest.py             # built_window fixture
│   ├── test_concurrent_sessions.py
│   └── test_turn_counting.py
├── notes/
│   └── subagent-coverage.md    # 0.2.0 设计笔记（已提交）
│                               # 其他 notes/*.md 是本地草稿（gitignored）
├── CHANGELOG.md
├── README.md                   # English
└── README_cn.md                # 中文（本文件）
```

---

## Roadmap

- **0.2.x（当前版本）** —— transcript-only，子 agent 全覆盖，token 总数去重正确，按 source / 按 model / 按 `agentType` 拆分，更细粒度的 Bash 分类，外加一套回归测试。作为 Claude Code 插件经由内置的单插件 marketplace 分发。
- **下一阶段** —— hook 默认接好（这样并行 bundle 也能拿到精确的单工具耗时）、`CLAUDE_CODE_ENABLE_TELEMETRY=1` 时可选摄入 OTel、单 Claude Code 会话内允许并发 profile、再加一个 `update-config` skill 帮你一键挂 hook。
- **再之后** —— 批量 run launcher + 跨 run 聚合（CSV / JSON）方便 benchmark 工作流；HTML 报告。
