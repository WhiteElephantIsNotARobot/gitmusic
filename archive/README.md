# 归档脚本

此目录包含已被新CLI命令取代的旧脚本，保留用于历史参考。

## 已归档的脚本

### 1. analyze_duplicates.py
- **状态**: 已归档
- **替代命令**: `analyze` (在cli.py中)
- **功能**: 分析metadata中的重复音频文件和重复文件名
- **归档原因**: 功能已整合到新的analyze命令中，新命令提供更完整的搜索、过滤和统计功能

### 2. analyze_metadata.py
- **状态**: 已归档
- **替代命令**: `analyze` (在cli.py中)
- **功能**: 分析metadata文件，支持搜索、过滤、字段提取和统计
- **归档原因**: 功能已整合到新的analyze命令中，新命令提供更多参数支持和更好的输出格式

### 3. download_ytdlp.py
- **状态**: 已归档
- **替代命令**: `download` (在cli.py中)
- **功能**: 使用yt-dlp下载音频并提取元数据
- **归档原因**: 功能已完全整合到新的download命令中，新命令支持更多参数（--fetch, --no-preview, --limit）和更好的错误处理

## 迁移说明

这些旧脚本已被新的统一CLI取代。新CLI提供：

1. **统一的命令接口**: 所有操作通过`cli.py`执行
2. **标准化的事件流**: 符合CLI视觉规范的事件输出
3. **完整的参数支持**: 所有规范要求的参数都已实现
4. **增强的错误处理**: 支持on_error策略（stop/continue/notify）
5. **日志持久化**: 自动将事件流记录到logs目录

### 使用新CLI

```bash
# 激活虚拟环境
.venv\Scripts\activate

# 使用新命令
python repo\tools\cli.py publish --changed-only
python repo\tools\cli.py analyze --missing cover,lyrics
python repo\tools\cli.py download <URL>
```

### 兼容性说明

旧脚本不再维护，建议尽快迁移到新CLI。如需临时使用旧脚本，请确保环境变量配置正确：

```powershell
$env:GITMUSIC_METADATA_FILE="path/to/metadata.jsonl"
$env:GITMUSIC_WORK_DIR="path/to/work/dir"
```

## 归档日期

2026-01-25
