## 1. 2026-07-13 10:02:08 +08

本次调整围绕 AgentDebugX 论文中的两阶段闭环重新整理代码结构：

```text
Diagnose = Detect -> Attribute -> Recover
Rerun = checkpoint + retry directive + branch comparison / executor
```

### 结构重构

- 新增 `src/agentdebug/schema/`
  - 承载 portable artifact/schema：
    `AgentTrajectory`、`AgentEvent`、`DiagnosticReport`、`FailureFinding`、taxonomy 等。
  - 原 `agentdebug.core.models`、`agentdebug.core.taxonomy` 现在是兼容 shim。

- 新增 `src/agentdebug/runtime/`
  - 承载运行基础设施：
    storage、LLM client、event bus、plugin registry、GUI taxonomy bridge、LLM channel。
  - 原 `agentdebug.core.storage`、`agentdebug.core.llm`、`agentdebug.core.events`、`agentdebug.core.plugins` 等现在是兼容 shim。

- 重构 `src/agentdebug/diagnose/`
  - 新主结构：
    ```text
    diagnose/
      pipeline.py
      detect/
        rules/
      attribute/
      recover/
    ```
  - `diagnose/pipeline.py` 新增 `DiagnosePipeline`，明确串联 Detect -> Attribute -> Recover。
  - `diagnose/detect/` 承载 visible failure 检测：
    heuristic analyzer、detectors、LLM judge、taxonomy induction。
  - `diagnose/detect/rules/` 承载 deterministic rule packs：
    core、agenterrorbench、gui、registry、base。
  - `diagnose/attribute/` 承载 root-cause attribution：
    attribution backends、MOE localizer、DeepDebug、DeepDebug memory。
  - `diagnose/recover/` 承载 recovery strategies：
    DeepDebugRecovery、Reflexion、CRITIC、SelfRefine、AutoManual、SagaRollback。

- 新增 `src/agentdebug/rerun/`
  - 承载论文中的 Rerun 阶段：
    `RerunRequest`、`RerunCheckpoint`、`RerunDirective`、branch comparison、local proxy evaluation、executor protocol。
  - 不自动执行外部工具，仍保持 policy-gated rerun 语义。

- 新增顶层 `src/agentdebug/hub/`
  - Error Hub 真实实现迁移到顶层。
  - 原 `agentdebug.diagnose.actions.hub.*` 保留为兼容 shim。

- 新增顶层 `src/agentdebug/integrations/`
  - Claude skill、OpenHands、debug skill generator 真实实现迁移到顶层。
  - `agentdebug_skill/` 资源目录迁移到 `src/agentdebug/integrations/agentdebug_skill/`。
  - 原 `agentdebug.diagnose.actions.integrations.*` 保留为兼容 shim。

### 兼容性处理

- 旧 import 路径继续可用，包括：
  - `agentdebug.core.*`
  - `agentdebug.models`
  - `agentdebug.storage`
  - `agentdebug.attribution`
  - `agentdebug.recovery`
  - `agentdebug.diagnose.actions.*`
  - `agentdebug.diagnose.rules.*`
- 旧 CLI 用法继续保留。
- `agentdebug diagnose examples/sample_trace.json` 现在默认可直接运行本地 heuristic pipeline，不再强制要求显式 `--attributor` 和 `--recovery`。

### 打包与文档

- 修复 `pyproject.toml` extras：
  - 补齐 `crewai`、`openai-agents`、`opentelemetry-*`、`fastapi`、`uvicorn`、`huggingface_hub` 等 optional dependency 声明。
  - 更新 skill package data 路径到 `src/agentdebug/integrations/agentdebug_skill/**/*.md`。
- 新增根目录 `.gitignore`，忽略 `.agentdebug/`、venv、缓存、coverage、构建产物、本地 env 等。
- 更新 `README.md` 的 repository layout。
- 更新 `docs/ARCHITECTURE.md` 中 DeepDebug、MOE、runtime GUI bridge 的实现路径。

