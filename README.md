# 厦门三地海况报告 · Xiamen Marine Report

输入北京时间日期，自动获取海沧湾、五缘湾、环东的潮汐、天气、海洋数据和官方网页证据，冻结来源，校验跨日覆盖，生成一个 PDF、每地一张综合 PNG 和一张独立地图 PNG。提供 Python CLI 和可选中文本地 App。

**报告仅供参考，不构成下水许可。** 官方潮时的时区/基准面、预警正文、近岸模型适用性未核实或数据不齐时，返回 `BLOCKED`，只允许显著标注的未批准部分查看版。演示使用合成数据。

当前版本已实现自动接口取证和报告核心；真实完整报告的证据门禁仍有未解决项，逐项进度见 [实现状态与验收边界](docs/IMPLEMENTATION_STATUS.md)。

取得潮位原件与核实绝对潮时分别记录。原件已取得但时区定义未核实时，报告会列出原始潮位表及 CSV，按来源时间字符串和 cm 单位原样显示，不画成已核北京时间曲线。

## 安装

需要 Python 3.11+。推荐使用 [uv](https://docs.astral.sh/uv/)。仓库的 `uv.lock` 锁定完整依赖。

```sh
git clone https://github.com/waldenhchua-dotcom/xiamen-marine-report.git
cd xiamen-marine-report
uv sync --extra dev --frozen
uv run marine-report --help
```

也支持 `python -m pip install .`，HTML App 随包提供。后者遵循依赖范围，严格复现请使用 uv 锁文件。

## 中文 App

```sh
uv run marine-report app --port 8765 --output runs/app
```

在浏览器选择日期与水域，点击“自动获取 API 数据”；校验结果会展示缺失来源。可下载冻结证据包、PDF、综合 PNG 和地图 PNG，并分别预览。点击“离线合成演示”可在没有网络或密钥时验证生成流程。App 默认仅绑定本机；多人部署前需要自行设置认证、存储隔离和资源限制。

## 命令行

```sh
uv run marine-report demo --fixture tests/fixtures/demo.json --output dist/demo
uv run marine-report fetch --date 2026-10-10 --sites haicang wuyuan huandong --output runs
uv run marine-report validate --run runs/<run-id>
uv run marine-report render --run runs/<run-id> --output dist
uv run marine-report render --run runs/<run-id> --output dist --allow-partial
uv run marine-report review --run runs/<run-id> --decision approve --review-file review.json
```

`fetch` 自动取数，生成唯一 run 目录和不可变 `manifest.json`、原始响应证据。`validate` 用机器可读 JSON 报告缺口。正式三段候选必须通过全范围门禁；`--allow-partial` 是显式的受限查看请求，始终标记 `UNAPPROVED_REFERENCE`。重复取数创建新 run，重复渲染创建新候选，不覆盖历史来源。

日期支持范围受上游 API 预报窗口限制；本程序不承诺任意过去或未来日期均可取得实时预报。API 失败不会改用合成数据、邻日数据或零值。

## 自动接口与现有限制

各接口的实际地址、请求结构、更新频率、单位、模型、许可和验证记录见 [DATA_SOURCES](docs/DATA_SOURCES.md)。

- Open-Meteo ECMWF 天气 API：风、阵风、来向、降雨、概率、天气码、气温。
- Open-Meteo Marine API：逐变量请求实际海洋格点，记录偏移和可得性。
- 国家海洋信息中心公开网站接口：参考站逐时潮位和分钟极值；源站信息和原始时区符号留存。缺少独立时区证明时隔离绝对潮时。
- 厦门海洋预报台公开网页：取证网页正文和栏目状态；未确认完整目标日期与覆盖时段时保持“未取得/未确认”。

免费 Open-Meteo 接口适用于非商业使用，并有请求量限制；商用需按其条款配置付费订阅。将代码开源不等于获得所有数据的再分发权。仓库不包含真实海况源包、私有密钥或私人报告。

## 地图图片

每处水域提供独立地图 PNG，也嵌入 PDF 和综合 PNG。地图包含局部湾区及区域位置图，显示海岸、主要道路、目标规划点、参考潮位站和各变量实际格点。未核实的站点坐标不强行定位，合成演示站点与格点显著标记。

底图采用随包提供的 OpenStreetMap 静态矢量快照；离线运行也能绘制。地图写明快照日期、WGS84 坐标与来源，并随候选包保存地图证据及哈希。静态海岸线不代表实时潮流，也不表示获准下水的位置。地图数据的 ODbL-1.0 许可与应用代码的 Apache-2.0 许可分别适用，详见 [DATA_SOURCES](docs/DATA_SOURCES.md)。

## 三段与时间轴

按每处参考站，选目标日本地午夜之后首个低潮，以及紧邻的下一次高潮，依次取三段完整 `low → high`。若凌晨首先出现高潮，则跳过该高潮，从当天首个低潮起算。每站最后一段后延伸到**严格晚于高潮的首个整点**；不同站终点分别计算。

逐小时变量不得缺失、重复或跳时，逐时潮线使用源逐时值，分钟极值单独标注。风向箭头表示吹去方向，中文标注来向；洋流箭头表示流去方向。每小时标刻度，跨日午夜同时标日期。每水域每日期显示天文算法日落估算，未校正地平线、地形和天气。

## 审核

校验通过、独立报告审核通过、现场安全可行是三个不同概念。程序只记录前两项。审核者必须回查源哈希、全部图页和站点适用性，并填写 [审核清单](docs/REVIEW.md)。决定绑定最终候选摘要；编辑来源、PDF 或 PNG 后旧审核失效。

PDF/PNG 本身保留未批准查看版字样；独立审核决定作为分离的 `review.json` 保存，当前版本不会悄悄把已生成的文件改成“正式安全报告”。无需 n8n/Claude 即可运行；`analysis_provider=none`，没有伪造 AI 调用记录。可选自动化接入边界见 [ARCHITECTURE](docs/ARCHITECTURE.md)。

## 测试和 GitHub 使用

```sh
uv run ruff check .
uv run pytest
uv run python scripts/security_scan.py
uv build
```

CI 在 Linux 和 Windows 上执行离线演示与结构/哈希检查；不依赖外网 API。仓库 Actions 里还有手动触发的真实取数流程，日期输入后将证据和校验结果打包成可下载产物，完整数据被阻断时不会伪装成功。

GitHub 只托管代码和 Actions，App 通过上述命令运行；本版本未部署到公网网站，也没有自动定时取数或向用户发消息。

## 许可

由仓库所有者选定：Apache-2.0，见 [LICENSE](LICENSE)。字体与数据许可见 [NOTICE](NOTICE) 和来源说明。风险边界见 [SAFETY](docs/SAFETY.md)。
