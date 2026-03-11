# 数据新鲜度问题分析与解决方案

## 问题现象

您发现Dashboard显示的Lighter资金费率与官方网站显示不一致。

## 根本原因

**主扫描器程序 (main.py) 没有运行！**

诊断结果显示：
- `dashboard_data.json` 文件不存在
- 这意味着没有后台程序在更新数据

## 系统架构说明

您的套利监控系统分为**两个独立的程序**：

### 1. 后台扫描器 (main.py)
**作用**：
- 持续轮询各个交易所的API获取实时资金费率
- 每15秒轮询一次Lighter API
- 每5秒更新一次 `dashboard_data.json` 文件

**状态**：❌ **未运行**

### 2. 前端仪表板 (dashboard.py)
**作用**：
- 通过Streamlit提供Web界面
- 每10秒读取 `dashboard_data.json` 并刷新显示
- **仅负责显示数据，不获取数据**

**状态**：可能在运行，但显示的是旧数据或空数据

## 数据流程

```
[Lighter API]
     ↓ (每15秒轮询)
[Scanner: main.py]
     ↓ (每5秒写入)
[dashboard_data.json]
     ↓ (每10秒读取)
[Dashboard: Streamlit界面]
     ↓
[用户看到的数据]
```

## 为什么会显示旧数据

### 情况1: Scanner未运行 ✓ (当前状态)
- `dashboard_data.json` 不存在或包含旧数据
- Dashboard显示的是上次Scanner运行时留下的数据
- **数据可能是几小时、几天前的**

### 情况2: Scanner运行但有延迟
即使Scanner正常运行，也可能有延迟：
- Lighter API轮询间隔：15秒
- 数据写入间隔：5秒
- Dashboard刷新间隔：10秒
- **最大延迟：15 + 5 + 10 = 30秒**

### 情况3: API缓存
- Lighter API本身可能有缓存
- 官网和API看到的数据时间点不同

## 解决方案

### 立即修复

1. **启动后台扫描器**：
   ```bash
   cd C:\Users\richi\Documents\web3\script\arb_bot
   python main.py
   ```

2. **等待初始化**：
   - 程序启动后等待10秒进行初始数据采集
   - 再等待30秒确保数据完全刷新

3. **验证数据新鲜度**：
   ```bash
   python diagnose_data_freshness.py
   ```
   - 检查文件age应该 < 30秒

### 长期运行建议

#### Windows

**方法1: 使用任务计划程序**
1. 打开"任务计划程序"
2. 创建基本任务
3. 触发器：开机时启动
4. 操作：启动程序 `python.exe`
5. 参数：`C:\Users\richi\Documents\web3\script\arb_bot\main.py`

**方法2: 使用NSSM (Non-Sucking Service Manager)**
```bash
# 安装NSSM
# 下载: https://nssm.cc/download

# 安装服务
nssm install ArbBot "C:\Path\To\Python\python.exe" "C:\Users\richi\Documents\web3\script\arb_bot\main.py"
nssm start ArbBot
```

**方法3: 使用PowerShell启动脚本**
创建 `start_scanner.ps1`:
```powershell
cd C:\Users\richi\Documents\web3\script\arb_bot
Start-Process python -ArgumentList "main.py" -NoNewWindow
```

#### Linux/Mac

**方法1: systemd服务 (推荐)**
创建 `/etc/systemd/system/arb-bot.service`:
```ini
[Unit]
Description=Arbitrage Bot Scanner
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/arb_bot
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启动服务：
```bash
sudo systemctl daemon-reload
sudo systemctl enable arb-bot
sudo systemctl start arb-bot
```

**方法2: screen或tmux**
```bash
screen -S arb-bot
cd /path/to/arb_bot
python main.py
# 按 Ctrl+A, D 分离会话
```

### 监控与告警

创建监控脚本 `monitor_scanner.py`:
```python
import time
from pathlib import Path
from datetime import datetime

dashboard_file = Path(__file__).parent / "dashboard_data.json"

while True:
    if not dashboard_file.exists():
        print(f"[{datetime.now()}] ERROR: dashboard_data.json missing!")
    else:
        age = time.time() - dashboard_file.stat().st_mtime
        if age > 60:
            print(f"[{datetime.now()}] WARNING: Data stale ({age:.1f}s)")
        else:
            print(f"[{datetime.now()}] OK: Data fresh ({age:.1f}s)")

    time.sleep(30)
```

## 验证修复

运行以下命令验证一切正常：

```bash
# 1. 检查进程
ps aux | grep "python.*main.py"

# 2. 检查数据文件
ls -lh dashboard_data.json

# 3. 运行诊断
python diagnose_data_freshness.py

# 4. 实时监控
tail -f logs/arb_bot.log  # 如果有日志文件
```

## 代码本身没有问题！

经过验证：
- ✅ Lighter费率转换逻辑正确（除以8）
- ✅ API轮询逻辑正确
- ✅ 数据存储逻辑正确

**唯一的问题是程序没有运行！**

## 快速检查清单

- [ ] main.py 正在运行
- [ ] dashboard_data.json 存在
- [ ] 文件修改时间 < 30秒前
- [ ] Streamlit dashboard能看到实时数据
- [ ] 费率数据与官网基本一致（允许30秒延迟）

## 总结

**问题**：Dashboard显示旧数据
**原因**：后台扫描器未运行
**解决**：启动 `python main.py`
**预防**：配置为系统服务自动启动
**验证**：使用诊断脚本监控数据新鲜度
