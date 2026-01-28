# GitMusic 项目当前状态调查与阶段三、四修复计划

## 项目现状总结

基于对当前代码库的审查，阶段一（复合工作流串联）和阶段二（实时进度丢失）已在另一会话中完成部分工作。以下是当前状态：

### ✅ 已完成的改进

1. **复合工作流串联部分完成**
   - `release` 命令已添加 `git pull` 逻辑（`cli.py:774-777`），符合规范要求
   - `release` 命令步骤函数已实现，但可能缺少前置的 `sync` 和 `verify` 步骤

2. **实时进度丢失已部分修复**
   - `download_ytdlp.py` 中的 `download_audio` 函数已改用 `subprocess.Popen`（第77-104行）
   - 能够实时读取 `yt-dlp` 输出并打印到控制台

3. **Git操作库已实现**
   - `libgitmusic/git.py` 提供了完整的Git操作封装
   - 支持 `add`、`commit`、`push`、`pull` 等操作

### ⚠️ 仍存在的问题

1. **输入接口标准化偏差**（严重）
   - 所有 wrapper 脚本（10个）仍使用 `argparse` 解析命令行参数
   - 违反《脚本实现规范总结》"零选项、全Stdin输入"原则
   - 示例：`publish_meta.py`、`checkout.py`、`create_release.py` 等

2. **输出标准不符合规范**（严重）
   - `EventEmitter` 设计：有监听器时不输出到 stdout（`events.py:40-49`）
   - 但脚本中仍有直接 `print` 输出（如 `download_ytdlp.py:94`）
   - CLI 的 `_process_event_stream` 尝试解析所有 stdout 行作为 JSON，非 JSON 行被当作普通文本处理
   - 这导致混合输出模式，不符合 "所有脚本通过 stdout 输出 JSONL 事件流" 标准

3. **复合工作流未完整串联**
   - `publish` 命令步骤链只有 `publish_scan` 和 `publish_process`（`cli.py:416-421`）
   - 缺少 `compress_images`、`verify`、`sync` 等后续步骤
   - `release` 命令可能缺少前置 `sync` 和 `verify` 步骤

4. **行号输出缺失**
   - `MetadataManager.load_all()` 未保留行号信息
   - `analyze` 命令输出不包含条目行号

5. **其他小问题**
   - 需要确认 `sync` 命令正确从 `config.yaml` 读取配置
   - 错误处理策略一致性检查

---

## 阶段三：输入接口标准化与输出规范修复

### 🎯 目标

严格遵循《脚本实现规范总结》和《plan_cli.md》规范：

1. **输入**：所有脚本通过 stdin 接收 JSONL 配置，零命令行选项
2. **输出**：所有脚本通过 stdout 输出 JSONL 事件流，无直接 print
3. **CLI集成**：cli.py 正确传递配置并捕获标准化事件流

### 📋 实施原则（基于规范文档）

#### 输入标准（《脚本实现规范总结》）

- **零硬编码路径**：所有路径通过配置传递
- **零选项**：所有参数通过标准输入接收
- **配置格式**：JSON 对象，包含 `paths`、`options`、`transport` 等字段

#### 输出标准（《plan_cli.md》）

- **事件流格式**：stdout 输出 JSONL，每行一个事件对象
- **事件类型**：`phase_start`、`batch_progress`、`item_event`、`log`、`result`、`error`
- **必填字段**：所有事件必须包含 `ts`（时间戳）和 `cmd`（命令名）
- **无UI渲染**：脚本不直接调用 rich 表格或进度条，所有UI由CLI负责

#### CLI与脚本交互

- **配置传递**：cli.py 将参数转换为 JSON 配置，写入子进程 stdin
- **事件捕获**：cli.py 捕获子进程 stdout，解析 JSONL 事件流
- **进度渲染**：cli.py 根据 `batch_progress` 事件显示聚合进度条
- **日志记录**：cli.py 将事件追加到 `logs/<command>-TIMESTAMP.jsonl`

### 📝 具体任务清单

#### 任务 3.1：设计统一 JSON 配置格式

分析所有脚本的 argparse 参数，设计向后兼容的 JSON Schema：

```json
{
  "version": "1.0",
  "paths": {
    "work_dir": "/absolute/path/to/work",
    "cache_root": "/absolute/path/to/cache",
    "metadata_file": "/absolute/path/to/metadata.jsonl",
    "release_dir": "/absolute/path/to/release"
  },
  "transport": {
    "user": "username",
    "host": "hostname",
    "remote_data_root": "/srv/music/data",
    "retries": 5,
    "timeout": 60,
    "workers": 4
  },
  "options": {
    // 命令特定选项，如 dry_run、changed_only、quality 等
  },
  "input_data": {
    // 输入数据，如 URL 列表、audio_oid 列表等
  }
}
```

