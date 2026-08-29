# 开播雷达 LiveRadar

一个本地运行的开播监测工具，支持 Bilibili、虎牙和抖音。主播数量由你自己在页面中添加，程序会把多个平台统一成一张监测列表。

## 已实现

- 手动添加 Bilibili、虎牙直播间，支持房间链接或房间 ID。
- 抖音支持直播间链接，并可额外保存主播主页链接作为长期标识。
- 保存到本地 SQLite，不依赖云服务。
- 默认每 60 秒自动检查一次，也可以手动立即检查或检查全部。
- 状态统一显示为直播中、未开播、回放、检查失败、已停用。
- 只有状态发生变化时才通知，首次检查不会因为主播已经开播而重复通知。
- 微信通知支持：
  - `Server酱`：适合个人微信，通过 SendKey 接收通知。
  - `企业微信机器人`：适合企业微信，通过群机器人 Webhook 接收通知。
- 不抓取、不转播直播流，只保存直播间地址并跳转到平台观看。

抖音当前使用直播间公开页面探测状态，不依赖主播授权；建议同时填写主播主页链接。抖音页面无法确认状态时会显示“检查失败”，不会自动当作未开播。

## 启动

当前目录下执行：

```powershell
python server.py
```

然后在浏览器打开：

```text
http://127.0.0.1:8765
```

默认只监听本机地址。数据库会自动创建在 `data/monitor.db`。

## 微信通知配置

在页面右上角打开“通知设置”：

1. 选择 `微信（Server酱）`，填写 Server酱 SendKey。
2. 或选择 `企业微信机器人`，填写机器人 Webhook。
3. 保存后点击“发送测试通知”确认配置。

通知配置保存在本地数据库中，`serverchan_sendkey` 和 Webhook 不会通过列表接口返回。
个人微信采用 Server酱这类通知中转，不需要程序登录个人微信；企业微信则直接使用群机器人 Webhook。

## 项目结构

```text
backend/
  app.py          HTTP 路由和静态文件服务
  database.py     SQLite 数据库
  monitor.py      定时监测和状态变化通知
  notifier.py     Server酱、企业微信通知
  platforms.py    Bilibili、虎牙、抖音平台适配器
frontend/
  index.html
  app.js
  styles.css
tests/
  test_platforms.py
server.py         本地启动入口
```

## 校验

不需要启动前后端服务即可执行：

```powershell
python -m unittest discover -s tests -v
python -m compileall backend server.py
```

平台页面结构和公开接口可能变化；如果某个平台出现“检查失败”，页面会保留错误信息，不会把错误状态误报为未开播。
