# 数据来源与真实接入边界

最后核验日期：**2026-10-10**。这是本版本的接口调查记录，接口和条款会变化；不是长期免费或持续可用的承诺。代码许可证 Apache-2.0 不会重新许可上游数据。真实下载保存在用户自己的 run 中，默认不随公开仓库分发。

## 自动取得什么

`marine-report fetch` 自动查询官方潮表、ECMWF 气象、另源降水概率、六项海洋变量以及厦门官方公开页面。原始响应字节、请求、HTTP 状态、取证 UTC 时间和 SHA-256 被冻结。每次取数创建新 run；缺值保留 `null`，不使用零或邻近日期代替。

| 接入 | 自动保存的数据 | 当前可核验范围 | 仍然阻断完整报告的事项 |
| --- | --- | --- | --- |
| 国家海洋信息中心全球潮汐预报平台 | 每站三天的逐小时厘米潮位和分钟极值原件 | 返回站号、年月日、站名、坐标字段、基准面文字 | `TimeArea=-0800` 符号含义缺独立官方说明；参考站与具体湾内关系及坐标 CRS 尚未核验 |
| Open-Meteo ECMWF | 风、阵风、风向、小时降水、天气代码、气温 | 固定请求 `ecmwf_ifs025`，逐项单位及实际返回格点 | 未返回可独立核验的模式起报时间；网格预报并非实测 |
| Open-Meteo GFS | 降水概率 | 另一次 `gfs_seamless` 请求，单独 source/grid | 不把该变量署名为 ECMWF；集合概率不等于官方预警 |
| Open-Meteo Marine | 有效波高、涌浪高及周期、流速/流向、海温 | 每变量单独请求和实际返回格点 | 约 8 km 模型对封闭湾内的适用性未证实，始终阻断局地安全结论 |
| 厦门海洋中心公开页面 | 主页及公开预报页原件 | 能访问页面并保存原件 | 尚未可靠结构化核验目标日预警/预报正文、发布时间、有效段和海区覆盖 |

真实取数能够成功，而完整报告仍可以是 `BLOCKED`。网页 HTTP 200、预报数值存在、离线示例成功，均不证明现场安全可行。

## 官方潮汐：已实现真实自动接口