#### 任务 3.2：修改 cli.py 的 run_script 方法

- 移除 `_inject_env()` 调用（改为通过 stdin 传递路径）
- 构建 JSON 配置对象，包含 paths、transport 和解析后的 options
- 使用 `subprocess.Popen(stdin=subprocess.PIPE)` 传递配置
- 保持 stdout/stderr 捕获不变

#### 任务 3.3：修复 EventEmitter 输出逻辑

- 修改 `EventEmitter.emit()` 方法，确保即使有监听器也输出到 stdout
- 或确保所有脚本调用都通过监听器，但保持 stdout 输出作为备份
- 关键：保证脚本独立运行时能输出 JSONL，被 CLI 调用时也能被正确捕获

#### 任务 3.4：批量修改 wrapper 脚本（10个）

为每个脚本创建标准化模板：

```python
#!/usr/bin/env python3
"""
脚本名称：标准化模板
输入：stdin JSON 配置
输出：stdout JSONL 事件流
"""

import sys
import json
from pathlib import Path

# 导入核心库
sys.path.append(str(Path(__file__).parent.parent))
from libgitmusic.events import EventEmitter

def main():
    """主函数：从 stdin 读取配置，执行业务逻辑"""
    # 1. 读取配置
    if sys.stdin.isatty():
        # 无 stdin 输入，使用默认配置或报错
        config = {}
        EventEmitter.error("No configuration provided via stdin")
        sys.exit(1)
    else:
        try:
            config = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            EventEmitter.error(f"Invalid JSON configuration: {str(e)}")
            sys.exit(1)

    # 2. 提取路径和选项
    paths = config.get("paths", {})
    options = config.get("options", {})

    # 3. 验证必需路径
    required_paths = ["work_dir", "cache_root", "metadata_file"]
    for path_key in required_paths:
        if path_key not in paths:
            EventEmitter.error(f"Missing required path: {path_key}")
            sys.exit(1)

    # 4. 执行业务逻辑（调用 libgitmusic 库）
    # ...

    # 5. 输出结果
    EventEmitter.result("ok", message="Operation completed")

if __name__ == "__main__":
    main()
```

需要修改的脚本列表：

1. `repo/work/publish_meta.py` - 发布元数据
2. `repo/work/checkout.py` - 检出音频
3. `repo/release/create_release.py` - 生成成品
4. `repo/data/sync_cache.py` - 同步缓存
5. `repo/data/verify_hashes.py` - 校验哈希
6. `repo/data/compress_images.py` - 压缩封面
7. `repo/data/cleanup_orphaned.py` - 清理孤立文件
8. `repo/tools/download_ytdlp.py` - 下载音频（特别注意移除直接 print）
9. `repo/tools/analyze_metadata.py` - 元数据分析
10. `repo/tools/analyze_duplicates.py` - 重复项分析

#### 任务 3.5：移除所有直接 print 调用

- 在 `download_ytdlp.py` 中：将 `print(line, flush=True)` 改为 `EventEmitter.log("info", line)` 或解析为进度事件
- 检查其他脚本中的 `print`、`sys.stdout.write` 调用
- 确保所有输出都通过 `EventEmitter` 方法

#### 任务 3.6：更新 cli.py 中的命令步骤函数

- 修改步骤函数不再解析 `args`，而是从 `ctx.config` 获取配置
- 步骤函数构建 JSON 配置传递给子进程
- 保持步骤间的数据传递（通过 `ctx.artifacts`）

#### 任务 3.7：兼容性处理与过渡

- 保留环境变量支持作为过渡（警告提示）
- 添加配置验证和默认值逻辑
- 提供详细的错误信息帮助迁移

#### 任务 3.8：全面测试

1. 单元测试：每个脚本单独测试 stdin 配置接收
2. 集成测试：通过 CLI 调用验证参数传递正确性
3. 输出验证：确保所有输出为有效 JSONL
4. 回退测试：测试环境变量回退机制

### 📅 实施顺序建议

1. 先修改 `EventEmitter` 确保输出逻辑正确
2. 设计并验证 JSON 配置格式
3. 修改 `cli.py` 的 `run_script` 方法
4. 按依赖顺序修改脚本：
   - 先修改基础脚本（`publish_meta.py`、`checkout.py`）
   - 再修改数据管理脚本
   - 最后修改工具脚本
5. 测试每个脚本的独立运行
6. 测试 CLI 集成
7. 移除环境变量支持（可选）

---

## 阶段四：行号输出与其他修复

### 🎯 目标

修复 `analyze` 命令缺少行号输出的问题，并完成其他规范符合性修复。

### 📝 具体任务清单

#### 任务 4.1：行号输出修复