### 测试与验证

- 新增 `tests/test_public_api.py`，覆盖：
  - 公共 API trace record/analyze
  - CLI help
  - CLI diagnose 默认 heuristic pipeline
  - 新架构 facade 与旧实现兼容
  - `DiagnosePipeline.local_default()`
  - Poetry extras 一致性
- 已运行并通过：
  - `python -m pytest tests -q`
  - `python -m compileall -q src/agentdebug tests`
  - `python -m agentdebug.cli --help`
  - `python -m agentdebug.cli diagnose examples/sample_trace.json`
  - `python -m agentdebug.cli diagnose examples/sample_trace.json --rule-pack core`
  - 新旧 import 路径一致性检查

### 当前注意点

- `diagnose/actions/` 和 `diagnose/rules/` 现在应视为 compatibility paths，不再是新架构主线。
- `AgentDebugX_EMNLP_demo.pdf` 是本地未跟踪文件，本次没有修改。
- `ruff` 当前环境未安装，因此没有运行 lint。

## 2. 2026-07-13 10:12:50 +08

本次调整把 `diagnose/detect/rules` 从“裸 Python 模块规则包”升级为更接近插件系统的组件结构。

### Rule Pack 组件化

- 将真实 rule pack 实现从单个 `.py` 文件迁移到目录化组件：

  ```text
  src/agentdebug/diagnose/detect/rules/packs/
    core/
      manifest.json
      rules.py
      __init__.py
    agenterrorbench/
      manifest.json
      rules.py
      __init__.py
    gui/
      manifest.json
      rules.py
      __init__.py
  ```

- 每个 rule pack 新增 `manifest.json`，描述组件元信息：
  - `id`
  - `name`
  - `stage`
  - `description`
  - `entrypoint`
  - `capabilities`
  - `dependencies`
  - `cost_profile`
  - `enabled_by_default`

- 新增 `RulePackMetadata` 数据类型，作为 manifest 的结构化表示。

### Registry 改造

- `diagnose/detect/rules/registry.py` 不再硬编码 `if pack == "core"` 这种加载逻辑。
- 现在通过 manifest 的 `entrypoint` 动态加载规则模块。
- 新增：
  - `list_rule_packs()`
  - `get_rule_pack_metadata(pack_id)`
- 保留原有：
  - `available_rule_packs()`
  - `resolve_rule_pack_names()`
  - `load_event_rules()`
  - `load_trajectory_rules()`

### 兼容性处理

- 旧路径继续可用：

  ```python
  from agentdebug.diagnose.detect.rules.core import KeywordRule
  from agentdebug.diagnose.rules.core import KeywordRule
  ```

  两者指向同一个实现。

- CLI 用法不变：

  ```bash
  agentdebug diagnose examples/sample_trace.json --rule-pack core
  ```

### 打包

- 更新 `pyproject.toml`，把 rule pack manifest 加入 package include：

  ```text
  src/agentdebug/diagnose/detect/rules/packs/*/manifest.json
  ```

### 测试与验证

- 扩展 `tests/test_public_api.py`：
  - 验证 rule packs 来自 manifest-backed components。
  - 验证 `RulePackMetadata`。
  - 验证新旧 import 路径兼容。
- 已运行并通过：
  - `python -m pytest tests -q`
  - `python -m compileall -q src/agentdebug tests`
  - `python -m agentdebug.cli diagnose examples/sample_trace.json --rule-pack core`
  - rule pack manifest 加载检查：
    `core`、`agenterrorbench`、`gui` 均可发现并加载。

## 3. 2026-07-13 10:16:14 +08

本次调整把原来的单文件 `src/agentdebug/cli.py` 拆成包结构，让 CLI 入口变薄，并为后续继续拆 workflow 做准备。

### CLI 包结构

