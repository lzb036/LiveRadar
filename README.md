# 开播雷达 LiveRadar

一个本地运行的开播监测工具，支持 Bilibili、虎牙和抖音。主播数量由你自己在页面中添加，程序会把多个平台统一成一张监测列表。

## 已实现

- 手动添加 Bilibili、虎牙直播间，支持房间链接或房间 ID。
- 抖音支持填写直播间 ID，直接根据直播间地址检测状态。
- 抖音点击“打开”始终进入直播间地址；主页链接不是添加和检测的必填项。
- 保存到本地 SQLite，不依赖云服务。
- 默认每 60 秒自动检查一次，也可以手动立即检查或检查全部。
- 状态统一显示为直播中、未开播、回放、检查失败、已停用。
- 直播间列表显示开播时间和已播时长；下播后保留上一场数据，下次开播时更新。
- Bilibili 和虎牙优先使用平台返回的开播时间；抖音未提供稳定的公开开始时间时，使用首次确认开播时间。
- 每个直播会话最多发送一次开播通知和一次下播通知；检测失败不会重置直播会话。
- 通知内容简洁显示为：`（平台）主播开播了：直播标题`，下播显示为 `主播下播了，时长为HH:MM:SS`。
- 微信通知支持：
  - `Server酱`：适合个人微信，通过 SendKey 接收通知。
  - `企业微信机器人`：适合企业微信，通过群机器人 Webhook 接收通知。
- 不抓取、不转播直播流，只保存直播间地址并跳转到平台观看。
- 管理页面使用自定义登录页，不再依赖浏览器原生 Basic Auth 弹窗。
- 管理台拆分为首页、直播间、通知记录三个 Tab，顶部导航在页面滚动时保持可见。
- 直播间列表支持按平台筛选，通知记录支持确认后清空。

抖音填写直播间 ID 即可检测。生产部署使用本机 `douyinLive` 的只读状态接口，避免直播页旧场次数据造成误判；辅助服务无法确认状态时会显示“检查失败”，不会自动当作未开播。辅助服务仅用于读取状态，不会播放、录制或转发直播。
虎牙标准页面偶发返回降级内容时，程序会自动尝试带 `hyaction=home` 的完整直播页。

部署到宝塔后，可以通过独立子域名访问，例如 `https://liveradar.bugtuisan.com/`。原来的 `/liveradar/` 入口仍可保留。

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

## 登录

应用账号通过环境变量配置：

```text
LIVE_MONITOR_USERNAME=liveradar
LIVE_MONITOR_PASSWORD=替换为强密码
```

第一次启动且数据库还没有账号时，会创建账号；如果没有设置密码，程序会在启动日志中输出一次随机初始密码。登录会话保存在本地 SQLite 中，有效期为 7 天；服务重启或重新部署后，只要会话没有过期，就不需要重新登录。

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
deploy/
  liveradar.service
  liveradar.nginx.conf
  liveradar-subdomain.nginx.conf
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
