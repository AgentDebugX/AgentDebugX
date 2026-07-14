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

## 7. 2026-07-13 12:29:14 +08

本次调整拆分 `inspect/ui/server.py`，让 UI server 不再承载所有 API、case db、debug branch、rerun evaluation 和 LLM continuation 逻辑。

### UI 模块拆分

- 新增 `src/agentdebug/inspect/ui/app.py`
  - 承载应用入口：
    `serve()`、`store_from_path()`。
  - 从 `routes.py` 引入 `build_app()`。

- 新增 `src/agentdebug/inspect/ui/routes.py`
  - 承载 FastAPI route registration。
  - 保留原有 API 路径和页面路径：
    `/`、`/overview`、`/space`、`/gui`、`/trace/{trace_id}`、
    `/api/v1/traces`、`/api/v1/cases`、`/api/v1/taxonomy`、
    debug continuation、debug branches、rerun-from-event 等。

- 新增 `src/agentdebug/inspect/ui/views.py`
  - 承载 HTML view rendering。
  - 原有 no-build HTML/CSS/JS 保持原样迁移到该文件。
  - `render_page()`、`render_space_page()`、`render_gui_page()`、`gui_embed_url()` 迁入此处。

- 新增 `src/agentdebug/inspect/ui/services.py`
  - 承载 UI 层使用的服务函数：
    serialization、overview aggregation、debug continuation prompt context、
    LLM completion request/response parsing、rerun local proxy evaluation。

- 新增 `src/agentdebug/inspect/ui/branch_store.py`
  - 承载本地 JSONL store：
    `typical_error_cases.jsonl` 和 `.agentdebug/debug_branches.jsonl`。
  - 包含 case records 和 debug branch records 的 read/append/delete/write 操作。

### 兼容性

- `src/agentdebug/inspect/ui/server.py` 现在变成 compatibility module。
- 旧 import 路径继续可用：

  ```python
  from agentdebug.inspect.ui.server import build_app, build_overview, render_page, serve
  from agentdebug.inspect.ui import build_app, render_page, serve
  ```

- `inspect/ui/__init__.py` 文档从 single-file 描述更新为 small FastAPI app。

### 行为保持

- 没有修改现有 UI 路由路径。
- 没有修改前端 HTML/CSS/JS 行为。
- 没有修改 case db 文件名和 debug branch 文件名。
- 没有修改 `agentdebug serve` 的调用方式。

### 测试与验证

- 已运行并通过：
  - `python -m pytest tests -q`
  - `python -m compileall -q src/agentdebug tests`

- 已验证旧路径 import：
  - `agentdebug.inspect.ui.server.build_app`
  - `agentdebug.inspect.ui.server.build_overview`
  - `agentdebug.inspect.ui.server.render_page`
  - `agentdebug.inspect.ui.server.serve`

- 已验证 `build_app()` 能正常创建 FastAPI app，关键路由缺失数为 0。

## 8. 2026-07-13 15:18:21 +08

本次调整清理仓库中的 API / URL 泄漏风险和构建产物残留。

### 泄漏扫描结论

- 使用严格模式扫描以下敏感形态，未发现真实密钥：
  - `sk-...`
  - GitHub token
  - Google API key
  - Slack token
  - PyPI token
  - 之前临时实验中出现过的 `sk-master`、`trycloudflare`、`xiamiapi` 相关片段

### 清理内容

- 更新 `src/agentdebug/runtime/llm.py`
  - 移除 docstring 中具体的临时 `trycloudflare.com` gateway URL。
  - 改成通用 OpenAI-compatible `/v1` endpoint 描述。

- 更新 `src/agentdebug/inspect/ui/views.py`
  - UI 不再把 rerun backend API key 写入 browser `localStorage`。
  - 如果旧浏览器缓存里已经有 `api_key`，下次加载会自动删除。
  - API key 仍可在本地 UI 中临时输入并发送给本地 backend，但不会持久化在前端缓存中。

- 删除已跟踪构建产物：
  - `dist/agentdebugx-0.3.0.tar.gz`
  - `dist/agentdebugx-0.3.0-py3-none-any.whl`

### 原因

- `dist/` 中的旧构建包仍包含旧源码里的临时 gateway URL。
- 虽然该 URL 不是密钥，但开源仓库不应该保留临时实验地址。
- `.gitignore` 已包含 `dist/`，删除已跟踪构建产物后，后续本地 build artifact 不会再被默认加入仓库。

### 测试与验证

- 已运行并通过：
  - `python -m pytest tests -q`
  - `python -m compileall -q src/agentdebug tests`

