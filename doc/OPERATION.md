# GitMusic 操作手册

**版本**: 1.0  
**日期**: 2026-01-25  
**适用对象**: 日常操作人员、管理员

## 目录

1. [快速开始](#快速开始)
2. [日常操作流程](#日常操作流程)
3. [场景化操作指南](#场景化操作指南)
4. [维护任务](#维护任务)
5. [故障应急处理](#故障应急处理)
6. [最佳实践](#最佳实践)
7. [常用命令速查](#常用命令速查)

---

## 快速开始

### 环境准备

1. **安装依赖**
   ```bash
   # Python 3.9+
   python --version
   
   # 安装GitMusic（如果未安装）
   git clone <repository>
   cd gitmusic
   
   # 创建虚拟环境
   python -m venv .venv
   
   # Windows
   .\.venv\Scripts\activate
   
   # Linux/macOS
   source .venv/bin/activate
   ```

2. **配置环境**
   ```bash
   # 复制配置文件
   cp config.example.yaml config.yaml
   
   # 编辑配置（使用VS Code或任意编辑器）
   code config.yaml
   ```

3. **配置SSH免密登录**
   ```bash
   # 生成SSH密钥（如果还没有）
   ssh-keygen -t ed25519 -C "your_email@example.com"
   
   # 复制公钥到服务器
   ssh-copy-id user@your.server.com
   
   # 测试连接
   ssh user@your.server.com
   ```

### 首次运行检查清单

- [ ] config.yaml已配置所有路径
- [ ] SSH免密登录测试成功
- [ ] work/、cache/、release/、logs/目录存在且有写权限
- [ ] metadata.jsonl已初始化为Git仓库
- [ ] 测试命令运行正常

**测试命令**:
```bash
# 测试配置
gitmusic analyze --limit 5

# 测试SSH连通性
gitmusic sync --dry-run --direction upload
```

---

## 日常操作流程

### 流程1: 添加新音乐

**场景**: 从下载到发布的完整流程

```bash
# 步骤1: 下载音频（可选）
# 方式A: 使用download命令
gitmusic download "https://youtube.com/watch?v=xxx"

# 方式B: 手动放置到work目录
# 将MP3文件复制到 work/ 目录

# 步骤2: 预览发布内容
gitmusic publish --preview
# 检查预览表格，确认文件列表和变更

# 步骤3: 执行发布
gitmusic publish
# 等待完成，检查summary

# 步骤4: 同步到远程
gitmusic push
# 或 gitmusic sync --direction upload

# 步骤5: 验证发布
gitmusic verify --mode data
```

**预期输出**:
- publish: 显示NEW/MOD/SAME状态，成功提交metadata
- push: 上传对象和metadata到远程
- verify: 100%通过，无失败项

**处理失败**:
```bash
# 如果publish失败，检查日志
cat logs/publish-*.jsonl | jq 'select(.type == "error")'

# 如果是网络问题，重试
gitmusic publish

# 如果是文件损坏，删除并重新下载
gitmusic verify --mode data --delete
gitmusic download <url>
```

---

### 流程2: 检出音乐编辑

**场景**: 检出歌曲到work目录进行标签编辑

```bash
# 步骤1: 搜索要编辑的歌曲
gitmusic analyze "歌曲名或艺术家" --limit 10

# 步骤2: 检出到work目录
gitmusic checkout "精确的查询" --limit 5
# 或通过行号精确检出
gitmusic checkout --line 1234-1240

# 步骤3: 编辑音频标签（使用外部工具）
# 推荐使用: Mp3tag, Kid3, 或 ffmpeg

# 示例: 使用ffmpeg修改标题
ffmpeg -i "work/Artist - Title.mp3" \
  -c copy \
  -metadata title="新标题" \
  -metadata artist="新艺术家" \
  "work/Artist - Title.mp3"

# 步骤4: 发布更改
gitmusic publish --changed-only
# 仅发布修改过的文件
```

**冲突处理**:
```bash
# 如果work目录有未发布的更改
gitmusic checkout "Artist"
# 会提示冲突，提供两个选项:
# 1. 先发布更改: gitmusic publish --preview
gitmusic publish
# 2. 强制覆盖: gitmusic checkout "Artist" --force
```

---

### 流程3: 生成成品库

**场景**: 生成用于播放的成品音乐库

```bash
# 步骤1: 同步最新数据（如果在server模式）
gitmusic pull
# 或 gitmusic sync --direction download

# 步骤2: 预览生成内容
gitmusic release --mode local --dry-run
# 会显示生成计划和预估时间

# 步骤3: 执行生成
gitmusic release --mode local --workers 4
# 使用4个并行线程加速

# 步骤4: 验证生成结果
gitmusic verify --mode release

# 步骤5: 在播放器中测试
# 打开release目录，在Musicbee/Foobar2000中播放测试
```

**增量生成**:
```bash
# 仅生成修改过的文件（默认）
gitmusic release

# 强制重新生成所有文件
gitmusic release --force

# 分批生成大目录
gitmusic release --line 1-1000
gitmusic release --line 1001-2000
gitmusic release --line 2001-3000
```

---

### 流程4: 双向同步

**场景**: 在多设备间同步音乐库

```bash
# 日常同步（双向）
gitmusic sync

# 仅上传本地新增
gitmusic push

# 仅下载远程更新
gitmusic pull

# 预览同步内容
gitmusic sync --dry-run
```

**处理冲突**:
```bash
# 如果本地和远程有相同OID但内容不同
# verify会自动检测到

# 优先使用远程版本
gitmusic sync --direction download

# 或重新发布本地版本
gitmusic publish
gitmusic push
```

---

## 场景化操作指南

### 场景1: 批量下载专辑

**目标**: 从YouTube下载完整专辑

```bash
# 步骤1: 创建URL列表
# 在album.txt中每行一个视频URL
vim album.txt

# 步骤2: 开始批量下载
gitmusic download --batch-file album.txt --limit 15

# 步骤3: 预览发布
gitmusic publish --preview

# 步骤4: 检查和修正标签（如果需要）
# 检出歌曲，用Mp3tag批量修改标签
gitmusic checkout --line <start>-<end>
# 在Mp3tag中修改

# 步骤5: 发布
gitmusic publish

# 步骤6: 同步
gitmusic push
```

**技巧**:
- 使用`--limit 15`分批下载，避免网络中断从头开始
- 下载失败会自动重试（最多5次）
- 建议在非高峰时段下载

---

### 场景2: 修复损坏文件

**目标**: 识别并修复损坏的音频文件

```bash
# 步骤1: 全面校验
gitmusic verify --mode data

# 步骤2: 如果发现问题
gitmusic verify --mode data --delete
# 移动损坏文件到回收站

# 步骤3: 重新下载或恢复
gitmusic download <url>  # 重新下载
# 或从备份恢复

# 步骤4: 重新发布
gitmusic publish

# 步骤5: 再次验证
gitmusic verify --mode data
```

**预防**:
```bash
# 定期校验（每周一次）
# 添加到cron或systemd timer
0 2 * * 0 cd /path/to/gitmusic && gitmusic verify --mode data --on-error=notify
```

---

### 场景3: 清理旧数据

**目标**: 清理孤立对象，释放磁盘空间

```bash
# 步骤1: 预览孤立对象
gitmusic cleanup --mode both

# 步骤2: 分析孤立对象来源
# 检查最近删除的metadata条目

# 步骤3: 确认删除
gitmusic cleanup --mode both --confirm
# 输入 yes 确认

# 步骤4: 验证空间释放
du -sh cache/ release/
```

**安全建议**:
- 执行前确保metadata.jsonl已备份
- 先在`--mode local`测试，再`--mode both`
- 不要在其他命令运行时执行cleanup

---

### 场景4: 迁移到新服务器

**目标**: 将GitMusic库迁移到新服务器

```bash
# 在原服务器
# 步骤1: 完整同步
gitmusic sync --direction upload
# 确保所有对象已上传

# 步骤2: 备份metadata
git log --oneline  # 确认提交历史
git push origin main

# 在新服务器
# 步骤3: 安装GitMusic
git clone <repository>
cd gitmusic
cp config.example.yaml config.yaml
# 编辑配置指向新服务器

# 步骤4: 拉取metadata
git pull

# 步骤5: 下载所有对象
gitmusic pull
# 或 gitmusic sync --direction download

# 步骤6: 验证完整性
gitmusic verify --mode data
gitmusic verify --mode release

# 步骤7: 测试生成
gitmusic release --mode server --dry-run
```

---

### 场景5: 恢复误删文件

**目标**: 从回收站恢复误删的文件

```bash
# 步骤1: 查看回收站位置
cache/.trash/
release/.trash/

# 步骤2: 查找需要恢复的文件
ls -lh cache/.trash/data/ab/

# 步骤3: 手动恢复（mv或cp）
# 从回收站复制回原位置
mkdir -p cache/data/ab/
cp cache/.trash/data/ab/orph123... cache/data/ab/

# 步骤4: 执行校验
gitmusic verify --mode data

# 步骤5: 如果验证通过，更新metadata
gitmusic publish
```

**注意事项**:
- 回收站不会自动清理，需定期手动清理
- 恢复后务必执行verify验证完整性
- 如果metadata也被删除，需要重新发布

---

## 维护任务

### 每日任务（自动化）

**systemd服务示例**:

```ini
# /etc/systemd/system/gitmusic-sync.service
[Unit]
Description=GitMusic Daily Sync
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/path/to/gitmusic
ExecStart=/path/to/gitmusic/.venv/bin/python repo/tools/cli.py sync
User=gitmusic

[Install]
WantedBy=multi-user.target
```

**定时任务**:

```bash
# crontab -e
# 每天凌晨2点同步
0 2 * * * cd /path/to/gitmusic && python repo/tools/cli.py sync --on-error=notify > /dev/null 2>&1

# 每周日凌晨3点完整校验
0 3 * * 0 cd /path/to/gitmusic && python repo/tools/cli.py verify --mode data --on-error=notify

# 每月1号清理孤立对象
0 4 1 * * cd /path/to/gitmusic && python repo/tools/cli.py cleanup --mode both --confirm
```

### 每周任务（手动）

1. **检查日志**
   ```bash
   # 查看本周错误
   cat logs/*.jsonl | jq 'select(.type == "error")' | wc -l
   
   # 查看命令执行统计
   cat logs/*.jsonl | jq '.cmd' | sort | uniq -c
   ```

2. **磁盘空间检查**
   ```bash
   # 检查各目录大小
du -sh work/ cache/ release/ logs/
   
   # 检查metadata大小
   ls -lh repo/metadata.jsonl
   ```

3. **性能评估**
   ```bash
   # 运行性能测试
cd tests/
pytest test_performance.py -v
   ```

### 每月任务（手动）

1. **清理旧日志**
   ```bash
   # 保留最近30天日志
find logs/ -name "*.jsonl" -mtime +30 -delete
   
   # 压缩旧日志
   find logs/ -name "*.jsonl" -mtime +7 -exec gzip {} \;
   ```

2. **清理回收站**
   ```bash
   # 查看回收站大小
du -sh cache/.trash/ release/.trash/
   
   # 清空回收站（谨慎操作）
   rm -rf cache/.trash/* release/.trash/*
   ```

3. **备份验证**
   ```bash
   # 测试从备份恢复
   git clone <backup-repo> /tmp/restore-test
   
   # 验证metadata完整性
jq 'empty' /tmp/restore-test/metadata.jsonl && echo "JSON valid"
   ```

### 每季度任务（手动）

1. **全量回归测试**
   ```bash
   # 运行完整测试套件
cd tests/
pytest --cov=libgitmusic --cov-report=html
   
   # 生成测试报告
   python -m pytest --html=report.html
   ```

2. **配置审查**
   ```bash
   # 检查配置是否符合最佳实践
   python scripts/audit_config.py
   
   # 更新过时的配置
   vim config.yaml
   ```

3. **依赖更新**
   ```bash
   # 更新Python包
.venv\Scripts\activate
   pip list --outdated
   pip install --upgrade <package>
   
   # 更新yt-dlp
   pip install --upgrade yt-dlp
   ```

---

## 故障应急处理

### 应急场景1: 服务器磁盘已满

**症状**:
- 命令失败，提示"No space left on device"
- sync命令无法上传

**应急处理**:

```bash
# 1. 立即停止所有命令
# Ctrl+C 或 kill相关进程

# 2. 快速检查磁盘使用
df -h
du -sh * | sort -rh | head -20

# 3. 清理可回收空间
# 方法A: 清理旧日志
cd logs/
ls -lt | tail -n +100 | awk '{print $NF}' | xargs rm -f  # 保留最近100个

# 方法B: 清理回收站
cd cache/.trash/
rm -rf data/*

# 方法C: 压缩旧封面
gitmusic compress_images --size 500kb  # 压缩大封面

# 4. 紧急清理孤立对象（谨慎）
gitmusic cleanup --mode local --confirm
# 确认后输入yes

# 5. 恢复服务
# 重新执行失败的命令
```

**预防措施**:
```bash
# 设置磁盘使用告警（90%）
# 配置logrotate自动轮转日志
# 定期执行cleanup
```

---

### 应急场景2: metadata.jsonl损坏

**症状**:
- JSON解析错误
- 命令无法读取metadata
- Git仓库状态异常

**应急处理**:

```bash
# 1. 立即停止所有写入操作
# 不要执行publish, checkout等命令

# 2. 备份当前文件
cp repo/metadata.jsonl repo/metadata.jsonl.backup.$(date +%Y%m%d)

# 3. 检查Git历史，找到最后一个好的版本
# 方法A: 如果有Git历史
cd repo/
git log --oneline metadata.jsonl
# 找到最后一个好的commit哈希（如abc123）

git checkout abc123 -- metadata.jsonl
# 恢复到该版本

# 方法B: 如果有旧备份
# 找到最新的备份文件
cp repo/metadata.jsonl.backup.20260120 repo/metadata.jsonl

# 4. 验证JSON完整性
jq empty repo/metadata.jsonl
# 应无错误输出

# 5. 检查数据完整性
gitmusic verify --mode data

# 6. 恢复服务
# 如果数据不完整，可能需要重新发布部分文件
```

**数据恢复**:
```bash
# 如果Git历史也没有好的版本
# 尝试从远程仓库恢复
cd repo/
git fetch origin
git reset --hard origin/main

# 然后同步对象
gitmusic pull
```

---

### 应急场景3: 网络中断导致对象损坏

**症状**:
- verify命令报告SHA256不匹配
- 大量对象传输失败
- 文件大小异常

**应急处理**:

```bash
# 1. 识别损坏范围
gitmusic verify --mode data --on-error=notify

# 2. 删除损坏对象
gitmusic verify --mode data --delete

# 3. 重新同步
gitmusic pull  # 下载正确版本

# 4. 验证修复
gitmusic verify --mode data

# 5. 如果有大量损坏，考虑重新发布
gitmusic publish --force  # 强制重新处理
```

---

### 应急场景4: 并发冲突（锁问题）

**症状**:
- "Failed to acquire lock"
- 命令卡住无法执行

**应急处理**:

```bash
# 1. 检查锁状态
ls -la .locks/

# 2. 查找持有锁的进程
ps aux | grep gitmusic

# 3. 如果进程已死，手动删除锁文件
# 极度谨慎操作！
rm -rf .locks/*
rm -rf repo/.locks/*

# 4. 重新执行命令
```

**预防措施**:
- 避免同时执行多个写入命令
- 使用--on-error=continue减少锁持有时间
- 设置合理的超时时间

---

## 最佳实践

### 1. 配置管理

**版本控制配置**:
```bash
# 将config.yaml.example纳入Git，config.yaml添加到.gitignore
cp config.example.yaml config.yaml
# 编辑config.yaml，不要提交敏感信息
```

**配置备份**:
```bash
# 定期备份配置
cd gitmusic/
cp config.yaml /backup/gitmusic-config-$(date +%Y%m%d).yaml
```

### 2. 命令使用习惯

**始终先预览**:
```bash
# 好的习惯
gitmusic publish --preview  # 先看预览
gitmusic publish            # 确认后再执行

# 避免直接执行
gitmusic publish  # 风险: 可能误发布
```

**分批处理大数据**:
```bash
# 好的习惯
gitmusic download --batch-file urls.txt --limit 20  # 分批
gitmusic release --line 1-1000                    # 分阶段

# 避免一次性处理所有
gitmusic download --batch-file urls.txt  # 风险: 失败需要全部重试
```

### 3. 日志管理

**定期清理**:
```bash
# 创建清理脚本 vim cleanup-logs.sh
#!/bin/bash
cd /path/to/gitmusic/logs/
find . -name "*.jsonl" -mtime +30 -delete
find . -name "*.log" -mtime +30 -delete

# 添加到crontab（每月执行）
0 0 1 * * /path/to/cleanup-logs.sh
```

**日志分析**:
```bash
# 创建分析报告
./.venv/Scripts/python.exe -c "
import json
import sys
from pathlib import Path

errors = []
logs_dir = Path('logs')
for log_file in logs_dir.glob('*.jsonl'):
    for line in log_file.read_text().splitlines():
        try:
            event = json.loads(line)
            if event.get('type') == 'error':
                errors.append(event)
        except:
            pass

print(f'本周错误数: {len(errors)}')
print('Top 5 错误类型:')
# 简单统计...
" > weekly-report.txt
```

### 4. 备份策略

**3-2-1备份原则**:
- 3份数据副本
- 2种不同存储介质
- 1份异地备份

**备份脚本**:
```bash
#!/bin/bash
# backup.sh
set -e

cd /path/to/gitmusic
BACKUP_DIR="/backup/gitmusic-$(date +%Y%m%d)"

# 1. 备份metadata（Git仓库）
cd repo
git bundle create "$BACKUP_DIR/metadata.bundle" --all
cd ..

# 2. 备份配置和脚本
cp config.yaml "$BACKUP_DIR/"
cp -r repo/tools "$BACKUP_DIR/"

# 3. 备份关键数据（可选）
# rsync -av cache/data/00/ "$BACKUP_DIR/cache-00/"

echo "备份完成: $BACKUP_DIR"
```

### 5. 性能优化

**并行度调优**:
```yaml
# config.yaml
command_defaults:
  sync:
    workers: 8  # 网络好、服务器强时增加
  release:
    workers: 4  # CPU密集型，根据核心数调整
  publish:
    on_error: continue  # 减少失败重试等待
```

**对象存储优化**:
```bash
# 定期压缩封面
gitmusic compress_images --size 200kb

# 清理孤立对象
gitmusic cleanup --mode local --confirm
```

### 6. 安全建议

**SSH安全**:
- 使用SSH密钥，禁用密码登录
- 限制SSH用户权限（仅访问data目录）
- 配置SSH超时和重试策略

**配置安全**:
```yaml
# config.yaml
transport:
  # 不要提交敏感信息
  user: your_user  # 使用占位符
  host: your.host  # 使用占位符
  # 实际值在部署时通过环境变量覆盖
```

### 7. 监控告警

**简单监控脚本**:
```bash
#!/bin/bash
# monitor.sh
cd /path/to/gitmusic

# 检查磁盘使用率
DISK_USAGE=$(df . | awk 'NR==2 {print $5}' | sed 's/%//')
if [ $DISK_USAGE -gt 90 ]; then
    echo "警告: 磁盘使用率超过90%: ${DISK_USAGE}%" | mail -s "GitMusic磁盘告警" admin@example.com
fi

# 检查Git仓库状态
cd repo
if ! git status --porcelain | grep -q "^??"; then
    echo "警告: metadata.jsonl有未提交更改" | mail -s "GitMusic状态告警" admin@example.com
fi
```

---

## 常用命令速查

### 发布和同步

```bash
# 发布
gitmusic publish --preview              # 预览
gitmusic publish --changed-only         # 仅变更
gitmusic publish                         # 完整发布

# 同步
gitmusic sync                            # 双向
gitmusic push                            # 上传
gitmusic pull                            # 下载
gitmusic sync --dry-run                  # 预览
```

### 检出和生成

```bash
# 检出
gitmusic checkout "查询"
gitmusic checkout --limit 10
gitmusic checkout --line 100-200
gitmusic checkout --missing cover

# 生成
gitmusic release
gitmusic release --force
gitmusic release --workers 4
gitmusic release --line 1000-2000
```

### 校验和清理

```bash
# 校验
gitmusic verify --mode data
gitmusic verify --mode release
gitmusic verify --mode data --delete

# 清理
gitmusic cleanup --mode local
gitmusic cleanup --mode both --confirm
```

### 分析和下载

```bash
# 分析
gitmusic analyze
gitmusic analyze "查询" --limit 20
gitmusic analyze --fields title,artists
gitmusic analyze --missing lyrics

# 下载
gitmusic download <url>
gitmusic download --batch-file urls.txt
gitmusic download --fetch <url>
```

### 其他

```bash
# 压缩封面
gitmusic compress_images
gitmusic compress_images --size 500kb

# 查看日志
cat logs/publish-20260125-221530.jsonl | jq '.'
cat logs/publish-20260125-221530.jsonl | jq 'select(.type == "error")'

# 获取帮助
gitmusic --help
gitmusic publish --help
```

---

**文档版本**: 1.0  
**更新日期**: 2026-01-25  
**反馈**: 提交issue到项目仓库
