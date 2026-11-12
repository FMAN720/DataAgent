# 工作台改版验收（2026-11-03）

用户确认首页以分析提问和结果为中心，采用“证据索引”：墨蓝侧栏、浅色分析画布、结论与记录清晰对应。改动以原生 HTML、CSS、JavaScript 完成，没有引入前端框架、远程字体或新的依赖。

## 已完成

- 默认首页改为分析工作台，表格、文档、检索集中在导航栏；新建分析可从其他页面返回提问区。
- 输入区、三条业务示例、历史入口和数据准备入口重新排版。示例只填入问题，不自动提交；支持 Ctrl / Command + Enter 主动分析。
- 回答中的 E 编号可展开并定位相应工具记录。保留失败记录和原有审查状态，记录索引不表示该结论已核验。
- 表格详情、上传、文档检索、图片核对、图表、历史与导出延续现有能力；新增同源 SVG 图标，并补充打包声明。
- 手机收为顶部导航和单列分析内容；取消会遮住展开记录的悬浮输入框。正文、提示文字、按钮和侧栏的六组关键配色均达到 4.5:1 对比度。

## 验证

| 检查 | 结果 |
|---|---|
| `node --test tests/workspace.test.cjs` | 20 项通过，包含示例不自动提交、主动快捷键、E 编号定位与完整记录保留 |
| `python -m pytest -q tests/test_phase5_documents.py tests/test_api.py tests/test_vision.py` | 47 项通过 |
| `node --check app/web/workspace.js` | 通过 |
| `ruff check app eval tests lab`、`mypy app` | 通过 |
| 实际浏览器 | 1440×900、1280×800、390×844；无页面横向溢出、无运行时异常 |
| 交互 | 示例填入、数据详情、两类上传入口、检索导航、新建分析、历史列表、模拟流式回答和 E3 定位均通过 |

共享预览截图超时、本机交互浏览器连接不可用后，使用已安装 Chrome 的隔离无界面实例，以精确视口覆盖和全页截图完成验证；没有下载浏览器或测试依赖。聊天请求在浏览器内拦截并返回明确标注的合成内容，没有触发付费模型或保存测试报告。

机器可读证据：[浏览器检查](../../.impeccable/review/browser-checks.json)、[对比度](../../.impeccable/review/contrast.json)。截图：[桌面首页](../../.impeccable/review/desktop.png)、[手机首页](../../.impeccable/review/mobile.png)、[数据详情](../../.impeccable/review/desktop-data.png)、[合成回答](../../.impeccable/review/desktop-result.png)、[手机展开记录](../../.impeccable/review/mobile-result.png)。

## 设计审查

独立审查第一次因额度限制中断，随后恢复原审查并完成，最终 `disposition: ship`、无待修复项，见[审查记录](../../.impeccable/review/finish-review.md)。设计方向为用户确认的代码实现，没有批准的效果图；旧版技能 CLI 不能验证新版种子引用，这项工具版本限制已记录，未把后来生成的无关轮次当作用户选择。

机械检测只运行一次。源码解析器不能解析 FastAPI 的 `/assets/` 挂载路径，因此 HTML 的“字体层级相同”属于未加载样式的结果；图片核对的页图在打开草稿后才赋值，不在首页显示缺图；旧设计令牌的提示由本次设计规范更新处理。没有据此更改正常的资源路由或放宽业务检查。