- 删除单文件入口 `src/agentdebug/cli.py`，改为包：

  ```text
  src/agentdebug/cli/
    __init__.py
    __main__.py
    main.py
    legacy.py
    commands/
      __init__.py
      diagnose.py
      ingest.py
      store.py
      config.py
      rerun.py
      serve.py
      hub.py
      integrations.py
      doctor.py
  ```

- `cli/main.py` 现在负责：
  - parser 组装
  - 子命令注册
  - handler 分发

- `cli/commands/` 现在负责暴露各子命令 workflow 入口：
  - `diagnose.run`
  - `ingest.run`
  - `store.list_traces`
  - `store.show_trace`
  - `rerun.run`
  - `serve.run`
  - `hub.run`
  - `integrations.run`
  - `doctor.run`

### 兼容性处理

- `agentdebug.cli:main` 仍然可用，`pyproject.toml` 的 script entry point 不需要改。
- `python -m agentdebug.cli` 仍然可用，通过新的 `cli/__main__.py` 进入。
- 原命令不变：

  ```bash
  agentdebug diagnose ...
  agentdebug ingest ...
  agentdebug rerun ...
  agentdebug hub ...
  agentdebug integrations ...
  ```

- 为降低风险，原 `cli.py` 的大部分共享实现先迁入 `cli/legacy.py`。
  - 当前 `legacy.py` 是兼容和共享实现层。
  - 下一步可以继续把每个 command 的真实逻辑从 `legacy.py` 逐个抽到 `cli/commands/*`。

### 测试与验证

- 扩展 `tests/test_public_api.py`：
  - 验证 `agentdebug.cli.main` 可调用。
  - 验证 `agentdebug.cli.commands.diagnose.run` 和 `ingest.run` 可导入。
- 已运行并通过：
  - `python -m pytest tests -q`
  - `python -m compileall -q src/agentdebug tests`
  - `python -m agentdebug.cli --help`
  - `python -m agentdebug.cli diagnose examples/sample_trace.json --rule-pack core`

## 4. 2026-07-13 10:21:00 +08

本次调整为 Diagnose 的三个阶段建立统一 component registry，使 Detect / Attribute / Recover 都遵循同一套组件协议。

### 统一组件协议

- 新增 `src/agentdebug/diagnose/registry.py`。
- 新增 `DiagnoseComponentMetadata`，统一描述 Diagnose 组件：
  - `id`
  - `stage`
  - `name`
  - `description`
  - `entrypoint`
  - `capabilities`
  - `dependencies`
  - `cost_profile`
  - `enabled_by_default`

- 新增统一 registry API：
  - `list_components(stage=None)`
  - `available_components(stage=None)`
  - `get_component_metadata(component_id)`
  - `load_component(component_id)`
  - `is_component_available(component_id)`

### Manifest-backed 组件发现

- 新增 Diagnose 组件 manifest 目录：

  ```text
  src/agentdebug/diagnose/component_manifests/
    detect/
    attribute/
    recover/
  ```

- Detect 组件 manifest：
  - `detect.heuristic`
  - `detect.llm_judge`

- Attribute 组件 manifest：
  - `attribute.heuristic`
  - `attribute.all_at_once`
  - `attribute.step_by_step`
  - `attribute.binary_search`
  - `attribute.counterfactual`
  - `attribute.deepdebug`

- Recover 组件 manifest：
  - `recover.deepdebug`
  - `recover.reflexion`
  - `recover.critic`
  - `recover.self_refine`
  - `recover.auto_manual`
  - `recover.saga_rollback`

- Rule pack manifest 也被纳入统一 registry，作为 Detect 组件暴露：
  - `detect.rules.core`
  - `detect.rules.agenterrorbench`
  - `detect.rules.gui`

### API 导出

- `agentdebug.diagnose` 现在导出统一 registry API：

  ```python
  from agentdebug.diagnose import list_components, load_component
  ```

### 打包

- 更新 `pyproject.toml`，把 Diagnose component manifests 纳入 package include：

  ```text
  src/agentdebug/diagnose/component_manifests/**/*.json
  ```

