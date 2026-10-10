# 独立报告审核

先打开 run/latest_candidate.json 指向的候选目录，逐页查看 PDF、各水域综合 PNG 和各水域独立地图 PNG，再回查 evidence 原件。审核记录的 candidate_digest 必须是该候选 candidate.json 的摘要。不得让分析提供者自我批准，也不得为 BLOCKED 候选提交 approve。

review.json 格式如下。请据实填写 reviewer 和 checked_at，将摘要替换为实际候选值；模板的 false 必须在完成对应核验后才能改为 true。不是脚本自动勾选的批准模板。

```json
{
  "reviewer": {"id": "independent-human-reviewer"},
  "checked_at": "2026-10-10T09:00:00+08:00",
  "candidate_digest": "paste-the-actual-final-candidate-digest",
  "checklist": {
    "request_and_source_dates": false,
    "source_hashes": false,
    "tide_extrema_and_hourly_values": false,
    "wind_and_gust_peaks": false,
    "hourly_and_total_precipitation": false,
    "weather_codes_and_official_warnings": false,
    "spatial_applicability": false,
    "three_flood_segments_cross_day_coverage": false,
    "tide_and_current_distinction": false,
    "all_pdf_pages_and_three_pngs_readable": false,
    "map_images_and_source_attribution": false
  },
  "notes": "Describe limitations, corrections and original sources inspected"
}
```

独立地图新增的 `map_images_and_source_attribution` 必须完成以下核验后才能勾选；综合图可读性的旧清单项继续单独保留。

1. 逐张打开海沧湾、五缘湾、环东的独立地图；只选择部分水域时逐张检查所选水域。核对地图标题、目标日期、`UNAPPROVED_REFERENCE` 或 `PARTIAL` 标签，检查岸线、地名、图例和来源归属在放大后可读。地图不能替代原有每地综合图。
2. 核对目标规划点、参考潮位站及每个气象/海洋变量格点；其坐标、距离、精度和局限须与冻结 manifest 一致。地图采用 WGS84，不能暗中当作 GCJ-02 使用；规划点不是经许可的下水口，潮位站不能充当潮流站。
3. 回查 `map_sources.json`、地图 artifact 的 `map_source_id`、派生 GeoJSON 与原始 OSM 响应的 SHA-256；确认两份静态原件均包含在候选证据包中，清楚标注 OpenStreetMap 来源、贡献者及 ODbL 许可。不能只查看图片存在或接口 HTTP 200。
4. 独立核对真实静态地图的抓取 UTC 时间和 OSM 快照时间；它们与报告目标日期、合成气象潮位的生成时间分别记录。地图地理底图是静态资料，不是目标日期的实时岸线、水深或全湾潮流场。合成演示中的站点和格点仍是合成值，不能因真实底图而冒称实测定位。
5. 检查流向箭头只落在源支持的实际格点，来向/去向与北↑东→定义一致；零流速和缺失值不应绘制强方向箭头。地图不能给出未经证据支持的全湾流场或现场可下水结论。

候选包必须含每个所选水域一张 `map_png`、原有综合 PNG、一个合并 PDF 及被哈希绑定的地图来源记录。程序会检查地图数量、PNG 结构与最低尺寸，以及静态源原件的哈希关系；这些结构检查不能代替上述逐图审核。缺少地图、删除来源绑定或把地图类型改成普通图片，均不能绕过审核门禁。有静态地图来源的新 run 必须保留对应地图输出。

执行 `marine-report review --run runs/<run-id> --decision approve --review-file review.json`。失败或不完整的候选可以记录 reject。以运行目录中的规范候选为审核对象；对外拷贝始终是未批准查看版。`checked_at` 必须不早于最终候选生成时间且不晚于实际审核时刻。调用 review_status 会重查原件、候选文件和当前候选摘要；编辑地图、来源归属、PDF、综合图或重排产生的新候选均使旧决定失效。缺少冻结地图源的旧 run 应重新执行 fetch 或 demo。

这是一份与哈希绑定的独立审核记录，没有密码学身份验证。操作者应在自己的可信流程中核实审核者身份，不应把此 JSON 当成防伪电子证书。
