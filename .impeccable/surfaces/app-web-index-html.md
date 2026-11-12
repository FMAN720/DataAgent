---
version: 1
slug: "app-web-index-html"
primary_target: "app/web/index.html"
related_targets: ["app/web/workspace.css","app/web/workspace.js"]
---

# 分析工作台

## Scope

Route `/`，Operate 模式。2026-11-03 用户要求重做前端，并选择分析提问与结果优先的首页、证据索引方向。表格、文档、检索、图片核对、历史与导出继续可用。保持原生 HTML/CSS/JavaScript，不增加运行时、远程字体或依赖。

## Direction contract

**THESIS**：让用户从问题进入分析，并能沿真实 E 编号回到查询依据。首页不再以资源管理表格开场，也不放未经查询的经营数字。

**OWN-WORLD**：证据索引。墨蓝导航与浅蓝灰画布、白色输入区、青色选中态；系统无衬线字体，等宽字体仅用于代码和证据编号。细分隔线与紧凑索引表达归档感，所有操作保持标准按钮、表单和侧栏导航。

**STORY**：打开即可理解如何提问，选择示例只填入问题，主动提交才运行分析。回答、图表、审查和来源在同一阅读流中，数据准备可随时进入。

**FIRST VIEWPORT**：桌面固定宽度导航；顶部为面包屑和实际服务状态。主区左侧以问题输入框为视觉中心，包含简短欢迎语、三条具体示例与外发说明；右侧是数据入口和提问指导。手机收为顶部导航、单列输入与结果，次要指导移至下方。已有会话收起欢迎内容；E 编号索引点击后展开并定位对应工具记录。

**FORM**：用户已选的第六候选“证据索引”，code-led。保留档案索引的清晰信息密度，借网格研究方向的对齐纪律与逐项可定位性提高精度，不引入装饰网格或仿终端控件。其余候选的闪烁、图版与形变影响工作任务，未采用。seed: none (unavailable)。

**FINISH**：unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Behavior and verification

浏览与示例填入不发起聊天请求。真实状态、金额字符串、引用、图片外发同意和审查限制保持原逻辑。验证桌面、手机、数据详情、输入、历史、停止与来源索引；视觉检查最多两轮。将最终令牌从实际实现记录到 DESIGN.md 与 `.impeccable/design.json`。