### 测试与验证

- 扩展 `tests/test_public_api.py`：
  - 验证 Detect / Attribute / Recover 三阶段组件均可发现。
  - 验证 component metadata 结构。
  - 验证 `load_component()` 能加载类和 rule-pack 模块。
- 已运行并通过：
  - `python -m pytest tests -q`
  - `python -m compileall -q src/agentdebug tests`
  - `python -m agentdebug.cli diagnose examples/sample_trace.json --rule-pack core`
  - 手动列出 registry 中的全部组件，确认三阶段和 rule packs 均已注册。

## 5. 2026-07-13 10:26:13 +08

本次调整为复杂 workflow 补充最小英文 README/spec，使每个主要模块都能说明自身职责、流程、依赖、使用场景和扩展边界。

### 新增 Workflow 文档

- 新增 `src/agentdebug/cli/README.md`
  - 说明 CLI 只是 workflow 入口，应保持 thin command layer。
  - 记录 `diagnose`、`ingest`、`rerun`、`hub`、`integrations`、`serve`、`doctor` 等命令的定位。
  - 明确新命令应放在 `cli/commands/`，真实逻辑应下沉到对应 workflow package。

- 新增 `src/agentdebug/diagnose/README.md`
  - 说明 Diagnose 的主流程：

    ```text
    Detect -> Attribute -> Recover
    ```

  - 记录统一 component registry 的 API 和 manifest 元信息字段。
  - 明确 `diagnose/actions` 和 `diagnose/rules` 应继续作为 compatibility shim。

- 新增 `src/agentdebug/diagnose/detect/README.md`
  - 说明 Detect 负责发现 visible failure signals。
  - 记录 rule pack 的插件式结构：
    `manifest.json` + `rules.py` + `__init__.py`。
  - 明确 rule metadata 用 JSON，执行逻辑保留 Python。

- 新增 `src/agentdebug/diagnose/attribute/README.md`
  - 说明 Attribute 负责从 detected evidence 推断 root causes。
  - 记录 heuristic、step-by-step、binary search、counterfactual、DeepDebug、MOE 等 attribution 类型。
  - 明确 Attribute 不负责生成 recovery action。

- 新增 `src/agentdebug/diagnose/recover/README.md`
  - 说明 Recover 负责把 root causes 转成 recovery strategies。
  - 记录 Reflexion、CRITIC、Self-Refine、DeepDebug、AutoManual、SagaRollback 等模式。
  - 明确 Recover 不执行外部系统，执行属于 Rerun。

- 新增 `src/agentdebug/ingest/README.md`
  - 说明 Ingest 负责把外部框架或 benchmark traces 规范化为 AgentDebugX schema。
  - 记录 raw、LangGraph、CrewAI、OpenAI Agents、OpenTelemetry、GAIA/ODR、OSWorld 等 adapter 家族。

- 新增 `src/agentdebug/rerun/README.md`
  - 说明 Rerun 是 Diagnose 之后的第二阶段。
  - 记录 request construction、checkpoint/directive、executor dispatch、branch comparison 等流程。
  - 明确 executor 可能有外部副作用，需要显式边界。

- 新增 `src/agentdebug/hub/README.md`
  - 说明 Error Hub 负责 failure case 的 scrub、bundle、store、publish。
  - 明确远端发布依赖 backend 和 credentials。

- 新增 `src/agentdebug/integrations/README.md`
  - 说明 integrations 负责生成外部工具可消费的资产。
  - 记录 Claude-style debugging skill、OpenHands、debug skill templates。

- 新增 `src/agentdebug/inspect/README.md`
  - 说明 Inspect 负责人工查看 traces 和 reports。
  - 明确 inspection 默认只读，UI server 依赖 `ui` extra。

### 打包

- 更新 `pyproject.toml`，把包内 workflow README 纳入发布文件：

  ```text
  src/agentdebug/**/README.md
  ```