1. **修改 `MetadataManager.load_all()` 方法**
   - 使用 `enumerate()` 记录行号
   - 将行号注入元数据条目：`entry["_line_no"] = line_no`
   - 或返回 `(line_no, entry)` 元组列表
   - 注意：不破坏现有代码对返回数据结构的期望

2. **更新 `analyze` 命令逻辑**
   - 修改 `libgitmusic/commands/analyze.py` 使用行号信息
   - 确保输出包含行号字段
   - 实现 `--read-fields` 未指定 oid 时默认隐藏 oid

3. **更新 wrapper 脚本**
   - 修改 `tools/analyze_metadata.py` 反映行号输出
   - 确保输出格式符合 JSONL 事件流标准

#### 任务 4.2：复合工作流完整性验证

1. **检查 `publish` 命令步骤链**
   - 确认是否已添加 `compress_images_step`、`verify_step`、`sync_step`
   - 如果没有，按阶段一计划添加
   - 确保步骤间数据传递正确（`ctx.artifacts["processed_audio_oids"]`）

2. **检查 `release` 命令步骤链**
   - 确认是否已添加前置 `sync_step` 和 `verify_step`
   - 如果没有，按阶段一计划添加
   - 确保 `git pull` 在合适位置执行

#### 任务 4.3：配置读取验证

1. **验证 `sync` 命令配置**
   - 确认从 `config.yaml` 正确读取远程参数
   - 检查 `TransportAdapter` 初始化
   - 测试超时和重试机制

2. **统一配置管理**
   - 确保所有配置通过统一方式读取
   - 避免硬编码值和魔法数字

#### 任务 4.4：错误处理策略检查

1. **验证 `on_error` 策略**
   - 检查各命令的 `on_error` 设置是否合理
   - `publish`、`release` 应为 `"stop"`
   - `sync`、`verify` 可为 `"continue"`
   - 确保错误事件正确传播

2. **原子操作验证**
   - 检查 `.tmp` 文件使用是否正确
   - 验证 `AudioIO.atomic_write` 和 `MetadataManager.save_all`
   - 确保异常情况下的清理工作

#### 任务 4.5：文档与测试更新

1. **更新操作文档**
   - 记录新的配置传递方式
   - 提供迁移指南
   - 更新故障排查清单

2. **添加集成测试**
   - 创建端到端测试脚本
   - 测试完整工作流：`publish` → `sync` → `release`
   - 验证事件流符合规范

### ⚠️ 风险与缓解措施

1. **向后兼容性风险**
   - 风险：现有脚本调用会立即失败
   - 缓解：保留环境变量支持过渡期，添加清晰的错误信息指导迁移

2. **JSON 解析错误风险**
   - 风险：无效 JSON 配置导致脚本崩溃
   - 缓解：添加健壮的 JSON 解析和验证，提供详细错误信息

3. **事件流中断风险**
   - 风险：EventEmitter 修改导致输出丢失
   - 缓解：充分测试独立运行和 CLI 集成两种模式

4. **性能影响风险**
   - 风险：JSON 序列化/反序列化增加开销
   - 缓解：性能测试，必要时优化大配置处理

### 📊 成功标准

#### 阶段三成功标准

1. ✅ 所有脚本移除 `argparse`，通过 stdin 接收 JSON 配置
2. ✅ 所有脚本输出纯净 JSONL 事件流，无直接 print
3. ✅ CLI 能正确传递配置并捕获事件流
4. ✅ 进度显示正常工作（通过 `batch_progress` 事件）
5. ✅ 向后兼容过渡平稳

#### 阶段四成功标准

1. ✅ `analyze` 命令输出包含行号信息
2. ✅ `publish` 和 `release` 命令步骤链完整
3. ✅ 所有配置正确读取，无硬编码
4. ✅ 错误处理策略一致且合理
5. ✅ 原子操作在关键路径正确实现

### 🚀 实施时间预估

- **阶段三**：3-5天（涉及大量脚本修改和测试）
- **阶段四**：1-2天（针对性修复和验证）

---

## 下一步行动建议

1. **立即开始阶段三**，从最关键的部分着手：
   - 先修复 `EventEmitter` 输出逻辑
   - 设计并测试 JSON 配置格式
   - 修改 `cli.py` 的 `run_script` 方法

2. **按依赖顺序修改脚本**，每修改一个立即测试：
   - 测试脚本独立运行（通过 stdin 传递配置）
   - 测试通过 CLI 调用
   - 验证输出格式

3. **阶段四可并行进行**，特别是行号输出修复

4. **建议创建备份**，在修改前备份所有脚本

5. **考虑分批次部署**，先部署核心脚本，再部署工具脚本

此计划基于对当前代码库的详细调查和规范文档的要求制定。成功实施后，GitMusic 将完全符合《脚本实现规范总结》和《plan_cli.md》的设计标准，实现真正的一站式 CLI 驱动工作流。
