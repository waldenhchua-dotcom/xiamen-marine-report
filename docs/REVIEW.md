# 独立报告审核

先打开 run/latest_candidate.json 指向的候选目录，逐页查看 PDF 和全部 PNG，再回查 evidence 原件。审核记录的 candidate_digest 必须是该候选 candidate.json 的摘要。不得让分析提供者自我批准，也不得为 BLOCKED 候选提交 approve。

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
    "all_pdf_pages_and_three_pngs_readable": false
  },
  "notes": "Describe limitations, corrections and original sources inspected"
}
```

执行 `marine-report review --run runs/<run-id> --decision approve --review-file review.json`。失败或不完整的候选可以记录 reject。以运行目录中的规范候选为审核对象；对外拷贝始终是未批准查看版。调用 review_status 会重查原件、候选文件和当前候选摘要；文件变化或重排产生的新候选使旧决定失效。

这是一份与哈希绑定的独立审核记录，没有密码学身份验证。操作者应在自己的可信流程中核实审核者身份，不应把此 JSON 当成防伪电子证书。