- 已重新扫描并确认无命中：
  - `trycloudflare.com`
  - `sprint-intellectual`
  - `reach-fruits`
  - `sk-master`
  - `sk-wGQ`
  - `xiamiapi`
  - 常见 API key / token 正则形态

- 已确认 `dist/` 下没有剩余文件。

## 9. 2026-07-13 15:36:23 +08

本次调整重写项目根目录 README，使其更适合高质量开源项目首页。

### Overview 资产

- 新增 `docs/assets/overview.pdf`
  - 存放完整系统总览 PDF。

- 新增 `docs/assets/overview.png`
  - 使用用户提供的高清截图作为 README 中直接展示的系统总览图。
  - 图片分辨率为 `2472x1494`，适合 GitHub README 展示。

### README 结构重写

- 重写 `README.md`，新的结构包括：
  - 项目定位和 badges
  - System Overview
  - Why AgentDebugX
  - Core Capabilities
  - Install
  - Quick Start: Python API
  - Quick Start: CLI
  - CLI Reference
  - Architecture
  - Component Model
  - Local UI
  - Privacy and Safety
  - Examples
  - Development
  - License

- README 中直接展示：

  ```text
  docs/assets/overview.png
  ```

- README 中保留高清 PDF 链接：

  ```text
  docs/assets/overview.pdf
  ```

### 内容调整

- 强化 AgentDebugX 的开源项目定位：
  - local-first debugging framework
  - Diagnose + Rerun two-stage loop
  - portable diagnostic artifacts
  - manifest-backed component model
  - privacy and safety boundaries

- 明确两阶段结构：

  ```text
  Diagnose = Detect -> Attribute -> Recover
  Rerun    = checkpoint -> retry directive -> branch execution -> evaluation
  ```

- 更新 Local UI 描述，反映 `inspect/ui/server.py` 已拆分为：
  - `app.py`
  - `routes.py`
  - `views.py`
  - `services.py`
  - `branch_store.py`

- 将 README 顶部的 Website、GitHub 和 Demo Video 文本链接改为紧凑的彩色入口卡片。

- 移除 System Overview 图片下方单独展示的 PDF 链接，完整 PDF 仍保留在 Detailed References 中。

- 扩充 Quick Start: CLI，覆盖无需 UI 的完整命令行工作流：
  - 外部 trace 导入与格式归一化
  - 本地确定性 Diagnose
  - LLM 配置与增强诊断
  - Rerun 配置生成
  - SQLite / JSONL trace 查询
  - Error Hub 与 host runtime integrations
  - 可选 Local UI 启动方式

- 补全 CLI Reference，并修复旧诊断示例缺少必需 pipeline 参数的问题。

- 新增 `docs/assets/logo.png`，并将 AgentDebugX logo 居中展示在 README 最顶部。

## 10. 2026-07-13 17:49:58 +08

本次更新为仓库增加 GitHub Actions 持续集成和发布包质量检查。

### CI 工作流

- 新增 `.github/workflows/ci.yml`，在以下事件中自动运行：
  - push 到 `main`
  - pull request
  - 手动触发

- 在 README 顶部增加 CI 状态 badge，直接展示默认分支的工作流状态。

- 增加 Python `3.9`、`3.10`、`3.11`、`3.12`、`3.13` 测试矩阵。

- 自动执行：
  - 核心 Pytest 测试
  - Python compile check
  - Ruff 静态检查
  - Mypy 类型检查（初期作为 advisory，不阻塞其他 CI）
  - source distribution 和 wheel 构建
  - Twine distribution metadata 检查
  - wheel 安装后的 CLI 和 import smoke test

### 发布包验证

- 新增 `tests/check_distribution.py`，验证 wheel 必须包含：
  - Python public package
  - `py.typed`
  - Diagnose component manifests
  - Detect rule-pack manifests
  - AgentDebugX skill documentation

- 拒绝包含 `.env` 和 `.pyc` 的 wheel。

### Python 兼容性

- 新增 `src/agentdebug/py.typed`，使 `Typing :: Typed` 发布声明与实际包内容一致。

- 为 Python 3.9 测试增加 `tomli` fallback，并由 CI 显式安装兼容依赖，保证测试矩阵覆盖项目声明支持的最低 Python 版本。

## 11. 2026-07-13 18:16:51 +08

本次更新系统扩充 AgentDebugX 自动化测试体系，并将核心覆盖率纳入 CI 门禁。

### 核心测试

- 将根测试套件从 `10` 个测试扩充到 `111` 个测试。

