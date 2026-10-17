# TouchSight EDU

面向全盲或重度视障学生的 STEM 动态触觉学习软件原型。
## 当前软件闭环

### 入口一：图片直转

```text
上传已准备好的图片
→ 完整 contain（不裁剪、不拉伸）
→ 40×50 H0-H3 Matrix（2000 点）
→ 2D/3D Simulator
→ Hardware Adapter
```

### 入口二：AI 教学图

```text
科目 + 学习目标 + 教学内容/题目 + 可选图片
→ AI 规划教学场景（决定学生现在应该摸什么）
→ 确定性 Scene Compiler
→ 40×50 H0-H3 Matrix（2000 点）
→ 继续指令：加入曲线 / 放大顶点 / 隐藏坐标轴 / 突出重点
→ 新 Matrix
```

入口二现在有三种明确方式：

- `AI 根据文字绘图`：本地千问根据学科、目标、题目和指令规划图形；
- `AI 看图提取主体`：视觉模型读取题目截图，只重建教学主体，并在模型输出后再次强制删除坐标轴；
- `本次图片直接转点阵`：点阵只来自本次新上传图片，本地千问只负责生成文字教学计划。

每次选择新文件时，页面会立即清空旧点阵、清除上一张图的教学描述，并给当前文件生成短指纹。生成请求与该文件指纹及请求代次绑定；旧请求即使较晚返回，也不能覆盖新图片的结果。

复杂普通照片应使用“图片直接转点阵”：软件依据当前图片的明暗和轮廓生成点阵，不做语义猜测。带题干、坐标轴、刻度或公式的教学截图应使用“AI 看图提取主体”：视觉模型只重建需要触摸的主体，编译器再过滤坐标轴类区域。40×50 点阵可以表达复杂照片的主要结构，但不会保留原图的全部视觉细节。

上传新图片后，如果视觉模型已经就绪，页面会自动选择“AI 看图提取主体”；否则自动选择“本次图片直接转点阵”。右侧旧点阵会立即清空，旧结果仍可在历史记录中单独恢复。

当前同时配置两条模型链路：本地 `qwen/qwen3-4b-2507` 负责文字场景规划；OpenRouter `openrouter/free` 负责看图提取主体。视觉请求完成后，页面会显示 OpenRouter 实际选择的模型、输入类型与编译校验状态。配置了模型但调用失败时会明确报错，不会用离线模板冒充 AI 结果。

AI 结果页会给出三步触摸教学路径：先保留主体，再聚焦关键区域，最后生成对比帧；每一步同时说明预期触觉效果和软件实现方式。可执行步骤可以直接生成下一帧。

## 生成历史

每次生成都会把完整矩阵、教学说明、生成方式和时间保存到 `output/history/`。页面底部可以查看缩略图并恢复旧帧；恢复历史不会再次调用模型。

当前没有模型密钥时，离线演示编译器支持二次函数和三角形，并在页面明确标注，不冒充在线 AI。

## 硬件边界

原机械 V3.0 文档中的 12×16 限制已按本轮决定取消。当前软件侧暂定：

- 当前软件输出为 40 列 × 50 行，共 2000 Pixel；
- H0/H1/H2/H3 = 0/2/4/6 mm；
- 行优先矩阵，原点左上；
- RDK 负责 Web/UI、AI 和矩阵生成；
- ESP32 负责 Writer、X/Y、Lock、Reset 并接收高度矩阵。

尚未冻结，因此软件没有擅自实现：

- ESP32 串口/网络具体命令；
- Writer 扫描顺序与并行头数量；
- Lock/Release/Reset 时序；
- 完整刷新完成确认与错误码。

详细边界见 [`docs/HARDWARE_ADAPTER_CONTRACT.md`](./docs/HARDWARE_ADAPTER_CONTRACT.md)。当前完成与未完成项见 [`docs/STATUS.md`](./docs/STATUS.md)。

现在默认把同一帧导出为 `output/hardware/current_frame.json` 和 2-bit `current_frame.tse`。硬件组提供控制桥后，在 `config.env` 设置 `TOUCHSIGHT_HARDWARE_URL` 即可把同一 JSON POST 给控制桥。

## 启动

Windows 双击 `start.bat`，或者：

```powershell
cd touchsight-edu
$env:PYTHONPATH="$PWD\src"
python -m touchsight_edu --host 0.0.0.0 --port 8877
```

打开 <http://127.0.0.1:8877/>。

项目只依赖 Pillow。若当前 Python 未安装：

```powershell
python -m pip install -e .
```

## 在线 AI

复制 `config.example.env` 为 `config.env`，可以分别填写文字模型和视觉模型：

```text
AI_API_KEY=...
AI_BASE_URL=...
AI_MODEL=...
AI_SUPPORTS_VISION=true 或 false

VISION_API_KEY=...
VISION_BASE_URL=https://openrouter.ai/api/v1
VISION_MODEL=openrouter/free
VISION_SUPPORTS_VISION=true
```

密钥文件已经被 `.gitignore` 排除。

## 测试

```powershell
$env:PYTHONPATH="$PWD\src"
python -m unittest discover -s tests -v
node --check web/app.js
node --check web/pin_renderer.js
```

## 当前完成度声明

- 软件模拟器：已实现；
- 图片直转：已实现原型；
- 函数教学动态交互：已实现 MVP；
- 第二入口的本次图片直转：已实现；
- 生成历史与恢复查看：已实现；
- 本地 AI：已接入并实测 `qwen/qwen3-4b-2507`；该模型仅支持文字；
- 在线视觉 AI：已通过 OpenRouter 免费视觉路由实测，页面显示实际返回模型且失败时不静默降级；
- 3D Pin 自适应取景：已包含底板边缘与最高针脚，不再裁掉底部；
- 实体 40×50 阵列：不宣称已经完成；
- 真机自动刷新：等待硬件控制协议后接入。
