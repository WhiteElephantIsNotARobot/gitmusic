# GitMusic 故障排查指南

**版本**: 1.0  
**日期**: 2026-01-25  
**目标**: 快速定位和解决常见问题

## 目录

1. [诊断流程](#诊断流程)
2. [常见问题与解决方案](#常见问题与解决方案)
3. [错误代码和日志分析](#错误代码和日志分析)
4. [性能问题](#性能问题)
5. [网络和SSH问题](#网络和ssh问题)
6. [数据完整性问题](#数据完整性问题)
7. [获取帮助](#获取帮助)

---

## 诊断流程

### 标准诊断步骤

遇到问题时，按以下顺序排查：

1. **查看错误信息**
   ```bash
   # 查看最近错误
   cat logs/*.jsonl | jq 'select(.type == "error")' | tail -20
   
   # 查看命令日志
cat logs/<command>-<timestamp>.jsonl | jq '.'
   ```

2. **检查配置**
   ```bash
   # 验证config.yaml语法
   python -c "import yaml; yaml.safe_load(open('config.yaml'))"
   
   # 检查路径权限
   ls -la work/ cache/ release/ logs/ repo/
   ```

3. **测试连通性**
   ```bash
   # 测试SSH
   ssh user@host "ls -la /path/to/remote/data"
   
   # 测试Git
   cd repo && git status
   ```

4. **检查资源**
   ```bash
   # 磁盘空间
   df -h
   du -sh cache/ release/ logs/
   
   # 内存使用
   free -h
   
   # CPU负载
   top
   ```

5. **运行诊断命令**
   ```bash
   # 快速测试
gitmusic analyze --limit 5
   
   # 验证数据
gitmusic verify --mode data --on-error=notify
   ```

### 信息收集清单

提交问题前，准备以下信息：

- [ ] 完整错误消息（截图或文本）
- [ ] 执行的命令和参数
- [ ] 相关日志文件（`logs/*.jsonl`）
- [ ] 配置文件（脱敏后）
- [ ] `gitmusic analyze` 输出
- [ ] 系统信息: `uname -a`, `python --version`
- [ ] 磁盘空间: `df -h`
- [ ] Git状态: `cd repo && git status`

---

## 常见问题与解决方案

### 1. SSH连接失败

**症状**:
```
error: SSH connection failed
error: [Errno 111] Connection refused
```

**排查步骤**:

```bash
# 步骤1: 测试基础连通性
ping your.server.com

# 步骤2: 测试SSH端口
ssh -v user@your.server.com
# 观察输出中的认证过程

# 步骤3: 验证配置
cat config.yaml | grep -A 10 "transport:"

# 步骤4: 手动测试SSH命令
ssh user@your.server.com "ls -la /srv/music/data"
```

**常见原因和解决方案**:

| 原因 | 检查命令 | 解决方案 |
|------|---------|----------|
| SSH密钥未配置 | `ls ~/.ssh/id_*` | `ssh-keygen` + `ssh-copy-id` |
| 主机地址错误 | `ping host` | 修正config.yaml中的host |
| 端口未开放 | `telnet host 22` | 检查防火墙/SSH服务 |
| 权限问题 | `ssh -v user@host` | 服务器上检查~/.ssh权限 |
| 目录不存在 | `ssh user@host "ls /remote/path"` | 创建远程目录 |

**权限修复**:
```bash
# 在服务器上
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
chmod 755 /srv/music
chmod 755 /srv/music/data
```

---

### 2. 命令卡住无响应

**症状**:
- 命令启动后长时间无输出
- 进度条不更新
- Ctrl+C无法退出

**排查步骤**:

```bash
# 步骤1: 查看进程状态
ps aux | grep gitmusic

# 步骤2: 查看锁状态
ls -la .locks/

# 步骤3: 检查日志最后输出
tail -f logs/<command>-<timestamp>.jsonl | jq '.'

# 步骤4: 检查系统资源
# CPU是否满载
top -p <pid>

# 是否等待IO
iotop -p <pid>
```

**常见原因和解决方案**:

| 原因 | 现象 | 解决方案 |
|------|------|----------|
| 等待锁 | `.locks/` 中有旧锁文件 | 删除锁文件（谨慎） |
| 大文件处理 | CPU占用高 | 等待完成，或分批处理 |
| 网络阻塞 | 无网络活动 | 检查网络，重启命令 |
| 内存交换 | 大量swap使用 | 增加内存或交换空间 |

**强制终止**:
```bash
# 找到PID
ps aux | grep "repo/tools/cli.py"

# 终止进程
kill -9 <pid>

# 清理锁文件
rm -rf .locks/* repo/.locks/*
```

---

### 3. metadata.jsonl损坏

**症状**:
```
error: JSON decode error
error: Expecting ',' delimiter
```

**排查步骤**:

```bash
# 步骤1: 验证JSON语法
cd repo
jq empty metadata.jsonl
# 或
python -c "import json; [json.loads(line) for line in open('metadata.jsonl')]"

# 步骤2: 查找错误行
awk 'BEGIN {line=1} {if (!json.parse($0)) print "Line " line ": " $0; line++}' metadata.jsonl

# 步骤3: 检查Git状态
git status
git log --oneline metadata.jsonl
```

**修复方案**:

**方案A: 从Git恢复（推荐）**:
```bash
cd repo
# 找到最后一个好的commit
git log --oneline metadata.jsonl

# 恢复到该版本（例如abc123）
git checkout abc123 -- metadata.jsonl

# 验证
cat metadata.jsonl | jq empty

# 如果有未提交的好版本，先提交
git add metadata.jsonl
git commit -m "Fix corrupted metadata"
git push
```

**方案B: 手动修复**:
```bash
# 备份
cp metadata.jsonl metadata.jsonl.backup.corrupted

# 找到损坏的行（假设在第1234行）
head -n 1233 metadata.jsonl > metadata.jsonl.fixed  # 保留正确部分
tail -n +1235 metadata.jsonl >> metadata.jsonl.fixed  # 跳过错行

# 验证修复
cat metadata.jsonl.fixed | jq empty

# 替换
mv metadata.jsonl.fixed metadata.jsonl
```

**方案C: 使用备份**:
```bash
# 找到最近的备份
ls -lt metadata.jsonl.backup.* | head

# 恢复
cp metadata.jsonl.backup.20260120 metadata.jsonl

# 重新发布缺失的条目
# 手动对比并补发
```

**验证修复**:
```bash
# 验证JSON
jq 'empty' repo/metadata.jsonl

# 验证数据一致性
gitmusic analyze --limit 5
gitmusic verify --mode data
```

---

### 4. SHA256校验失败

**症状**:
```
error: SHA256 mismatch
error: Verify failed for sha256:abc123...
```

**排查步骤**:

```bash
# 步骤1: 识别损坏文件
gitmusic verify --mode data --on-error=notify

# 步骤2: 查看详细信息
# 在日志中查找verifyfail事件
cat logs/verify-*.jsonl | jq 'select(.type == "item_event" and .status == "verifyfail")'
```

**日志示例**:
```json
{
  "ts": "2026-01-25T22:00:15.234",
  "cmd": "verify",
  "type": "item_event",
  "status": "verifyfail",
  "id": "sha256:abc123...",
  "message": "SHA256 mismatch",
  "artifacts": {
    "expected": "sha256:abc123...",
    "actual": "sha256:def456...",
    "file": "cache/data/ab/abc123...",
    "size": 4582912
  }
}
```

**原因分析**:

| 原因 | 特征 | 检测方法 |
|------|------|---------|
| 网络传输中断 | 文件大小偏小 | `ls -lh cache/data/ab/abc123...` |
| 磁盘写入错误 | 文件大小正确但内容损坏 | SHA256不匹配 |
| 并发写入冲突 | 时间戳异常 | 检查是否有多个进程写入 |
| 对象被误修改 | mtime较新 | `stat cache/data/ab/abc123...` |

**修复步骤**:

```bash
# 步骤1: 删除损坏的对象
gitmusic verify --mode data --delete
# 移动到cache/.trash/

# 步骤2: 重新下载/同步
gitmusic pull
# 或
gitmusic sync --direction download

# 步骤3: 验证修复
gitmusic verify --mode data

# 步骤4: 如果仍然失败，检查远程
gitmusic verify --mode data --on-error=notify
# 如果远程也损坏，需要重新发布

gitmusic checkout --line <line_number>  # 检出到work
gitmusic publish --force               # 重新发布
```

**预防措施**:
- 定期执行`gitmusic verify --mode data`
- 传输时使用`--timeout`和重试机制
- 避免直接操作cache目录

---

### 5. Git提交失败

**症状**:
```
error: Git commit failed
error: Push rejected
```

**排查步骤**:

```bash
# 步骤1: 检查Git状态
cd repo
git status
git remote -v

# 步骤2: 检查Git日志
git log --oneline -10

# 步骤3: 测试手动提交
git add metadata.jsonl
git commit -m "Test commit"
git push
```

**常见原因和解决方案**:

| 原因 | 错误消息 | 解决方案 |
|------|---------|----------|
| 网络问题 | "Could not resolve host" | 检查网络，测试ssh连接 |
| 权限问题 | "Permission denied" | 检查SSH密钥，确认有写入权限 |
| 冲突 | "rejected because remote contains work" | 先pull再push: `git pull --rebase` |
| 认证失败 | "Authentication failed" | 检查SSH agent，`ssh-add ~/.ssh/id_rsa` |
| 仓库不存在 | "repository does not exist" | 确认远程仓库URL正确 |

**冲突解决**:
```bash
cd repo

# 如果有冲突
git pull --rebase
# 或
git pull origin main

# 如果有合并冲突
git status  # 查看冲突文件
# 手动编辑解决冲突
git add metadata.jsonl
git rebase --continue

# 重新push
git push origin main
```

**认证问题**:
```bash
# 检查SSH密钥
ssh-add -l  # 查看已加载密钥

# 如果没有，添加密钥
ssh-add ~/.ssh/id_ed25519

# 测试SSH认证
ssh -T git@github.com  # 或你的Git服务器
```

---

### 6. 磁盘空间不足

**症状**:
```
error: No space left on device
gitmusic verify --mode data shows 0 bytes free
```

**排查步骤**:

```bash
# 步骤1: 检查磁盘使用
df -h
du -sh * | sort -rh | head -20

# 步骤2: 检查GitMusic目录
cd /path/to/gitmusic
du -sh work/ cache/ release/ logs/ repo/
du -sh cache/data/* | sort -rh | head -10

# 步骤3: 查找大文件
find cache/ -type f -size +100M -exec ls -lh {} \;
```

**清理策略**:

**策略1: 清理日志**:
```bash
# 保留最近30天
find logs/ -name "*.jsonl" -mtime +30 -delete

# 压缩旧日志
find logs/ -name "*.jsonl" -mtime +7 -exec gzip {} \;
```

**策略2: 压缩封面**:
```bash
# 压缩大于200KB的封面
gitmusic compress_images --size 200kb

# 查看效果
# 压缩前
du -sh cache/covers/
# 压缩后
du -sh cache/covers/
```

**策略3: 清理孤立对象**:
```bash
# 预览
gitmusic cleanup --mode both

# 确认后删除
gitmusic cleanup --mode both --confirm
# 输入 yes
```

**策略4: 清理回收站**:
```bash
# 查看回收站大小
du -sh cache/.trash/ release/.trash/

# 清空回收站（极度谨慎！）
read -p "确定清空回收站? (yes/no): " confirm
if [ "$confirm" = "yes" ]; then
    rm -rf cache/.trash/* release/.trash/*
fi
```

**策略5: 迁移到更大磁盘**:
```bash
# 如果以上不足够，考虑扩容
# 1. 挂载新磁盘到/data
cp -a cache/ /data/gitmusic-cache/

# 2. 更新配置
# vim config.yaml
# cache_root: /data/gitmusic-cache/

# 3. 验证
gitmusic verify --mode data
```

---

### 7. yt-dlp相关问题

**症状**:
```
error: yt-dlp command not found
error: Download failed: Unable to extract video info
```

**排查步骤**:

```bash
# 步骤1: 检查yt-dlp安装
# Linux/macOS
yt-dlp --version

# Windows
.\.venv\Scripts\yt-dlp.exe --version

# 步骤2: 测试URL
yt-dlp --get-info "https://youtube.com/watch?v=xxx"

# 步骤3: 检查ffmpeg
ffmpeg -version
```

**常见问题**:

| 问题 | 症状 | 解决方案 |
|------|------|----------|
| 未安装 | "command not found" | `pip install yt-dlp` |
| 版本过旧 | 无法下载某些网站 | `pip install --upgrade yt-dlp` |
| 网络IP被禁 | "429 Too Many Requests" | 使用代理，降低下载频率 |
| 需要登录 | "Sign in to confirm" | 配置cookie，或使用登录后的cookie文件 |
| 格式不支持 | 下载后不是MP3 | 检查ffmpeg是否安装 |

**cookie配置**:
```bash
# 导出浏览器cookie
yt-dlp --cookies-from-browser chrome "https://youtube.com/watch?v=xxx"

# 或在配置中指定cookie文件
# config.yaml (暂不支持，需手动添加参数)
```

**代理设置**:
```bash
# 环境变量设置代理
export HTTP_PROXY=http://127.0.0.1:1080
export HTTPS_PROXY=http://127.0.0.1:1080

# 然后执行download
gitmusic download <url>
```

---

## 错误代码和日志分析

### 事件类型速查

| 事件类型 | 含义 | 级别 | 处理建议 |
|---------|------|------|---------|
| `phase_start` | 阶段开始 | info | 正常 |
| `batch_progress` | 批量进度 | info | 正常 |
| `item_event` | 单项事件 | info | 正常 |
| `progress` | 进度更新 | debug | 正常 |
| `log` | 日志信息 | debug | 正常 |
| `error` | 错误发生 | error | 需要处理 |
| `result` | 命令结果 | info | 正常 |
| `summary` | 汇总信息 | info | 正常 |

### 常用日志查询

**查询所有错误**:
```bash
cat logs/*.jsonl | jq 'select(.type == "error")'
```

**查询特定命令的错误**:
```bash
grep -l '"cmd": "publish"' logs/*.jsonl | head -5
```

**统计错误类型**:
```bash
cat logs/*.jsonl | jq -r 'select(.type == "error") | .artifacts.error_type' | sort | uniq -c | sort -nr
```

**查看命令执行时间**:
```bash
cat logs/*.jsonl | jq -r '{cmd: .cmd, ts: .ts, elapsed: .artifacts.elapsed}' | head
```

**生成简单报告**:
```bash
#!/bin/bash
# generate-report.sh
echo "GitMusic 运行报告 - $(date)"
echo "================================"
echo ""

echo "最近7天命令统计:"
find logs/ -name "*.jsonl" -mtime -7 | while read log; do
    cat "$log" | jq -r '.cmd' | sort | uniq -c
done | awk '{sum[$2]+=$1} END {for (cmd in sum) print sum[cmd], cmd}' | sort -nr

echo ""
echo "最近7天错误统计:"
cat logs/*.jsonl | jq 'select(.ts >= '"$(date -d "7 days ago" -Iseconds)"') | select(.type == "error")' | jq -r '.message' | sort | uniq -c | sort -nr

echo ""
echo "磁盘使用:"
du -sh work/ cache/ release/ logs/ repo/
```

### 典型错误模式

**模式1: 网络超时**
```json
{
  "ts": "2026-01-25T22:00:15.234",
  "cmd": "sync",
  "type": "error",
  "id": "sha256:abc123...",
  "message": "上传失败: 网络超时",
  "artifacts": {
    "retry": 5,
    "timeout": true,
    "error_type": "NetworkTimeoutError"
  }
}
```

**处理**: 增加`--timeout`，检查网络，重试

**模式2: 权限拒绝**
```json
{
  "ts": "2026-01-25T22:00:15.234",
  "cmd": "publish",
  "type": "error",
  "id": "metadata.jsonl",
  "message": "无法写入metadata: Permission denied",
  "artifacts": {
    "path": "repo/metadata.jsonl",
    "error_type": "PermissionError"
  }
}
```

**处理**: `chmod 644 repo/metadata.jsonl`，检查目录权限

**模式3: 哈希不匹配**
```json
{
  "ts": "2026-01-25T22:00:15.234",
  "cmd": "verify",
  "type": "item_event",
  "status": "verifyfail",
  "id": "sha256:abc123...",
  "message": "SHA256 mismatch",
  "artifacts": {
    "expected": "sha256:abc123...",
    "actual": "sha256:def456...",
    "file": "cache/data/ab/abc123..."
  }
}
```

**处理**: 删除损坏文件，重新同步/发布

---

## 性能问题

### 症状

- 命令执行速度慢
- CPU占用持续100%
- 内存占用持续增长
- 磁盘IO等待高

### 诊断工具

**查看命令耗时**:
```bash
# 统计各命令平均耗时
gitmusic analyze --log-only | jq '.artifacts.elapsed'  # 假设有这样的日志

# 查看具体命令时间
# 在日志中查找result事件
cat logs/*.jsonl | jq 'select(.type == "result") | {cmd: .cmd, elapsed: .artifacts.elapsed}'
```

**系统级诊断**:
```bash
# CPU使用情况
top -p <pid>

# 内存使用
ps aux | grep gitmusic

# 磁盘IO
iotop -p <pid>

# 网络使用
iftop -i eth0

# 进程调用跟踪（谨慎使用）
strace -p <pid> -c  # 统计系统调用
```

### 性能优化

**减少并发**:
```bash
# 如果CPU满载，减少workers
gitmusic sync --workers 2
gitmusic release --workers 2
```

**分批处理**:
```bash
# 大目录分批发布
gitmusic publish --changed-only  # 先处理变更
gitmusic publish  # 再完整发布

# 分批检出
gitmusic checkout --limit 100
gitmusic checkout --line 1-1000
gitmusic checkout --line 1001-2000
```

**减少日志输出**:
```bash
# 如果IO瓶颈，使用log-only模式
gitmusic publish --log-only > publish.jsonl
# 减少屏幕渲染开销
```

---

## 网络和SSH问题

### SSH调试

**启用详细日志**:
```bash
ssh -vvv user@your.server.com
```

**常见SSH问题**:

**问题1: 连接超时**
```
ssh: connect to host your.server.com port 22: Connection timed out
```

**解决**:
```bash
# 检查服务器SSH服务
systemctl status sshd  # Linux
netstat -an | grep :22  # 检查端口监听

# 检查本地网络
ping your.server.com
# 或
traceroute your.server.com
```

**问题2: 密钥认证失败**
```
Permission denied (publickey)
```

**解决**:
```bash
# 检查本地密钥
ls -la ~/.ssh/

# 检查密钥是否加载
ssh-add -l

# 添加密钥
ssh-add ~/.ssh/id_ed25519

# 确保公钥在服务器authorized_keys中
ssh user@your.server.com "cat ~/.ssh/authorized_keys" | grep "your_email@example.com"
```

**问题3: 主机密钥变化**
```
WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!
```

**解决**:
```bash
# 从known_hosts中删除旧密钥
ssh-keygen -R your.server.com

# 重新连接
ssh user@your.server.com
```

### 网络性能

**测试带宽**:
```bash
# 安装speedtest
pip install speedtest-cli

# 测试
speedtest
```

**SCP速度测试**:
```bash
# 测试上传
time scp test-file-100mb.bin user@your.server.com:/tmp/

# 测试下载
scp user@your.server.com:/tmp/test-file-100mb.bin /tmp/
```

**优化建议**:

- 使用支持压缩的SSH: `scp -C`
- 调整SSH配置: `~/.ssh/config`
```ssh-config
Host your.server.com
    Compression yes
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

---

## 数据完整性问题

### 严重数据损坏

**症状**:
- 大量verify失败
- 无法检出文件
- release生成的文件无法播放

### 灾难恢复

**情况1: 本地损坏，远程完好**
```bash
# 1. 备份本地（可选）
tar -czf gitmusic-backup-$(date +%Y%m%d).tar.gz cache/ release/

# 2. 清理本地cache
rm -rf cache/data/* cache/covers/*

# 3. 重新下载
gitmusic pull
# 或
gitmusic sync --direction download

# 4. 验证
gitmusic verify --mode data
gitmusic verify --mode release

# 5. 重新生成release
gitmusic release --force
```

**情况2: 本地完好，远程损坏**
```bash
# 1. 推送到远程覆盖
gitmusic push
# 或
gitmusic sync --direction upload

# 2. 验证远程
gitmusic sync --dry-run  # 检查差异
```

**情况3: 双方都损坏**
```bash
# 这是最坏情况，需要从备份恢复

# 1. 从Git仓库恢复metadata（如果有提交）
cd repo
git log --oneline metadata.jsonl  # 找到好的版本
git checkout <good-commit> -- metadata.jsonl

# 2. 从备份恢复对象（如果有）
# 解压备份到temp
tar -xzf /backup/gitmusic-*.tar.gz -C /tmp/

# 3. 手动复制对象
cp /tmp/cache/data/* cache/data/

# 4. 验证并补全缺失的
gitmusic verify --mode data --on-error=notify
# 对于缺失的，重新下载或手动复制

# 5. 发布和同步
gitmusic publish
gitmusic push
```

### 备份验证

**定期测试恢复**:
```bash
#!/bin/bash
# test-recovery.sh
set -e

# 创建测试目录
TEST_DIR="/tmp/gitmusic-recovery-test-$$"
mkdir -p "$TEST_DIR"

# 恢复metadata
git clone <repo-url> "$TEST_DIR/repo" && cd "$TEST_DIR/repo"
git checkout main

# 下载少量对象测试
gitmusic pull

# 验证
gitmusic verify --mode data --on-error=notify

# 生成测试
gitmusic release --mode local --limit 10

echo "恢复测试完成: $TEST_DIR"
```

---

## 获取帮助

### 自助资源

1. **查看文档**
   - 命令规范: `doc/command-spec.md`
   - 操作手册: `doc/OPERATION.md`
   - 本故障排查指南: `doc/TROUBLESHOOTING.md`

2. **查看日志**
   ```bash
   # 最近的错误
   cat logs/*.jsonl | jq 'select(.type == "error")' | tail -20
   
   # 命令帮助
   gitmusic --help
   gitmusic <command> --help
   ```

3. **测试示例**
   ```bash
   # 运行测试套件
   cd tests/
   pytest test_cli.py -v -k test_publish
   ```

### 提交Issue

在GitHub或其他平台提交问题时，包含：

**标题格式**:
```
[命令名] 简明描述 - 环境信息
示例: [publish] SHA256 mismatch on Windows 10
```

**问题模板**:
```markdown
**问题描述**
清晰简洁的问题描述

**复现步骤**
1. 执行命令：`gitmusic publish --preview`
2. 输入：'y'
3. 看到错误

**预期行为**
应该发生什么

**实际行为**
实际发生了什么

**错误信息**
```
# 粘贴完整错误输出
```

**日志文件**
```
# 粘贴相关日志（脱敏后）
```

**系统信息**
- OS: [e.g. Windows 10]
- Python: [e.g. 3.11.0]
- GitMusic version: [e.g. 1.0.0]
- SSH: [e.g. OpenSSH_8.2p1]

**配置文件（脱敏）**
```yaml
# 粘贴config.yaml（移除敏感信息）
transport:
  user: xxxxx
  host: xxxxx
  # ...
```

**尝试过的解决方案**
- [ ] 重启命令
- [ ] 检查SSH连接
- [ ] 验证配置文件
- [ ] 查看日志

**额外信息**
任何其他上下文信息
```

### 社区支持

（如果项目有社区）

- 加入Discord/Slack: [链接]
- 邮件列表: gitmusic-users@example.com
- 微信群/QQ群: [二维码]

---

## 快速解决清单

### 先试试这些

1. **清理并重新同步**
   ```bash
   gitmusic cleanup --mode local --confirm
gitmusic sync --direction both
   ```

2. **验证并修复**
   ```bash
   gitmusic verify --mode data --delete
gitmusic verify --mode release --delete
   ```

3. **更新工具和依赖**
   ```bash
   pip install --upgrade yt-dlp
   pip install --upgrade -r requirements.txt
   ```

4. **重启系统**
   如果所有方法都失败，重启机器（清理临时状态）

### 常见命令别名

```bash
# 添加到~/.bashrc或~/.zshrc
gitmusic_push() {
    cd /path/to/gitmusic
    gitmusic publish --preview && gitmusic publish
    gitmusic push
}

gitmusic_sync_all() {
    cd /path/to/gitmusic
    gitmusic sync
gitmusic verify --mode data
}

gitmusic_status() {
    cd /path/to/gitmusic
    echo "=== Disk Usage ==="
    df -h .
    echo "=== Git Status ==="
git status
    echo "=== Recent Errors ==="
    cat logs/*.jsonl | jq 'select(.type == "error")' | tail -5
}
```

---

**文档版本**: 1.0  
**维护者**: GitMusic Team  
**最后更新**: 2026-01-25

---

**许可证**: 本文档与GitMusic项目采用相同许可证