- 官方机构入口：[国家海洋信息中心](https://www.nmdis.org.cn/)。
- 官方平台：[全球潮汐预报服务平台](https://global-tide.nmdis.org.cn/Default.html)。
- 当前网页实现：[Site/Site.html](https://global-tide.nmdis.org.cn/Site/Site.html)、[BLLData.js](https://global-tide.nmdis.org.cn/script/BLLData.js)、[formScript.js](https://global-tide.nmdis.org.cn/script/formScript.js)、[BLLChart.js](https://global-tide.nmdis.org.cn/script/BLLChart.js)。
- 实际接口：HTTPS `POST https://global-tide.nmdis.org.cn/Api/Service.ashx`，表单 `application/x-www-form-urlencoded`，不是猜测出的 REST 端点。官方页面脚本提供了这项调用方式。

表单 `ApiRequest` 的内容如下；表单编码由客户端完成：

```json
{"Server":"User","Command":"GetData","Data":{"code":"T122","date":"2026-10-10"}}
```

成功响应的结构为：

```text
State=true
Data.Site: Code, Name, CoordX, CoordY
Data.Data: SiteCode, Year, Month, TimeArea, Benchmark, RecordTime
Data.SubData: Day, a0 ... a23, cs0/cg0 ... cs5/cg5
```

`a0...a23` 是平台原有逐小时数据，官方图表脚本标注单位 `cm`。`csN/cgN` 是按时序排列的极值时间和高度；不能假定 `cs0` 一定是低潮。代码只对具备前后邻点的极值按相邻**官方极值高度**作派生高低分类，保留方法说明。没有用四个极值插值制造“官方逐小时潮位”。当天只出现三个极值时，未用的 `cs3/cg3...` 可以正常为 `null`；存在时间但高度为 `null`、时间 `nu:ll` 或小时字段缺失则不能接受。

程序针对海沧候选参考站 `T122`，五缘湾和环东候选参考站 `NT1405` 各查询目标日、次日和第三日。两个站均自动查询、返回站号与请求逐日比对，不将站号对应关系当成水域适用性证据。

2026-10-10 的现场接口调查取得 `T122` 响应：站名厦门，返回坐标字段 `CoordX=118.07000000`、`CoordY=24.44833000`，基准面文字为“在平均海面下328cm”。该记录只描述本次响应；坐标字段的 CRS 和湾内代表性仍缺官方独立说明，不能据此推导水深或授权下水口。编制时间 `RecordTime` 为未经时区核实的原文字段，不能冒称模式起报 UTC 时间。

**时间隔离：**原件中的 `TimeArea=-0800` 不按 ISO UTC−08:00 解释，也没有未经证实地直接解释为 UTC+08:00。原始逐时值和分钟極值放入 `quarantined_tides`，保留无 UTC 偏移的 `local_time`，不进入具有绝对时间语义的 `series/extrema`。正式涨潮段识别必须等待独立时间证据。需要同时核验基准面及站点对湾内水域的适用关系。

连续原件的未限定时区本地时序可以用来判断“第三个 low→high 之后整点属于哪一个本地日期”，并记录为**临时日期检查**。这项检查不确认绝对时刻。若目标日及次日已包含三个完整本地段及随后整点，额外第三天的空响应仅为 `TIDE_SUPPLEMENTAL_DAY_UNAVAILABLE` 非阻断补充缺口；不能误报为必须数据跨日缺失。若真正需要的次日缺数据，仍返回 `BLOCKED_CROSS_DAY_DATA`。站点时间语义缺口仍阻断完整报告。

**许可：**公开页面可查询不等于可以任意再分发。当前没有找到足以给这批真实原件重新授权的开放数据许可。因此真实原件只冻结在本地 runs，示例使用项目生成的合成数据。发布真实数据包之前需核上游许可；代码没有海外镜像或失败后代理替换逻辑。

## 气象：ECMWF 主变量，GFS 降水概率另列

官方文档：[ECMWF API](https://open-meteo.com/en/docs/ecmwf-api)、[GFS API](https://open-meteo.com/en/docs/gfs-api)、[通用预报 API](https://open-meteo.com/en/docs)。

ECMWF 请求实例（换行仅为阅读）：

```text
GET https://api.open-meteo.com/v1/ecmwf
latitude=24.490&longitude=118.040
hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation,weather_code,temperature_2m
models=ecmwf_ifs025&wind_speed_unit=ms&temperature_unit=celsius
precipitation_unit=mm&timezone=Asia/Shanghai&cell_selection=nearest
start_date=2026-10-10&end_date=2026-10-12
```

| 返回变量 | 项目变量 | 归一单位 | 时间/方向约定 |
| --- | --- | --- | --- |
| `wind_speed_10m` | `wind_speed` | m/s | 10 m 持续风预报 |
| `wind_gusts_10m` | `wind_gust` | m/s | 10 m 阵风预报，与持续风分别求峰值 |
| `wind_direction_10m` | `wind_direction` | degrees | `from`，顺时针从真北，箭头绘制时转换成吹去方向 |
| `precipitation` | `precipitation` | mm | 之前一小时累计，不能与全天总雨量混写 |
| `weather_code` | `weather_code` | WMO code | 平台从模式要素计算，不能替代官方雷暴/预警正文 |
| `temperature_2m` | `temperature` | °C | 2 m 气温预报 |

固定请求 ECMWF IFS 0.25°，文档名义空间分辨率约 25 km、每六小时更新，原模式时步为 3/6 小时。平台返回小时化序列，并不证明原模式原生逐小时。`generationtime_ms` 是请求计算耗时，**不是模式起报时间**。本版本不猜起报时刻，记录 `model_run=null`、`model_run_verified=false`。

降水概率另请求：

```text
GET https://api.open-meteo.com/v1/forecast
latitude=24.490&longitude=118.040&hourly=precipitation_probability
models=gfs_seamless&timezone=Asia/Shanghai&cell_selection=nearest
start_date=2026-10-10&end_date=2026-10-12
```

概率数据单独标为 NOAA GFS/GEFS 经 Open-Meteo 分发。官方 GFS 文档说明其来自集合成员降水超过阈值的比例；名义集合格点约 0.25°，记录约 25 km。该变量保存自己的 source 和返回格点，不继承 ECMWF 格点。不同模式更新和小时化不能合称一次原生 ECMWF 模拟。

两项响应均严格核对 `timezone=Asia/Shanghai` 与 `utc_offset_seconds=28800`，然后把 API 无偏移 ISO 字符串明确存储成 `+08:00`。实际返回的 `latitude/longitude` 是使用的格点，而非请求目标点；计算 WGS84 球面近似距离并逐变量记录。需要的小时范围不足交给覆盖校验阻断，不自动延用邻日值。

## 海洋：每变量实际格点

官方文档：[Marine API](https://open-meteo.com/en/docs/marine-weather-api)。已核验接口为 `https://marine-api.open-meteo.com/v1/marine`。

每次只请求一个 `hourly` 变量，避免将混合模型响应中的一组坐标错误套给所有变量：

```text
GET https://marine-api.open-meteo.com/v1/marine
latitude=24.490&longitude=118.040&hourly=wave_height
models=meteofrance_wave&timezone=Asia/Shanghai&cell_selection=sea
length_unit=metric&start_date=2026-10-10&end_date=2026-10-12
```

| 返回变量 | 固定请求模式 | 名义分辨率/原生时步 | 归一变量和单位 |
| --- | --- | --- | --- |
| `wave_height` | `meteofrance_wave` | 约 8 km / 3 小时 | `wave_height`, m |
| `swell_wave_height` | `meteofrance_wave` | 约 8 km / 3 小时 | `swell_height`, m |
| `swell_wave_period` | `meteofrance_wave` | 约 8 km / 3 小时 | `swell_period`, s |
| `ocean_current_velocity` | `meteofrance_currents` | 约 8 km / 小时 | `current_speed`, m/s |
| `ocean_current_direction` | `meteofrance_currents` | 约 8 km / 小时 | `current_direction`, degrees，`toward` |
| `sea_surface_temperature` | `meteofrance_currents` | 约 8 km / 6 小时 | `sea_temperature`, °C |

Météo-France/Copernicus Marine 模式在平台上小时化。风浪模式文档更新约每十二小时，海流/海温约每日；实际起报时刻仍未在响应中独立验证。波高指有效波高，不是最大单个浪。流向是水流去向（0°向北、90°向东），不同于风来向。海温不是湾内现场测量。

实际响应 `hourly_units` 决定转换，而不是请求意图决定单位。调查中给海洋接口传 `velocity_unit=ms` 仍取得 `km/h`；实现不依赖这个未经有效验证的参数。若实际返回 `km/h` 则数值除以 3.6 后存 m/s，保存 `original_unit` 和 `unit_conversion_factor`。未知单位直接判缺数据。

海沧调查中不同单变量请求返回波浪格点约 `(24.458336,118.04167)`，海流格点约 `(24.458336,118.125015)`，说明不能共享“海洋格点”。生产 run 记录每一项本次真实返回值，这个示例没有被硬编码作后续格点。

官方文档明确提醒近岸潮汐/流的精度受限，并不适用于沿岸导航。本版本不请求 `sea_level_height_msl` 去替代官方潮位站，海流站也不由潮位站冒充。8 km 预报没有足以代表海沧湾、五缘湾、环东湾内细节的证据；自动添加 `COASTAL_MODEL_UNSUITABLE` 阻断缺口。只允许画实际格点的箭头，不能画虚构全湾流场。

## 访问条款和商用配置

核验：[Open-Meteo 使用条款](https://open-meteo.com/en/terms)、[许可](https://open-meteo.com/en/licence)、[定价与订阅](https://open-meteo.com/en/pricing)。

数据许可为 CC BY 4.0，需要 Open-Meteo 和原始数据机构的署名。**免费托管 API 访问仅允许非商业用途**，与数据 CC BY 许可是不同层面的条件。当前免费限制包含每日少于 10,000、每小时 5,000、每分钟 600 次；长范围/多变量请求可能按平台计费规则折算。上线付费、广告、商业产品或商业推广不可以仅因为本项目代码开源就使用免费端点。

默认用于私人/研究/非商业体验。商业模式：

```text
MARINE_REPORT_OPEN_METEO_MODE=commercial
MARINE_REPORT_OPEN_METEO_API_KEY=<从私人环境配置，不提交仓库>
marine-report fetch --date 2026-10-10 --sites haicang wuyuan huandong --output runs --commercial
```

商业模式使用文档规定的 `customer-api.open-meteo.com` 与 `customer-marine-api.open-meteo.com`，没有 key 则启动前报错。订阅资格、费用和当前条款由部署者核验，本项目没有调用未经授权的付费服务。key 不出现在对象 repr、日志、manifest 请求或公开夹具中；包含 key 回显的响应原件会被扣留并记录原因，不能伪装成正常原件。

## 官方预警/海洋预报正文

机构页面：[自然资源部厦门海洋中心 / 厦门海洋预报台](https://www.fjocean.com/)，公开预报页：[wx.fjocean.com](https://wx.fjocean.com/)。本版本自动下载这两个实际官方页面，冻结 HTTP 返回原件，并保留访问失败状态。未为其猜测未核验 API。

网页索引和预报页可能滚动更新、采用动态内容或只有“今天/明天”和月日。2026-10-10 的网页检索还显示前日发布内容，以及一处 `nu:ll` 潮时搭配 `0cm`。本版本不从该异常显示导出零潮高，也不把前日具体数值当作目标日数据。解析器尚未做到可靠取得所有目标日正文，因此每个地点的 `warning` 与 `marine_forecast` 默认为 `not_obtained`，显示“截至取证时尚未取得”，不是“无预警”。没有可靠证据区分“尚未发布”和“抓取失败”时，不声称 `not_yet_published`。

## 可选本地证据导入

`fetch --evidence-dir <目录>` 适合已由独立人员核验的官方原件与结构化提取。它补充自动取数，不改变默认联网路径、不自动批准报告、不能凭一个 boolean 解除湾内模式适用性缺口。

目录包含 `bundle.json` 及其引用的原件。bundle 的字段：

```text
request_date: 请求目标日
synthetic: false
import_review: {reviewer, checked_at(显式时区), original_source_values_checked:true}
sources[]: {id, file(目录内相对路径), sha256, retrieved_at, request:{url,params},
            http_status:200, kind, license, metadata}
stations[]: {site_id, source_id, id, name, latitude, longitude, datum,
             timezone_verified, station_relation_verified, provenance, limitations, coordinate_crs, ...}
series[], extrema[], official_documents[]: 与 manifest 同结构
```

必须使用公开、无凭据的来源 URL，所有源文件哈希匹配；不允许路径穿越、私人 URL、混入合成数据、错目标日或缺独立核验记录。建议所有导入 source 使用 `import_` 前缀；`weather_`、`marine_`、`tide_`、`xiamen_` 为自动源保留前缀，不允许导入覆盖。提取数据与原始正文分别冻结：提取源的 `metadata.original_source_id` 指向同 bundle 中保存原始正文的源。提取源的文件使用 JSON 包装 `{series:[...], extrema:[...], official_documents:[...]}`；bundle 中每个规范化记录必须与包装文件中的完整记录相同。这让 manifest 数字改动能够由原件绑定校验发现，同时如实说明这是人工审阅的提取，非原 API 原生字段。原始正文与提取仍需独立人员对照核验。

站点导入是完整记录替换，并需要官方提取 source_id。提取源的 JSON 包装另有 `stations:[...]`，与 bundle 里的完整站点记录一致；运行时去掉 site_id 后形成 reference_station，所有字段均由该冻结记录绑定。如果导入声明站点时区已经核实，必须给出 `timezone=Asia/Shanghai` 和 `timezone_evidence_source_id`，指向经过独立核验的官方原件；不能只给 `timezone_verified=true`。站点水域关系同理需 `relation_evidence_source_id`。本程序不自动理解一份时区正文，不将这种导入审阅等同于最终报告审核。原件补足时间语义后，三段范围和每变量逐小时覆盖仍需常规 `validate` 门禁。

## 坐标、地图、日落与故障行为

三个目标点是项目人工选择的约略 WGS84 规划点，分别为海沧 `(24.490,118.040)`、五缘湾 `(24.534,118.169)`、环东 `(24.603,118.174)`，不是测绘点或许可下水口。没有使用 GCJ-02 标记冒充 WGS84。源格点由每次 API 响应取得并注明偏移；官方潮站坐标保留“源未说明 CRS”。地图为程序绘制的点位示意，没有下载商业底图或将静态图标注成实时流场。日落由本地天文算法按地点、覆盖日期计算，输出“约/估算”，未修正局部地形、地平线与天气。

HTTP 请求最多尝试两次，每次超时默认 20 秒，上限 120 秒；暂时性网络/429/5xx失败在中间等待 0.25 秒，语义错误不无限重试。HTTP 200 但 `State=false/Data=null`、空响应、错日期、时区不符、格点缺失、单位未知都属于缺证据。跨日潮表缺失写 `BLOCKED_CROSS_DAY_DATA` 并保留源包。传输使用原始公开域名直连（不继承环境代理），国内官方源失败不会换海外镜像。原始响应最大 8 MiB；超限记为缺失，异常文字不把完整含密钥请求泄露到日志。

联网 smoke 是手动行为，CI 单元及集成验证使用离线合成数据。合成成功只说明程序流程可运行，不表示真实全范围海况成功。本版本没有 n8n、Claude 或其他付费模型调用，`analysis_provider=none`。