- 新增测试模块：
  - `test_schema_models.py`
  - `test_storage.py`
  - `test_ingest.py`
  - `test_diagnose_detect.py`
  - `test_diagnose_attribute.py`
  - `test_diagnose_recover.py`
  - `test_rerun.py`
  - `test_cli_commands.py`
  - `test_ui_routes.py`
  - `test_ui_services.py`
  - `test_hub.py`
  - `test_plugins_and_compat.py`
  - `test_llm_client.py`

- 覆盖以下关键契约：
  - Pydantic schema serialization 和 round-trip
  - JSONL / SQLite persistence
  - 多种外部 trace 格式导入
  - Detect / Attribute / Recover
  - Rerun approval、execution 和 evaluation
  - CLI exit code、兼容命令和 secret masking
  - FastAPI UI route 和 rerun API key persistence boundary
  - Error Hub scrub、bundle round-trip 和 artifact path safety
  - plugin manifest 与 legacy import compatibility
  - OpenAI-compatible completion、tool calling、embedding 和 token fallback

### 覆盖率与 CI

- 核心 branch coverage 从约 `27%` 提升到 `45.38%`。

- 新增独立 Coverage job，并设置 `40%` branch coverage 最低门槛。

- 新增 Pydantic v1 / v2 compatibility matrix。

- 新增 CUA Python `3.10` 到 `3.13` 独立测试矩阵；初期作为 advisory，避免重型 optional dependencies 阻塞核心包。

### 安全修复

- Error Hub bundle 现在拒绝绝对 artifact 路径和包含 `..` 的路径，防止 artifact 写出 bundle 目录。

### 贡献文档

- 补全 `CONTRIBUTING.md`，说明开发环境、测试分类、覆盖率命令、CUA 独立测试、质量检查和 PR 要求。

## 12. 2026-07-14 09:47:37 +08

本次更新修正 DeepDebug 的模块所有权，使代码结构与完整 Diagnose profile
的产品语义一致，同时保持现有 CLI 和 Python API 兼容。

### DeepDebug 结构迁移

- 新增 `src/agentdebug/diagnose/profiles/`，用于承载跨 Detect、Attribute、
  Recover guidance 的完整诊断工作流。
- 将 DeepDebug 真实实现从 `diagnose/attribute/deepdebug.py` 迁移到
  `diagnose/profiles/deepdebug.py`。
- `diagnose/attribute/moe.py` 和 `diagnose/attribute/deep_memory.py` 继续承载
  DeepDebug 使用的归因算法与记忆服务，不承担完整流程编排。
- 新增 `diagnose/profiles/README.md`，说明 profile 边界、DeepDebug 流程和
  read-only 约束。

### 兼容性

- 以下旧 import 路径继续导出同一组 canonical classes：
  - `agentdebug.deep`
  - `agentdebug.diagnose.deep`
  - `agentdebug.diagnose.attribute.deepdebug`
- 保留 `attribute.deepdebug` 组件 ID，避免破坏已有插件配置；其 entrypoint
  已改为 `agentdebug.diagnose.profiles.deepdebug:DeepDebugAnalyzer`。
- CLI 的 `--mode deep` / `--mode deepdebug` 用法及运行行为保持不变。

### 测试与文档

- 新增 DeepDebug canonical path、旧 import shim 和 registry entrypoint 的
  身份一致性测试。
- 更新 `docs/ARCHITECTURE.md` 中实现路径及 profile ownership 说明。

## 13. 2026-07-14 09:59:36 +08

本次更新在不改变 DeepDebug 定位算法和 LLM 调用顺序的前提下，将实现显式
对齐论文描述的四阶段流程，并增强类型安全与审计能力。

### 四阶段流程

- `DeepDebugAnalyzer` 现在明确执行并记录：
  1. `global_read`
  2. `structure_probe`
  3. `cross_examine`
  4. `diagnose_and_suggest`
- 候选一致时仍记录 cross-examination 结论，但不会增加额外 LLM 调用；
  候选冲突时才执行局部上下文仲裁。
- 最终诊断阶段固定已经选定的根因 step，不允许重新移动根因。

### 结构化结果

- 新增 `AttributionCandidate`、`ProbeDecision`、`StructureProbeResult`、
  `AdjudicationResult` 和 `AaoMoeAnalysis`。
- cascade / bisection 现在记录每次 upper/lower 区间、选择方向、置信度和
  最终候选窗口。
- 新增 `DeepDebugDiagnosis`，结构化承载最终 summary、evidence、suggestion
  和执行耗时。
- `DeepDebugResult` 新增 `analysis` 与 `diagnosis`，同时继续保留原有
  `report`、`rounds` 和代理属性。

### 兼容性与测试

