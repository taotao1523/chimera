# Chimera

**CDP eyes + Win32 hands — the undetectable browser.**

Chimera 是一个混合架构的浏览器自动化工具：用 Chrome DevTools Protocol 读取页面结构（不可见感知），用 Win32 `SendInput` API 发送真实硬件级鼠标键盘事件（无法检测的操作）。浏览器看到的只是 OS 级别的鼠标移动和键盘敲击，与真人操作无法区分。

## 为什么 Chimera 与众不同

| | Playwright / Puppeteer | Pydoll / OpenCLI | **Chimera** |
|---|---|---|---|
| 页面感知 | CDP | CDP | **CDP** |
| 鼠标操作 | CDP 事件 (可检测) | CDP 事件 (可检测) | **Win32 硬件事件 (不可检测)** |
| 键盘操作 | CDP 事件 (可检测) | CDP 事件 (可检测) | **Win32 硬件事件 (不可检测)** |
| 鼠标轨迹 | 无 | Bezier + 2~3 层模拟 | **Bezier + 5 层模拟** |
| 浏览器指纹 | 需要插件 | JS 层修补 | **CDP 预加载脚本** |

现有工具全部在浏览器层面模拟输入（`Input.dispatchMouseEvent`），能被 `KeyboardEvent.isTrusted` 检查和其他高级反自动化系统检测。Chimera 是唯一把「读」和「写」分离在不同层级的方案——读走 CDP（不可见），写走 OS（不可区分）。

## 架构

```
┌──────────────┐    读取 DOM / 计算坐标    ┌──────────────┐
│   CDP 通道    │ ──────────────────────→ │   桥接协调层   │
│  (不可见感知)  │                        │              │
│              │ ←────────────────────── │  元素→屏幕坐标  │
│ • DOM 全文    │   element.center=       │              │
│ • 元素定位    │   (800, 450)            │  滚动管理      │
│ • JS 执行    │                        │  DPI 修正      │
│ • 截图       │                        │  窗口偏移计算   │
└──────────────┘                        └──────┬───────┘
                                                │
                                         坐标 + 动作指令
                                                │
                                                ▼
                                        ┌──────────────┐
                                        │   Win32 API   │
                                        │  (真实硬件事件) │
                                        │              │
                                        │ • SetCursorPos│
                                        │ • SendInput   │
                                        │   鼠标/键盘    │
                                        │ • 贝塞尔轨迹   │
                                        │ • 生理震颤    │
                                        │ • Fitts 定律  │
                                        └──────────────┘
```

## 安装

```bash
cd chimera
pip install -e .
```

依赖: Python ≥ 3.10, `websockets`, `pydantic`。Win32 API 通过内置 `ctypes` 调用，无需额外安装。

## 快速开始

### Python API

```python
import asyncio
from chimera import Chimera
from chimera.hardware.keyboard import press_key

async def main():
    async with Chimera.launch("https://www.google.com") as c:
        # 按可见文字点击
        await c.click_text("I'm Feeling Lucky")

        # 按 CSS 选择器输入
        await c.type_into("textarea[name='q']", "Chimera browser")

        press_key("enter")
        await asyncio.sleep(2)
        await c.screenshot("result.png")

asyncio.run(main())
```

### CLI

```bash
# 启动浏览器
chimera launch https://example.com

# 交互式 REPL (支持 click, type, read, screenshot 等)
chimera> click #login-button
chimera> type input[name='email'] user@example.com
chimera> screenshot result.png
chimera> read
```

### 附加到已运行的 Chrome

```bash
# 先启动 Chrome (注意: 需要关闭所有 Chrome 窗口后重新启动)
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222

# 然后附加
python -c "
import asyncio
from chimera import Chimera
async def main():
    async with await Chimera.attach(port=9222) as c:
        await c.click_text('Login')
        print(await c.title)
asyncio.run(main())
"
```

## 人类行为模拟层

鼠标移动采用 5 层模拟 (参考: Flash & Hogan 1985, Fitts 1954, ISO 9241-9):

1. **贝塞尔曲线轨迹** — 不对称控制点产生自然弧线
2. **最小加加速度速度曲线** — 钟形速度分布 (先加速后减速)
3. **Fitts 定律计时** — 移动时间 = a + b × log₂(D/W + 1)
4. **生理震颤** — 高斯噪声，振幅与速度正相关
5. **过冲修正** — 快速移动时 ~70% 概率越过目标再回正

键盘输入模拟:
- 字符间随机间隔 (正态分布, 模拟人类打字节奏)
- 单词边界更长停顿
- 偶尔的打字错误 + Backspace 修正 (~1.5%)
- Shift 键真实按下 (无 JS 合路)

## Stealth 配置

```python
from chimera.stealth.launcher import ChromeLauncher

# 自定义 Chrome 参数
launcher = ChromeLauncher(
    chrome_path=r"C:\path\to\chrome.exe",
    user_data_dir="C:\\custom\\profile",
    extra_args=["--proxy-server=http://127.0.0.1:8080"],
    extra_prefs={"intl.accept_languages": "en-US,en"},
)
```

## 项目结构

```
chimera/
├── chimera/
│   ├── __init__.py
│   ├── cli.py              # CLI 入口
│   ├── core/
│   │   ├── cdp.py          # CDP 协议客户端 (WebSocket/Pipe)
│   │   ├── dom.py          # DOM 感知与元素定位
│   │   ├── browser.py      # 浏览器生命周期管理
│   │   └── chimera.py      # 主编排类 (公共 API)
│   ├── hardware/
│   │   ├── mouse.py        # Win32 鼠标 (贝塞尔+Fitts+震颤)
│   │   └── keyboard.py     # Win32 键盘 (人类节奏)
│   ├── humanize/
│   │   ├── trajectory.py   # 贝塞尔曲线 + 最小加加速度
│   │   ├── tremor.py       # 生理震颤模拟
│   │   └── timing.py       # Fitts 定律 + 反应延迟
│   └── stealth/
│       ├── launcher.py     # Chrome 无痕启动参数
│       └── patches.py      # CDP 预加载指纹补丁
├── examples/
│   ├── navigate.py         # 基本导航示例
│   ├── form.py             # 表单填写示例
│   └── cdp_raw.py          # 原始 CDP 操作示例
├── pyproject.toml
└── README.md
```

## 许可

MIT License
