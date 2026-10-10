# 0.1.2 功能与验收边界

此版本发布的是可安装的独立 Python 项目和本机 HTML App。输入日期后自动取数，不需要 n8n、Claude、Telegram 或私人密钥；付费 Open-Meteo 商业使用才需要自行配置订阅 key。

| 任务书项目 | 当前实现 |
| --- | --- |
| Python 3.11+、锁文件、跨平台 CLI、Apache-2.0 | 已实现 |
| HTML 操作界面 | 原生 HTML/CSS/JavaScript + 本机 Python HTTP 服务 |
| 官方潮汐 API 自动抓取 | 已真实请求，逐时数据和分钟极值保存；尚未取得独立 TimeArea 解释，绝对潮时隔离 |
| 风、阵风、风向、雨、概率、天气码、气温自动抓取 | ECMWF 主变量与 GFS 概率分源保存 |
| 波高、涌浪高/周期、海流/向、海温自动抓取 | 逐变量取数，实际响应单位转换，分别登记格点 |
| 官方预警与海洋预报 | 自动下载公开页面；可靠结构化正文核验尚未完成，状态保持未取得/未确认 |
| 三段 low→next high 与跨日逐时覆盖 | 确定性实现，有凌晨先高潮、不同站终点、第三日缺数据等测试 |
| PDF / 三张 PNG / 点位 / 日落 / 逐小时箭头与刻度 | 合成全流程实际生成，真实缺口可显式生成部分查看版 |
| 三地地图图片 | 真实静态 OpenStreetMap 海岸与道路底图；各地局部图和区域图，嵌入 PDF / 综合图，并单独输出地图 PNG |
| 已取得但时区未核实的潮位原件 | 单独显示取得状态、原始值数量和来源；PDF 原始潮位表及 CSV 保留原始时间字符串与 cm 值，不提升为已核绝对潮时 |
| 来源字节、哈希、请求/单位/时间元数据 | 冻结包；改数字与原件不符时阻断；重新获取建立新 run |
| 独立审核与候选摘要 | 分离审核记录；修改候选或重渲染使旧决定失效，无身份认证电子签名 |
| CI 与真实网络 smoke | Linux/Windows 离线 CI，手动真实取数 workflow |
| n8n / Claude 可选分析 | 尚未实现，明确 analysis_provider=none；不伪造调用或用量 |
| 公网在线服务 | 本版本未部署；GitHub 托管代码与可下载发布包，本机启动 HTML App |

真实模式尚不能自动通过全范围报告门禁。核心阻塞是潮时符号、参考站位置/湾内代表性、官方正文核验及近岸模型适用性。它们是显式的证据缺口，不以合成演示成功替代。可下载的 PDF/PNG 始终带 UNAPPROVED_REFERENCE；没有声称现实可下水。

运行 `uv run pytest` 执行完整离线验收；`uv run marine-report demo --fixture tests/fixtures/demo.json --output dist/demo` 能实际生成候选报告及源包。`uv run marine-report fetch --date YYYY-MM-DD --output runs` 执行真实取数。真实来源失败的运行退出码非零，证据与阻塞记录保留。

