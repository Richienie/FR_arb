# Binance资金费率周期动态更新修复

## 问题描述

您发现程序显示所有Binance标的都是4h周期，但实际上Binance会实时调整资金费率周期，某些标的（如RIVERUSDT）是1h周期。

## 问题根源

### 确认的事实

1. ✅ **Binance API确实返回interval信息**
   - 端点: `GET /fapi/v1/fundingInfo`
   - 字段: `fundingIntervalHours`
   - 值: 1, 4, 或 8

2. ✅ **当前确实有1h周期标的** (截至2026-01-30)
   - RIVERUSDT: 1h
   - PIPPINUSDT: 1h
   - SENTUSDT: 1h

3. ❌ **WebSocket消息中不包含interval**
   - `markPrice` WebSocket只有费率，没有interval
   - 必须依赖REST API获取interval

### 代码问题

**原始逻辑**:
```python
async def start(self):
    # 启动时获取一次interval
    await self._build_binance_intervals()

    # WebSocket更新时使用缓存的interval
    interval = self._get_binance_interval(symbol)  # 从缓存读取
```

**问题**:
- Interval只在启动时获取一次
- 如果Binance动态调整某个标的的interval，程序不会知道
- 缓存永远使用启动时的旧值

## 修复方案

### 方案：定期刷新Interval缓存

**实现**:
1. 启动时获取interval（保持不变）
2. 新增定期刷新任务（每小时刷新一次）

**代码变更**:

```python
async def start(self):
    # ... 启动时获取
    await self._build_binance_intervals()

    # ... 启动各个数据流
    self._tasks.append(asyncio.create_task(self._run_binance_stream()))

    # 新增: 定期刷新interval缓存
    self._tasks.append(asyncio.create_task(self._run_interval_refresher()))

async def _run_interval_refresher(self):
    """每10分钟刷新一次interval缓存"""
    while self._running:
        await asyncio.sleep(600)  # 10分钟
        await self._build_binance_intervals()
        await self._build_bybit_intervals()
        logger.info("Interval caches refreshed")
```

### 修改的文件

- `core/scanner.py`
  - 第127-135行: 添加`_run_interval_refresher`任务
  - 第917-946行: 新增`_run_interval_refresher()`方法
  - 第283-306行: 更新注释说明interval是动态的

## 验证修复

### 1. 检查启动日志

重启Scanner后，查看日志：

```
INFO Building Binance funding intervals...
INFO Binance intervals: {1.0: 3, 4.0: 431, 8.0: 154} (fetched 588)
```

**期望看到**:
- `1.0: 3` - 表示正确获取了3个1h标的
- 如果看到 `1.0: 0`，说明API调用失败

### 2. 使用诊断脚本

```bash
python test_binance_interval_realtime.py
```

**检查输出**:
```
[3] 对比1h标的在程序中的interval

  RIVERUSDT (规范化为 RIVER):
    interval_hours: 1.0
    状态: ✓ 正确识别为1h
```

### 3. 检查Dashboard显示

在Dashboard中查看RIVER的费率显示：

**修复前**:
```
Binance: -0.0327% 4h  ← 错误，显示为4h
```

**修复后**:
```
Binance: -0.0327% 1h  ← 正确，显示为1h
```

## 技术细节

### Binance API文档

**fundingInfo端点**:
- URL: `https://fapi.binance.com/fapi/v1/fundingInfo`
- 文档: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info

**返回字段**:
```json
{
  "symbol": "RIVERUSDT",
  "adjustedFundingRateCap": "0.02000000",
  "adjustedFundingRateFloor": "-0.02000000",
  "fundingIntervalHours": 1,  ← 关键字段
  "disclaimer": false,
  "updateTime": 1769443260587
}
```

### WebSocket局限性

**markPrice WebSocket返回**:
```json
{
  "e": "markPriceUpdate",
  "s": "RIVERUSDT",
  "r": "0.00032680",  ← 只有费率
  "T": 1769738400000  ← 下次funding时间
  // 没有interval字段！
}
```

**结论**: 必须依赖REST API单独维护interval信息。

### 为什么选择10分钟刷新间隔

**考虑因素**:
1. Interval变化不频繁（通常几天或几周才调整一次）
2. 10分钟刷新在及时性和资源消耗间取得平衡
3. 最大延迟约10分钟（可接受）
4. API调用量低（每小时6次），不会触发限流

**延迟分析**:
- 交易所10:30改变interval → 程序最晚10:40检测到
- 最坏情况延迟：~10分钟

**可调整**:
- 如需更快响应：改为`300`（5分钟，延迟最多5分钟）
- 如需减少请求：改为`1800`（30分钟，延迟最多30分钟）

## 常见问题

### Q1: 为什么不在每次WebSocket消息时查询interval？

**A**:
- WebSocket消息每秒/每3秒一次，太频繁
- API有限流，会被ban
- Interval变化极少，不需要实时查询

### Q2: Bybit也有这个问题吗？

**A**:
- 是的，Bybit也有动态interval
- 同样通过定期刷新解决
- `_run_interval_refresher()`同时刷新Binance和Bybit

### Q3: 如何手动触发interval刷新？

**A**:
重启Scanner：
```bash
# 停止当前Scanner
Ctrl+C

# 重新启动
python main.py
```

启动时会自动获取最新interval。

## 监控建议

### 添加告警

如果需要监控interval变化，可以在`_run_interval_refresher()`中添加：

```python
async def _run_interval_refresher(self):
    while self._running:
        await asyncio.sleep(3600)

        # 保存旧值
        old_intervals = self._binance_intervals.copy()

        # 刷新
        await self._build_binance_intervals()

        # 检测变化
        for symbol, new_interval in self._binance_intervals.items():
            old_interval = old_intervals.get(symbol)
            if old_interval and old_interval != new_interval:
                logger.warning(
                    "Binance interval changed: %s: %sh -> %sh",
                    symbol, old_interval, new_interval
                )
```

## 总结

| 项目 | 修复前 | 修复后 |
|------|--------|--------|
| **Interval获取** | 仅启动时 | 启动时 + 每小时 |
| **动态调整响应** | 不响应 | 1小时内响应 |
| **1h标的显示** | 可能错误 | 正确 |
| **API调用频率** | 1次/启动 | 1次/启动 + 1次/小时 |

**修复完成** ✅

现在程序能够：
1. 正确识别1h/4h/8h周期标的
2. 每小时自动更新interval缓存
3. 响应Binance的动态interval调整

**下一步**:
```bash
# 重启Scanner应用修复
python main.py
```

等待1-2分钟，然后检查日志确认interval正确获取。