- `aao_moe_attribute()` 保留原有 dictionary 返回契约，内部转发到新的
  `analyze_aao_moe()` 类型化接口。
- 新增 `tests/test_deepdebug_profile.py`，覆盖四阶段顺序、结构探测审计、
  一致候选的免调用仲裁和最终 report 映射。
- 本次只增强流程表达和可观测性，不宣称改变现有 benchmark 结果。

## 14. 2026-07-14 11:03:39 +08

本次更新强化 DeepDebug 根因事件身份和最终证据约束，避免多 Agent 重复 step
导致误定位，并阻止模型生成的虚构 evidence 进入诊断报告。

### 精确事件定位

- `AttributionCandidate` 新增 `event_id`，全局读取、结构探测和交叉仲裁均
  贯通事件身份。
- 事件解析顺序为：验证 `event_id` 与 step/agent 一致性，随后匹配
  `step_index + agent_name`，最后仅在 step 唯一时允许按 step 回退。
- 同一个 `step_index` 下不同 agent/event 的候选不再被视为 agreement，
  必须进入 cross-examination。
- cross-examination 改为选择 Candidate A/B 或明确 `event_id`，不再只返回
  无法区分重复 step 的整数。
- `AaoMoeAttributor`、DeepDebug profile 和 legacy dictionary 输出共享同一
  event identity。

### Evidence grounding

- 最终诊断 prompt 要求 evidence 使用 `{event_id, quote}`，且 quote 必须从
  展示的事件原文逐字引用。
- DeepDebug 会逐条验证 quote 是否存在于指定事件的 input、output 或 error；
  不存在、事件 ID 错误或旧字符串引用不唯一时直接拒绝。
- 新增 `DeepDebugEvidence`，记录验证通过的 event ID 与 quote。
- `DeepDebugDiagnosis` 新增 `evidence_references` 和
  `rejected_evidence_count`。
- 如果模型 evidence 全部无效，使用已解析根因事件的 error、output 或 input
  生成确定性兜底，不保留幻觉文本。
- report metadata 新增 `evidence_verified` 与 `rejected_evidence_count`。

### 测试

- 新增重复 `step_index`、不同 agent/event 的定位测试。
- 新增虚构 evidence 被拒绝并由根因事件原文兜底的测试。

## 15. 2026-07-14 13:50:17 +08

本次更新将 Rerun 从仅生成配置的入口升级为真正调用模型并生成新轨迹的第二
阶段，同时统一 CLI 与 UI 的执行实现。

### 内置 Rerun executor

- 新增 `LLMContinuationExecutor`，通过现有 `LLMClient` 调用
  OpenAI-compatible 模型执行 rollout。
- executor 输入包含任务目标、原失败轨迹、诊断结果生成的 retry directive
  和 checkpoint policy。
- 模型必须返回结构化 events；空事件或无效 JSON 会作为执行失败返回，不会
  生成伪成功结果。
- 输出统一规范化为新的 `AgentTrajectory`，包含 `rerun_of`、report ID、
  directive source、reported success 和 rollout summary 等元数据。
- 新增 `RolloutContext`、`build_rollout_prompt()`、
  `trajectory_from_rollout()` 和 OpenAI endpoint 规范化。

### CLI 与 UI

- `agentdebug rerun` 现在默认从任务开头执行完整模型 rollout，要求
  `--trajectory` 以及 URL、API key、model 配置。
- CLI 使用 `checkpoint_policy=from_start`，新轨迹从 step 1 开始。
- 新增 `--plan-only`，保留原先只生成可审计 rerun request 的能力。
- 使用 SQLite/JSONL store 时，CLI 会将新 trajectory 保存回同一 store。
- UI 继续支持从用户指定 event rerun，并改为复用同一个 executor；生成轨迹
  的首事件以指定 checkpoint event 为 parent。
- API key 只用于请求，不进入 CLI 输出、UI response 或 branch store。

### 能力边界

- 内置 executor 会真实调用模型并生成新的 observable trajectory。
- 它不会从离线日志恢复任意第三方工具进程；需要真实 LangGraph、OpenAI
  Agents 或 benchmark environment 工具执行时，应实现对应的
  runtime-specific `RerunExecutor`。

### 测试

- 新增 full-task CLI rollout、checkpoint parent、endpoint 兼容、trajectory
  normalization 和 secret boundary 测试。
- CLI、UI 和 Python workflow 共用同一 executor contract。

## 16. 2026-07-14 14:05:35 +08

本次更新将已经存在的 `DeepDebugRecovery` 正式接入 CLI，使 DeepDebug 的最终
fix guidance 自动成为标准 retry directive，可直接交给 Rerun 阶段消费。

