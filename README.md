# AI Usage HUD · 桌面余额与额度挂件

Windows 上的 Qt 半透明桌面挂件，查看 DeepSeek、MiMo 余额和 Codex 五小时／每周额度。

## 功能

- 默认 68×252 主挂件，悬停时在旁边独立弹出详情窗。
- 主挂件和详情窗分别调整宽高、形状：圆角、直角、圆形、正方形、三角形、胶囊等。
- 白色弧线拖拽缩放；形状内布局，小窗口滚动显示详情。
- 屏幕边缘吸附、半隐藏、悬停滑出，识别多屏外边界。
- 深浅色扩散动画、背景透明度、锁定和恢复默认布局。
- Codex 真实五小时／每周用量、剩余比例、重置时间及更新时间；只显示，不提醒。
- 读取失败保留旧值并标明过期，缺失额度显示“未获取”。

## 从源码运行

需要 Windows、Python 和 PySide6。此仓库不包含 EXE 或打包运行库。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe ai-hud-qt.py
```

打开设置：

```powershell
.\.venv\Scripts\python.exe ai-hud-qt.py --settings
```

不会自动添加开机启动项、计划任务或服务。

## 数据配置

### Codex

在当前 Windows 用户下安装并登录 Codex。程序读取官方 App Server 的 `account/rateLimits/read`，仅请求初始化和额度，不发送模型推理请求。Codex 在用户资料目录中管理登录；本项目不读取或保存 `auth.json`。

按实际 300／10080 分钟窗口识别五小时与每周额度。官方读取失败时尝试本机历史快照并明确标记过期。这里显示的是 Codex 账号额度，不是 ChatGPT 网页各模型的全部消息上限。

### DeepSeek

二选一：设置 `DEEPSEEK_API_KEY` 环境变量，或把 `.env.example` 复制为 `.env` 后在本机填写。`.env` 已被 Git 忽略。

也兼容 `%LOCALAPPDATA%\hermes\.env`；可用 `HUD_ENV_FILE` 指定文件。

### MiMo 与 Hermes 用量

MiMo 需要本地有效的 `mimo-cookie.txt`，或通过 `HUD_MIMO_COOKIE_FILE` 指定文件。Cookie 过期显示真实错误并退避六小时，不能自动替用户登录。

今日 token／费用来源为 Hermes 的 `session_model_usage` 表，可用 `HUD_STATE_DB` 指定只读数据库。它按当天活跃会话累计近似统计，不能覆盖其他入口的全部用量。没有接入数据库时显示 0，不代表账号当天实际消费为 0。峰谷提示是项目内预设，不是实时价格接口。

## 本地配置与隐私

外观保存在 `hud-qt.json`；凭证、Cookie、登录资料、数据库、个人配置和日志均不提交。不要把实际凭证写入 Issue、README、截图或代码。

## 检查

离线数据及额度测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

桌面 Qt 回归（需要可用的 Windows 桌面）：

```powershell
.\.venv\Scripts\python.exe tests/run_qt_checks.py
```

Qt 回归使用临时配置，不读取实际额度、不修改正在运行的 HUD，结束后退出测试进程。测试产物默认写到系统临时目录的 `ai-usage-hud-tests`；可用 `HUD_TEST_OUTPUT` 覆盖。

本次发布前验证：10 个离线测试、279 项 Qt 检查通过，Qt 实测运行库为 6.12.0。覆盖独立弹窗、形状安全区、多屏停靠、锁定、恢复默认以及缺失／过期额度显示。

## 文件

| 文件 | 用途 |
|---|---|
| `ai-hud-qt.py` | Qt 窗口、布局、设置与刷新 |
| `ai-hud.py` | DeepSeek／MiMo 纯数据层 |
| `hud_geometry.py` | 形状、可用内容区与吸附 |
| `hud_codex_usage.py` | Codex 额度读取与历史回落 |
| `tests/` | 离线和桌面回归 |