### 当前设计约束

- 文档使用英文，方便开源项目使用。
- 文档保持最小 spec 风格，不替代完整用户文档。
- 每个 workflow README 都明确：
  - purpose
  - when to use
  - flow
  - dependencies
  - extension rules

## 6. 2026-07-13 10:32:22 +08

本次调整把 `rerun/` 从类型和 evaluator 集合推进为真正的论文第二阶段 workflow。

### Rerun 第二阶段 Orchestration

- 新增 `src/agentdebug/rerun/workflow.py`。
- 新增 `RerunPlan`：
  - 表示执行前的可审计 rerun plan。
  - 包含 `RerunRequest`、执行状态、是否需要 executor、是否需要人工 approval、reason、metadata。

- 新增 `RerunWorkflowResult`：
  - 表示 Rerun 阶段输出。
  - 默认只包含 plan。
  - 如果显式执行，则包含 executor result 和 local proxy evaluation。

- 新增 `RerunWorkflow`：
  - `RerunWorkflow.suggest_only()`：默认安全入口，不执行外部系统。
  - `plan(report, trajectory=None)`：从 Diagnose report 生成第二阶段计划。
  - `run(report, trajectory, execute=False)`：默认只返回 plan；只有传入 executor 且 `execute=True` 时才执行。

- 新增 `build_rerun_request(report, trajectory=None)`：
  - 从 `DiagnosticReport` 构造 executor-facing `RerunRequest`。
  - 自动选择 root-cause checkpoint：
    `root_cause_event_id`、`root_cause_step_index` 或 finding 中的 event。
  - 自动选择 retry directive：
    recovery proposal、report suggestion、finding suggestion，最后才 fallback 到默认说明。

### Executor 边界

- `RerunWorkflow` 明确区分：
  - planning
  - execution
  - evaluation
- 默认不会执行任何外部系统。
- 只有调用方传入符合 `RerunExecutor` protocol 的 executor，并显式设置 `execute=True`，才会执行 rerun。
- 执行后的 branch 会通过 `evaluate_local_proxy()` 做本地对比。

### CLI 集成

- 更新 `agentdebug rerun` 的实现：
  - 继续兼容原参数：
    `agentdebug rerun <diagnostic_report> --trajectory <path-or-trace-id>`
  - 输出中新增 `plan` 字段，内容来自新的 `RerunWorkflow`。
  - 继续保留原有 `diagnostic_report`、`trajectory`、`llm` 字段，降低脚本兼容风险。

- 增加输入保护：
  - 如果用户误把 trajectory JSON 当作 diagnostic report 传入，CLI 会明确报错。

### API 导出

- `agentdebug.rerun` 现在导出：
  - `RerunPlan`
  - `RerunWorkflow`
  - `RerunWorkflowResult`
  - `RerunExecutor`
  - `RerunResult`
  - `build_rerun_request`

### 文档

- 更新 `src/agentdebug/rerun/README.md`：
  - 说明 Rerun 的第二阶段流程。
  - 记录 `RerunPlan`、`RerunWorkflow`、`build_rerun_request()` 和 `RerunExecutor` 的职责。
  - 强调 execution 需要显式 executor 和显式执行请求。

### 测试与验证

- 扩展 `tests/test_public_api.py`：
  - 覆盖 `RerunWorkflow.plan()`。
  - 覆盖 `RerunWorkflow.run(..., execute=False)` 的默认 plan-only 行为。
  - 覆盖传入 fake executor 后的 execute + local proxy evaluation。
  - 覆盖 `agentdebug rerun <report> --trajectory <trajectory>` 输出第二阶段 plan。

- 已运行并通过：
  - `python -m pytest tests -q`
  - `python -m compileall -q src/agentdebug tests`

- 已手动验证误用输入：
  - `python -m agentdebug.cli rerun examples/sample_trace.json`
  - 结果按预期报错：
    `expected a DiagnosticReport JSON, got a trajectory-like payload`