### CLI recovery 接入

- `--recovery` 新增 `deepdebug`，并兼容 `deep`、`deep-debug`、
  `deep_debug` 和 `DeepDebugRecovery` 别名。
- `--mode deepdebug` 未显式指定 recovery 时，自动运行
  `DeepDebugRecovery`，将根因事件、证据、失败原因和修复建议写入
  `report.recovery.primary.suggestion_text`。
- 普通 diagnose 模式未指定 recovery 时仍保持 `none`，不改变原有行为。
- 显式 `--recovery none` 继续有效，可用于关闭 DeepDebug 的标准 recovery
  payload，保证旧脚本兼容。
- `DeepDebugRecovery` 不发起额外 LLM 请求，只包装 DeepDebug 已验证的诊断
  结果。

### 文档与测试

- 更新 README、CLI README、AgentDebug skill references 和集成模板，移除
  DeepDebug 必须填写 `--attributor none --recovery none` 的旧说明。
- 新增 DeepDebug 自动 recovery、显式 `--recovery deepdebug` 和显式
  `--recovery none` 兼容测试。

## 17. 2026-07-14 14:26:14 +08

本次更新针对人工测试生成的 heuristic、LLM 和 DeepDebug 三份报告进行质量
修复，强化根因定位、事件身份、证据和 recovery 完整性。

### Heuristic diagnosis

- core rule pack 新增结构化 constraint-loss 规则，识别 recorder 写入的
  `dropped_constraint`、`violated_constraint` 和 `decision_error`。
- 结构化约束信号优先于通用 explicit-error fallback，避免把后续
  reflection 中的 postcondition failure 误判为 environment error。
- `RuleMatch.confidence` 现在会进入 `FailureFinding`，使高质量结构化规则的
  置信度不再丢失。

### LLM attribution 与 recovery

- All-at-Once attribution 在模型选择同 step 的下游 action 时，会使用唯一的
  detector finding event 作为精确事件锚点，并记录
  `detector_event_anchor` source。
- Self-Refine 会检查 critic/refined action 是否明显截断；不完整时使用原
  finding suggestion 生成完整、可执行且带 pre-side-effect verification 的
  retry action。

### DeepDebug grounding

- MoE 决策事件过滤 lifecycle 和纯 tool execution 事件，减少同 step 下
  Thought/Action 混淆。
- structure candidate 必须规范化到真实 trajectory event；`Step 2` 等展示
  标签不再作为 event ID 进入报告。
- 最终 root cause 无法唯一落到真实事件时直接失败，不再生成带伪 event ID
  的报告。
- 模型最终 JSON 为空或截断时，从已验证根因事件生成 grounded evidence、
  summary 和具体约束检查建议，保证 retry directive 不为空泛。

### 测试

- 新增结构化约束根因、同 step detector event 锚定、Self-Refine 截断兜底
  和 DeepDebug 非法 event label 规范化测试。
- 虚拟 ReAct 酒店轨迹的本地 heuristic 根因由错误的 step 3 environment
  error 修正为 step 2 `planning.constraint_ignorance`。

## 18. 2026-07-14 14:34:15 +08

本次更新调整公开诊断报告的 confidence 契约：Heuristic 和 DeepDebug 不再
输出不可校准的 confidence，LLM Judge 继续保留模型自报 confidence。

### 输出策略

- Heuristic 与 DeepDebug 的内部对象仍保留 confidence，供规则排序、候选
  仲裁和 recovery 兼容逻辑使用，但不会出现在 CLI、存储、Hub 或 UI 的公开
  report payload 中。
- 过滤为递归行为，同时移除 findings、attribution、recovery 和 metadata 内
  的所有 `confidence` 键，避免同一报告不同层级语义混杂。
- `LLMJudgeAnalyzer` 报告不经过过滤，finding、attribution 和 recovery 中的
  confidence 保持原有输出。

### 统一序列化

- 新增 `model_to_dict()` 作为公开 dictionary 序列化入口。
- `model_to_json()` 对 `DiagnosticReport` 复用同一策略，因此 CLI、SQLite
  report store 和 Error Hub bundle 行为一致。
- Inspect UI 的 `_to_dict()` 同样转发到统一入口，避免 API 与 CLI 契约分叉。

### 文档与测试

- README 与 AgentDebug skill 文档改为以 evidence 和 provenance 表达诊断
  可信度，并明确只有 LLM Judge 输出模型自报 confidence。
- 新增 Heuristic/DeepDebug 递归移除、LLM Judge 保留以及 UI 输出一致性测试。
